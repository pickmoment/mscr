import { useState } from 'react';
import { api, BrokerOrder, ReconcileResult } from '../../lib/api';
import { money } from '../../lib/format';
import ViewHeader from '../ViewHeader';
import Term from '../Term';
import { useTrading } from './TradingContext';
import { legLabel, num, statusLabel, statusTone } from './shared';

/** 계획을 브로커로 내보내는 유일한 화면. 실제 돈이 나가는 버튼이 여기에만 있다. */
export default function OrdersView() {
  const { status, plans, orders, armed, message, busy, setMessage, setBusy, fail, loadOrders, loadPlans, loadHeat } = useTrading();
  const [reconcileResult, setReconcileResult] = useState<ReconcileResult | null>(null);
  const [reconcileBusy, setReconcileBusy] = useState(false);

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

  return <div className="page stack stack--lg">
    <ViewHeader
      title="주문 실행"
      lede={<>조건을 충족한 계획을 브로커로 보내고 체결을 되받아옵니다. <Term id="dry_run" />은 판정만 하고 아무것도 전송하지 않습니다.</>}
    />

    <section className="panel panel--pad stack">
      <div className="toolbar">
        <div className="section-title">브로커</div>
        {status?.enabled
          ? <span className="badge" data-tone={status.env === 'real' ? 'real' : 'live'}>{status.env === 'real' ? '실전 계좌' : '모의 계좌'} · {status.account_masked || '계좌 미확인'}</span>
          : <span className="badge">브로커 미설정</span>}
        {!status?.enabled && <span className="subtle">{status?.reason || '상단 설정 탭에서 브로커 자격증명을 설정하세요.'}</span>}
        {status?.source === 'env' && <span className="subtle">환경변수 우선 적용 중</span>}
      </div>
      {/* 무엇이 나가는지 보고 나서 버튼을 누르도록, 대상 계획을 실행 버튼 위에 둔다. */}
      <div className="section-title">대상 계획</div>
      {armed.length
        ? <table className="table table--nowrap">
          <thead><tr><th>계획</th><th>종목</th><th>다음 동작</th><th>방향</th><th className="num">수량</th><th>사유</th></tr></thead>
          <tbody>{armed.map(item => <tr key={item.plan_id}>
            <td>{item.name}</td>
            <td className="mono">{item.ticker}</td>
            <td>{item.next_leg ? legLabel[item.next_leg] || item.next_leg : '—'}</td>
            <td className={item.order_side === 'sell' ? 'down' : 'up'}>{item.order_side === 'sell' ? '매도' : '매수'}</td>
            <td className="num">{num(item.order_quantity)}</td>
            <td className="subtle">{item.reason}</td>
          </tr>)}</tbody>
        </table>
        : <div className="empty empty--inline">조건을 충족한 계획이 없습니다. 최신 일봉이 계획의 진입·손절·목표 조건에 닿으면 여기에 나타납니다.</div>}
      <div className="toolbar">
        <button className="btn btn--ghost" disabled={busy} onClick={() => run(true)}>모의 실행</button>
        {/* 실제 돈이 나가는 버튼은 기본 액션 색을 쓰지 않는다. 모의 실행과 한눈에 구분돼야 한다. */}
        <button className="btn btn--danger" disabled={busy || !status?.enabled} title={status?.enabled ? undefined : status?.reason || '브로커가 설정되지 않았습니다.'} onClick={() => run(false)}>실주문 실행</button>
        <button className="btn btn--ghost" disabled={busy || !status?.enabled} onClick={sync}>체결 동기화</button>
        <button className="btn btn--ghost" disabled={reconcileBusy || !status?.enabled} title="broker_orders를 거치지 않은 수동 주문이나 동기화 누락으로 로컬 상태가 실제 계좌와 어긋났는지 확인합니다." onClick={runReconcile}>{reconcileBusy ? '대조 중…' : '잔고 대조'}</button>
      </div>
      {/* 작업 결과가 있으면 msg, 없으면 계획 건수만 조용히 보여준다. 둘 중 하나는 항상 렌더해 줄이 사라지지 않게 한다. */}
      {message ? <span className="msg">{message}</span> : <div className="subtle">대상 계획 {armed.length}건 / 전체 {plans.length}건</div>}
      {reconcileResult && <div className="stack">
        <div className="toolbar toolbar--tight">
          <span className="badge" data-tone={reconcileResult.mismatched ? 'danger' : 'ok'}>{reconcileResult.env === 'real' ? '실전' : '모의'} {reconcileResult.account_masked} · {reconcileResult.mismatched ? `불일치 ${reconcileResult.mismatched}건` : '일치'}</span>
          <span className="subtle">현금 — 로컬 {money(reconcileResult.local_cash_krw)} / 브로커 {money(reconcileResult.broker_cash_krw)}(사용자 입력값이라 다를 수 있습니다)</span>
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
            <td className="num">{money(order.filled_price)}</td>
            <td className="mono">{order.broker_order_id || '—'}</td>
            <td className="subtle">{order.message || ''}</td>
          </tr>)}</tbody>
        </table>
        {!orders.length && <div className="empty">주문 기록이 없습니다.</div>}
      </div>
    </section>
  </div>;
}
