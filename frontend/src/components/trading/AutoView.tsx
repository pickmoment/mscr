import { useEffect, useState } from 'react';
import { api, DaemonStatus, GuardState } from '../../lib/api';
import { money } from '../../lib/format';
import ViewHeader from '../ViewHeader';
import { useTrading } from './TradingContext';

const PHASE_LABEL: Record<string, string> = { before: '장 전', open: '장중', closing: '마감 임박', after: '장 마감', holiday: '휴장' };

/**
 * 장중 실행 데몬을 감시하는 화면. 데몬을 켜고 끄는 건 터미널(`mscr trade daemon`)의 몫이다 —
 * 실주문 책임을 웹 프로세스에 붙이지 않으려고 일부러 시작 버튼을 두지 않았다.
 * 대신 여기서는 "지금 살아 있는가"와 "멈출 수 있는가"를 본다.
 */
export default function AutoView() {
  const { setMessage, fail, loadOrders, loadPlans } = useTrading();
  const [daemon, setDaemon] = useState<DaemonStatus | null>(null);
  const [guard, setGuard] = useState<GuardState | null>(null);
  const [draft, setDraft] = useState({ entries: '', notional: '', paper: true });
  const [busy, setBusy] = useState(false);

  const load = () => {
    api.daemonStatus().then(setDaemon).catch(() => setDaemon(null));
    api.guardState().then(setGuard).catch(() => setGuard(null));
  };
  // 하트비트는 시간이 지나면 낡는다 — 화면이 열려 있는 동안 5초마다 다시 읽어 공백이 바로 드러나게 한다.
  useEffect(() => { load(); const timer = window.setInterval(load, 5000); return () => window.clearInterval(timer); }, []);
  useEffect(() => { if (guard) setDraft({ entries: String(guard.trade_daily_entry_limit), notional: String(guard.trade_daily_notional_limit_krw), paper: !!guard.trade_require_paper_first }); }, [guard]);

  const saveGuard = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      setGuard(await api.saveGuard({ daily_entry_limit: Number(draft.entries), daily_notional_limit_krw: Number(draft.notional), require_paper_first: draft.paper }));
      setMessage('안전장치를 저장했습니다.');
    } catch (error) { fail(error, '안전장치 저장 실패'); } finally { setBusy(false); }
  };
  const toggleKill = async (engaged: boolean) => {
    if (engaged && !window.confirm('킬스위치를 올리면 새 진입이 멈춥니다. 이미 잡은 포지션의 손절·익절은 계속 나갑니다.\n계속할까요?')) return;
    setBusy(true);
    try { setGuard(await api.toggleKillSwitch(engaged)); setMessage(engaged ? '킬스위치를 올렸습니다 — 신규 진입이 멈춥니다.' : '킬스위치를 내렸습니다.'); } catch (error) { fail(error, '킬스위치 변경 실패'); } finally { setBusy(false); }
  };
  const panic = async () => {
    if (!window.confirm('킬스위치를 올리고 시장에 남아 있는 미체결 주문을 전부 취소합니다.\n정말 실행할까요?')) return;
    setBusy(true);
    try {
      const result = await api.panic();
      setGuard(result.guard);
      setMessage(`긴급 정지 — 미체결 ${result.cancelled.length}건을 취소했습니다.`);
      loadOrders(); loadPlans(); load();
    } catch (error) { fail(error, '긴급 정지 실패'); } finally { setBusy(false); }
  };
  const manage = async () => {
    setBusy(true);
    try { const rows = await api.manageOpenOrders(); setMessage(rows.length ? `미체결 ${rows.length}건을 정리했습니다.` : '정리할 미체결 주문이 없습니다.'); loadOrders(); } catch (error) { fail(error, '미체결 정리 실패'); } finally { setBusy(false); }
  };

  const age = daemon?.heartbeat_age_sec;
  const tone = !daemon ? undefined : daemon.alive ? 'ok' : daemon.stale ? 'danger' : undefined;
  return <div className="page stack stack--lg">
    <ViewHeader
      title="자동 실행"
      lede={<>장중에 실시간 체결가를 보며 계획을 집행하는 데몬의 상태와 안전장치입니다. 데몬은 터미널에서 <code>mscr trade daemon --live</code>로 띄웁니다.</>}
    />

    <section className="panel panel--pad stack">
      <div className="toolbar">
        <div className="section-title">데몬</div>
        <span className="badge" data-tone={tone}>{!daemon ? '상태 미확인' : daemon.alive ? '감시 중' : daemon.stale ? '응답 없음' : '정지'}</span>
        <span className="badge">{PHASE_LABEL[daemon?.phase || ''] || '—'}</span>
        {daemon?.mode && <span className="badge" data-tone={daemon.mode === 'live' ? 'real' : undefined}>{daemon.mode === 'live' ? '실주문' : '관찰만'}</span>}
        {daemon?.stream && daemon.status === 'running' && <span className="badge">{daemon.stream === 'websocket' ? '실시간 시세' : daemon.stream === 'polling' ? 'REST 폴링' : daemon.stream}</span>}
      </div>
      {daemon?.stale && <p className="warning">데몬이 running으로 기록돼 있는데 하트비트가 {age ? `${Math.round(age)}초` : '오래'} 끊겼습니다 — 프로세스가 죽었을 수 있습니다. 감시가 없는 동안에는 손절도 나가지 않습니다.</p>}
      <div className="grid grid--auto">
        <div className="stat-card"><label>하트비트</label><strong>{age === null || age === undefined ? '—' : `${Math.round(age)}초 전`}</strong><span className="subtle">{daemon?.heartbeat_at || '기록 없음'}</span></div>
        <div className="stat-card"><label>감시 중</label><strong>{daemon?.plans || 0}개 계획</strong><span className="subtle">{daemon?.tickers || 0}종목 · 틱 {(daemon?.ticks || 0).toLocaleString()}건</span></div>
        <div className="stat-card"><label>오늘 주문</label><strong>{daemon?.orders || 0}건</strong><span className="subtle">{daemon?.started_at ? `시작 ${daemon.started_at.slice(11, 16)}` : '시작 기록 없음'}</span></div>
      </div>
      {daemon?.last_error && <p className="warning">마지막 오류: {daemon.last_error}</p>}
      {daemon?.message && <p className="subtle">{daemon.message}</p>}
    </section>

    <section className="panel panel--pad stack">
      <div className="toolbar">
        <div className="section-title">안전장치</div>
        {guard?.blocked && <span className="badge" data-tone="danger">킬스위치 ON</span>}
      </div>
      <p className="subtle">한도와 킬스위치는 <b>신규 진입만</b> 막습니다. 손절·익절·트레일링 청산은 어떤 경우에도 막지 않습니다 — 청산을 막는 안전장치는 안전장치가 아니기 때문입니다.</p>
      {guard?.blocked && <p className="warning">{guard.trade_kill_reason || '사유 미기록'}</p>}
      <div className="grid grid--auto">
        <div className="stat-card"><label>오늘 진입</label><strong>{guard?.usage.entries ?? 0}건{guard?.trade_daily_entry_limit ? ` / ${guard.trade_daily_entry_limit}건` : ''}</strong><span className="subtle">{guard?.trade_daily_entry_limit ? `${guard.entries_remaining ?? 0}건 남음` : '건수 무제한'}</span></div>
        <div className="stat-card"><label>오늘 진입 금액</label><strong>{money(guard?.usage.notional_krw || 0)}</strong><span className="subtle">{guard?.trade_daily_notional_limit_krw ? `한도까지 ${money(guard.notional_remaining_krw || 0)}` : '금액 무제한'}</span></div>
        <div className="stat-card"><label>모의 선행 검증</label><strong>{guard?.trade_require_paper_first ? '필수' : '해제'}</strong><span className="subtle">실계좌 진입 전 같은 계획의 모의 체결을 요구</span></div>
      </div>
      <form className="autoplan-form" onSubmit={saveGuard}>
        <label>일일 진입 건수<input type="number" min={0} value={draft.entries} onChange={event => setDraft({ ...draft, entries: event.target.value })} title="하루에 새로 열 수 있는 포지션 수입니다. 0이면 무제한이며, 청산 주문은 여기에 포함되지 않습니다." /></label>
        <label>일일 진입 금액(원)<input type="number" min={0} step={100000} value={draft.notional} onChange={event => setDraft({ ...draft, notional: event.target.value })} title="하루에 새로 투입할 수 있는 금액입니다. 0이면 무제한입니다." /></label>
        <label className="check"><input type="checkbox" checked={draft.paper} onChange={event => setDraft({ ...draft, paper: event.target.checked })} />실계좌 진입 전 같은 계획의 모의 체결을 요구</label>
        <div className="toolbar">
          <button type="submit" disabled={busy}>안전장치 저장</button>
          {guard?.blocked
            ? <button type="button" onClick={() => toggleKill(false)} disabled={busy}>킬스위치 내리기</button>
            : <button type="button" onClick={() => toggleKill(true)} disabled={busy}>킬스위치 올리기</button>}
          <button type="button" onClick={manage} disabled={busy}>미체결 정리</button>
          <button type="button" className="danger" onClick={panic} disabled={busy}>긴급 정지 · 전량 취소</button>
        </div>
      </form>
    </section>
  </div>;
}
