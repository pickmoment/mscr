import { useEffect, useMemo, useState } from 'react';
import { api, IndicatorDefinition, IndicatorParameter } from '../lib/api';
import FormulaInput from './FormulaInput';
import { definitionSuggestions } from '../lib/suggest';

type Draft = { key: string; label: string; unit: string; formula: string; parameters: IndicatorParameter[]; enabled: boolean };
const emptyForm: Draft = { key: '', label: '', unit: 'ratio', formula: '', parameters: [{ name: 'period', default: 20, min: 1, max: 500, integer: true }], enabled: true };

export default function IndicatorManager() {
  const [items, setItems] = useState<IndicatorDefinition[]>([]);
  const [form, setForm] = useState<Draft>(emptyForm);
  const [message, setMessage] = useState('');
  const [search, setSearch] = useState('');
  const refresh = () => api.indicators().then(setItems).catch((error: Error) => setMessage(error.message));
  useEffect(() => { refresh(); }, []);
  const suggestions = useMemo(() => definitionSuggestions(items, form.parameters.map(parameter => parameter.name), form.key), [items, form.parameters, form.key]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await api.saveIndicator(form);
      setMessage(`저장했습니다. 스크린 수식에서 ${form.key}(${form.parameters.map(parameter => parameter.name).join(', ')})로 사용하세요.`);
      setForm(emptyForm);
      window.dispatchEvent(new Event('mscr-indicators-changed'));
      refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '저장 실패');
    }
  };
  const remove = async (item: IndicatorDefinition) => {
    if (item.id === null) return;
    await api.deleteIndicator(item.id);
    if (form.key === item.key) setForm(emptyForm);
    window.dispatchEvent(new Event('mscr-indicators-changed'));
    refresh();
  };
  const updateParameter = (index: number, patch: Partial<IndicatorParameter>) => setForm(current => ({ ...current, parameters: current.parameters.map((parameter, position) => position === index ? { ...parameter, ...patch } : parameter) }));
  const visibleItems = items.filter(item => `${item.label} ${item.key} ${item.formula || ''}`.toLowerCase().includes(search.toLowerCase()));

  return <div className="indicator-layout">
    <section className="panel indicator-editor">
      <div className="section-title">PARAMETERIZED INDICATOR</div>
      <h1>사용자 지표</h1>
      <p className="subtle">기간과 계수는 이름을 가진 파라미터로 정의하고, 스크린 수식에서 위치 인자로 전달합니다.</p>
      <form onSubmit={submit} className="indicator-form">
        <label>키<input required pattern="[a-z][a-z0-9_]*" title="영문 소문자로 시작하고 소문자·숫자·밑줄만 씁니다. 내장 이름과 겹치면 저장이 거부됩니다." value={form.key} onChange={event => setForm(current => ({ ...current, key: event.target.value }))} placeholder="ma_gap" /></label>
        <label>표시명<input required value={form.label} onChange={event => setForm(current => ({ ...current, label: event.target.value }))} placeholder="이동평균 이격" /></label>
        <label>단위<select value={form.unit} onChange={event => setForm(current => ({ ...current, unit: event.target.value }))}><option value="number">숫자</option><option value="krw">원</option><option value="count">개</option><option value="ratio">비율</option><option value="pct">퍼센트</option><option value="x">배</option></select></label>
        <div className="parameter-editor"><div className="parameter-title"><span>파라미터</span><button type="button" className="ghost" onClick={() => setForm(current => ({ ...current, parameters: [...current.parameters, { name: `param${current.parameters.length + 1}`, default: 1, min: null, max: null, integer: false }] }))}>＋ 추가</button></div>{form.parameters.map((parameter, index) => <div className="parameter-row" key={index}><input aria-label="파라미터 이름" required pattern="[a-z][a-z0-9_]*" value={parameter.name} onChange={event => updateParameter(index, { name: event.target.value })} /><input aria-label="기본값" type="number" value={parameter.default} onChange={event => updateParameter(index, { default: Number(event.target.value) })} /><input aria-label="최솟값" type="number" placeholder="최소" value={parameter.min ?? ''} onChange={event => updateParameter(index, { min: event.target.value === '' ? null : Number(event.target.value) })} /><input aria-label="최댓값" type="number" placeholder="최대" value={parameter.max ?? ''} onChange={event => updateParameter(index, { max: event.target.value === '' ? null : Number(event.target.value) })} /><label className="check"><input type="checkbox" checked={parameter.integer} onChange={event => updateParameter(index, { integer: event.target.checked })} />정수</label><button type="button" className="danger" onClick={() => setForm(current => ({ ...current, parameters: current.parameters.filter((_, position) => position !== index) }))}>×</button></div>)}</div>
        <label className="formula-field">수식<FormulaInput multiline required value={form.formula} onChange={formula => setForm(current => ({ ...current, formula }))} suggestions={suggestions} placeholder="close / sma(close, period) - 1" ariaLabel="지표 정의 수식" /></label>
        <label className="check"><input type="checkbox" checked={form.enabled} onChange={event => setForm(current => ({ ...current, enabled: event.target.checked }))} />스크린 수식에서 사용</label>
        <div className="toolbar"><button className="primary">저장</button><button type="button" className="ghost" onClick={() => setForm(emptyForm)}>새 지표</button><span className="subtle">{message}</span></div>
      </form>
      <div className="formula-help"><b>정의</b> close / sma(close, period) - 1<br /><b>호출</b> ma_gap(20) &gt; 0.1<br /><b>함수</b> sma, ema, rsi, returns, volume_ratio, historical_volatility, atr, rolling_max, rolling_min, crosses_above, crosses_below, abs</div>
    </section>
    <section className="panel indicator-list">
      <div className="indicator-list-head"><div className="section-title">INDICATOR CATALOG <span className="badge">{items.length}</span></div><input placeholder="지표명 / 키 검색" value={search} onChange={event => setSearch(event.target.value)} /></div>
      {visibleItems.map(item => <article key={`${item.builtin ? 'builtin' : 'custom'}-${item.key}`} className="indicator-item"><div><strong>{item.label}</strong><span className="badge">{item.builtin ? item.kind === 'function' ? '함수' : '입력' : item.enabled ? '사용자' : '숨김'}</span><code>{item.key}</code></div><p>{item.formula || `스크린 수식에서 ${item.key}로 사용`}</p>{item.parameters.length > 0 && <div className="parameter-chips">{item.parameters.map(parameter => <span key={parameter.name}>{parameter.name}={parameter.default} · {parameter.min ?? '−∞'}…{parameter.max ?? '∞'}</span>)}</div>}{!item.builtin && <div className="toolbar"><button className="ghost" onClick={() => setForm({ key: item.key, label: item.label, unit: item.unit, formula: item.formula || '', parameters: item.parameters, enabled: item.enabled })}>편집</button><button className="danger" onClick={() => remove(item)}>삭제</button></div>}</article>)}
      {!visibleItems.length && <div className="empty">검색 결과가 없습니다.</div>}
    </section>
  </div>;
}
