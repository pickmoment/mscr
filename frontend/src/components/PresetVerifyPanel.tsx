import { useEffect, useState } from 'react';
import { api, BacktestProtocol, BacktestResult, ForwardReturns } from '../lib/api';
import Term from './Term';

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

export default function PresetVerifyPanel({ screenId, screenName }: { screenId: number; screenName: string }) {
  const [protocol, setProtocol] = useState<ProtocolDraft>(defaultProtocol);
  const [verification, setVerification] = useState<BacktestResult | null>(null);
  const [forward, setForward] = useState<ForwardReturns | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyMessage, setVerifyMessage] = useState('');
  useEffect(() => {
    setVerification(null);
    setForward(null);
    setVerifyMessage('');
  }, [screenId]);
  const runVerification = async () => {
    if (verifyBusy) return;
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
    if (verifyBusy) return;
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

  // 결과 영역은 화면 높이에 맞춰 고정되므로 길어지는 표는 이 안에서 스크롤한다.
  return <div className="stack scroll">
    <div className="toolbar"><div className="section-title">프리셋 검증</div><span className="badge">{screenName}</span></div>
    <p className="hint">신호 로그를 아래 청산 규칙으로 그대로 재생합니다. <Term id="expectancy" />은 실제로 진입한 거래만의 평균 R이고, 트리거되지 않은 신호는 신호 수에만 남습니다. <Term id="r">1R</Term>은 진입가와 손절가의 차이입니다.</p>
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
      <div className="grid grid--2">
        <table className="table table--kv"><tbody>
          <tr><td>기간</td><td>{verification.period.start || '—'} ~ {verification.period.end || '—'} ({verification.period.days.toLocaleString('ko-KR')}일)</td></tr>
          <tr><td>신호 수</td><td>{verification.signals.toLocaleString('ko-KR')}건</td></tr>
          <tr><td>트리거 수</td><td>{verification.triggered.toLocaleString('ko-KR')}건</td></tr>
          <tr><td>거래 수</td><td>{verification.trades.toLocaleString('ko-KR')}건</td></tr>
          <tr><td>트리거율</td><td>{rateText(verification.trigger_rate)}</td></tr>
        </tbody></table>
        <table className="table table--kv"><tbody>
          <tr><td>승률</td><td>{rateText(verification.win_rate)}</td></tr>
          <tr><td>목표 도달</td><td>{rateText(verification.target_rate)}</td></tr>
          <tr><td>손절</td><td>{rateText(verification.stop_rate)}</td></tr>
          <tr><td>시간 초과</td><td>{rateText(verification.timeout_rate)}</td></tr>
          <tr><td>평균 보유일</td><td>{verification.avg_days_held == null ? '—' : `${verification.avg_days_held.toFixed(1)}일`}</td></tr>
          <tr><td>평균 리스크</td><td>{rateText(verification.avg_risk_pct, 2)}</td></tr>
          <tr><td>흑자월</td><td>{verification.profitable_months}/{verification.total_months}</td></tr>
        </tbody></table>
      </div>
      <div className="msg" data-tone={verification.expectancy_r != null && verification.expectancy_r > 0 ? 'ok' : 'warn'}>
        <b>기대값</b> {rText(verification.expectancy_r)} ± {verification.stderr_r == null ? '—' : verification.stderr_r.toFixed(2)}R <span className="subtle">(진입한 거래 {verification.trades.toLocaleString('ko-KR')}건 평균)</span>
      </div>
      <div className="grid grid--2">
        <div className="stack">
          <div className="section-title">전후 반기 <span className="subtle">기간을 반으로 갈랐을 때</span></div>
          <table className="table table--nowrap">
            <thead><tr><th>구간</th><th className="num">거래</th><th className="num">기대값</th></tr></thead>
            <tbody>{verification.by_half.map(row => <tr key={row.label}><td>{row.label}</td><td className="num">{intText(row.trades)}</td><td className="num">{rText(row.expectancy_r)}</td></tr>)}
              {!verification.by_half.length && <tr><td colSpan={3} className="subtle">반기 비교를 낼 거래가 부족합니다.</td></tr>}</tbody>
          </table>
        </div>
        <div className="stack">
          <div className="section-title">월별</div>
          {/* 화면 전체가 세로로 스크롤되므로 표를 별도 스크롤 상자에 넣지 않는다. .table-scroll는 min-height:0이라 이 그리드 안에서 높이가 접힌다. */}
          <table className="table table--nowrap">
            <thead><tr><th>월</th><th className="num">거래</th><th className="num">기대값</th></tr></thead>
            <tbody>{verification.by_month.map(row => <tr key={row.month}><td className="mono">{row.month}</td><td className="num">{intText(row.trades)}</td><td className={`num ${row.expectancy_r != null && row.expectancy_r > 0 ? 'up' : 'down'}`}>{rText(row.expectancy_r)}</td></tr>)}
              {!verification.by_month.length && <tr><td colSpan={3} className="subtle">월별로 나눌 거래가 없습니다.</td></tr>}</tbody>
          </table>
        </div>
      </div>
      {verification.warnings.length > 0 && <div className="msg" data-tone="warn"><b>확인하세요</b><ul>{verification.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></div>}
    </>}
    {forward && <>
      <div className="section-title"><Term id="forward" /> <span className="badge">{forward.signals.toLocaleString('ko-KR')}건</span></div>
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
  </div>;
}
