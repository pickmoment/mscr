import { useEffect, useMemo, useRef, useState } from 'react';
import { api, BacktestProtocol, BacktestResult, ForwardReturns, IndicatorDefinition, JobStatus, ScreenRow, ScreenSpec, SignalCoverage, SignalDiff, SignalRow } from '../lib/api';
import FormulaInput from './FormulaInput';
import { screenSuggestions } from '../lib/suggest';
import { SelectTicker } from '../lib/nav';
import { won } from '../lib/format';

// 신호 로그 화면에서 보유 종목은 수백 건이 될 수 있어 좁은 사이드바에서는 앞부분만 보여주고 나머지는 건수로 알린다.
const HELD_LIMIT = 20;
// 백엔드 DEFAULT_PROTOCOL과 같은 값. 폼은 지우는 도중의 빈 문자열을 허용해야 해서 문자열로 들고 있다가 제출할 때만 숫자로 바꾼다.
type ProtocolDraft = { entry: 'next_open' | 'breakout'; trigger_window: string; trigger_buffer_pct: string; stop_mode: 'atr' | 'box'; atr_multiple: string; atr_period: string; box_lookback: string; box_buffer_atr: string; target_r: string; horizon_days: string; cost_pct: string; top_n: string; non_overlap: boolean };
const defaultProtocol: ProtocolDraft = { entry: 'next_open', trigger_window: '5', trigger_buffer_pct: '0.1', stop_mode: 'atr', atr_multiple: '2', atr_period: '14', box_lookback: '20', box_buffer_atr: '0.25', target_r: '3', horizon_days: '60', cost_pct: '0.25', top_n: '5', non_overlap: true };
const toProtocol = (draft: ProtocolDraft): BacktestProtocol => ({
  entry: draft.entry,
  trigger_window: Number(draft.trigger_window) || 1,
  trigger_buffer_pct: Number(draft.trigger_buffer_pct) || 0,
  stop_mode: draft.stop_mode,
  atr_multiple: Number(draft.atr_multiple) || 0,
  atr_period: Number(draft.atr_period) || 1,
  box_lookback: Number(draft.box_lookback) || 1,
  box_buffer_atr: Number(draft.box_buffer_atr) || 0,
  target_r: Number(draft.target_r) || 0,
  horizon_days: Number(draft.horizon_days) || 1,
  cost_pct: Number(draft.cost_pct) || 0,
  top_n: draft.top_n.trim() ? Number(draft.top_n) : null,
  non_overlap: draft.non_overlap,
});
// 백엔드는 비율을 이미 0~100 퍼센트로 내려준다. 다시 100을 곱하지 않는다.
const rateText = (value: number | null | undefined, digits = 1) => value == null ? '—' : `${value.toFixed(digits)}%`;
const rText = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)}R`;
const intText = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ko-KR');

const defaults: ScreenSpec = {
  universe: { kinds: ['stock', 'etf'], markets: ['KOSPI', 'KOSDAQ'], exclude_preferred: true, exclude_spac: true, exclude_halted: true, min_bars: 20 },
  formula: 'prior_avg_ratio(volume, 20) >= 3',
  sort: { formula: 'prior_avg_ratio(volume, 20)', dir: 'desc' },
  limit: 500,
  as_of_offset: 0,
};

export default function ScreenerPanel({ onResults, onSelect }: { onResults: (rows: ScreenRow[]) => void; onSelect: SelectTicker }) {
  const [spec, setSpec] = useState<ScreenSpec>(defaults);
  const [saved, setSaved] = useState<{ id: number; name: string; spec: ScreenSpec; updated_at: string }[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [presetName, setPresetName] = useState('');
  const [presetStatus, setPresetStatus] = useState('');
  const [status, setStatus] = useState('');
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const [catalog, setCatalog] = useState<IndicatorDefinition[]>([]);
  const suggestions = useMemo(() => screenSuggestions(catalog), [catalog]);
  const loadPresets = () => api.screens().then(setSaved);
  useEffect(() => { loadPresets().catch(() => undefined); }, []);
  useEffect(() => {
    const refresh = () => api.indicators().then(setCatalog).catch(() => undefined);
    refresh();
    window.addEventListener('mscr-indicators-changed', refresh);
    return () => window.removeEventListener('mscr-indicators-changed', refresh);
  }, []);

  const updateUniverse = (key: 'kinds' | 'markets', value: string) => setSpec(current => ({ ...current, universe: { ...current.universe, [key]: current.universe[key].includes(value) ? current.universe[key].filter(item => item !== value) : [...current.universe[key], value] } }));
  useEffect(() => {
    if (!running) return;
    setElapsed(0);
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [running]);
  useEffect(() => () => abortRef.current?.abort(), []);
  const runScreen = async () => {
    if (running) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setStatus('');
    const started = Date.now();
    try {
      const response = await api.screen(spec, controller.signal);
      onResults(response.rows);
      setStatus(`${response.count.toLocaleString()}개 결과 · ${response.as_of || '미수집'} · ${((Date.now() - started) / 1000).toFixed(1)}초`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '검색 실패');
    } finally {
      abortRef.current = null;
      setRunning(false);
    }
  };
  const cancelScreen = () => abortRef.current?.abort();
  const selectedPreset = saved.find(entry => String(entry.id) === selectedId);
  const applyPreset = () => {
    if (!selectedPreset) return;
    setSpec({ ...defaults, ...selectedPreset.spec, as_of_offset: selectedPreset.spec.as_of_offset ?? 0 });
    setPresetName(selectedPreset.name);
    setPresetStatus(`${selectedPreset.name} 불러옴 · 저장 ${selectedPreset.updated_at}`);
  };
  const savePreset = async () => {
    const name = presetName.trim();
    if (!name) return setPresetStatus('프리셋 이름을 입력하세요');
    const clash = saved.find(entry => entry.name === name);
    if (clash && !window.confirm(`'${name}' 프리셋을 덮어쓸까요?`)) return;
    try {
      const { id } = await api.saveScreen(name, spec);
      await loadPresets();
      setSelectedId(String(id));
      setPresetStatus(clash ? `${name} 덮어썼습니다` : `${name} 저장했습니다`);
    } catch (error) {
      setPresetStatus(error instanceof Error ? error.message : '저장 실패');
    }
  };
  const updatePreset = async () => {
    if (!selectedPreset) return setPresetStatus('갱신할 프리셋을 선택하세요');
    const name = presetName.trim() || selectedPreset.name;
    try {
      const result = await api.updateScreen(selectedPreset.id, name, spec);
      await loadPresets();
      setPresetStatus(`${result.name} 갱신했습니다 · ${result.updated_at}`);
    } catch (error) {
      setPresetStatus(error instanceof Error ? error.message : '갱신 실패');
    }
  };
  const removePreset = async () => {
    if (!selectedPreset) return setPresetStatus('삭제할 프리셋을 선택하세요');
    if (!window.confirm(`'${selectedPreset.name}' 프리셋을 삭제할까요?`)) return;
    try {
      await api.deleteScreen(selectedPreset.id);
      await loadPresets();
      setSelectedId('');
      setPresetStatus(`${selectedPreset.name} 삭제했습니다`);
    } catch (error) {
      setPresetStatus(error instanceof Error ? error.message : '삭제 실패');
    }
  };

  // ── 신호 로그 / 검증 ── 모두 위에서 고른 프리셋 하나에만 적용된다.
  const screenId = selectedPreset ? selectedPreset.id : null;
  const [coverage, setCoverage] = useState<SignalCoverage | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [diff, setDiff] = useState<SignalDiff | null>(null);
  const [captureDays, setCaptureDays] = useState('1');
  const [signalMessage, setSignalMessage] = useState('');
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [protocol, setProtocol] = useState<ProtocolDraft>(defaultProtocol);
  const [verification, setVerification] = useState<BacktestResult | null>(null);
  const [forward, setForward] = useState<ForwardReturns | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyMessage, setVerifyMessage] = useState('');
  const loadCoverage = () => api.signalCoverage().then(setCoverage).catch(() => undefined);
  const loadDiff = (id: number) => api.signalDiff(id).then(setDiff).catch(() => setDiff(null));
  useEffect(() => { loadCoverage(); api.signalStatus().then(setJob).catch(() => undefined); }, []);
  useEffect(() => {
    setDiff(null);
    setVerification(null);
    setForward(null);
    setSignalMessage('');
    setVerifyMessage('');
    if (screenId != null) loadDiff(screenId);
  }, [screenId]);
  // 수집은 백그라운드 작업이라 상태를 되물어야 한다. 끝나는 순간 커버리지와 차이 목록을 다시 읽는다.
  useEffect(() => {
    if (!job?.running) return;
    const timer = setInterval(() => {
      api.signalStatus().then(next => {
        setJob(next);
        if (next.running) return;
        loadCoverage();
        if (screenId != null) loadDiff(screenId);
      }).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.running, screenId]);
  const capture = async (force: boolean) => {
    if (screenId == null || job?.running) return;
    setSignalMessage('');
    try {
      setJob(await api.captureSignals({ days: Math.min(1000, Math.max(1, Number(captureDays) || 1)), force, screen_ids: [screenId] }));
    } catch (error) {
      setSignalMessage(error instanceof Error ? error.message : '수집 요청 실패');
    }
  };
  const runVerification = async () => {
    if (screenId == null || verifyBusy) return;
    setVerifyBusy(true);
    setVerifyMessage('');
    try {
      setVerification(await api.backtest(screenId, toProtocol(protocol)));
    } catch (error) {
      setVerifyMessage(error instanceof Error ? error.message : '검증 실패');
    } finally {
      setVerifyBusy(false);
    }
  };
  const runForward = async () => {
    if (screenId == null || verifyBusy) return;
    setVerifyBusy(true);
    setVerifyMessage('');
    try {
      setForward(await api.forwardReturns(screenId));
    } catch (error) {
      setVerifyMessage(error instanceof Error ? error.message : '후보 성과 조회 실패');
    } finally {
      setVerifyBusy(false);
    }
  };
  const coverageRow = coverage && screenId != null ? coverage.screens.find(entry => entry.id === screenId) : undefined;
  const heldShown = diff ? diff.held.slice(0, HELD_LIMIT) : [];
  // 이 섹션에 실제로 보이는 종목들이 종목 상세의 앞뒤 이동 범위가 된다.
  const diffTickers = diff ? [...diff.entered, ...heldShown, ...diff.exited].map(row => row.ticker) : [];
  const signalRows = (rows: SignalRow[], streak: boolean) => rows.map(row => <tr key={row.ticker} onClick={() => onSelect(row.ticker, diffTickers)} title="종목 상세로 이동">
    <td>{row.name || row.ticker}<span className="subtle mono"> {row.ticker}</span></td>
    <td className="num">{row.rank}</td>
    <td className="num">{won(row.close)}</td>
    {streak && <td className="num">연속 {row.streak_days ?? diff?.streaks[row.ticker]?.days ?? 1}일</td>}
  </tr>);

  // 패널 자체가 세로 스택이라 섹션 사이 여백은 .stack gap + .section-title 여백으로만 만든다.
  return <aside className="panel scroll screener-panel stack">
    <div className="section-title">프리셋 <span className="badge">{saved.length}</span></div>
    <div className="preset-manager">
      <div className="toolbar"><select aria-label="프리셋 목록" value={selectedId} onChange={event => setSelectedId(event.target.value)}><option value="">프리셋 선택</option>{saved.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button className="btn btn--ghost" onClick={applyPreset}>불러오기</button></div>
      <input aria-label="프리셋 이름" value={presetName} onChange={event => setPresetName(event.target.value)} placeholder="프리셋 이름" maxLength={60} />
      <div className="toolbar"><button className="btn btn--ghost" onClick={savePreset}>새로 저장</button><button className="btn btn--ghost" onClick={updatePreset}>선택 갱신</button><button className="btn btn--danger" onClick={removePreset}>삭제</button></div>
      {presetStatus && <div className="msg">{presetStatus}</div>}
    </div>
    {!selectedPreset && <p className="hint">프리셋을 선택하면 그 프리셋의 신호 로그와 검증 도구가 열립니다.</p>}
    {selectedPreset && <>
      <div className="section-title">신호 로그</div>
      <p className="hint">저장된 프리셋을 과거 거래일마다 다시 돌려 편입된 종목을 기록해 둔 것입니다. 신규 진입은 오늘 새로 들어온 종목, 이탈은 빠진 종목, 연속 N일은 프리셋에 계속 남아 있는 날짜 수입니다(1일이면 신규).</p>
      <table className="table table--kv"><tbody>
        <tr><td>수집일</td><td>{coverageRow ? `${coverageRow.days.toLocaleString('ko-KR')}일` : '0일'} {coverageRow?.first_date ? `(${coverageRow.first_date} ~ ${coverageRow.last_date})` : ''}</td></tr>
        <tr><td>신호 누적</td><td>{coverageRow ? `${coverageRow.signals.toLocaleString('ko-KR')}건` : '0건'}</td></tr>
        <tr><td>DB 거래일</td><td>{coverage ? `${coverage.trading_days.toLocaleString('ko-KR')}일` : '—'}{coverage && coverageRow ? ` · 미수집 ${Math.max(0, coverage.trading_days - coverageRow.days).toLocaleString('ko-KR')}일` : coverage ? ` · 미수집 ${coverage.trading_days.toLocaleString('ko-KR')}일` : ''}</td></tr>
      </tbody></table>
      <div className="toolbar toolbar--tight">
        <label className="check">일수 <input className="w-sm" type="number" min={1} max={1000} value={captureDays} onChange={event => setCaptureDays(event.target.value)} /></label>
        <button className="btn btn--primary" disabled={!!job?.running} onClick={() => capture(false)} title="아직 저장되지 않은 날짜만 채웁니다">수집</button>
        <button className="btn btn--ghost" disabled={!!job?.running} onClick={() => capture(true)} title="이미 저장된 날짜도 다시 계산해 덮어씁니다">다시 계산</button>
      </div>
      <p className="hint">일수만큼의 거래일을 하루씩 되짚으며 매일 전 종목 유니버스를 다시 계산합니다. 수백 일을 지정하면 몇 분 이상 걸립니다.</p>
      {job?.running && <span className="progress-note" role="status"><span className="spinner" aria-hidden="true" />수집 중 {job.processed ?? 0}/{job.total ?? '?'} · {job.current || '준비 중'}</span>}
      {!job?.running && job?.result && <div className="subtle">최근 수집 · 프리셋 {job.result.screens}개 · {job.result.dates}일 · {job.result.rows.toLocaleString('ko-KR')}건 저장 · {job.result.skipped.toLocaleString('ko-KR')}일 건너뜀</div>}
      {!job?.running && job?.error && <div className="msg" data-tone="error">{job.error}</div>}
      {signalMessage && <div className="msg" data-tone="error">{signalMessage}</div>}
      {diff && <>
        <div className="subtle">기준일 {diff.date || '—'} · 직전 수집일 {diff.previous || '—'}</div>
        <div className="section-title">신규 진입 <span className="badge" data-tone="ok">{diff.entered.length}</span></div>
        {diff.entered.length > 0
          ? <table className="table table--nowrap table--rows">
            <thead><tr><th>종목</th><th className="num">순위</th><th className="num">종가</th></tr></thead>
            <tbody>{signalRows(diff.entered, false)}</tbody>
          </table>
          : <div className="subtle">신규 진입 종목이 없습니다.</div>}
        <div className="section-title">이탈 <span className="badge" data-tone="danger">{diff.exited.length}</span></div>
        {diff.exited.length > 0
          ? <table className="table table--nowrap table--rows">
            <thead><tr><th>종목</th><th className="num">순위</th><th className="num">종가</th></tr></thead>
            <tbody>{signalRows(diff.exited, false)}</tbody>
          </table>
          : <div className="subtle">이탈 종목이 없습니다.</div>}
        <div className="section-title">유지 <span className="badge">{diff.held.length}</span></div>
        {heldShown.length > 0
          ? <table className="table table--nowrap table--rows">
            <thead><tr><th>종목</th><th className="num">순위</th><th className="num">종가</th><th className="num">연속</th></tr></thead>
            <tbody>{signalRows(heldShown, true)}</tbody>
          </table>
          : <div className="subtle">직전 수집일과 겹치는 종목이 없습니다.</div>}
        {diff.held.length > heldShown.length && <div className="subtle">외 {diff.held.length - heldShown.length}건은 표에서 생략했습니다.</div>}
      </>}
      {!diff && <div className="subtle">이 프리셋의 신호 로그가 아직 없습니다. 위에서 수집하세요.</div>}

      <div className="toolbar">
        <div className="section-title">검증</div>
        <button className="btn btn--ghost btn--sm push" onClick={() => setVerifyOpen(current => !current)}>{verifyOpen ? '접기' : '펼치기'}</button>
      </div>
      {verifyOpen && <>
        <p className="hint">신호 로그를 아래 청산 규칙으로 그대로 재생합니다. 기대값은 실제로 진입한 거래만의 평균 R이고, 트리거되지 않은 신호는 신호 수에만 남습니다. 1R은 진입가와 손절가의 차이입니다.</p>
        <div className="form-grid">
          <label>진입<select value={protocol.entry} onChange={event => setProtocol(current => ({ ...current, entry: event.target.value as 'next_open' | 'breakout' }))}><option value="next_open">다음 봉 시가</option><option value="breakout">박스 상단 돌파</option></select></label>
          <label>손절 방식<select value={protocol.stop_mode} onChange={event => setProtocol(current => ({ ...current, stop_mode: event.target.value as 'atr' | 'box' }))}><option value="atr">ATR 배수</option><option value="box">박스 바닥</option></select></label>
          {protocol.entry === 'breakout' && <label>트리거 창(봉)<input type="number" min="1" step="1" value={protocol.trigger_window} onChange={event => setProtocol(current => ({ ...current, trigger_window: event.target.value }))} /></label>}
          {protocol.entry === 'breakout' && <label>트리거 버퍼(%)<input type="number" min="0" step="any" value={protocol.trigger_buffer_pct} onChange={event => setProtocol(current => ({ ...current, trigger_buffer_pct: event.target.value }))} /></label>}
          {protocol.stop_mode === 'atr' && <label>ATR 배수<input type="number" min="0" step="any" value={protocol.atr_multiple} onChange={event => setProtocol(current => ({ ...current, atr_multiple: event.target.value }))} /></label>}
          {protocol.stop_mode === 'box' && <label>박스 되돌림(봉)<input type="number" min="1" step="1" value={protocol.box_lookback} onChange={event => setProtocol(current => ({ ...current, box_lookback: event.target.value }))} /></label>}
          {protocol.stop_mode === 'box' && <label>박스 여유(ATR)<input type="number" min="0" step="any" value={protocol.box_buffer_atr} onChange={event => setProtocol(current => ({ ...current, box_buffer_atr: event.target.value }))} /></label>}
          <label>ATR 기간<input type="number" min="1" step="1" value={protocol.atr_period} onChange={event => setProtocol(current => ({ ...current, atr_period: event.target.value }))} /></label>
          <label>목표 R<input type="number" min="0" step="any" value={protocol.target_r} onChange={event => setProtocol(current => ({ ...current, target_r: event.target.value }))} /></label>
          <label>보유일<input type="number" min="1" step="1" value={protocol.horizon_days} onChange={event => setProtocol(current => ({ ...current, horizon_days: event.target.value }))} /></label>
          <label>비용(%)<input type="number" min="0" step="any" value={protocol.cost_pct} onChange={event => setProtocol(current => ({ ...current, cost_pct: event.target.value }))} /></label>
          <label>상위 N<input type="number" min="1" step="1" placeholder="전체" value={protocol.top_n} onChange={event => setProtocol(current => ({ ...current, top_n: event.target.value }))} /></label>
          <label className="check"><input type="checkbox" checked={protocol.non_overlap} onChange={event => setProtocol(current => ({ ...current, non_overlap: event.target.checked }))} />같은 종목 중복 진입 제외</label>
          <div className="toolbar">
            <button className="btn btn--primary" disabled={verifyBusy} onClick={runVerification}>{verifyBusy ? '계산 중…' : '검증 실행'}</button>
            <button className="btn btn--ghost" disabled={verifyBusy} onClick={runForward} title="진입 규칙 없이 신호 다음 날부터 그냥 들고 있었다면 어땠는지">후보 성과</button>
          </div>
        </div>
        {verifyMessage && <div className="msg" data-tone="error">{verifyMessage}</div>}
        {verification && <>
          <table className="table table--kv"><tbody>
            <tr><td>기간</td><td>{verification.period.start || '—'} ~ {verification.period.end || '—'} ({verification.period.days.toLocaleString('ko-KR')}일)</td></tr>
            <tr><td>신호 수</td><td>{verification.signals.toLocaleString('ko-KR')}건</td></tr>
            <tr><td>트리거 수</td><td>{verification.triggered.toLocaleString('ko-KR')}건</td></tr>
            <tr><td>거래 수</td><td>{verification.trades.toLocaleString('ko-KR')}건</td></tr>
            <tr><td>트리거율</td><td>{rateText(verification.trigger_rate)}</td></tr>
          </tbody></table>
          <div className="msg" data-tone={verification.expectancy_r != null && verification.expectancy_r > 0 ? 'ok' : 'warn'}>
            <b>기대값</b> {rText(verification.expectancy_r)} ± {verification.stderr_r == null ? '—' : verification.stderr_r.toFixed(2)}R <span className="subtle">(진입한 거래 {verification.trades.toLocaleString('ko-KR')}건 평균)</span>
          </div>
          <table className="table table--kv"><tbody>
            <tr><td>승률</td><td>{rateText(verification.win_rate)}</td></tr>
            <tr><td>목표 도달</td><td>{rateText(verification.target_rate)}</td></tr>
            <tr><td>손절</td><td>{rateText(verification.stop_rate)}</td></tr>
            <tr><td>시간 초과</td><td>{rateText(verification.timeout_rate)}</td></tr>
            <tr><td>평균 보유일</td><td>{verification.avg_days_held == null ? '—' : `${verification.avg_days_held.toFixed(1)}일`}</td></tr>
            <tr><td>평균 리스크</td><td>{rateText(verification.avg_risk_pct, 2)}</td></tr>
            <tr><td>흑자월</td><td>{verification.profitable_months}/{verification.total_months}</td></tr>
          </tbody></table>
          <div className="section-title">전후 반기 <span className="subtle">기간을 반으로 갈랐을 때</span></div>
          <table className="table table--nowrap">
            <thead><tr><th>구간</th><th className="num">거래</th><th className="num">기대값</th></tr></thead>
            <tbody>{verification.by_half.map(row => <tr key={row.label}><td>{row.label}</td><td className="num">{intText(row.trades)}</td><td className="num">{rText(row.expectancy_r)}</td></tr>)}
              {!verification.by_half.length && <tr><td colSpan={3} className="subtle">반기 비교를 낼 거래가 부족합니다.</td></tr>}</tbody>
          </table>
          <div className="section-title">월별</div>
          {/* 패널 자체가 세로로 스크롤되므로 표를 별도 스크롤 상자에 넣지 않는다. .table-scroll는 min-height:0이라 이 그리드 안에서 높이가 접힌다. */}
          <table className="table table--nowrap">
            <thead><tr><th>월</th><th className="num">거래</th><th className="num">기대값</th></tr></thead>
            <tbody>{verification.by_month.map(row => <tr key={row.month}><td className="mono">{row.month}</td><td className="num">{intText(row.trades)}</td><td className={`num ${row.expectancy_r != null && row.expectancy_r > 0 ? 'up' : 'down'}`}>{rText(row.expectancy_r)}</td></tr>)}
              {!verification.by_month.length && <tr><td colSpan={3} className="subtle">월별로 나눌 거래가 없습니다.</td></tr>}</tbody>
          </table>
          {verification.warnings.length > 0 && <div className="msg" data-tone="warn"><b>확인하세요</b><ul>{verification.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></div>}
        </>}
        {forward && <>
          <div className="section-title">후보 성과 <span className="badge">{forward.signals.toLocaleString('ko-KR')}건</span></div>
          <p className="hint">진입·손절 규칙 없이 신호일 종가에 사서 그대로 들고 있었을 때의 분포입니다. 비용도 손절도 반영하지 않은, 매매하지 않았을 때의 기준선입니다.</p>
          <table className="table">
            <thead><tr><th className="num">일수</th><th className="num">건수</th><th className="num">평균</th><th className="num">중앙값</th><th className="num">승률</th><th className="num">p10</th><th className="num">p90</th></tr></thead>
            <tbody>{forward.horizons.map(row => <tr key={row.days}>
              <td className="num">{row.days}</td>
              <td className="num">{intText(row.count)}</td>
              <td className={`num ${row.mean_pct != null && row.mean_pct > 0 ? 'up' : 'down'}`}>{rateText(row.mean_pct, 2)}</td>
              <td className="num">{rateText(row.median_pct, 2)}</td>
              <td className="num">{rateText(row.win_rate)}</td>
              <td className="num">{rateText(row.p10_pct, 2)}</td>
              <td className="num">{rateText(row.p90_pct, 2)}</td>
            </tr>)}
              {!forward.horizons.length && <tr><td colSpan={7} className="subtle">신호 로그가 비어 있어 계산할 수 없습니다.</td></tr>}</tbody>
          </table>
          {forward.warnings.length > 0 && <div className="msg" data-tone="warn"><b>확인하세요</b><ul>{forward.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></div>}
        </>}
      </>}
    </>}
    <div className="section-title">유니버스</div>
    <div className="toolbar">{['stock','etf'].map(value => <label className="check" key={value}><input type="checkbox" checked={spec.universe.kinds.includes(value)} onChange={() => updateUniverse('kinds', value)} />{value === 'stock' ? '주식' : 'ETF'}</label>)}{['KOSPI','KOSDAQ'].map(value => <label className="check" key={value}><input type="checkbox" checked={spec.universe.markets.includes(value)} onChange={() => updateUniverse('markets', value)} />{value}</label>)}</div>
    <div className="toolbar"><label className="check"><input type="checkbox" checked={spec.universe.exclude_preferred} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, exclude_preferred: event.target.checked } }))} />우선주 제외</label><label className="check"><input type="checkbox" checked={spec.universe.exclude_spac} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, exclude_spac: event.target.checked } }))} />스팩 제외</label><label className="check"><input type="checkbox" checked={spec.universe.exclude_halted} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, exclude_halted: event.target.checked } }))} />정지 제외</label></div>
    <div className="toolbar">
      <label className="check">최소 유효 봉 <input className="w-sm" type="number" value={spec.universe.min_bars} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, min_bars: Number(event.target.value) } }))} /></label>
      <label className="check" title="0=당일, 1=하루전, 2=이틀전 … 마지막 유효 봉 기준으로 며칠 전 데이터로 스크리닝할지">기준일(N봉 전) <input className="w-sm" type="number" min={0} max={250} value={spec.as_of_offset} onChange={event => setSpec(current => ({ ...current, as_of_offset: Math.max(0, Number(event.target.value)) }))} /></label>
    </div>
    <div className="formula-block">
      <div className="section-title">스크린 수식</div>
      <FormulaInput multiline value={spec.formula} onChange={formula => setSpec(current => ({ ...current, formula }))} suggestions={suggestions} ariaLabel="스크린 수식" />
      <p className="hint">지표명을 입력하면 자동완성됩니다. <code>and</code> · <code>or</code> · <code>not</code>과 비교식을 조합합니다.</p>
      <code className="formula-example">close &gt; sma(close, 20) and rsi(close, 14) &lt;= 30</code>
    </div>
    <div className="section-title">정렬 수식</div>
    <div className="toolbar"><FormulaInput value={spec.sort.formula} onChange={formula => setSpec(current => ({ ...current, sort: { ...current.sort, formula } }))} suggestions={suggestions} placeholder="예: returns(close, 120)" ariaLabel="정렬 수식" /><select className="w-md" value={spec.sort.dir} onChange={event => setSpec(current => ({ ...current, sort: { ...current.sort, dir: event.target.value as 'asc' | 'desc' } }))}><option value="desc">내림차순</option><option value="asc">오름차순</option></select></div>
    <div className="toolbar">
      <button className="btn btn--primary" onClick={runScreen} disabled={running}>{running ? '조회 중…' : '스크린 실행'}</button>
      {running && <button className="btn btn--ghost" onClick={cancelScreen}>취소</button>}
      {running && <span className="progress-note" role="status"><span className="spinner" aria-hidden="true" />전 종목 동적 계산 중… {elapsed}초 경과</span>}
    </div>
    {!running && status && <div className="msg">{status}</div>}
  </aside>;
}
