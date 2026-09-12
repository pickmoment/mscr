import { useEffect, useState } from 'react';
import { api, RiskLimits } from '../../lib/api';
import { money } from '../../lib/format';
import ViewHeader from '../ViewHeader';
import Term from '../Term';
import { useTrading } from './TradingContext';
import { LimitDraft, num, pct, pctPoint, riskColumns, stateLabel, stateTone } from './shared';

/** 포트폴리오 히트. 계획 하나하나의 손실 한도는 그 합을 묶어 주지 않는다. 이 화면이 그 합을 본다. */
export default function RiskView() {
  const { heat, busy, setHeat, setMessage, setBusy, fail } = useTrading();
  const [draft, setDraft] = useState<LimitDraft>({ risk: '', heat: '' });
  const [showClosed, setShowClosed] = useState(false);
  // 한도 저장 응답이 새 히트를 그대로 주므로, 폼은 서버가 확정한 값만 되비춘다.
  useEffect(() => { if (heat) setDraft({ risk: String(heat.limits.risk_per_trade_pct), heat: String(heat.limits.max_portfolio_heat_pct) }); }, [heat]);
  const rows = heat ? heat.plans.filter(row => showClosed || row.state === 'open' || row.state === 'pending') : [];
  const hidden = heat ? heat.plans.length - rows.length : 0;
  const saveLimits = async (event: React.FormEvent) => {
    event.preventDefault();
    const limits: RiskLimits = { risk_per_trade_pct: Number(draft.risk), max_portfolio_heat_pct: Number(draft.heat) };
    setBusy(true);
    try { setHeat(await api.saveRiskLimits(limits)); setMessage(`리스크 한도를 저장했습니다. 1회 ${limits.risk_per_trade_pct}% / 히트 ${limits.max_portfolio_heat_pct}%.`); } catch (error) { fail(error, '리스크 한도 저장 실패'); } finally { setBusy(false); }
  };

  return <div className="page stack stack--lg">
    <ViewHeader
      title="리스크"
      lede={<>계획들이 동시에 손절당했을 때 잃는 총액(<Term id="heat" />)과 그 한도를 봅니다.</>}
    />
    <section className="panel autoplan-panel">
      <p className="subtle">보유 중인 거래와 대기 중인 계획이 <b>모두 손절에 닿았을 때</b> 잃는 총액입니다. 계획마다 손실 한도를 걸어도 그 합에는 상한이 생기지 않습니다. <Term id="heat" />는 그 합을 총자산으로 나눈 값입니다.</p>
      {!heat ? <div className="empty">리스크 정보를 불러오지 못했습니다.</div> : <div className="stack">
        <div className="autoplan-head">
          <span className="badge" data-tone={heat.over_limit ? 'danger' : 'ok'}>히트 {pctPoint(heat.heat_pct)} / 한도 {heat.limits.max_portfolio_heat_pct}%</span>
          <span className="subtle">기준일 {heat.as_of || '—'} · 보유분만 보면 {pctPoint(heat.open_heat_pct)} · 계획 {heat.plans.length}건</span>
        </div>
        <div className="grid grid--auto">
          <div className="stat-card"><label>진행 위험</label><strong>{money(heat.open_risk_krw)}</strong><span className="subtle">체결된 포지션이 손절까지 잃을 금액</span></div>
          <div className="stat-card"><label>대기 위험</label><strong>{money(heat.pending_risk_krw)}</strong><span className="subtle">아직 진입하지 않은 계획의 최초 위험</span></div>
          <div className="stat-card"><label>총자산</label><strong>{money(heat.equity)}</strong><span className="subtle">현금 {money(heat.cash_krw)} · 평가 {money(heat.market_value)}</span></div>
          <div className="stat-card"><label>남은 여유</label><strong className={heat.budget.remaining_krw > 0 ? undefined : 'down'}>{money(heat.budget.remaining_krw)}</strong><span className="subtle">한도 {money(heat.budget.heat_limit_krw)} · 총 위험 {money(heat.total_risk_krw)}</span></div>
        </div>
        <form className="autoplan-form" onSubmit={saveLimits}>
          <label><Term id="risk_per_trade">1회 위험</Term>(%)<input required type="number" min="0" step="any" value={draft.risk} onChange={event => setDraft(current => ({ ...current, risk: event.target.value }))} title="계획 한 건에 허용하는 손실을 총자산 대비 비율로 정합니다. 계획 자동 생성의 최대 손실 금액 기본값이 됩니다." /></label>
          <label>히트 한도(%)<input required type="number" min="0" step="any" value={draft.heat} onChange={event => setDraft(current => ({ ...current, heat: event.target.value }))} title="모든 계획의 손실 합계가 넘지 말아야 할 총자산 대비 비율입니다." /></label>
          <button className="btn btn--primary" disabled={busy}>한도 저장</button>
          <span className="subtle">1회 위험 {money(heat.budget.per_trade_krw)}{heat.budget.suggested_max_loss == null ? '' : ` · 다음 계획 권장 손실 한도 ${money(heat.budget.suggested_max_loss)}`}</span>
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
              <td className="num">{money(row.risk_krw)}</td>
              <td className="num">{money(row.initial_risk_krw)}</td>
              <td className="num">{pctPoint(row.risk_pct)}</td>
            </tr>)}</tbody>
          </table>
          {!rows.length && <div className="empty">{heat.plans.length ? '진행·대기 중인 계획이 없습니다. 종료·중지 포함을 켜면 지난 계획이 보입니다.' : '위험을 계산할 계획이 없습니다.'}</div>}
        </div>
        {heat.unprotected.length > 0 && <div className="stack">
          <div className="section-title">손절 없는 보유 <span className="badge" data-tone="danger">{heat.unprotected.length}</span></div>
          <div className="chip-row">{heat.unprotected.map(item => <span className="badge" data-tone="danger" key={item.ticker}>{item.name}({item.ticker}) · {num(item.quantity)}주 · {money(item.market_value)} · 비중 {pct(item.weight)}</span>)}</div>
          <div className="subtle">손절가를 가진 계획이 없는 보유입니다. 최대 손실이 정해져 있지 않아 위의 히트에 잡히지 않습니다. 계획을 만들어 손절을 걸어야 이 표에서 빠집니다.</div>
        </div>}
      </div>}
    </section>
  </div>;
}
