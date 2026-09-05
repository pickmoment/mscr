import { useEffect, useMemo, useState } from 'react';
import { api, BrokerOrder, PlanCandidate, PlanEvaluation, PlanProposal, TradePlan, TradingStatus } from '../lib/api';
import { positionFromTradePlan, storePositionPlan } from '../lib/position';
import { SelectTicker } from '../lib/nav';
import { won } from '../lib/format';

type Draft = { id: number | null; name: string; ticker: string; side: 'buy' | 'sell'; quantity: string; order_type: 'limit' | 'market'; limit_price: string; entry_price: string; stop_price: string; tp1_price: string; tp1_ratio: string; tp2_price: string; tp2_ratio: string; tp3_trailing_pct: string; enabled: boolean; note: string };
const emptyForm: Draft = { id: null, name: '', ticker: '', side: 'buy', quantity: '', order_type: 'limit', limit_price: '', entry_price: '', stop_price: '', tp1_price: '', tp1_ratio: '', tp2_price: '', tp2_ratio: '', tp3_trailing_pct: '', enabled: true, note: '' };
const statusLabel: Record<string, string> = { dry_run: '모의', submitted: '접수', partial: '부분체결', filled: '체결', rejected: '거부', failed: '실패', skipped: '건너뜀' };
const statusTone: Record<string, string | undefined> = { filled: 'ok', rejected: 'danger', failed: 'danger', submitted: 'live', partial: 'live', dry_run: undefined, skipped: undefined };
const phaseLabel: Record<string, string> = { waiting_entry: '진입 대기', holding: '보유 중', tp1_done: '1차 완료', trailing: '트레일링', closed: '청산 완료' };
const legLabel: Record<string, string> = { entry: '진입', stop: '손절 청산', tp1: '1차 익절', tp2: '2차 익절', trailing: '트레일링 청산' };
const num = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`;
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
  const fail = (error: unknown, fallback: string) => setMessage(error instanceof Error ? error.message : fallback);
  const loadPlans = () => { api.tradePlans().then(setPlans).catch(error => fail(error, '계획 조회 실패')); api.evaluatePlans().then(setEvaluations).catch(() => setEvaluations([])); };
  const loadOrders = () => api.tradeOrders().then(setOrders).catch(() => undefined);
  const loadStatus = () => api.tradingStatus().then(setStatus).catch(() => setStatus({ enabled: false, env: null, account_masked: null, source: null, reason: '브로커 상태를 확인할 수 없습니다.', active_env: 'paper', accounts: { paper: null, real: null } }));
  useEffect(() => { loadStatus(); loadPlans(); loadOrders(); }, []);
  useEffect(() => { window.addEventListener('mscr-settings-changed', loadStatus); return () => window.removeEventListener('mscr-settings-changed', loadStatus); }, []);
  // 종목 상세 차트에서 계획을 저장하면 이 탭은 다시 마운트되지 않으므로 이벤트로 목록을 새로 읽는다.
  useEffect(() => { const reload = () => loadPlans(); window.addEventListener('mscr-plans-changed', reload); return () => window.removeEventListener('mscr-plans-changed', reload); }, []);
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
      enabled: true, note: '',
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
        enabled: form.enabled, note: form.note.trim() || null,
      });
      setMessage(`계획 "${form.name.trim()}"을 저장했습니다.`);
      setForm(emptyForm);
      loadPlans();
    } catch (error) { fail(error, '계획 저장 실패'); } finally { setBusy(false); }
  };
  const edit = (plan: TradePlan) => setForm({
    id: plan.id, name: plan.name, ticker: plan.ticker, side: plan.side, quantity: String(plan.quantity),
    order_type: plan.order_type, limit_price: plan.limit_price == null ? '' : String(plan.limit_price),
    entry_price: String(plan.entry_price), stop_price: String(plan.stop_price),
    tp1_price: String(plan.tp1_price), tp1_ratio: String(plan.tp1_ratio * 100),
    tp2_price: String(plan.tp2_price), tp2_ratio: String(plan.tp2_ratio * 100),
    tp3_trailing_pct: String(plan.tp3_trailing_pct),
    enabled: plan.enabled, note: plan.note || '',
  });
  const remove = async (plan: TradePlan) => {
    if (!window.confirm(`계획 "${plan.name}"을 삭제합니다. 계속할까요?`)) return;
    try { await api.deleteTradePlan(plan.id); if (form.id === plan.id) setForm(emptyForm); setMessage(`계획 "${plan.name}"을 삭제했습니다.`); loadPlans(); } catch (error) { fail(error, '계획 삭제 실패'); }
  };
  // 계획의 가격을 그대로 차트 블록으로 넘긴다. 이동 대상 목록은 계획 목록의 종목들이라 ◀▶로 계획 사이를 오갈 수 있다.
  const showOnChart = (plan: TradePlan) => {
    storePositionPlan(plan.ticker, positionFromTradePlan(plan));
    onSelect(plan.ticker, plans.map(item => item.ticker));
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
    try { const rows = await api.runPlans({ dry_run: dryRun }); setMessage(`${dryRun ? '모의 실행' : '실주문 실행'} — ${summarize(rows)}`); loadOrders(); loadPlans(); } catch (error) { fail(error, '실행 실패'); } finally { setBusy(false); }
  };
  const sync = async () => {
    setBusy(true);
    try { const rows = await api.syncOrders(); setMessage(rows.length ? `체결 동기화 — ${summarize(rows)}` : '동기화할 미체결 주문이 없습니다.'); loadOrders(); loadPlans(); window.dispatchEvent(new Event('mscr-trades-changed')); } catch (error) { fail(error, '동기화 실패'); } finally { setBusy(false); }
  };

  return <div className="trading-layout">
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
        </div>
      </div>
      {/* 작업 결과가 있으면 msg, 없으면 계획 건수만 조용히 보여준다. 둘 중 하나는 항상 렌더해 줄이 사라지지 않게 한다. */}
      {message ? <span className="msg">{message}</span> : <div className="subtle">대상 계획 {armed.length}건 / 전체 {plans.length}건</div>}
    </section>

    <section className="panel autoplan-panel">
      <h1>계획 자동 생성</h1>
      <p className="subtle">종목·진입가·최대 투자 금액·최대 손실 금액만 넣으면 손절폭별 후보를 계산합니다. 후보를 고른 뒤 아래 계획 폼으로 불러와 이름을 붙이고 저장하세요. 자동 생성 자체로는 아무 주문도 나가지 않습니다.</p>
      <form className="autoplan-form" onSubmit={propose}>
        <label>종목코드<input required pattern="[0-9A-Z]{6}" value={proposeForm.ticker} onChange={event => setProposeForm(current => ({ ...current, ticker: event.target.value.toUpperCase() }))} placeholder="005930" /></label>
        <label>매매구분<select value={proposeForm.side} onChange={event => setProposeForm(current => ({ ...current, side: event.target.value as 'buy' | 'sell' }))}><option value="buy">매수</option><option value="sell">매도</option></select></label>
        <label>진입가<input required type="number" min="0" step="any" value={proposeForm.entry_price} onChange={event => setProposeForm(current => ({ ...current, entry_price: event.target.value }))} placeholder="257000" /></label>
        <label>최대 투자 금액<input required type="number" min="0" step="any" value={proposeForm.max_investment} onChange={event => setProposeForm(current => ({ ...current, max_investment: event.target.value }))} placeholder="10000000" /></label>
        <label>최대 손실 금액<input required type="number" min="0" step="any" value={proposeForm.max_loss} onChange={event => setProposeForm(current => ({ ...current, max_loss: event.target.value }))} placeholder="300000" /></label>
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
          <div className="toolbar toolbar--tight"><button className="btn btn--ghost btn--sm" onClick={() => showOnChart(plan)} title="계획의 가격을 종목 상세 차트에 블록으로 띄웁니다">차트에서 보기</button><button className="btn btn--ghost btn--sm" onClick={() => edit(plan)}>편집</button><button className="btn btn--danger btn--sm" onClick={() => remove(plan)}>삭제</button></div>
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
  </div>;
}
