import { useEffect, useState } from 'react';
import { api, PlanCandidate, PlanProposal, PlanSimulation, TradePlan } from '../../lib/api';
import { positionFromTradePlan, storePositionPlan } from '../../lib/position';
import { SelectTicker } from '../../lib/nav';
import { won } from '../../lib/format';
import { phaseLabel } from '../../lib/labels';
import ViewHeader from '../ViewHeader';
import TickerSearch from '../TickerSearch';
import Term from '../Term';
import { useTrading } from './TradingContext';
import { bindingLabel, candidateColumns, emptyForm, emptyPropose, fieldText, isoDaysAgo, legLabel, num, pct, ProposeDraft, rMultiple } from './shared';

/** 계획을 만들고 고치는 화면. 여기서는 브로커로 아무것도 나가지 않는다. */
export default function PlansView({ onSelect }: { onSelect: SelectTicker }) {
  const { plans, setups, evaluationOf, heat, message, busy, form, setForm, setMessage, setBusy, fail, loadPlans, loadHeat } = useTrading();
  const [proposeForm, setProposeForm] = useState<ProposeDraft>(emptyPropose);
  const [proposal, setProposal] = useState<PlanProposal | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  // 자동 생성의 최대 손실 금액은 리스크 한도에서 나온 권장값으로 시작한다. 사용자가 한 번이라도 고치면 그 값을 덮지 않는다.
  const [maxLossTouched, setMaxLossTouched] = useState(false);
  const [simPlan, setSimPlan] = useState<number | null>(null);
  const [simRange, setSimRange] = useState<{ start: string; end: string }>({ start: isoDaysAgo(180), end: new Date().toISOString().slice(0, 10) });
  const [simResult, setSimResult] = useState<PlanSimulation | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState('');
  useEffect(() => {
    const suggested = heat?.budget.suggested_max_loss;
    if (suggested != null && !maxLossTouched) setProposeForm(current => ({ ...current, max_loss: String(Math.round(suggested)) }));
  }, [heat, maxLossTouched]);
  const pickedCandidate: PlanCandidate | undefined = proposal && picked !== null ? proposal.candidates[picked] : undefined;
  const loadable = pickedCandidate !== undefined && pickedCandidate.rejected === null;

  const propose = async (event: React.FormEvent) => {
    event.preventDefault();
    // 티커 칸이 검색 입력으로 바뀌면서 브라우저의 pattern 검증이 사라졌다. 같은 조건을 여기서 본다.
    if (!/^[0-9A-Z]{6}$/.test(proposeForm.ticker.trim())) { setMessage('종목코드 6자리를 입력하거나 검색에서 고르세요.'); return; }
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
    if (!/^[0-9A-Z]{6}$/.test(form.ticker.trim())) { setMessage('종목코드 6자리를 입력하거나 검색에서 고르세요.'); return; }
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

  return <div className="page stack stack--lg">
    <ViewHeader
      title="계획"
      lede={<>진입가·손절가·분할 익절을 미리 정해 두는 곳입니다. 여기서는 아무 주문도 나가지 않습니다 — 실제 전송은 <b>주문 실행</b> 화면에서만 합니다.</>}
    />
    {message && <div className="msg">{message}</div>}

    <section className="panel autoplan-panel">
      <div className="section-title">계획 자동 생성</div>
      <p className="subtle">종목·진입가·최대 투자 금액·최대 손실 금액만 넣으면 손절폭별 후보를 계산합니다. 후보를 고른 뒤 아래 계획 폼으로 불러와 이름을 붙이고 저장하세요. 자동 생성 자체로는 아무 주문도 나가지 않습니다.</p>
      <form className="autoplan-form" onSubmit={propose}>
        <label>종목코드<TickerSearch
          ariaLabel="자동 생성 종목 검색"
          placeholder="005930 또는 종목명"
          value={proposeForm.ticker}
          onChange={next => setProposeForm(current => ({ ...current, ticker: next.toUpperCase() }))}
          onPick={hit => setProposeForm(current => ({ ...current, ticker: hit.ticker }))}
        /></label>
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
      <div className="section-title">{form.id === null ? '새 계획' : `계획 편집 #${form.id}`}</div>
      <p className="subtle">진입가 돌파 시 진입 주문이 나가고, 손절가 이탈 시 즉시 전량 청산합니다. 1차·2차는 목표가 도달 시 지정 비율만큼 시장가 익절하고, 나머지는 트레일링 스탑으로 관리합니다. 실주문은 주문 실행 화면의 실주문 실행 버튼으로만 전송됩니다.</p>
      <form onSubmit={submit} className="form-grid">
        <label>이름<input required value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} placeholder="삼성전자 돌파매수" /></label>
        <label>티커<TickerSearch
          ariaLabel="계획 종목 검색"
          placeholder="005930 또는 종목명"
          value={form.ticker}
          onChange={next => setForm(current => ({ ...current, ticker: next.toUpperCase() }))}
          onPick={hit => setForm(current => ({ ...current, ticker: hit.ticker }))}
        /></label>
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
        <label><Term id="setup">셋업</Term><input list="trading-setups" value={form.setup} onChange={event => setForm(current => ({ ...current, setup: event.target.value }))} placeholder="저장한 프리셋 이름" title="복기 화면에서 셋업별 기대 R로 묶이는 태그입니다. 저장된 스크리너 프리셋 이름을 고르거나 직접 적으세요." /></label>
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
  </div>;
}
