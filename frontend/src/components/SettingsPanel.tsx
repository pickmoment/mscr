import { useEffect, useState } from 'react';
import { api, AppSettings, IngestSource, IngestStatus } from '../lib/api';
import ViewHeader from './ViewHeader';

const krxModeLabel: Record<AppSettings['krx']['mode'], string> = { openapi: 'Open API 키', idpw: '아이디/비밀번호', anonymous: '인증 없이 사용' };
const sourceNote: Record<'env' | 'file', string> = { env: '환경변수 우선 적용 중', file: '이 툴에 저장됨' };
const delaySourceLabel: Record<AppSettings['request_delay_source'], string> = { default: '기본값', env: '환경변수', file: '저장됨' };
const kisEnvLabel: Record<'paper' | 'real', string> = { paper: '모의 계좌', real: '실전 계좌' };
const emptyKrx = { openapi_key: '', krx_id: '', krx_pw: '' };
const sourceLabel: Record<IngestSource, string> = {
  krx: 'krx (기본)',
  fdr: 'fdr (전종목 스냅샷 대체, 주식만)',
  alphasquare: 'alphasquare (종목별 개별 조회, 최후 폴백, 수집 기간 그대로 적용)',
  massive: 'massive (미국 전종목 일별 시장 요약)',
};
const emptyKisForm = { app_key: '', app_secret: '', account: '' };
type Feedback = { text: string; ok: boolean };
const emptyFeedback: Feedback = { text: '', ok: true };

