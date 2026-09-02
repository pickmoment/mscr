import { useEffect, useMemo, useRef, useState } from 'react';
import { api, IndicatorDefinition, ScreenRow, ScreenSpec } from '../lib/api';
import FormulaInput from './FormulaInput';
import { screenSuggestions } from '../lib/suggest';

const defaults: ScreenSpec = {
  universe: { kinds: ['stock', 'etf'], markets: ['KOSPI', 'KOSDAQ'], exclude_preferred: true, exclude_spac: true, exclude_halted: true, min_bars: 20 },
  formula: 'prior_avg_ratio(volume, 20) >= 3',
  sort: { formula: 'prior_avg_ratio(volume, 20)', dir: 'desc' },
  limit: 500,
  as_of_offset: 0,
};

export default function ScreenerPanel({ onResults }: { onResults: (rows: ScreenRow[]) => void }) {
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

  return <aside className="panel scroll screener-panel" style={{ padding: 18 }}>
    <div className="section-title">PRESETS <span className="badge">{saved.length}</span></div>
    <div className="preset-manager">
      <div className="toolbar"><select className="control" aria-label="프리셋 목록" value={selectedId} onChange={event => setSelectedId(event.target.value)}><option value="">프리셋 선택</option>{saved.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button className="ghost" onClick={applyPreset}>불러오기</button></div>
      <input className="control" aria-label="프리셋 이름" value={presetName} onChange={event => setPresetName(event.target.value)} placeholder="프리셋 이름" maxLength={60} />
      <div className="toolbar"><button className="ghost" onClick={savePreset}>새로 저장</button><button className="ghost" onClick={updatePreset}>선택 갱신</button><button className="danger" onClick={removePreset}>삭제</button></div>
      <span className="subtle">{presetStatus}</span>
    </div>
    <div style={{ marginTop: 20 }} className="section-title">UNIVERSE</div>
    <div className="toolbar">{['stock','etf'].map(value => <label className="check" key={value}><input type="checkbox" checked={spec.universe.kinds.includes(value)} onChange={() => updateUniverse('kinds', value)} />{value === 'stock' ? '주식' : 'ETF'}</label>)}{['KOSPI','KOSDAQ'].map(value => <label className="check" key={value}><input type="checkbox" checked={spec.universe.markets.includes(value)} onChange={() => updateUniverse('markets', value)} />{value}</label>)}</div>
    <div className="toolbar" style={{ marginTop: 10 }}><label className="check"><input type="checkbox" checked={spec.universe.exclude_preferred} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, exclude_preferred: event.target.checked } }))} />우선주 제외</label><label className="check"><input type="checkbox" checked={spec.universe.exclude_spac} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, exclude_spac: event.target.checked } }))} />스팩 제외</label><label className="check"><input type="checkbox" checked={spec.universe.exclude_halted} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, exclude_halted: event.target.checked } }))} />정지 제외</label></div>
    <div className="toolbar" style={{ marginTop: 10 }}>
      <label className="check">최소 유효 봉 <input className="control" type="number" value={spec.universe.min_bars} onChange={event => setSpec(current => ({ ...current, universe: { ...current.universe, min_bars: Number(event.target.value) } }))} /></label>
      <label className="check" title="0=당일, 1=하루전, 2=이틀전 … 마지막 유효 봉 기준으로 며칠 전 데이터로 스크리닝할지">기준일(N봉 전) <input className="control" type="number" min={0} max={250} value={spec.as_of_offset} onChange={event => setSpec(current => ({ ...current, as_of_offset: Math.max(0, Number(event.target.value)) }))} /></label>
    </div>
    <div className="screen-formula-block"><label>SCREEN EXPRESSION<FormulaInput multiline value={spec.formula} onChange={formula => setSpec(current => ({ ...current, formula }))} suggestions={suggestions} ariaLabel="스크린 수식" /></label><p>지표명을 입력하면 자동완성됩니다. <code>and</code> · <code>or</code> · <code>not</code>과 비교식을 조합합니다.</p><code className="formula-example">close &gt; sma(close, 20) and rsi(close, 14) &lt;= 30</code></div>
    <div style={{ marginTop: 18 }} className="section-title">SORT EXPRESSION</div>
    <div className="toolbar"><div className="sort-formula"><FormulaInput value={spec.sort.formula} onChange={formula => setSpec(current => ({ ...current, sort: { ...current.sort, formula } }))} suggestions={suggestions} placeholder="예: returns(close, 120)" ariaLabel="정렬 수식" /></div><select className="control" value={spec.sort.dir} onChange={event => setSpec(current => ({ ...current, sort: { ...current.sort, dir: event.target.value as 'asc' | 'desc' } }))}><option value="desc">내림차순</option><option value="asc">오름차순</option></select></div>
    <div className="toolbar" style={{ marginTop: 10 }}>
      <button className="primary" onClick={runScreen} disabled={running}>{running ? '조회 중…' : '스크린 실행'}</button>
      {running && <button className="ghost" onClick={cancelScreen}>취소</button>}
      {running
        ? <span className="subtle screen-progress" role="status"><span className="spinner" aria-hidden="true" />전 종목 동적 계산 중… {elapsed}초 경과</span>
        : <span className="subtle">{status}</span>}
    </div>
  </aside>;
}
