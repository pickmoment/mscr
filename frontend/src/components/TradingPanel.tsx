import { useEffect, useMemo, useState } from 'react';
import { api, BrokerOrder, PlanCandidate, PlanEvaluation, PlanProposal, PlanSimulation, ReconcileResult, ReviewPlanResult, ReviewStats, RiskHeat, RiskLimits, TradePlan, TradingStatus } from '../lib/api';
import { positionFromTradePlan, storePositionPlan } from '../lib/position';
import { SelectTicker } from '../lib/nav';
import { won } from '../lib/format';

type Draft = { id: number | null; name: string; ticker: string; side: 'buy' | 'sell'; quantity: string; order_type: 'limit' | 'market'; limit_price: string; entry_price: string; stop_price: string; tp1_price: string; tp1_ratio: string; tp2_price: string; tp2_ratio: string; tp3_trailing_pct: string; enabled: boolean; setup: string; note: string };
const emptyForm: Draft = { id: null, name: '', ticker: '', side: 'buy', quantity: '', order_type: 'limit', limit_price: '', entry_price: '', stop_price: '', tp1_price: '', tp1_ratio: '', tp2_price: '', tp2_ratio: '', tp3_trailing_pct: '', enabled: true, setup: '', note: '' };
const statusLabel: Record<string, string> = { dry_run: '모의', submitted: '접수', partial: '부분체결', filled: '체결', rejected: '거부', failed: '실패', skipped: '건너뜀' };
const statusTone: Record<string, string | undefined> = { filled: 'ok', rejected: 'danger', failed: 'danger', submitted: 'live', partial: 'live', dry_run: undefined, skipped: undefined };
const phaseLabel: Record<string, string> = { waiting_entry: '진입 대기', holding: '보유 중', tp1_done: '1차 완료', trailing: '트레일링', closed: '청산 완료' };
const legLabel: Record<string, string> = { entry: '진입', stop: '손절 청산', tp1: '1차 익절', tp2: '2차 익절', trailing: '트레일링 청산' };
// 계획 기간 시뮬레이션의 기본 구간 — 오늘, 그리고 그 180일 전.
const isoDaysAgo = (days: number) => { const date = new Date(); date.setDate(date.getDate() - days); return date.toISOString().slice(0, 10); };
const num = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`;
// 리스크·복기 응답의 *_pct는 이미 0~100 퍼센트라 그대로 찍는다. 위의 pct()는 0~1 비율용이라 섞으면 100배가 어긋난다.
const pctPoint = (value: number | null | undefined, digits = 2) => value == null ? '—' : `${value.toFixed(digits)}%`;
const rMultiple = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)}R`;
const stateLabel: Record<string, string> = { pending: '대기', open: '진행', closed: '종료', disabled: '중지' };
const stateTone: Record<string, string | undefined> = { pending: undefined, open: 'live', closed: 'ok', disabled: undefined };
const rTone = (value: number | null | undefined) => value == null ? 'subtle' : value >= 0 ? 'up' : 'down';
const bindingLabel: Record<string, string> = { max_loss: '최대 손실 금액이 수량을 결정', max_investment: '최대 투자 금액이 수량을 결정' };
// 수동 폼은 모든 값을 문자열로 들고 있다. 표시용 포맷과 상태용 문자열은 분리하고, 상태에는 자릿수를 줄이지 않은 값을 넣는다.
const fieldText = (value: number | null | undefined) => value == null ? '' : String(value);
type ProposeDraft = { ticker: string; side: 'buy' | 'sell'; entry_price: string; max_investment: string; max_loss: string };
const emptyPropose: ProposeDraft = { ticker: '', side: 'buy', entry_price: '', max_investment: '', max_loss: '' };
const candidateColumns: { label: string; title?: string; num?: boolean }[] = [
  { label: '선택' },
  { label: '손절폭', title: '손절가까지의 거리를 ATR의 몇 배로 잡는지입니다.' },
  { label: '손절가', num: true },
  { label: '수량', num: true },
  { label: '실투자액', num: true },
  { label: '최대손실액', num: true, title: '손절가에 그대로 체결됐을 때의 손실입니다. 갭 하락으로 손절가를 건너뛰면 보장되지 않습니다.' },
  { label: '한도소진', num: true, title: '입력한 최대 손실 금액 중 이 후보가 쓰는 비율입니다.' },
  { label: '분할', num: true, title: '1차 익절 / 2차 익절 / 트레일링 레그에 배분되는 주식 수입니다.' },
  { label: '1차 목표가', num: true },
  { label: '2차 목표가', num: true },
  { label: '1차 도달확률', num: true, title: '표본 기간 동안 손절보다 1차 목표가에 먼저 도달한 비율입니다.' },
  { label: '2차 도달확률', num: true },
  { label: '본전 필요 1차', num: true, title: '이 구조가 본전이 되려면 1차 목표 도달률이 최소 이 값 이상이어야 합니다. 같은 행의 1차 도달확률과 비교하세요.' },
  { label: '기준선 기대R', num: true, title: '진입 근거가 없을 때의 과거 기준선입니다. 예측이 아니며, 진입 판단이 이 기준선을 넘어야 이익이 납니다.' },
  { label: '트레일링', num: true },
];

type LimitDraft = { risk: string; heat: string };
const riskColumns: { label: string; title?: string; num?: boolean }[] = [
  { label: '이름' },
  { label: '종목' },
  { label: '상태', title: '대기 = 아직 진입 전, 진행 = 체결되어 보유 중, 종료 = 청산 완료, 중지 = 사용 꺼짐입니다.' },
  { label: '수량', num: true },
  { label: '현재 위험', num: true, title: '지금 손절가에 닿으면 잃는 금액입니다. 진입 후 손절가를 올렸다면 최초 위험보다 작아집니다.' },
  { label: '최초 위험', num: true, title: '계획을 세울 때의 진입가와 손절가로 계산한 손실 금액입니다.' },
  { label: '자산 대비', num: true, title: '현재 위험이 총자산에서 차지하는 비율입니다.' },
];

