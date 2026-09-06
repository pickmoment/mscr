import { useEffect, useState } from 'react';
import { api, JobStatus, SignalCoverage, SignalDiff, SignalRow } from '../lib/api';
import { SelectTicker } from '../lib/nav';
import { won } from '../lib/format';
import Term from './Term';

// 보유 종목은 수백 건이 될 수 있어 결과 영역 한 화면에 담기는 만큼만 보여주고 나머지는 건수로 알린다.
const HELD_LIMIT = 50;

export default function SignalLogPanel({ screenId, screenName, onSelect }: { screenId: number; screenName: string; onSelect: SelectTicker }) {
  const [coverage, setCoverage] = useState<SignalCoverage | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [diff, setDiff] = useState<SignalDiff | null>(null);
  const [captureDays, setCaptureDays] = useState('1');
  const [signalMessage, setSignalMessage] = useState('');
  const loadCoverage = () => api.signalCoverage().then(setCoverage).catch(() => undefined);
  const loadDiff = (id: number) => api.signalDiff(id).then(setDiff).catch(() => setDiff(null));
  useEffect(() => { loadCoverage(); api.signalStatus().then(setJob).catch(() => undefined); }, []);
  useEffect(() => {
    setDiff(null);
    setSignalMessage('');
    loadDiff(screenId);
  }, [screenId]);
  // 수집은 백그라운드 작업이라 상태를 되물어야 한다. 끝나는 순간 커버리지와 차이 목록을 다시 읽는다.
  useEffect(() => {
    if (!job?.running) return;
    const timer = setInterval(() => {
      api.signalStatus().then(next => {
        setJob(next);
        if (next.running) return;
        loadCoverage();
        loadDiff(screenId);
      }).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.running, screenId]);
  const capture = async (force: boolean) => {
    if (job?.running) return;
    setSignalMessage('');
    try {
      setJob(await api.captureSignals({ days: Math.min(1000, Math.max(1, Number(captureDays) || 1)), force, screen_ids: [screenId] }));
    } catch (error) {
      setSignalMessage(error instanceof Error ? error.message : '수집 요청 실패');
    }
  };
  const coverageRow = coverage ? coverage.screens.find(entry => entry.id === screenId) : undefined;
  const heldShown = diff ? diff.held.slice(0, HELD_LIMIT) : [];
  // 이 섹션에 실제로 보이는 종목들이 종목 상세의 앞뒤 이동 범위가 된다.
  const diffTickers = diff ? [...diff.entered, ...heldShown, ...diff.exited].map(row => row.ticker) : [];
  const signalRows = (rows: SignalRow[], streak: boolean) => rows.map(row => <tr key={row.ticker} onClick={() => onSelect(row.ticker, diffTickers)} title="종목 상세로 이동">
    <td>{row.name || row.ticker}<span className="subtle mono"> {row.ticker}</span></td>
    <td className="num">{row.rank}</td>
    <td className="num">{won(row.close)}</td>
    {streak && <td className="num">연속 {row.streak_days ?? diff?.streaks[row.ticker]?.days ?? 1}일</td>}
  </tr>);

  // 결과 영역은 화면 높이에 맞춰 고정되므로 길어지는 표는 이 안에서 스크롤한다.
  return <div className="stack scroll">
    <div className="toolbar"><div className="section-title">신호 로그</div><span className="badge">{screenName}</span></div>
    <p className="hint">저장된 프리셋을 과거 거래일마다 다시 돌려 편입된 종목을 기록해 둔 <Term id="signal_log" />입니다. 신규 진입은 오늘 새로 들어온 종목, 이탈은 빠진 종목, <Term id="streak">연속 N일</Term>은 프리셋에 계속 남아 있는 날짜 수입니다(1일이면 신규).</p>
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
  </div>;
}