export default function SettingsPanel() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [krx, setKrx] = useState(emptyKrx);
  const [kisForms, setKisForms] = useState({ paper: emptyKisForm, real: emptyKisForm });
  const [delay, setDelay] = useState('');
  const [loadError, setLoadError] = useState('');
  const [krxMsg, setKrxMsg] = useState<Feedback>(emptyFeedback);
  const [kisMsgs, setKisMsgs] = useState({ paper: emptyFeedback, real: emptyFeedback });
  const [prefMsg, setPrefMsg] = useState<Feedback>(emptyFeedback);
  const [busy, setBusy] = useState(false);
  const [ingestForm, setIngestForm] = useState({ days: '400', force: false, source: 'krx' as IngestSource });
  const [massiveKey, setMassiveKey] = useState('');
  const [massiveMsg, setMassiveMsg] = useState<Feedback>(emptyFeedback);
  const [massiveDelay, setMassiveDelay] = useState('');
  const [ingestStatus, setIngestStatus] = useState<IngestStatus | null>(null);
  const [ingestMsg, setIngestMsg] = useState<Feedback>(emptyFeedback);
  const [latestDayMsg, setLatestDayMsg] = useState<Feedback>(emptyFeedback);
  const apply = (next: AppSettings) => {
    setSettings(next);
    setDelay(String(next.request_delay_sec));
    setMassiveDelay(String(next.massive.request_delay_sec));
    setIngestForm({ days: String(next.ingest_defaults.days), force: next.ingest_defaults.force, source: next.ingest_defaults.source });
  };
  const write = async (action: () => Promise<void>) => { setBusy(true); try { await action(); } finally { setBusy(false); } };
  const fields = (event: React.FormEvent) => { const data = new FormData(event.currentTarget as HTMLFormElement); return (name: string) => String(data.get(name) ?? '').trim(); };
  const setKisMsg = (env: 'paper' | 'real', msg: Feedback) => setKisMsgs(current => ({ ...current, [env]: msg }));
  useEffect(() => { api.settings().then(apply).catch(error => setLoadError(error instanceof Error ? error.message : '설정을 불러올 수 없습니다.')); }, []);
  useEffect(() => { api.ingestStatus().then(setIngestStatus).catch(() => undefined); }, []);
  useEffect(() => {
    if (!ingestStatus?.running) return;
    const id = setInterval(() => { api.ingestStatus().then(setIngestStatus).catch(() => undefined); }, 1500);
    return () => clearInterval(id);
  }, [ingestStatus?.running]);
  useEffect(() => {
    if (ingestStatus && !ingestStatus.running && ingestStatus.finished_at && ingestStatus.ok) {
      api.settings().then(apply).catch(() => undefined);
    }
  }, [ingestStatus?.finished_at]);

  const saveKrx = (event: React.FormEvent) => {
    event.preventDefault();
    const field = fields(event);
    const payload = Object.fromEntries(Object.keys(emptyKrx).map(key => [key, field(key)]).filter(([, value]) => value !== ''));
    if (!Object.keys(payload).length) { setKrxMsg({ text: '입력한 항목이 없습니다.', ok: false }); return; }
    setKrxMsg(emptyFeedback);
    return write(async () => {
      try { apply(await api.saveKrxCredentials(payload)); setKrx(emptyKrx); setKrxMsg({ text: 'KRX 자격증명을 저장했습니다.', ok: true }); } catch (error) { setKrxMsg({ text: error instanceof Error ? error.message : 'KRX 자격증명 저장 실패', ok: false }); }
    });
  };
  const clearKrx = () => {
    if (!window.confirm('저장된 KRX 자격증명을 삭제할까요?')) return;
    return write(async () => {
      try { apply(await api.clearKrxCredentials()); setKrx(emptyKrx); setKrxMsg({ text: 'KRX 자격증명을 삭제했습니다.', ok: true }); } catch (error) { setKrxMsg({ text: error instanceof Error ? error.message : 'KRX 자격증명 삭제 실패', ok: false }); }
    });
  };
  const saveKis = (env: 'paper' | 'real') => (event: React.FormEvent) => {
    event.preventDefault();
    const field = fields(event);
    const draft = { app_key: field('app_key'), app_secret: field('app_secret'), account: field('account'), env };
    if (!draft.app_key || !draft.app_secret || !draft.account) { setKisMsg(env, { text: '앱키·앱시크릿·계좌번호를 모두 입력하세요. 보안상 앱키와 앱시크릿은 저장 후 화면에서 지워지므로 다시 입력해야 합니다.', ok: false }); return; }
    if (!/^\d{8}(-?\d{2})?$/.test(draft.account)) { setKisMsg(env, { text: '계좌번호는 8자리 숫자(모의계좌) 또는 12345678-01 형식(실전계좌)이어야 합니다.', ok: false }); return; }
    setKisMsg(env, emptyFeedback);
    return write(async () => {
      try {
        const status = await api.saveBrokerCredentials(draft);
        setSettings(current => current && { ...current, kis: status });
        setKisForms(current => ({ ...current, [env]: emptyKisForm }));
        setKisMsg(env, { text: `${kisEnvLabel[env]} 자격증명을 저장했습니다. (${status.accounts[env]})`, ok: true });
        window.dispatchEvent(new Event('mscr-settings-changed'));
      } catch (error) { setKisMsg(env, { text: error instanceof Error ? error.message : '브로커 자격증명 저장 실패', ok: false }); }
    });
  };
  const clearKis = (env: 'paper' | 'real') => () => {
    if (!window.confirm(`저장된 ${kisEnvLabel[env]} 자격증명을 삭제할까요?`)) return;
    return write(async () => {
      try {
        const status = await api.deleteBrokerCredentials(env);
        setSettings(current => current && { ...current, kis: status });
        setKisMsg(env, { text: `${kisEnvLabel[env]} 자격증명을 삭제했습니다.`, ok: true });
        window.dispatchEvent(new Event('mscr-settings-changed'));
      } catch (error) { setKisMsg(env, { text: error instanceof Error ? error.message : '브로커 자격증명 삭제 실패', ok: false }); }
    });
  };
  const activateKis = (env: 'paper' | 'real') => () => write(async () => {
    try {
      const status = await api.setActiveEnv(env);
      setSettings(current => current && { ...current, kis: status });
      window.dispatchEvent(new Event('mscr-settings-changed'));
    } catch (error) { setKisMsg(env, { text: error instanceof Error ? error.message : '사용 계좌 전환 실패', ok: false }); }
  });
  const savePreferences = (event: React.FormEvent) => {
    event.preventDefault();
    const raw = fields(event)('request_delay_sec');
    const seconds = Number(raw);
    if (raw === '' || !Number.isFinite(seconds) || seconds < 0 || seconds > 10) { setPrefMsg({ text: '요청 간격은 0~10초 사이 숫자여야 합니다.', ok: false }); return; }
    setPrefMsg(emptyFeedback);
    return write(async () => {
      const massiveSeconds = Number(massiveDelay);
      if (massiveDelay !== '' && (!Number.isFinite(massiveSeconds) || massiveSeconds < 0 || massiveSeconds > 60)) { setPrefMsg({ text: 'Massive 요청 간격은 0~60초 사이 숫자여야 합니다.', ok: false }); return; }
      try { apply(await api.savePreferences({ request_delay_sec: seconds, massive_request_delay_sec: massiveDelay === '' ? undefined : massiveSeconds })); setPrefMsg({ text: '수집 요청 간격을 저장했습니다.', ok: true }); } catch (error) { setPrefMsg({ text: error instanceof Error ? error.message : '수집 요청 간격 저장 실패', ok: false }); }
    });
  };

  const checkLatestDay = () => write(async () => {
    try { const result = await api.latestDay(); setLatestDayMsg({ text: `최신 거래일: ${result.as_of}`, ok: true }); }
    catch (error) { setLatestDayMsg({ text: error instanceof Error ? error.message : '최신 거래일 조회 실패', ok: false }); }
  });

  const saveMassive = (event: React.FormEvent) => {
    event.preventDefault();
    const key = fields(event)('api_key');
    if (!key) { setMassiveMsg({ text: 'API 키를 입력하세요.', ok: false }); return; }
    setMassiveMsg(emptyFeedback);
    return write(async () => {
      try { apply(await api.saveMassiveCredentials({ api_key: key })); setMassiveKey(''); setMassiveMsg({ text: 'Massive API 키를 저장했습니다.', ok: true }); }
      catch (error) { setMassiveMsg({ text: error instanceof Error ? error.message : 'Massive API 키 저장 실패', ok: false }); }
    });
  };
  const clearMassive = () => {
    if (!window.confirm('저장된 Massive API 키를 삭제할까요?')) return;
    return write(async () => {
      try { apply(await api.clearMassiveCredentials()); setMassiveMsg({ text: 'Massive API 키를 삭제했습니다.', ok: true }); }
      catch (error) { setMassiveMsg({ text: error instanceof Error ? error.message : 'Massive API 키 삭제 실패', ok: false }); }
    });
  };
  const startIngest = (event: React.FormEvent) => {
    event.preventDefault();
    const days = Number(ingestForm.days);
    if (!Number.isFinite(days) || days <= 0 || days > 3650) { setIngestMsg({ text: '수집 기간은 1~3650일 사이여야 합니다.', ok: false }); return; }
    setIngestMsg(emptyFeedback);
    return write(async () => {
      try { setIngestStatus(await api.runIngest({ days, force: ingestForm.force, source: ingestForm.source })); }
      catch (error) { setIngestMsg({ text: error instanceof Error ? error.message : '수집 시작 실패', ok: false }); }
    });
  };

  if (!settings) return <div className="empty">{loadError || '설정을 불러오는 중…'}</div>;
  const krxNote = settings.krx.source && sourceNote[settings.krx.source];
  const feedback = (msg: Feedback) => msg.text && <span className="msg" data-tone={msg.ok ? 'ok' : 'error'}>{msg.text}</span>;
  // 설정 카드는 높이를 꽉 채우고 스스로 스크롤하므로, 머리말은 그 바깥에서 한 행을 차지한다.
  return <div className="page page--fill">
    <ViewHeader
      title="설정"
      lede={<>데이터 수집 인증·브로커 인증·수집 실행을 관리합니다. 값은 <code>~/.mscr/</code>에 저장되며 환경변수가 있으면 그쪽이 우선합니다.</>}
    />
    <div className="settings-layout">
      <section className="panel settings-card">
        <div className="section-title">KRX 데이터 수집 인증 <span className="badge">한국</span></div>
        <div className="toolbar">
          <span className="badge" data-tone={settings.krx.mode === 'anonymous' ? undefined : 'ok'}>{krxModeLabel[settings.krx.mode]}</span>
          {krxNote && <span className="subtle">{krxNote}</span>}
          {settings.krx.openapi_key_masked && <span className="subtle">키 {settings.krx.openapi_key_masked}</span>}
          {settings.krx.krx_id_masked && <span className="subtle">아이디 {settings.krx.krx_id_masked}</span>}
        </div>
        <form className="form-grid" onSubmit={saveKrx}>
          <label>Open API 키<input name="openapi_key" value={krx.openapi_key} autoComplete="off" placeholder="PS12...cdef" onChange={event => setKrx(current => ({ ...current, openapi_key: event.target.value }))} /></label>
          <label>KRX 아이디<input name="krx_id" value={krx.krx_id} autoComplete="off" placeholder="krx_login_id" onChange={event => setKrx(current => ({ ...current, krx_id: event.target.value }))} /></label>
          <label>KRX 비밀번호<input name="krx_pw" type="password" value={krx.krx_pw} autoComplete="new-password" placeholder="비밀번호" onChange={event => setKrx(current => ({ ...current, krx_pw: event.target.value }))} /></label>
          <div className="toolbar"><button className="btn btn--primary" disabled={busy}>저장</button><button type="button" className="btn btn--danger" disabled={busy || !settings.krx.stored.length} onClick={clearKrx}>삭제</button><span className="subtle">입력한 항목만 저장됩니다 · {settings.credential_paths.krx}</span></div>
          {feedback(krxMsg)}
        </form>
      </section>
  
      <section className="panel settings-card">
        <div className="section-title">Massive 데이터 수집 인증 <span className="badge">미국</span></div>
        <div className="toolbar">
          {settings.massive.mode === 'apikey'
            ? <span className="badge" data-tone="ok">API 키 사용</span>
            : <span className="badge">키 미설정</span>}
          {settings.massive.source && <span className="subtle">{sourceNote[settings.massive.source]}</span>}
          {settings.massive.api_key_masked && <span className="subtle">키 {settings.massive.api_key_masked}</span>}
          <a className="subtle" href={settings.massive.signup_url} target="_blank" rel="noreferrer">키 발급</a>
        </div>
        <form className="form-grid" onSubmit={saveMassive}>
          <label>Massive API 키<input name="api_key" value={massiveKey} autoComplete="off" placeholder="massive api key" onChange={event => setMassiveKey(event.target.value)} /></label>
          <div className="toolbar"><button className="btn btn--primary" disabled={busy}>저장</button><button type="button" className="btn btn--danger" disabled={busy || !settings.massive.stored.length} onClick={clearMassive}>삭제</button><span className="subtle">환경변수 <code>MASSIVE_API_KEY</code>가 있으면 그쪽이 우선 · {settings.credential_paths.massive}</span></div>
          {feedback(massiveMsg)}
        </form>
      </section>

      {settings.market.trading && <section className="panel settings-card">
        <div className="section-title">브로커 인증</div>
        <div className="toolbar">
          {settings.kis.enabled
            ? <span className="badge" data-tone={settings.kis.env === 'real' ? 'real' : 'live'}>사용 중 · {settings.kis.env === 'real' ? '실전' : '모의'} · {settings.kis.account_masked || '계좌 미확인'}</span>
            : <span className="badge">브로커 미설정</span>}
          {!settings.kis.enabled && <span className="subtle">{settings.kis.reason || '앱키·앱시크릿·계좌번호를 저장하거나 KIS 환경변수를 설정하세요.'}</span>}
          {settings.kis.source === 'env' && <span className="subtle">환경변수 우선 적용 중</span>}
        </div>
        {(['paper', 'real'] as const).map(env => {
          const configuredAccount = settings.kis.accounts[env];
          const active = settings.kis.source === 'file' && settings.kis.active_env === env;
          return <div className="kis-env-block" key={env}>
            <div className="toolbar">
              <b>{kisEnvLabel[env]}</b>
              {configuredAccount
                ? <span className="badge" data-tone={active ? 'ok' : undefined}>{active ? '사용 중' : '설정됨'} · {configuredAccount}</span>
                : <span className="badge">미설정</span>}
              {configuredAccount && !active && <button type="button" className="btn btn--ghost" disabled={busy} onClick={activateKis(env)}>이 계좌로 전환</button>}
            </div>
            <form className="form-grid" onSubmit={saveKis(env)}>
              <label>앱키<input name="app_key" value={kisForms[env].app_key} autoComplete="off" placeholder="APP KEY" onChange={event => setKisForms(current => ({ ...current, [env]: { ...current[env], app_key: event.target.value } }))} /></label>
              <label>앱시크릿<input name="app_secret" type="password" value={kisForms[env].app_secret} autoComplete="new-password" placeholder="APP SECRET" onChange={event => setKisForms(current => ({ ...current, [env]: { ...current[env], app_secret: event.target.value } }))} /></label>
              <label>계좌번호<input name="account" value={kisForms[env].account} placeholder={env === 'real' ? '12345678-01' : '12345678'} onChange={event => setKisForms(current => ({ ...current, [env]: { ...current[env], account: event.target.value } }))} /></label>
              <div className="toolbar"><button className="btn btn--primary" disabled={busy}>저장</button><button type="button" className="btn btn--danger" disabled={busy || !configuredAccount} onClick={clearKis(env)}>삭제</button></div>
              {feedback(kisMsgs[env])}
            </form>
          </div>;
        })}
        <span className="subtle">{settings.credential_paths.kis}</span>
      </section>}
  
      <section className="panel settings-card">
        <div className="section-title">수집 요청 간격</div>
        <div className="toolbar"><span className="badge">{delaySourceLabel[settings.request_delay_source]}</span><span className="subtle">현재 KRX {settings.request_delay_sec}초 · Massive {settings.massive.request_delay_sec}초</span></div>
        <form className="form-grid" onSubmit={savePreferences}>
          <label>KRX 요청 간격(초)<input name="request_delay_sec" type="number" step="0.1" min="0" max="10" value={delay} onChange={event => setDelay(event.target.value)} /></label>
          <label title="Massive 무료 플랜은 분당 5회 제한이라 12초를 권장합니다. 유료 플랜이면 0으로 낮추세요.">Massive 요청 간격(초)<input type="number" step="0.5" min="0" max="60" value={massiveDelay} onChange={event => setMassiveDelay(event.target.value)} /></label>
          <div className="toolbar"><button className="btn btn--primary" disabled={busy}>저장</button></div>
          {feedback(prefMsg)}
        </form>
      </section>
  
      <section className="panel settings-card">
        <div className="section-title">데이터 수집</div>
        <div className="toolbar">
          <span className="badge">{settings.market.label} 주식</span>
          <button type="button" className="btn btn--ghost" disabled={busy} onClick={checkLatestDay}>최신 거래일 조회</button>
          {feedback(latestDayMsg)}
        </div>
        <form className="form-grid" onSubmit={startIngest}>
          <label>수집 기간(일)<input type="number" min="1" max="3650" value={ingestForm.days} onChange={event => setIngestForm(current => ({ ...current, days: event.target.value }))} /></label>
          <label>소스<select value={ingestForm.source} onChange={event => setIngestForm(current => ({ ...current, source: event.target.value as IngestSource }))}>
            {settings.ingest_defaults.sources.map(item => <option key={item} value={item}>{sourceLabel[item]}</option>)}
          </select></label>
          <label className="check"><input type="checkbox" checked={ingestForm.force} onChange={event => setIngestForm(current => ({ ...current, force: event.target.checked }))} />이미 수집된 날짜도 다시 수집(--force)</label>
          <div className="toolbar">
            <button className="btn btn--primary" disabled={busy || !!ingestStatus?.running}>{ingestStatus?.running ? '수집 중…' : '수집 시작'}</button>
            {ingestStatus?.running && <span className="progress-note"><span className="spinner" aria-hidden="true" />{ingestStatus.total ? `${ingestStatus.processed}/${ingestStatus.total} · ${ingestStatus.current_day || ''}` : '진행 중…'}</span>}
          </div>
          {feedback(ingestMsg)}
          {!ingestStatus?.running && ingestStatus?.finished_at && (
            ingestStatus.ok
              ? <span className="msg" data-tone="ok">마지막 수집 완료 ({ingestStatus.finished_at})</span>
              : <span className="msg" data-tone="error">마지막 수집 실패: {ingestStatus.error}</span>
          )}
        </form>
      </section>
  
      <section className="panel settings-card">
        <div className="section-title">환경 정보</div>
        <table className="table table--kv"><tbody>
          <tr><td>MSCR_HOME</td><td>{settings.mscr_home}</td></tr>
          <tr><td>데이터베이스</td><td>{settings.db_path}</td></tr>
          <tr><td>스키마 버전</td><td>{settings.schema_version}</td></tr>
          <tr><td>기준일</td><td>{settings.data.as_of || '—'}</td></tr>
          <tr><td>일봉 행 수</td><td>{settings.data.bars_rows.toLocaleString('ko-KR')}</td></tr>
          <tr><td>종목 수</td><td>주식 {settings.data.instrument_count.stock.toLocaleString('ko-KR')} · ETF {settings.data.instrument_count.etf.toLocaleString('ko-KR')}</td></tr>
          <tr><td>최근 수집</td><td>{settings.data.last_ingest_at || '—'}</td></tr>
        </tbody></table>
      </section>
    </div>
  </div>;
}