/** 포트폴리오 히트. 계획 하나하나의 손실 한도는 그 합을 묶어 주지 않는다. 이 구간이 그 합을 본다. */
function RiskSection({ heat, busy, onSaveLimits }: { heat: RiskHeat | null; busy: boolean; onSaveLimits: (limits: RiskLimits) => void }) {
  const [draft, setDraft] = useState<LimitDraft>({ risk: '', heat: '' });
  const [showClosed, setShowClosed] = useState(false);
  // 한도 저장 응답이 새 히트를 그대로 주므로, 폼은 서버가 확정한 값만 되비춘다.
  useEffect(() => { if (heat) setDraft({ risk: String(heat.limits.risk_per_trade_pct), heat: String(heat.limits.max_portfolio_heat_pct) }); }, [heat]);
  const rows = heat ? heat.plans.filter(row => showClosed || row.state === 'open' || row.state === 'pending') : [];
  const hidden = heat ? heat.plans.length - rows.length : 0;
  const saveLimits = (event: React.FormEvent) => {
    event.preventDefault();
    onSaveLimits({ risk_per_trade_pct: Number(draft.risk), max_portfolio_heat_pct: Number(draft.heat) });
  };

  return <section className="panel autoplan-panel">
    <h1>리스크 (포트폴리오 히트)</h1>
    <p className="subtle">보유 중인 거래와 대기 중인 계획이 <b>모두 손절에 닿았을 때</b> 잃는 총액입니다. 계획마다 손실 한도를 걸어도 그 합에는 상한이 생기지 않습니다. 히트는 그 합을 총자산으로 나눈 값입니다.</p>
    {!heat ? <div className="empty">리스크 정보를 불러오지 못했습니다.</div> : <div className="stack">
      <div className="autoplan-head">
        <span className="badge" data-tone={heat.over_limit ? 'danger' : 'ok'}>히트 {pctPoint(heat.heat_pct)} / 한도 {heat.limits.max_portfolio_heat_pct}%</span>
        <span className="subtle">기준일 {heat.as_of || '—'} · 보유분만 보면 {pctPoint(heat.open_heat_pct)} · 계획 {heat.plans.length}건</span>
      </div>
      <div className="grid grid--auto">
        <div className="stat-card"><label>진행 위험</label><strong>{won(heat.open_risk_krw)}</strong><span className="subtle">체결된 포지션이 손절까지 잃을 금액</span></div>
        <div className="stat-card"><label>대기 위험</label><strong>{won(heat.pending_risk_krw)}</strong><span className="subtle">아직 진입하지 않은 계획의 최초 위험</span></div>
        <div className="stat-card"><label>총자산</label><strong>{won(heat.equity)}</strong><span className="subtle">현금 {won(heat.cash_krw)} · 평가 {won(heat.market_value)}</span></div>
        <div className="stat-card"><label>남은 여유</label><strong className={heat.budget.remaining_krw > 0 ? undefined : 'down'}>{won(heat.budget.remaining_krw)}</strong><span className="subtle">한도 {won(heat.budget.heat_limit_krw)} · 총 위험 {won(heat.total_risk_krw)}</span></div>
      </div>
      <form className="autoplan-form" onSubmit={saveLimits}>
        <label>1회 위험(%)<input required type="number" min="0" step="any" value={draft.risk} onChange={event => setDraft(current => ({ ...current, risk: event.target.value }))} title="계획 한 건에 허용하는 손실을 총자산 대비 비율로 정합니다. 계획 자동 생성의 최대 손실 금액 기본값이 됩니다." /></label>
        <label>히트 한도(%)<input required type="number" min="0" step="any" value={draft.heat} onChange={event => setDraft(current => ({ ...current, heat: event.target.value }))} title="모든 계획의 손실 합계가 넘지 말아야 할 총자산 대비 비율입니다." /></label>
        <button className="btn btn--primary" disabled={busy}>한도 저장</button>
        <span className="subtle">1회 위험 {won(heat.budget.per_trade_krw)}{heat.budget.suggested_max_loss == null ? '' : ` · 다음 계획 권장 손실 한도 ${won(heat.budget.suggested_max_loss)}`}</span>
      </form>
      {heat.warnings.length > 0 && <div className="msg autoplan-warnings" data-tone="warn">
        <b>확인하세요</b>
        <ul>{heat.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
      </div>}
      <div className="toolbar toolbar--tight">
        <div className="section-title">계획별 위험</div>
        <label className="check"><input type="checkbox" checked={showClosed} onChange={event => setShowClosed(event.target.checked)} />종료·중지 포함{hidden > 0 && !showClosed ? ` (${hidden}건 숨김)` : ''}</label>
      </div>
      <div className="autoplan-scroll">
        <table className="table table--nowrap">
          <thead><tr>{riskColumns.map(column => <th key={column.label} className={column.num ? 'num' : undefined} title={column.title}>{column.label}</th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.plan_id}>
            <td>{row.name}{row.setup && <span className="chip">{row.setup}</span>}</td>
            <td className="mono">{row.ticker}</td>
            <td><span className="badge" data-tone={stateTone[row.state]}>{stateLabel[row.state] || row.state}</span></td>
            <td className="num">{num(row.quantity)}</td>
            <td className="num">{won(row.risk_krw)}</td>
            <td className="num">{won(row.initial_risk_krw)}</td>
            <td className="num">{pctPoint(row.risk_pct)}</td>
          </tr>)}</tbody>
        </table>
        {!rows.length && <div className="empty">{heat.plans.length ? '진행·대기 중인 계획이 없습니다. 종료·중지 포함을 켜면 지난 계획이 보입니다.' : '위험을 계산할 계획이 없습니다.'}</div>}
      </div>
      {heat.unprotected.length > 0 && <div className="stack">
        <div className="section-title">손절 없는 보유 <span className="badge" data-tone="danger">{heat.unprotected.length}</span></div>
        <div className="chip-row">{heat.unprotected.map(item => <span className="badge" data-tone="danger" key={item.ticker}>{item.name}({item.ticker}) · {num(item.quantity)}주 · {won(item.market_value)} · 비중 {pct(item.weight)}</span>)}</div>
        <div className="subtle">손절가를 가진 계획이 없는 보유입니다. 최대 손실이 정해져 있지 않아 위의 히트에 잡히지 않습니다. 계획을 만들어 손절을 걸어야 이 표에서 빠집니다.</div>
      </div>}
    </div>}
  </section>;
}

const reviewColumns: { label: string; title?: string; num?: boolean }[] = [
  { label: '진입일' },
  { label: '종목' },
  { label: '이름' },
  { label: '셋업' },
  { label: '상태' },
  { label: '진입가', num: true, title: '실제 체결가와 계획가 대비 슬리피지입니다. 양수는 불리하게 체결됐다는 뜻입니다.' },
  { label: '실현 R', num: true, title: '청산된 물량의 손익을 1R(=진입가-손절가)로 나눈 값입니다.' },
  { label: '미실현 R', num: true, title: '아직 들고 있는 물량의 평가손익을 R로 환산한 값입니다.' },
  { label: 'MAE R', num: true, title: '보유 기간 중 가장 불리했던 지점까지의 폭입니다.' },
  { label: 'MFE R', num: true, title: '보유 기간 중 가장 유리했던 지점까지의 폭입니다.' },
  { label: '보유일', num: true },
];

