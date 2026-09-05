import { useState } from 'react';
import { alignTick, legQuantities, levelLabel, PositionLevel, PositionOrigin, PositionPlan, positionMetrics, positionOutcome, PositionSplit, tickSize } from '../lib/position';
import { api } from '../lib/api';
import { won } from '../lib/format';

type Props = { plan: PositionPlan; ticker: string; name: string; kind: string; onChange: (plan: PositionPlan) => void; onReset: () => void; onClose: () => void };
type PlanForm = { id: number | null; name: string; orderType: 'limit' | 'market'; tp1Ratio: string; tp2Ratio: string; trailing: string; baseTarget2: number | null };
const levelClass: Record<PositionLevel, string> = { entry: 'level-entry', stop: 'level-stop', target: 'level-target', target2: 'level-target2' };
type PriceLevel = 'entry' | 'stop' | 'target';
const editableLevels: PriceLevel[] = ['entry', 'stop', 'target'];
const rate = (value: number | null) => value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`;
const number = (text: string) => { const value = Number(text); return Number.isFinite(value) ? value : 0; };
const signedWon = (value: number) => `${value >= 0 ? '+' : '-'}${won(Math.abs(value))}`;

export default function PositionPlanner({ plan, ticker, name, kind, onChange, onReset, onClose }: Props) {
  const [form, setForm] = useState<PlanForm | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const metrics = positionMetrics(plan);
  // 입력 도중 빈 칸이 되면 Number('')는 0이다. 0은 유효한 가격이 아니므로 그대로 두고 차트가 해당 선을 숨긴다.
  const setPrice = (level: PositionLevel, text: string) => onChange({ ...plan, [level]: Math.max(0, number(text)) });
  const openForm = () => {
    // 트레이딩 탭에서 넘어온 계획은 이미 2차 청산선을 갖고 있다. 그 값은 그대로 두고, 없을 때만 1차 거리의 2배로 잡는다.
    onChange({ ...plan, target2: plan.target2 ?? alignTick(plan.entry + 2 * (plan.target - plan.entry), kind) });
    setMessage('');
    const origin = plan.origin;
    setForm(origin
      ? { id: origin.id, name: origin.name, orderType: origin.orderType, tp1Ratio: String(origin.tp1Ratio), tp2Ratio: String(origin.tp2Ratio), trailing: String(origin.trailing), baseTarget2: plan.target2 }
      : { id: null, name: `${name} ${new Date().toISOString().slice(0, 10)}`, orderType: 'limit', tp1Ratio: '40', tp2Ratio: '30', trailing: (plan.entry > 0 ? metrics.risk / plan.entry * 100 : 1).toFixed(2), baseTarget2: plan.target2 });
  };
  const closeForm = () => { if (form) onChange({ ...plan, target2: form.baseTarget2 }); setForm(null); };

  const tp1Ratio = form ? number(form.tp1Ratio) : 0;
  const tp2Ratio = form ? number(form.tp2Ratio) : 0;
  const legs = legQuantities(plan.quantity, tp1Ratio, tp2Ratio);
  // 분할 비율은 계획 폼을 열었을 때(입력값)나 연결된 계획이 있을 때(저장값)만 알 수 있다. 그때만 레그별 손익을 낸다.
  const split: PositionSplit | null = form ? { tp1Ratio, tp2Ratio, trailing: number(form.trailing) }
    : plan.origin ? { tp1Ratio: plan.origin.tp1Ratio, tp2Ratio: plan.origin.tp2Ratio, trailing: plan.origin.trailing }
    : null;
  const outcome = split && plan.target2 !== null ? positionOutcome(plan, split) : null;
  const ordered = plan.target2 === null ? false : metrics.long ? plan.entry < plan.target && plan.target < plan.target2 : plan.entry > plan.target && plan.target > plan.target2;
  const blocked = !form ? null
    : metrics.warning ? metrics.warning
    : plan.quantity <= 0 ? '수량을 입력해야 계획을 저장할 수 있습니다'
    : !form.name.trim() ? '계획 이름을 입력하세요'
    : !ordered ? `2차 청산가는 1차 청산가보다 ${metrics.long ? '높아야' : '낮아야'} 합니다`
    : tp1Ratio + tp2Ratio >= 100 ? '1차+2차 익절 비율의 합은 100%보다 작아야 합니다 (나머지가 트레일링 대상입니다)'
    : Math.min(...legs) < 1 ? `3분할이 성립하지 않습니다 (배분 ${legs.join('/')}주)`
    : number(form.trailing) <= 0 ? '트레일링 스탑 비율은 0보다 커야 합니다'
    : null;

  const save = async () => {
    if (!form || blocked || plan.target2 === null) return;
    setBusy(true);
    const label = form.name.trim();
    const note = form.id === null || !plan.origin ? (metrics.rr === null ? '차트에서 작성' : `차트에서 작성 · 손익비 ${metrics.rr.toFixed(2)}R`) : plan.origin.note;
    const enabled = form.id === null || !plan.origin ? true : plan.origin.enabled;
    try {
      const { id } = await api.saveTradePlan({
        ...(form.id === null ? {} : { id: form.id }),
        name: label, ticker, side: metrics.long ? 'buy' : 'sell', quantity: plan.quantity,
        order_type: form.orderType, limit_price: form.orderType === 'limit' ? plan.entry : null,
        entry_price: plan.entry, stop_price: plan.stop,
        tp1_price: plan.target, tp1_ratio: tp1Ratio / 100,
        tp2_price: plan.target2, tp2_ratio: tp2Ratio / 100,
        tp3_trailing_pct: number(form.trailing), enabled, note,
      });
      // 저장한 계획에 블록을 묶어 둔다. 이어서 선을 고치고 다시 저장하면 같은 계획이 갱신된다.
      const origin: PositionOrigin = { id, name: label, tp1Ratio, tp2Ratio, trailing: number(form.trailing), orderType: form.orderType, enabled, note };
      onChange({ ...plan, origin });
      setMessage(`계획 "${label}"을 ${form.id === null ? '저장' : '수정'}했습니다. 트레이딩 탭에서 실행합니다.`);
      setForm(null);
      window.dispatchEvent(new Event('mscr-plans-changed'));
    } catch (error) {
      const reason = error instanceof Error ? error.message : '계획 저장 실패';
      // 트레이딩 탭에서 이미 지운 계획이면 연결을 끊어, 다음 저장이 새 계획으로 만들어지게 한다.
      if (reason.includes('찾을 수 없')) { onChange({ ...plan, origin: null }); setForm(current => current && { ...current, id: null }); }
      setMessage(reason);
    } finally { setBusy(false); }
  };

  return <div className="position-planner">
    <div className="toolbar" style={{ gap: 6, margin: 0 }}>
      <div className="section-title">포지션</div>
      <button className="ghost" style={{ marginLeft: 'auto', padding: '3px 9px', minHeight: 24, fontSize: 11 }} onClick={onReset} title="현재가 기준 손절 -5% · 청산 +10%로 되돌립니다">초기화</button>
      <button className="ghost" style={{ padding: '3px 9px', minHeight: 24, fontSize: 11 }} onClick={onClose}>닫기</button>
    </div>
    {editableLevels.map(level => <label key={level} className="planner-field">
      <span className={levelClass[level]}>{levelLabel[level]}</span>
      <input type="number" min="0" step={tickSize(plan[level] || 1, kind)} value={plan[level] || ''} aria-label={`${levelLabel[level]}가`}
        onChange={event => setPrice(level, event.target.value)} onBlur={() => plan[level] > 0 && onChange({ ...plan, [level]: alignTick(plan[level], kind) })} />
    </label>)}
    {plan.target2 !== null && <label className="planner-field">
      <span className={levelClass.target2}>{levelLabel.target2}</span>
      <input type="number" min="0" step={tickSize(plan.target2 || 1, kind)} value={plan.target2 || ''} aria-label="2차 청산가"
        onChange={event => setPrice('target2', event.target.value)} onBlur={() => plan.target2 && onChange({ ...plan, target2: alignTick(plan.target2, kind) })} />
    </label>}
    <label className="planner-field"><span>수량</span><input type="number" min="0" step="1" value={plan.quantity || ''} aria-label="수량" onChange={event => onChange({ ...plan, quantity: Math.max(0, Math.floor(number(event.target.value))) })} /></label>
    <div className="planner-readout">
      <span title="청산까지의 폭 ÷ 손절까지의 폭. 전량을 그 가격에 청산했을 때의 배수이며 분할 비중은 빠져 있습니다."><i className="subtle">손익비</i> <b className={metrics.rr !== null && metrics.rr >= 1 ? 'red' : 'blue'}>{metrics.rr === null ? '—' : `${metrics.rr.toFixed(2)}R`}</b></span>
      <span className="level-stop"><i>손절 {rate(metrics.stopPct)}</i>{plan.quantity > 0 && <b>-{won(metrics.loss)}</b>}</span>
      <span className="level-target" title="전량을 1차 청산가에 넘겼을 때의 손익입니다. 분할하면 아래 '계획 합계'의 1차 몫만 실현됩니다."><i>청산 {rate(metrics.targetPct)}</i>{plan.quantity > 0 && <b>+{won(metrics.gain)}</b>}</span>
      {plan.target2 !== null && <span className="level-target2" title="전량을 2차 청산가에 넘겼을 때의 배수입니다."><i>2차 청산 {rate(metrics.target2Pct)}</i><b>{metrics.rr2 === null ? '—' : `${metrics.rr2.toFixed(2)}R`}</b></span>}
      {plan.quantity > 0 && <span className="subtle"><i>투자금</i> <b>{won(metrics.cost)}</b></span>}
      {metrics.warning && <span className="planner-warning">{metrics.warning}</span>}
    </div>
    {outcome && <div className="planner-readout plan-legs">
      <span title="손절에 닿지 않고 1·2차 목표에 도달하고, 3차는 2차 도달 직후 되돌림(하한)으로 청산됐을 때의 합계입니다. 확률은 반영하지 않습니다.">
        <i className="subtle">계획 합계</i>
        <b className={outcome.totalR !== null && outcome.totalR >= 1 ? 'red' : 'blue'}>{outcome.totalR === null ? '—' : `${outcome.totalR.toFixed(2)}R`}{plan.quantity > 0 ? ` · ${signedWon(outcome.totalAmount)}` : ''}</b>
      </span>
      {outcome.legs.map(leg => <span key={leg.key} className={leg.key === 'tp1' ? 'level-target' : leg.key === 'tp2' ? 'level-target2' : 'subtle'}
        title={leg.floor
          ? `트레일링 청산가는 사후에 정해집니다. 2차 청산가에서 곧바로 ${split?.trailing ?? 0}% 되돌리는 최악의 경우인 ${won(leg.price)}를 하한으로 씁니다.`
          : `${won(leg.price)}에 ${leg.quantity}주를 청산합니다.`}>
        <i>{leg.label} {plan.quantity > 0 ? `${leg.quantity}주` : `${Math.round(leg.weight * 100)}%`}{leg.floor ? ' ≥' : ''}</i>
        <b>{leg.r === null ? '—' : `${leg.r.toFixed(2)}R`}{plan.quantity > 0 ? ` · ${signedWon(leg.amount)}` : ''}</b>
      </span>)}
    </div>}
    {!form && <>
      <span className="subtle planner-hint">{plan.origin ? `계획 "${plan.origin.name}"에 연결됨 · 선을 끌어 고친 뒤 수정하세요.` : '차트의 선을 위아래로 끌어 조정합니다.'}</span>
      <button className="ghost" onClick={openForm} title={plan.origin ? '연결된 트레이딩 계획을 이 가격으로 수정합니다' : '그린 가격을 트레이딩 계획으로 저장합니다'}>{plan.origin ? '계획 수정' : '계획 만들기'}</button>
    </>}
    {form && <div className="plan-form">
      <div className="toolbar" style={{ gap: 6, margin: 0 }}>
        <div className="section-title">{form.id === null ? '새 계획' : '계획 수정'}</div>
        <span className={`badge ${metrics.long ? 'badge-buy' : 'badge-sell'}`}>{metrics.long ? '매수' : '매도'}</span>
      </div>
      <label className="planner-field"><span>이름</span><input type="text" style={{ textAlign: 'left' }} value={form.name} aria-label="계획 이름" onChange={event => setForm({ ...form, name: event.target.value })} /></label>
      <label className="planner-field"><span className="level-target">1차 비율</span><input type="number" min="1" max="98" step="1" value={form.tp1Ratio} aria-label="1차 익절 비율" onChange={event => setForm({ ...form, tp1Ratio: event.target.value })} /></label>
      <label className="planner-field"><span className="level-target2">2차 비율</span><input type="number" min="1" max="98" step="1" value={form.tp2Ratio} aria-label="2차 익절 비율" onChange={event => setForm({ ...form, tp2Ratio: event.target.value })} /></label>
      <label className="planner-field"><span>트레일링</span><input type="number" min="0" step="0.1" value={form.trailing} aria-label="트레일링 스탑 비율" onChange={event => setForm({ ...form, trailing: event.target.value })} /></label>
      <label className="planner-field"><span>주문</span><select value={form.orderType} aria-label="주문 유형" onChange={event => setForm({ ...form, orderType: event.target.value as 'limit' | 'market' })}><option value="limit">지정가</option><option value="market">시장가</option></select></label>
      <div className="planner-readout">
        {blocked && <span className="planner-warning">{blocked}</span>}
      </div>
      <div className="toolbar" style={{ gap: 6, margin: 0 }}>
        <button className="primary" style={{ flex: 1 }} disabled={busy || blocked !== null} onClick={save}>{form.id === null ? '저장' : '수정'}</button>
        <button className="ghost" onClick={closeForm}>취소</button>
      </div>
    </div>}
    {message && <div className="notice">{message}</div>}
  </div>;
}
