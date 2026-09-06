import { useEffect, useMemo, useRef, useState } from 'react';
import type { ViewKey } from '../App';
import { api, BriefData, BriefScreen, JobStatus } from '../lib/api';
import { won } from '../lib/format';
import { phaseLabel } from '../lib/labels';
import { SelectTicker } from '../lib/nav';
import NextSteps from './NextSteps';
import Term from './Term';
import ViewHeader from './ViewHeader';

const ROW_CAP = 10;
const changeClass = (value: number | null | undefined) => value == null ? '' : value > 0 ? 'change-up' : value < 0 ? 'change-down' : '';
// 브리핑 페이로드의 `_pct`는 전부 이미 0~100 퍼센트다. 어떤 값에도 100을 곱하지 않는다.
const pctText = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)}%`;
const signedPct = (value: number | null | undefined) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const dash = (value: string | null | undefined) => value && value.length ? value : '—';
const uniqueTickers = (...groups: { ticker: string }[][]) => {
  const seen: string[] = [];
  for (const group of groups) for (const row of group) if (!seen.includes(row.ticker)) seen.push(row.ticker);
  return seen;
};
// 트리거된 계획이 먼저, 그다음은 진입가까지 가까운 순. 거리가 없는 계획은 맨 뒤로 민다.
const planOrder = (a: { triggered: boolean; distance_pct: number | null }, b: { triggered: boolean; distance_pct: number | null }) => {
  if (a.triggered !== b.triggered) return a.triggered ? -1 : 1;
  const left = a.distance_pct == null ? Number.POSITIVE_INFINITY : Math.abs(a.distance_pct);
  const right = b.distance_pct == null ? Number.POSITIVE_INFINITY : Math.abs(b.distance_pct);
  return left - right;
};

export default function BriefPanel({ onSelect, onOpenView }: { onSelect: SelectTicker; onOpenView: (view: ViewKey) => void }) {
  const [data, setData] = useState<BriefData | null>(null);
  const [loading, setLoading] = useState(true);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [message, setMessage] = useState('');
  const [tone, setTone] = useState<'ok' | 'warn' | 'error' | undefined>(undefined);
  const [expanded, setExpanded] = useState<number[]>([]);
  const [allScreens, setAllScreens] = useState<{ id: number; name: string }[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const timer = useRef<number | null>(null);

  const fail = (error: unknown, fallback: string) => { setTone('error'); setMessage(error instanceof Error ? error.message : fallback); };

  const load = async () => {
    setLoading(true);
    try { setData(await api.brief()); } catch (error) { fail(error, '브리핑을 불러올 수 없습니다.'); } finally { setLoading(false); }
  };

  // 수집 작업은 백그라운드에서 돌기 때문에 진행 상황을 1.5초 간격으로 되묻는다.
  const poll = () => {
    timer.current = window.setTimeout(async () => {
      try {
        const status = await api.signalStatus();
        setJob(status);
        if (status.running) { poll(); return; }
        setCapturing(false);
        if (status.error) { setTone('error'); setMessage(`신호 수집 실패: ${status.error}`); }
        else {
          const result = status.result;
          setTone('ok');
          setMessage(result ? `신호 수집 완료 — 프리셋 ${result.screens}개 · 거래일 ${result.dates}일 · ${result.rows}건 저장 (건너뜀 ${result.skipped})` : '신호 수집을 마쳤습니다.');
        }
        load();
      } catch (error) { setCapturing(false); fail(error, '신호 수집 진행 상황을 확인할 수 없습니다.'); }
    }, 1500);
  };

  const capture = async () => {
    setTone(undefined);
    setMessage('');
    setCapturing(true);
    try {
      const started = await api.captureSignals({ days: 1, force: false });
      setJob(started);
      if (started.running) { poll(); return; }
      setCapturing(false);
      if (started.error) { setTone('error'); setMessage(`신호 수집 실패: ${started.error}`); }
      load();
    } catch (error) { setCapturing(false); fail(error, '신호 수집을 시작할 수 없습니다.'); }
  };

  // 추적할 프리셋 설정. `tracked_screen_ids`가 null이면 전체 프리셋을 추적하는 것으로 취급한다.
  const toggleTrackedScreen = async (id: number) => {
    const current = data?.tracked_screen_ids ?? allScreens.map(screen => screen.id);
    const next = current.includes(id) ? current.filter(value => value !== id) : [...current, id];
    try { setData(await api.saveBriefScreens(next.length === allScreens.length ? null : next)); }
    catch (error) { fail(error, '브리핑 설정을 저장할 수 없습니다.'); }
  };

  const resetTrackedScreens = async () => {
    try { setData(await api.saveBriefScreens(null)); }
    catch (error) { fail(error, '브리핑 설정을 저장할 수 없습니다.'); }
  };

  useEffect(() => {
    load();
    api.signalStatus().then(status => { setJob(status); if (status.running) { setCapturing(true); poll(); } }).catch(() => undefined);
    api.screens().then(rows => setAllScreens(rows.map(row => ({ id: row.id, name: row.name })))).catch(() => undefined);
    return () => { if (timer.current !== null) window.clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const running = capturing || (job?.running ?? false);

  const summary = useMemo(() => {
    if (!data) return null;
    return {
      // 최초 수집 프리셋은 비교 대상이 없어 전 종목이 entered로 온다. 신규 건수에 섞으면 숫자가 의미를 잃는다.
      entered: data.screens.reduce((total, screen) => total + (screen.baseline ? 0 : screen.entered.length), 0),
      exited: data.screens.reduce((total, screen) => total + screen.exited.length, 0),
      baselines: data.screens.filter(screen => screen.baseline).length,
      near: data.plans.filter(plan => plan.near).length,
      reached: data.watchlist.reached.length,
      unprotected: data.positions.filter(position => position.unprotected).length,
    };
  }, [data]);

  const plans = useMemo(() => data ? [...data.plans].sort(planOrder) : [], [data]);
  const planTickers = plans.map(plan => plan.ticker);
  const reachedTickers = data ? data.watchlist.reached.map(item => item.ticker) : [];
  const positionTickers = data ? data.positions.map(position => position.ticker) : [];

  const screenBlock = (screen: BriefScreen) => {
    const tickers = uniqueTickers(screen.entered, screen.exited, screen.streak_leaders);
    const open = expanded.includes(screen.screen_id);
    // 최초 수집은 100건이 넘게 오기도 한다. 브리핑은 훑는 화면이라 상위 몇 줄만 두고 나머지는 접는다.
    const enteredRows = open ? screen.entered : screen.entered.slice(0, ROW_CAP);
    const exitedRows = open ? screen.exited : screen.exited.slice(0, ROW_CAP);
    const hidden = (screen.entered.length - enteredRows.length) + (screen.exited.length - exitedRows.length);
    const quiet = !screen.entered.length && !screen.exited.length;
    return <div className="stack" key={screen.screen_id}>
      <div className="toolbar">
        <div className="section-title">{screen.name}</div>
        <span className="badge">{dash(screen.date)}</span>
        {screen.stale && <span className="badge" data-tone="warn">기준일보다 오래됨</span>}
        <span className="badge">{screen.matched}종목 적중</span>
        {screen.baseline
          ? <span className="badge" data-tone="accent">최초 수집 (비교 대상 없음)</span>
          : <>
            <span className="badge" data-tone={screen.entered.length ? 'ok' : undefined}>신규 {screen.entered.length}</span>
            <span className="badge" data-tone={screen.exited.length ? 'down' : undefined}>이탈 {screen.exited.length}</span>
            <span className="badge">유지 {screen.held}</span>
          </>}
      </div>
      {quiet
        ? <div className="subtle">{screen.baseline
          ? '최초 수집이지만 적중한 종목이 없습니다. 조건이 너무 좁거나 그날 시장에 맞는 종목이 없었습니다.'
          : `신규 편입도 이탈도 없습니다. 직전 거래일(${dash(screen.previous)}) 대비 구성 그대로입니다.`}</div>
        : <>
          {!!enteredRows.length && <table className="table table--nowrap table--rows">
            <thead><tr>{[screen.baseline ? '후보' : '신규', '시장', '종가', '등락률', '순위'].map(label => <th key={label} className={label === '시장' || label === '후보' || label === '신규' ? '' : 'num'}>{label}</th>)}</tr></thead>
            <tbody>
              {enteredRows.map(row => <tr key={row.ticker} onClick={() => onSelect(row.ticker, tickers)} title="종목 상세로 이동">
                <td>{row.name ?? row.ticker}<span className="subtle mono"> {row.ticker}</span></td>
                <td className="subtle">{dash(row.market)}</td>
                <td className="num">{won(row.close)}</td>
                <td className={`num ${changeClass(row.change_pct)}`}>{signedPct(row.change_pct)}</td>
                <td className="num">{row.rank}</td>
              </tr>)}
            </tbody>
          </table>}
          {!!exitedRows.length && <div className="chip-row">
            <span className="subtle">이탈</span>
            {exitedRows.map(row => <button className="chip" key={row.ticker} onClick={() => onSelect(row.ticker, tickers)}>{row.name ?? row.ticker}<span className="mono">{row.ticker}</span></button>)}
          </div>}
          {(hidden > 0 || open) && <button className="btn btn--ghost btn--sm" onClick={() => setExpanded(current => open ? current.filter(id => id !== screen.screen_id) : [...current, screen.screen_id])}>
            {open ? '접기' : `…외 ${hidden}건 펼치기`}
          </button>}
        </>}
      {!!screen.streak_leaders.length && <div className="chip-row">
        <span className="subtle">연속 편입</span>
        {screen.streak_leaders.map(row => <button className="chip" key={row.ticker} onClick={() => onSelect(row.ticker, tickers)}>{row.name ?? row.ticker}<span className="mono">연속 {row.streak_days ?? 1}일</span></button>)}
      </div>}
    </div>;
  };

  // .page가 스크롤을 맡고 안쪽 .panel은 내용만큼 자란다. 패널 자체를 스크롤 컨테이너로 두면 높이가 없어 상단 내비게이션까지 밀려난다.
  return <div className="page"><div className="panel panel--pad stack stack--lg">
    <ViewHeader
      title="오늘의 브리핑"
      lede={<>이미 쌓인 <Term id="signal_log" />·계획·관심종목·보유만 읽어 오늘 볼 것을 모읍니다. 스크리너를 다시 돌리지 않으므로 즉시 뜹니다.</>}
      meta={<>
        <span className="badge">기준일 {data ? dash(data.as_of) : '—'}</span>
        <span className="badge">직전 {data ? dash(data.previous) : '—'}</span>
      </>}
      actions={<>
        <button className="btn btn--ghost" onClick={load} disabled={loading}>새로고침</button>
        <button className="btn btn--primary" onClick={capture} disabled={running} title="저장된 스크리너 프리셋을 오늘 날짜로 다시 돌려 신호 로그에 남깁니다.">신호 수집</button>
        <button className="btn btn--ghost" aria-pressed={showSettings} onClick={() => setShowSettings(current => !current)} title="브리핑의 프리셋 신호 구획에서 추적할 프리셋을 고릅니다.">프리셋 설정</button>
        {/* 작업을 막 띄운 직후에는 서버가 아직 총 건수를 세지 않아 total·current가 비어 온다. 그때는 '준비 중'만 보여 준다. */}
        {running && <span className="progress-note"><span className="spinner" />
          {job?.total == null ? '준비 중' : `${job.processed ?? 0}/${job.total}`}
          {job?.current ? ` ${job.current}` : ''}
        </span>}
      </>}
    />

    <NextSteps data={data} onOpenView={onOpenView} />

    {showSettings && <div className="stack">
      <div className="toolbar">
        <div className="section-title">추적할 프리셋</div>
        <span className="badge">{data?.tracked_screen_ids ? `${data.tracked_screen_ids.length}/${allScreens.length}` : `전체 ${allScreens.length}`}</span>
        <button className="btn btn--ghost push" onClick={resetTrackedScreens} disabled={!data?.tracked_screen_ids}>전체 추적</button>
      </div>
      <div className="chip-row">
        {allScreens.map(screen => <label className="check" key={screen.id}>
          <input type="checkbox" checked={data ? (data.tracked_screen_ids ?? allScreens.map(item => item.id)).includes(screen.id) : true} onChange={() => toggleTrackedScreen(screen.id)} />
          {screen.name}
        </label>)}
        {!allScreens.length && <span className="subtle">저장된 프리셋이 없습니다. 스크리너 화면에서 프리셋을 먼저 저장하세요.</span>}
      </div>
    </div>}

    {message && <div className="msg" data-tone={tone}>{message}</div>}
    {loading && !data && <div className="empty empty--inline">브리핑을 불러오는 중입니다…</div>}
    {!loading && !data && <div className="empty empty--inline">브리핑을 불러오지 못했습니다. 새로고침을 눌러 다시 시도하세요.</div>}

    {data && summary && <>
      <div className="chip-row">
        <span className="badge" data-tone={summary.entered ? 'ok' : undefined}>신규 신호 {summary.entered}</span>
        {!!summary.baselines && <span className="badge" data-tone="accent"><Term id="baseline">최초 수집</Term> {summary.baselines}</span>}
        <span className="badge" data-tone={summary.exited ? 'down' : undefined}>이탈 {summary.exited}</span>
        <span className="badge" data-tone={summary.near ? 'warn' : undefined}>트리거 대기 {summary.near}</span>
        <span className="badge" data-tone={summary.reached ? 'accent' : undefined}>목표 도달 {summary.reached}</span>
        <span className="badge" data-tone={summary.unprotected ? 'danger' : undefined}>손절 없는 보유 {summary.unprotected}</span>
        <span className="badge" data-tone={data.heat.over_limit ? 'danger' : undefined}><Term id="heat">히트</Term> {pctText(data.heat.heat_pct)}</span>
      </div>

      {!!data.warnings.length && <div className="stack">
        <div className="section-title">경고</div>
        {data.warnings.map(warning => <div className="msg" data-tone="warn" key={warning}>{warning}</div>)}
      </div>}

      {data.screens.length
        ? <div className="stack stack--lg">
          <div className="toolbar">
            <div className="section-title">프리셋 신호</div>
            {!!data.tracked_screen_ids && <span className="badge" data-tone="accent" title="브리핑 설정에서 고른 프리셋만 봅니다.">추적 {data.tracked_screen_ids.length}/{allScreens.length}</span>}
          </div>
          {data.screens.map(screenBlock)}
        </div>
        : <div className="stack">
          <div className="toolbar">
            <div className="section-title">프리셋 신호</div>
            {!!data.tracked_screen_ids && <span className="badge" data-tone="accent" title="브리핑 설정에서 고른 프리셋만 봅니다.">추적 {data.tracked_screen_ids.length}/{allScreens.length}</span>}
          </div>
          <div className="msg">신호 로그가 비어 있습니다. 저장된 스크리너 프리셋을 과거 거래일에 다시 돌려 적중 종목을 쌓아야 신규 편입·이탈·연속 편입일을 볼 수 있습니다. 위의 <b>신호 수집</b> 버튼이 오늘 하루치를 채웁니다.</div>
        </div>}

      <div className="stack">
        <div className="toolbar">
          <div className="section-title">계획</div>
          <span className="badge">{plans.length}건</span>
          <button className="btn btn--ghost push" onClick={() => onOpenView('plans')}>계획 화면</button>
        </div>
        <table className="table table--nowrap table--rows">
          <thead><tr>{['이름', '종목', '단계', '현재가', '진입가', '손절가', '거리', '사유'].map(label => <th key={label} className={['현재가', '진입가', '손절가', '거리'].includes(label) ? 'num' : ''}>{label}</th>)}</tr></thead>
          <tbody>
            {plans.map(plan => <tr key={plan.plan_id} data-tone={plan.triggered ? 'live' : undefined} aria-selected={plan.triggered} onClick={() => onSelect(plan.ticker, planTickers)} title="종목 상세로 이동">
              <td>{plan.name}{plan.setup && <span className="subtle"> {plan.setup}</span>}</td>
              <td>{plan.ticker_name ?? plan.ticker}<span className="subtle mono"> {plan.ticker}</span></td>
              <td>
                <span className="badge">{phaseLabel[plan.phase] ?? plan.phase}</span>
                {plan.triggered && <span className="badge" data-tone="live"> 트리거</span>}
                {!plan.triggered && plan.near && <span className="badge" data-tone="warn"> 근접</span>}
              </td>
              <td className="num">{won(plan.close)}</td>
              <td className="num">{won(plan.entry_price)}</td>
              <td className="num">{won(plan.stop_price)}</td>
              <td className={`num ${plan.near ? 'ok' : 'muted'}`}>{signedPct(plan.distance_pct)}</td>
              <td className="subtle">{dash(plan.reason)}</td>
            </tr>)}
            {!plans.length && <tr><td colSpan={8} className="subtle">활성 계획이 없습니다. 계획 화면에서 진입가·손절가를 정한 계획을 만들면 여기에서 거리와 트리거 여부를 봅니다.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="stack">
        <div className="toolbar">
          <div className="section-title">관심종목 목표 도달</div>
          <span className="badge">{data.watchlist.lists}개 목록 · {data.watchlist.items}종목</span>
          <button className="btn btn--ghost push" onClick={() => onOpenView('watchlist')}>관심종목 화면</button>
        </div>
        <table className="table table--nowrap table--rows">
          <thead><tr>{['목록', '종목', '종가', '목표가', '괴리'].map(label => <th key={label} className={['종가', '목표가', '괴리'].includes(label) ? 'num' : ''}>{label}</th>)}</tr></thead>
          <tbody>
            {data.watchlist.reached.map(item => <tr key={`${item.watchlist_id}-${item.ticker}`} onClick={() => onSelect(item.ticker, reachedTickers)} title="종목 상세로 이동">
              <td className="subtle">{item.watchlist}</td>
              <td>{item.name ?? item.ticker}<span className="subtle mono"> {item.ticker}</span></td>
              <td className="num">{won(item.close)}</td>
              <td className="num">{won(item.target_price)}</td>
              <td className={`num ${changeClass(item.target_gap_pct == null ? null : -item.target_gap_pct)}`}>{signedPct(item.target_gap_pct)}</td>
            </tr>)}
            {!data.watchlist.reached.length && <tr><td colSpan={5} className="subtle">목표가에 닿은 종목이 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="stack">
        <div className="toolbar">
          <div className="section-title">보유</div>
          <span className="badge">{data.positions.length}종목</span>
          <button className="btn btn--ghost push" onClick={() => onOpenView('portfolio')}>포트폴리오 화면</button>
        </div>
        <table className="table table--nowrap table--rows">
          <thead><tr>{['종목', '수량', '평단', '현재가', '평가손익', '비중', '손절 계획', '손절까지'].map(label => <th key={label} className={['종목', '손절 계획'].includes(label) ? '' : 'num'}>{label}</th>)}</tr></thead>
          <tbody>
            {data.positions.map(position => <tr key={position.ticker} onClick={() => onSelect(position.ticker, positionTickers)} title="종목 상세로 이동">
              <td>{position.name}<span className="subtle mono"> {position.ticker}</span></td>
              <td className="num">{position.quantity.toLocaleString('ko-KR')}</td>
              <td className="num">{won(position.avg_cost)}</td>
              <td className="num">{won(position.last_close)}</td>
              <td className={`num ${changeClass(position.unrealized_pct)}`}>{signedPct(position.unrealized_pct)}</td>
              <td className="num">{pctText(position.weight_pct)}</td>
              <td>{position.plan_name ?? <span className="badge" data-tone="danger">손절 없음</span>}</td>
              <td className="num">{pctText(position.stop_distance_pct)}</td>
            </tr>)}
            {!data.positions.length && <tr><td colSpan={8} className="subtle">보유 종목이 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>
    </>}
  </div></div>;
}