/** 누적 R 곡선. 차트 라이브러리를 붙일 만한 밀도가 아니라 인라인 SVG로 그린다. */
function EquitySpark({ points }: { points: { date: string; cumulative_r: number }[] }) {
  const width = 260, height = 48, pad = 4;
  const values = points.map(point => point.cumulative_r);
  const top = Math.max(...values, 0), bottom = Math.min(...values, 0);
  const span = top - bottom || 1;
  const path = points.map((point, index) => {
    const x = pad + index * (width - pad * 2) / (points.length - 1);
    const y = pad + (top - point.cumulative_r) * (height - pad * 2) / span;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  const zeroY = pad + top * (height - pad * 2) / span;
  const last = points[points.length - 1];
  return <div className="toolbar toolbar--tight">
    <span className="subtle mono">{points[0].date} {rMultiple(points[0].cumulative_r)}</span>
    <span className={rTone(last.cumulative_r)}>
      <svg width={width} height={height} role="img" aria-label="누적 R 곡선">
        <line x1={pad} x2={width - pad} y1={zeroY} y2={zeroY} stroke="currentColor" strokeOpacity="0.3" strokeDasharray="3 3" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    </span>
    <span className={`mono ${rTone(last.cumulative_r)}`}>{last.date} {rMultiple(last.cumulative_r)}</span>
  </div>;
}

/** 결산. 계획대로 들어가고 나왔는지를 체결 기록으로 되짚는다. */
function ReviewSection({ onSelect }: { onSelect: SelectTicker }) {
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [results, setResults] = useState<ReviewPlanResult[]>([]);
  const [message, setMessage] = useState('');
  const load = () => {
    api.reviewStats().then(setStats).catch(error => setMessage(error instanceof Error ? error.message : '결산 조회 실패'));
    api.reviewPlans().then(setResults).catch(() => setResults([]));
  };
  useEffect(load, []);
  // 체결을 동기화하면 실현 R이 달라진다. 탭을 다시 열지 않아도 반영되게 이벤트로 다시 읽는다.
  useEffect(() => { window.addEventListener('mscr-trades-changed', load); return () => window.removeEventListener('mscr-trades-changed', load); }, []);
  const tickers = useMemo(() => results.map(row => row.ticker), [results]);

  return <section className="panel autoplan-panel">
    <h1>복기 (결산)</h1>
    <p className="subtle">체결 기록을 계획과 맞춰 본 결과입니다. 1R은 계획의 진입가와 손절가 차이라, 실현 R이 +1이면 걸었던 위험만큼 벌었다는 뜻입니다.</p>
    {message && <div className="msg" data-tone="error">{message}</div>}
    {stats && <div className="stack">
      <div className="grid grid--auto">
        <div className="stat-card"><label>거래 수</label><strong>{stats.trades.toLocaleString('ko-KR')}</strong><span className="subtle">승 {stats.wins} · 패 {stats.losses} · 보유 중 {stats.open.count}</span></div>
        <div className="stat-card"><label>승률</label><strong>{pctPoint(stats.win_rate, 1)}</strong><span className="subtle">평균 이익 {rMultiple(stats.avg_win_r)} · 평균 손실 {rMultiple(stats.avg_loss_r)}</span></div>
        <div className="stat-card"><label>기대 R</label><strong className={rTone(stats.expectancy_r)}>{rMultiple(stats.expectancy_r)}</strong><span className="subtle">거래 한 건당 평균 R</span></div>
        <div className="stat-card"><label>합계 R</label><strong className={rTone(stats.total_r)}>{rMultiple(stats.total_r)}</strong><span className="subtle">미실현 {rMultiple(stats.open.total_open_r)}</span></div>
        <div className="stat-card"><label>프로핏팩터</label><strong>{stats.profit_factor == null ? '—' : stats.profit_factor.toFixed(2)}</strong><span className="subtle">총이익 ÷ 총손실</span></div>
        <div className="stat-card"><label>최대 연속 손실</label><strong>{stats.max_consecutive_losses}회</strong><span className="subtle">연달아 진 횟수의 최대</span></div>
        <div className="stat-card"><label>최대 낙폭 R</label><strong className={stats.max_drawdown_r ? 'down' : undefined}>{rMultiple(stats.max_drawdown_r)}</strong><span className="subtle">누적 R 고점 대비 최대 하락</span></div>
        <div className="stat-card"><label>평균 보유일</label><strong>{stats.avg_days_held == null ? '—' : `${stats.avg_days_held.toFixed(1)}일`}</strong><span className="subtle">진입일부터 청산일까지</span></div>
        <div className="stat-card"><label>평균 진입 슬리피지</label><strong>{pctPoint(stats.avg_entry_slippage_pct)}</strong><span className="subtle">양수면 계획가보다 불리하게 체결</span></div>
      </div>
      {stats.equity_curve.length >= 2 && <div className="stack">
        <div className="section-title">누적 R</div>
        <EquitySpark points={stats.equity_curve} />
      </div>}
      {stats.warnings.length > 0 && <div className="msg autoplan-warnings" data-tone="warn">
        <b>확인하세요</b>
        <ul>{stats.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
      </div>}
      <div className="grid grid--2">
        <div className="stack">
          <div className="section-title">셋업별</div>
          <table className="table table--nowrap">
            <thead><tr><th>셋업</th><th className="num">거래</th><th className="num">승률</th><th className="num">평균 R</th><th className="num">합계 R</th></tr></thead>
            <tbody>{stats.by_setup.map(group => <tr key={group.setup || '—'}>
              <td>{group.setup || '태그 없음'}</td>
              <td className="num">{group.trades}</td>
              <td className="num">{pctPoint(group.win_rate, 1)}</td>
              <td className={`num ${rTone(group.avg_r)}`}>{rMultiple(group.avg_r)}</td>
              <td className={`num ${rTone(group.total_r)}`}>{rMultiple(group.total_r)}</td>
            </tr>)}</tbody>
          </table>
          {!stats.by_setup.length && <div className="empty">셋업 태그가 붙은 청산 거래가 없습니다.</div>}
        </div>
        <div className="stack">
          <div className="section-title">월별</div>
          <table className="table table--nowrap">
            <thead><tr><th>월</th><th className="num">거래</th><th className="num">승률</th><th className="num">평균 R</th><th className="num">합계 R</th></tr></thead>
            <tbody>{stats.by_month.map(group => <tr key={group.month || '—'}>
              <td className="mono">{group.month || '—'}</td>
              <td className="num">{group.trades}</td>
              <td className="num">{pctPoint(group.win_rate, 1)}</td>
              <td className={`num ${rTone(group.avg_r)}`}>{rMultiple(group.avg_r)}</td>
              <td className={`num ${rTone(group.total_r)}`}>{rMultiple(group.total_r)}</td>
            </tr>)}</tbody>
          </table>
          {!stats.by_month.length && <div className="empty">청산된 거래가 없습니다.</div>}
        </div>
      </div>
    </div>}
    <div className="section-title">계획별 결과 <span className="badge">{results.length}</span></div>
    <div className="autoplan-scroll">
      <table className="table table--nowrap">
        <thead><tr>{reviewColumns.map(column => <th key={column.label} className={column.num ? 'num' : undefined} title={column.title}>{column.label}</th>)}</tr></thead>
        <tbody>{results.map(row => <tr key={row.plan_id}>
          <td className="mono">{row.entry_date || '—'}</td>
          <td><button className="btn btn--quiet btn--sm mono" onClick={() => onSelect(row.ticker, tickers)} title="종목 상세로 이동합니다">{row.ticker}</button> <span className="subtle">{row.ticker_name || ''}</span></td>
          <td>{row.name}</td>
          <td>{row.setup ? <span className="chip">{row.setup}</span> : <span className="subtle">—</span>}</td>
          <td><span className="badge" data-tone={row.status === 'open' ? 'live' : 'ok'}>{row.status === 'open' ? '보유 중' : '청산'}</span></td>
          <td className="num">{won(row.entry_price)} <span className="subtle">{row.entry_slippage_pct == null ? '' : `(${row.entry_slippage_pct >= 0 ? '+' : ''}${row.entry_slippage_pct.toFixed(2)}%)`}</span></td>
          <td className={`num ${rTone(row.realized_r)}`}>{rMultiple(row.realized_r)}</td>
          <td className={`num ${rTone(row.open_r)}`}>{rMultiple(row.open_r)}</td>
          <td className="num down">{rMultiple(row.mae_r)}</td>
          <td className="num up">{rMultiple(row.mfe_r)}</td>
          <td className="num">{row.days_held == null ? '—' : `${row.days_held}일`}</td>
        </tr>)}</tbody>
      </table>
      {!results.length && <div className="empty">체결된 계획이 없습니다. 계획이 진입 체결되면 여기에 결과가 쌓입니다.</div>}
    </div>
  </section>;
}

export default function TradingPanel({ onSelect }: { onSelect: SelectTicker }) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [plans, setPlans] = useState<TradePlan[]>([]);
  const [evaluations, setEvaluations] = useState<PlanEvaluation[]>([]);
  const [orders, setOrders] = useState<BrokerOrder[]>([]);
  const [form, setForm] = useState<Draft>(emptyForm);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [proposeForm, setProposeForm] = useState<ProposeDraft>(emptyPropose);
  const [proposal, setProposal] = useState<PlanProposal | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [heat, setHeat] = useState<RiskHeat | null>(null);
  const [setups, setSetups] = useState<string[]>([]);
  const [simPlan, setSimPlan] = useState<number | null>(null);
  const [simRange, setSimRange] = useState<{ start: string; end: string }>({ start: isoDaysAgo(180), end: new Date().toISOString().slice(0, 10) });
  const [simResult, setSimResult] = useState<PlanSimulation | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState('');
  const [reconcileResult, setReconcileResult] = useState<ReconcileResult | null>(null);
  const [reconcileBusy, setReconcileBusy] = useState(false);
  // 자동 생성의 최대 손실 금액은 리스크 한도에서 나온 권장값으로 시작한다. 사용자가 한 번이라도 고치면 그 값을 덮지 않는다.
  const [maxLossTouched, setMaxLossTouched] = useState(false);
  const fail = (error: unknown, fallback: string) => setMessage(error instanceof Error ? error.message : fallback);
  const loadPlans = () => { api.tradePlans().then(setPlans).catch(error => fail(error, '계획 조회 실패')); api.evaluatePlans().then(setEvaluations).catch(() => setEvaluations([])); };
  const loadHeat = () => api.riskHeat().then(setHeat).catch(() => undefined);
  const loadOrders = () => api.tradeOrders().then(setOrders).catch(() => undefined);
  const loadStatus = () => api.tradingStatus().then(setStatus).catch(() => setStatus({ enabled: false, env: null, account_masked: null, source: null, reason: '브로커 상태를 확인할 수 없습니다.', active_env: 'paper', accounts: { paper: null, real: null } }));
  useEffect(() => { loadStatus(); loadPlans(); loadOrders(); loadHeat(); api.screens().then(rows => setSetups(rows.map(row => row.name))).catch(() => setSetups([])); }, []);
  useEffect(() => { window.addEventListener('mscr-settings-changed', loadStatus); return () => window.removeEventListener('mscr-settings-changed', loadStatus); }, []);
  // 종목 상세 차트에서 계획을 저장하면 이 탭은 다시 마운트되지 않으므로 이벤트로 목록을 새로 읽는다.
  useEffect(() => { const reload = () => { loadPlans(); loadHeat(); }; window.addEventListener('mscr-plans-changed', reload); return () => window.removeEventListener('mscr-plans-changed', reload); }, []);
  useEffect(() => {
    const suggested = heat?.budget.suggested_max_loss;
    if (suggested != null && !maxLossTouched) setProposeForm(current => ({ ...current, max_loss: String(Math.round(suggested)) }));
  }, [heat, maxLossTouched]);
  const saveLimits = async (limits: RiskLimits) => {
    setBusy(true);
    try { setHeat(await api.saveRiskLimits(limits)); setMessage(`리스크 한도를 저장했습니다. 1회 ${limits.risk_per_trade_pct}% / 히트 ${limits.max_portfolio_heat_pct}%.`); } catch (error) { fail(error, '리스크 한도 저장 실패'); } finally { setBusy(false); }
  };
  const evaluationOf = useMemo(() => new Map(evaluations.map(item => [item.plan_id, item])), [evaluations]);
  const armed = useMemo(() => { const active = new Set(plans.filter(plan => plan.enabled).map(plan => plan.id)); return evaluations.filter(item => item.triggered && item.next_leg !== null && active.has(item.plan_id)); }, [plans, evaluations]);
  const pickedCandidate: PlanCandidate | undefined = proposal && picked !== null ? proposal.candidates[picked] : undefined;
  const loadable = pickedCandidate !== undefined && pickedCandidate.rejected === null;

  const propose = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api.proposePlan({
        ticker: proposeForm.ticker.trim(), side: proposeForm.side, entry_price: Number(proposeForm.entry_price),
        max_investment: Number(proposeForm.max_investment), max_loss: Number(proposeForm.max_loss),
      });
      setProposal(result);
      setPicked(result.recommended);
      setMessage(`${result.name}(${result.ticker}) 후보 ${result.candidates.filter(candidate => candidate.rejected === null).length}개를 계산했습니다.`);
    } catch (error) { setProposal(null); setPicked(null); fail(error, '계획 제안 실패'); } finally { setBusy(false); }
  };
  const loadCandidate = () => {
    if (!proposal || !pickedCandidate || pickedCandidate.rejected !== null) return;
    const entry = String(proposal.entry_price);
    setForm({
      id: null, name: '', ticker: proposal.ticker, side: proposal.side,
      quantity: fieldText(pickedCandidate.quantity), order_type: 'limit', limit_price: entry, entry_price: entry,
      stop_price: fieldText(pickedCandidate.stop_price),
      tp1_price: fieldText(pickedCandidate.tp1_price), tp1_ratio: pickedCandidate.tp1_ratio == null ? '' : String(pickedCandidate.tp1_ratio * 100),
      tp2_price: fieldText(pickedCandidate.tp2_price), tp2_ratio: pickedCandidate.tp2_ratio == null ? '' : String(pickedCandidate.tp2_ratio * 100),
      tp3_trailing_pct: fieldText(pickedCandidate.tp3_trailing_pct),
      enabled: true, setup: '', note: '',
    });
    setMessage(`손절 ATR ${pickedCandidate.stop_atr_multiple}배 후보를 아래 계획 폼에 채웠습니다. 이름을 입력하고 저장하세요.`);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.saveTradePlan({
        ...(form.id === null ? {} : { id: form.id }),
        name: form.name.trim(), ticker: form.ticker.trim(), side: form.side, quantity: Number(form.quantity),
        order_type: form.order_type, limit_price: form.order_type === 'limit' && form.limit_price !== '' ? Number(form.limit_price) : null,
        entry_price: Number(form.entry_price), stop_price: Number(form.stop_price),
        tp1_price: Number(form.tp1_price), tp1_ratio: Number(form.tp1_ratio) / 100,
        tp2_price: Number(form.tp2_price), tp2_ratio: Number(form.tp2_ratio) / 100,
        tp3_trailing_pct: Number(form.tp3_trailing_pct),
        enabled: form.enabled, setup: form.setup.trim() || null, note: form.note.trim() || null,
      });
      setMessage(`계획 "${form.name.trim()}"을 저장했습니다.`);
      setForm(emptyForm);
      loadPlans();
      loadHeat();
    } catch (error) { fail(error, '계획 저장 실패'); } finally { setBusy(false); }
  };
  const edit = (plan: TradePlan) => setForm({
    id: plan.id, name: plan.name, ticker: plan.ticker, side: plan.side, quantity: String(plan.quantity),
    order_type: plan.order_type, limit_price: plan.limit_price == null ? '' : String(plan.limit_price),
    entry_price: String(plan.entry_price), stop_price: String(plan.stop_price),
    tp1_price: String(plan.tp1_price), tp1_ratio: String(plan.tp1_ratio * 100),
    tp2_price: String(plan.tp2_price), tp2_ratio: String(plan.tp2_ratio * 100),
    tp3_trailing_pct: String(plan.tp3_trailing_pct),
    enabled: plan.enabled, setup: plan.setup || '', note: plan.note || '',
  });
  const remove = async (plan: TradePlan) => {
    if (!window.confirm(`계획 "${plan.name}"을 삭제합니다. 계속할까요?`)) return;
    try { await api.deleteTradePlan(plan.id); if (form.id === plan.id) setForm(emptyForm); setMessage(`계획 "${plan.name}"을 삭제했습니다.`); loadPlans(); loadHeat(); } catch (error) { fail(error, '계획 삭제 실패'); }
  };
  // 계획의 가격을 그대로 차트 블록으로 넘긴다. 이동 대상 목록은 계획 목록의 종목들이라 ◀▶로 계획 사이를 오갈 수 있다.
  const showOnChart = (plan: TradePlan) => {
    storePositionPlan(plan.ticker, positionFromTradePlan(plan));
    onSelect(plan.ticker, plans.map(item => item.ticker));
  };
  // 진입이 체결로 기록된 적 없는 계획은 청산 로직에 절대 도달하지 못한다(run_plans의 dry_run은
  // broker_orders에 아무것도 안 남기므로). 과거 구간을 재생해 진입→청산 전이를 미리 보는 용도다.
  const toggleSimulate = (plan: TradePlan) => {
    if (simPlan === plan.id) { setSimPlan(null); return; }
    setSimPlan(plan.id); setSimResult(null); setSimError('');
  };
  const runSimulate = async (plan: TradePlan) => {
    setSimBusy(true); setSimError('');
    try { setSimResult(await api.simulatePlan(plan.id, simRange.start, simRange.end)); }
    catch (error) { setSimResult(null); setSimError(error instanceof Error ? error.message : '시뮬레이션 실패'); }
    finally { setSimBusy(false); }
  };


  const summarize = (rows: BrokerOrder[]) => {
    if (!rows.length) return '조건을 충족한 계획이 없어 주문하지 않았습니다.';
    const counts = new Map<string, number>();
    rows.forEach(row => counts.set(row.status, (counts.get(row.status) || 0) + 1));
    return `${rows.length}건 처리 · ${[...counts].map(([key, count]) => `${statusLabel[key] || key} ${count}`).join(' · ')}`;
  };
  const run = async (dryRun: boolean) => {
    if (!dryRun) {
      if (!status?.enabled) return;
      if (!window.confirm(`${status.env === 'real' ? '실전' : '모의'} 계좌 ${status.account_masked || '(미확인)'}에 실제 주문을 전송합니다.\n조건을 충족한 계획 ${armed.length}건이 대상입니다.\n정말 실행할까요?`)) return;
    }
    setBusy(true);
    try { const rows = await api.runPlans({ dry_run: dryRun }); setMessage(`${dryRun ? '모의 실행' : '실주문 실행'} — ${summarize(rows)}`); loadOrders(); loadPlans(); loadHeat(); } catch (error) { fail(error, '실행 실패'); } finally { setBusy(false); }
  };
  const sync = async () => {
    setBusy(true);
    try { const rows = await api.syncOrders(); setMessage(rows.length ? `체결 동기화 — ${summarize(rows)}` : '동기화할 미체결 주문이 없습니다.'); loadOrders(); loadPlans(); loadHeat(); window.dispatchEvent(new Event('mscr-trades-changed')); } catch (error) { fail(error, '동기화 실패'); } finally { setBusy(false); }
  };
  const runReconcile = async () => {
    setReconcileBusy(true);
    try { setReconcileResult(await api.reconcile()); } catch (error) { setReconcileResult(null); fail(error, '잔고 대조 실패'); } finally { setReconcileBusy(false); }
  };

  // 리스크·복기는 기존 그리드 밖에 둔다. .trading-layout의 행 크기는 기존 다섯 구획에 맞춰져 있어, 안에 끼워 넣으면 주문 기록 칸이 눌린다.
  // .trading-layout은 height:100%로 스스로 스크롤 컨테이너가 되려 한다. 블록 상자로 한 번 감싸 높이를 내용에 맡기고, 스크롤은 바깥 .page가 맡는다.
  return <div className="page stack stack--lg">
    <RiskSection heat={heat} busy={busy} onSaveLimits={saveLimits} />
    <div>
      <div className="trading-layout">
        <section className="panel trading-head">
          <div className="toolbar">
            <div className="section-title">브로커</div>
            {status?.enabled
              ? <span className="badge" data-tone={status.env === 'real' ? 'real' : 'live'}>{status.env === 'real' ? '실전 계좌' : '모의 계좌'} · {status.account_masked || '계좌 미확인'}</span>
              : <span className="badge">브로커 미설정</span>}
            {!status?.enabled && <span className="subtle">{status?.reason || '상단 설정 탭에서 브로커 자격증명을 설정하세요.'}</span>}
            {status?.source === 'env' && <span className="subtle">환경변수 우선 적용 중</span>}
            <div className="toolbar push">
              <button className="btn btn--ghost" disabled={busy} onClick={() => run(true)}>모의 실행</button>
              {/* 실제 돈이 나가는 버튼은 기본 액션 색을 쓰지 않는다. 모의 실행과 한눈에 구분돼야 한다. */}
              <button className="btn btn--danger" disabled={busy || !status?.enabled} title={status?.enabled ? undefined : status?.reason || '브로커가 설정되지 않았습니다.'} onClick={() => run(false)}>실주문 실행</button>
              <button className="btn btn--ghost" disabled={busy || !status?.enabled} onClick={sync}>체결 동기화</button>
              <button className="btn btn--ghost" disabled={reconcileBusy || !status?.enabled} title="broker_orders를 거치지 않은 수동 주문이나 동기화 누락으로 로컬 상태가 실제 계좌와 어긋났는지 확인합니다." onClick={runReconcile}>{reconcileBusy ? '대조 중…' : '잔고 대조'}</button>
            </div>
          </div>
          {/* 작업 결과가 있으면 msg, 없으면 계획 건수만 조용히 보여준다. 둘 중 하나는 항상 렌더해 줄이 사라지지 않게 한다. */}
          {message ? <span className="msg">{message}</span> : <div className="subtle">대상 계획 {armed.length}건 / 전체 {plans.length}건</div>}
          {reconcileResult && <div className="stack">
            <div className="toolbar toolbar--tight">
              <span className="badge" data-tone={reconcileResult.mismatched ? 'danger' : 'ok'}>{reconcileResult.env === 'real' ? '실전' : '모의'} {reconcileResult.account_masked} · {reconcileResult.mismatched ? `불일치 ${reconcileResult.mismatched}건` : '일치'}</span>
              <span className="subtle">현금 — 로컬 {won(reconcileResult.local_cash_krw)} / 브로커 {won(reconcileResult.broker_cash_krw)}(사용자 입력값이라 다를 수 있습니다)</span>
            </div>
            {!!reconcileResult.positions.length && <table className="table table--nowrap table--rows">
              <thead><tr><th>종목</th><th className="num">로컬 수량</th><th className="num">브로커 수량</th><th className="num">차이</th></tr></thead>
              <tbody>{reconcileResult.positions.map(row => <tr key={row.ticker} data-tone={row.matched ? undefined : 'danger'}>
                <td>{row.name}<span className="subtle mono"> {row.ticker}</span></td>
                <td className="num">{num(row.local_quantity)}</td>
                <td className="num">{num(row.broker_quantity)}</td>
                <td className={`num ${row.matched ? 'subtle' : 'down'}`}>{row.quantity_diff > 0 ? '+' : ''}{num(row.quantity_diff)}</td>
              </tr>)}</tbody>
            </table>}
          </div>}
        </section>
  
        <section className="panel autoplan-panel">
          <h1>계획 자동 생성</h1>
          <p className="subtle">종목·진입가·최대 투자 금액·최대 손실 금액만 넣으면 손절폭별 후보를 계산합니다. 후보를 고른 뒤 아래 계획 폼으로 불러와 이름을 붙이고 저장하세요. 자동 생성 자체로는 아무 주문도 나가지 않습니다.</p>
          <form className="autoplan-form" onSubmit={propose}>
            <label>종목코드<input required pattern="[0-9A-Z]{6}" value={proposeForm.ticker} onChange={event => setProposeForm(current => ({ ...current, ticker: event.target.value.toUpperCase() }))} placeholder="005930" /></label>
            <label>매매구분<select value={proposeForm.side} onChange={event => setProposeForm(current => ({ ...current, side: event.target.value as 'buy' | 'sell' }))}><option value="buy">매수</option><option value="sell">매도</option></select></label>
            <label>진입가<input required type="number" min="0" step="any" value={proposeForm.entry_price} onChange={event => setProposeForm(current => ({ ...current, entry_price: event.target.value }))} placeholder="257000" /></label>
            <label>최대 투자 금액<input required type="number" min="0" step="any" value={proposeForm.max_investment} onChange={event => setProposeForm(current => ({ ...current, max_investment: event.target.value }))} placeholder="10000000" /></label>
            <label>최대 손실 금액<input required type="number" min="0" step="any" value={proposeForm.max_loss} onChange={event => { setMaxLossTouched(true); setProposeForm(current => ({ ...current, max_loss: event.target.value })); }} placeholder="300000" title="리스크 한도에서 계산한 권장값으로 채워집니다. 직접 고치면 그 값을 유지합니다." /></label>
            <div className="toolbar">
              <button className="btn btn--primary" disabled={busy}>제안 받기</button>
              {proposal && <button type="button" className="btn btn--ghost" onClick={() => { setProposal(null); setPicked(null); }}>결과 지우기</button>}
            </div>
          </form>
          {proposal && <div className="stack">
            <div className="autoplan-head">
              <strong>{proposal.name || proposal.ticker}</strong>
              <code>{proposal.ticker}</code>
              <span className="badge" data-tone={proposal.side === 'sell' ? 'sell' : 'buy'}>{proposal.side === 'sell' ? '매도' : '매수'}</span>
              <span className="badge" data-tone="accent">{bindingLabel[proposal.binding] || proposal.binding}</span>
              <span className="subtle">기준일 {proposal.as_of || '—'} · 최근 종가 {won(proposal.reference_close)} · ATR {won(proposal.atr)}({proposal.atr_pct.toFixed(2)}%) · 표본 {proposal.sample.observations.toLocaleString('ko-KR')}건 / {proposal.sample.horizon_days}봉 추적</span>
            </div>
            {proposal.binding === 'max_loss' && <div className="subtle">최대 손실 금액이 수량을 결정하고 있어 최대 투자 금액 {won(proposal.max_investment)}은 실제로 제약이 되지 않습니다. 후보의 실투자액이 한도에 훨씬 못 미칠 수 있습니다.</div>}
            {proposal.warnings.length > 0 && <div className="msg autoplan-warnings" data-tone="warn">
              <b>확인하세요</b>
              <ul>{proposal.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
            </div>}
            <div className="autoplan-reason"><span className="badge" data-tone="ok">권장</span><span>{proposal.recommendation_reason}</span></div>
            {/* 이 계획 하나의 손실 한도만 보면 포트폴리오 전체가 얼마나 남았는지는 안 보인다. 그 여유를 표 옆에 같이 둔다. */}
            <div className="grid grid--auto">
              <div className="stat-card"><label>1회 위험 한도</label><strong>{won(proposal.risk_budget.per_trade_krw)}</strong><span className="subtle">총자산 대비 1회 위험 비율</span></div>
              <div className="stat-card"><label>포트폴리오 히트 한도</label><strong>{won(proposal.risk_budget.heat_limit_krw)}</strong><span className="subtle">모든 계획의 손실 합계 상한</span></div>
              <div className="stat-card"><label>남은 여유</label><strong className={proposal.risk_budget.remaining_krw > 0 ? undefined : 'down'}>{won(proposal.risk_budget.remaining_krw)}</strong><span className="subtle">이미 잡힌 위험을 뺀 나머지</span></div>
              <div className="stat-card"><label>권장 손실 한도</label><strong>{won(proposal.risk_budget.suggested_max_loss)}</strong><span className="subtle">1회 한도와 남은 여유 중 작은 쪽</span></div>
            </div>
            <div className="autoplan-scroll">
              <table className="table table--rows table--nowrap">
                <thead><tr>{candidateColumns.map(column => <th key={column.label} className={column.num ? 'num' : undefined} title={column.title}>{column.label}</th>)}</tr></thead>
                <tbody>{proposal.candidates.map((candidate, index) => {
                  const blocked = candidate.rejected !== null;
                  const chosen = picked === index;
                  const shortfall = candidate.breakeven_tp1_prob != null && candidate.reach_tp1_prob != null && candidate.reach_tp1_prob < candidate.breakeven_tp1_prob;
                  return <tr key={candidate.stop_atr_multiple} className={blocked ? 'blocked' : undefined} aria-selected={chosen} onClick={blocked ? undefined : () => setPicked(index)}>
                    <td><input type="radio" name="autoplan-candidate" checked={chosen} disabled={blocked} onChange={() => setPicked(index)} /></td>
                    <td>ATR {candidate.stop_atr_multiple}배{index === proposal.recommended && <span className="badge" data-tone="ok">권장</span>}</td>
                    <td className="num">{won(candidate.stop_price)}</td>
                    <td className="num">{num(candidate.quantity)}주</td>
                    {blocked
                      ? <td className="reject subtle" colSpan={candidateColumns.length - 4}>{candidate.rejected}</td>
                      : <>
                        <td className="num">{won(candidate.invested)}</td>
                        <td className="num">{won(candidate.max_loss_krw)}</td>
                        <td className="num">{pct(candidate.loss_budget_used)}</td>
                        <td className="num">{candidate.leg_quantities ? candidate.leg_quantities.join(' / ') : '—'}</td>
                        <td className="num">{won(candidate.tp1_price)} <span className="subtle">{pct(candidate.tp1_ratio)}</span></td>
                        <td className="num">{won(candidate.tp2_price)} <span className="subtle">{pct(candidate.tp2_ratio)}</span></td>
                        <td className="num">{pct(candidate.reach_tp1_prob)}</td>
                        <td className="num">{pct(candidate.reach_tp2_prob)}</td>
                        <td className={`num ${shortfall ? 'up' : 'ok'}`} title={`본전이 되려면 1차 목표 도달률 ${pct(candidate.breakeven_tp1_prob)} 이상 필요`}>{pct(candidate.breakeven_tp1_prob)}</td>
                        <td className="num muted" title="진입 근거가 없을 때의 과거 기준선입니다. 예측이 아닙니다.">{candidate.baseline_expectancy_r == null ? '—' : `${candidate.baseline_expectancy_r.toFixed(2)}R`}</td>
                        <td className="num">{candidate.tp3_trailing_pct == null ? '—' : `${candidate.tp3_trailing_pct}%`}</td>
                      </>}
                  </tr>;
                })}</tbody>
              </table>
            </div>
            <ul className="autoplan-notes subtle">
              <li><b>기준선 기대R</b> — 진입 근거가 없을 때의 과거 기준선입니다(표본 {proposal.sample.observations.toLocaleString('ko-KR')}건, {proposal.sample.horizon_days}봉 추적). 예측이 아니며 대개 음수입니다. 당신의 진입 판단이 이 기준선을 넘어야 이익이 납니다.</li>
              <li><b>본전 필요 1차</b> — 이 구조가 본전이 되려면 1차 목표 도달률이 그 값 이상이어야 합니다. 같은 행의 1차 도달확률이 이보다 낮으면 붉게 표시됩니다.</li>
              <li><b>최대손실액</b> — 손절가에 그대로 체결된다는 전제의 값입니다. 갭 하락으로 손절가를 건너뛰면 최대 손실 금액은 보장되지 않습니다.</li>
            </ul>
            <div className="toolbar">
              <button type="button" className="btn btn--primary" disabled={!loadable} onClick={loadCandidate}>이 계획 불러오기</button>
              <span className="subtle">{loadable && pickedCandidate ? `선택: ATR ${pickedCandidate.stop_atr_multiple}배 · ${num(pickedCandidate.quantity)}주 · 손절 ${won(pickedCandidate.stop_price)}` : '표에서 후보를 선택하세요.'}</span>
            </div>
          </div>}
        </section>
    
        <section className="panel plan-editor">
          <h1>{form.id === null ? '새 계획' : `계획 편집 #${form.id}`}</h1>
          <p className="subtle">진입가 돌파 시 진입 주문이 나가고, 손절가 이탈 시 즉시 전량 청산합니다. 1차·2차는 목표가 도달 시 지정 비율만큼 시장가 익절하고, 나머지는 트레일링 스탑으로 관리합니다. 실주문은 위의 실주문 실행 버튼으로만 전송됩니다.</p>
          <form onSubmit={submit} className="form-grid">
            <label>이름<input required value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} placeholder="삼성전자 돌파매수" /></label>
            <label>티커<input required pattern="[0-9A-Z]{6}" value={form.ticker} onChange={event => setForm(current => ({ ...current, ticker: event.target.value.toUpperCase() }))} placeholder="005930" /></label>
            <label>방향<select value={form.side} onChange={event => setForm(current => ({ ...current, side: event.target.value as 'buy' | 'sell' }))}><option value="buy">매수</option><option value="sell">매도</option></select></label>
            <label>수량<input required type="number" min="0" step="any" value={form.quantity} onChange={event => setForm(current => ({ ...current, quantity: event.target.value }))} placeholder="10" /></label>
            <label>주문 유형<select value={form.order_type} onChange={event => setForm(current => ({ ...current, order_type: event.target.value as 'limit' | 'market' }))}><option value="limit">지정가</option><option value="market">시장가</option></select></label>
            <label>진입 주문가<input type="number" min="0" step="any" disabled={form.order_type === 'market'} required={form.order_type === 'limit'} value={form.order_type === 'market' ? '' : form.limit_price} onChange={event => setForm(current => ({ ...current, limit_price: event.target.value }))} placeholder={form.order_type === 'market' ? '시장가 주문' : '70000'} /></label>
            <label>진입가(돌파)<input required type="number" min="0" step="any" value={form.entry_price} onChange={event => setForm(current => ({ ...current, entry_price: event.target.value }))} placeholder="69000" /></label>
            <label>손절가<input required type="number" min="0" step="any" value={form.stop_price} onChange={event => setForm(current => ({ ...current, stop_price: event.target.value }))} placeholder="65000" /></label>
            <label>1차 익절가<input required type="number" min="0" step="any" value={form.tp1_price} onChange={event => setForm(current => ({ ...current, tp1_price: event.target.value }))} placeholder="75000" /></label>
            <label>1차 익절 비율(%)<input required type="number" min="0" max="100" step="any" value={form.tp1_ratio} onChange={event => setForm(current => ({ ...current, tp1_ratio: event.target.value }))} placeholder="40" /></label>
            <label>2차 익절가<input required type="number" min="0" step="any" value={form.tp2_price} onChange={event => setForm(current => ({ ...current, tp2_price: event.target.value }))} placeholder="82000" /></label>
            <label>2차 익절 비율(%)<input required type="number" min="0" max="100" step="any" value={form.tp2_ratio} onChange={event => setForm(current => ({ ...current, tp2_ratio: event.target.value }))} placeholder="30" /></label>
            <label>3차 트레일링 스탑(%)<input required type="number" min="0" step="any" value={form.tp3_trailing_pct} onChange={event => setForm(current => ({ ...current, tp3_trailing_pct: event.target.value }))} placeholder="5" /></label>
            {/* 셋업은 이 계획이 어느 스크리너 프리셋에서 나왔는지다. 복기의 셋업별 성적이 이 값으로 묶인다. */}
            <label>셋업<input list="trading-setups" value={form.setup} onChange={event => setForm(current => ({ ...current, setup: event.target.value }))} placeholder="저장한 프리셋 이름" title="복기 화면에서 셋업별 기대 R로 묶이는 태그입니다. 저장된 스크리너 프리셋 이름을 고르거나 직접 적으세요." /></label>
            <datalist id="trading-setups">{setups.map(setup => <option key={setup} value={setup} />)}</datalist>
            <label className="check"><input type="checkbox" checked={form.enabled} onChange={event => setForm(current => ({ ...current, enabled: event.target.checked }))} />계획 사용</label>
            <label className="wide">메모<input value={form.note} onChange={event => setForm(current => ({ ...current, note: event.target.value }))} placeholder="박스권 상단 돌파 / 실적 발표 전 청산" /></label>
            <div className="toolbar"><button className="btn btn--primary" disabled={busy}>저장</button><button type="button" className="btn btn--ghost" onClick={() => setForm(emptyForm)}>새 계획</button></div>
          </form>
        </section>
    
        <section className="panel plan-list">
          <div className="section-title">계획 <span className="badge">{plans.length}</span></div>
          {plans.map(plan => {
            const evaluation = evaluationOf.get(plan.id);
            return <article className="plan-item" key={plan.id}>
              <div className="plan-head">
                <strong>{plan.name}</strong>
                <span className="badge" data-tone={plan.side === 'sell' ? 'sell' : 'buy'}>{plan.side === 'sell' ? '매도' : '매수'}</span>
                <code>{plan.ticker}</code>
                {plan.setup && <span className="chip" title="이 계획의 셋업 태그입니다. 복기에서 셋업별 성적으로 묶입니다.">{plan.setup}</span>}
                <span className="subtle">{num(plan.quantity)}주 · {plan.order_type === 'market' ? '시장가' : `지정가 ${won(plan.limit_price)}`}</span>
                <span className="badge" data-tone={plan.enabled ? 'ok' : undefined}>{plan.enabled ? '사용' : '중지'}</span>
                <span className="badge">{evaluation ? phaseLabel[evaluation.phase] || evaluation.phase : '평가 없음'}</span>
              </div>
              <p className="code-note">진입 {won(plan.entry_price)} · 손절 {won(plan.stop_price)} · 1차 {won(plan.tp1_price)}({pct(plan.tp1_ratio)}) · 2차 {won(plan.tp2_price)}({pct(plan.tp2_ratio)}) · 트레일링 {plan.tp3_trailing_pct}%</p>
              <div className="plan-eval">
                <span className={evaluation?.triggered ? 'ok' : 'subtle'}>{evaluation ? (evaluation.next_leg ? `${evaluation.triggered ? '●' : '○'} 다음 동작: ${legLabel[evaluation.next_leg]}` : '○ 대기') : '평가 없음'}</span>
                <span className="subtle">{evaluation?.reason || ''}</span>
                <span className="subtle">{evaluation?.as_of || '기준일 없음'} · 종가 {won(evaluation?.close ?? null)}</span>
              </div>
              {plan.note && <div className="subtle">{plan.note}</div>}
              <div className="toolbar toolbar--tight"><button className="btn btn--ghost btn--sm" onClick={() => showOnChart(plan)} title="계획의 가격을 종목 상세 차트에 블록으로 띄웁니다">차트에서 보기</button><button className="btn btn--ghost btn--sm" onClick={() => edit(plan)}>편집</button><button className="btn btn--ghost btn--sm" onClick={() => toggleSimulate(plan)} title="진입이 실제로 체결된 적 없어도, 과거 구간의 일봉으로 진입→청산 전이를 재생해 봅니다.">{simPlan === plan.id ? '기간 시뮬레이션 닫기' : '기간 시뮬레이션'}</button><button className="btn btn--danger btn--sm" onClick={() => remove(plan)}>삭제</button></div>
              {simPlan === plan.id && <div className="plan-sim stack">
                <div className="toolbar toolbar--tight">
                  <label className="subtle">시작 <input type="date" value={simRange.start} max={simRange.end} onChange={event => setSimRange(current => ({ ...current, start: event.target.value }))} /></label>
                  <label className="subtle">종료 <input type="date" value={simRange.end} min={simRange.start} onChange={event => setSimRange(current => ({ ...current, end: event.target.value }))} /></label>
                  <button className="btn btn--primary btn--sm" disabled={simBusy} onClick={() => runSimulate(plan)}>재생</button>
                </div>
                {simError && <div className="msg" data-tone="error">{simError}</div>}
                {simResult && simResult.plan_id === plan.id && <>
                  <div className="toolbar toolbar--tight">
                    <span className="badge">{phaseLabel[simResult.phase] || simResult.phase}</span>
                    <span className="subtle">{simResult.bars}봉 재생 · {simResult.start} ~ {simResult.end}</span>
                  </div>
                  {!!simResult.legs.length && <table className="table table--nowrap table--rows">
                    <thead><tr><th>레그</th><th>날짜</th><th className="num">가격</th><th className="num">수량</th></tr></thead>
                    <tbody>{simResult.legs.map((leg, index) => <tr key={index}>
                      <td>{legLabel[leg.leg] || leg.leg}</td>
                      <td className="mono">{leg.date}</td>
                      <td className="num">{won(leg.price)}</td>
                      <td className="num">{num(leg.quantity)}</td>
                    </tr>)}</tbody>
                  </table>}
                  <div className="subtle">실현 {rMultiple(simResult.realized_r)} · 미실현 {rMultiple(simResult.open_r)} · 합계 {rMultiple(simResult.total_r)}</div>
                  {simResult.warnings.map(warning => <div className="msg" data-tone="warn" key={warning}>{warning}</div>)}
                </>}
              </div>}
            </article>;
          })}
          {!plans.length && <div className="empty">등록된 트레이딩 계획이 없습니다.</div>}
        </section>
    
        <section className="panel order-panel">
          <div className="section-title">주문 기록 <span className="badge">{orders.length}</span></div>
          <div className="table-scroll">
            <table className="table table--nowrap">
              <thead><tr>{['요청시각', '계획', '단계', '티커', '방향', '수량', '상태', '체결', '체결가', '브로커 주문번호', '메시지'].map(label => <th key={label}>{label}</th>)}</tr></thead>
              <tbody>{orders.map(order => <tr key={order.id}>
                <td className="mono">{order.requested_at.replace('T', ' ').slice(0, 16)}</td>
                <td>{order.plan_name || '—'}</td>
                <td className="subtle">{order.leg ? legLabel[order.leg] || order.leg : '—'}</td>
                <td className="mono">{order.ticker}</td>
                <td className={order.side === 'sell' ? 'down' : 'up'}>{order.side === 'sell' ? '매도' : '매수'}</td>
                <td className="num">{num(order.quantity)}</td>
                <td><span className="badge" data-tone={statusTone[order.status]}>{statusLabel[order.status] || order.status}</span></td>
                <td className="num">{num(order.filled_quantity)}</td>
                <td className="num">{won(order.filled_price)}</td>
                <td className="mono">{order.broker_order_id || '—'}</td>
                <td className="subtle">{order.message || ''}</td>
              </tr>)}</tbody>
            </table>
            {!orders.length && <div className="empty">주문 기록이 없습니다.</div>}
          </div>
        </section>
      </div>
    </div>
    <ReviewSection onSelect={onSelect} />
  </div>;
}
