import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, BarsResponse, ChartBar, ChartFreq, ChartIndicatorParams, ChartPlotPane, ChartPlotSpec, ChartPlotStyleName, ChartSource, IndicatorDefinition, Instrument, Watchlist } from '../lib/api';
import FormulaInput from './FormulaInput';
import { chartSuggestions } from '../lib/suggest';
import TickerChart from './TickerChart';
import { ratio, money } from '../lib/format';
import PositionPlanner from './PositionPlanner';
import { marketInfo } from '../lib/market';
import { defaultPlan, loadPositionPlans, POSITION_EVENT, PositionPlan, storePositionPlan } from '../lib/position';
import { MeasureMode } from '../lib/measure';
import { SelectTicker } from '../lib/nav';
import ViewHeader from './ViewHeader';

const defaultConfig: ChartIndicatorParams = { maPeriods: [5, 20, 60], rsiPeriod: 14, macdFast: 12, macdSlow: 26, macdSignal: 9, bbPeriod: 20, bbK: 2, volumeMaPeriod: 50 };
const defaultEnabled = ['ma', 'rsi', 'macd', 'bb', 'volume_ma'];
// 수식 지표 — 스크리너와 같은 수식 언어로 아무 지표나 만들어 차트에 얹는다. 서버 상한(MAX_CHART_PLOTS)과 맞춘다.
const MAX_PLOTS = 8;
const PLOT_COLORS = ['#f5c451', '#5ba7ff', '#c08aff', '#39c6b5', '#ff8f70', '#e5849b', '#8fd14f', '#7f8ea3'];
const PANE_OPTIONS: { value: ChartPlotPane; label: string }[] = [
  { value: 'price', label: '가격판' },
  { value: 'volume', label: '거래량판' },
  { value: 'sub1', label: '보조판 1' },
  { value: 'sub2', label: '보조판 2' },
  { value: 'sub3', label: '보조판 3' },
];
const STYLE_OPTIONS: { value: ChartPlotStyleName; label: string }[] = [
  { value: 'line', label: '선' },
  { value: 'dashed', label: '점선' },
  { value: 'histogram', label: '막대' },
];
const PANE_VALUES = PANE_OPTIONS.map(option => option.value);
const STYLE_VALUES = STYLE_OPTIONS.map(option => option.value);
// localStorage에는 이전 버전이나 손으로 고친 값이 남아 있을 수 있어 읽을 때마다 형태를 맞춘다.
const normalizePlot = (item: unknown, index: number): ChartPlotSpec => {
  const saved = (item ?? {}) as Partial<ChartPlotSpec>;
  return {
    id: typeof saved.id === 'string' && saved.id ? saved.id : `plot-${index}-${Math.random().toString(36).slice(2, 8)}`,
    label: typeof saved.label === 'string' ? saved.label : '',
    formula: typeof saved.formula === 'string' ? saved.formula : '',
    pane: PANE_VALUES.includes(saved.pane as ChartPlotPane) ? saved.pane as ChartPlotPane : 'price',
    style: STYLE_VALUES.includes(saved.style as ChartPlotStyleName) ? saved.style as ChartPlotStyleName : 'line',
    color: typeof saved.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(saved.color) ? saved.color : PLOT_COLORS[index % PLOT_COLORS.length],
    enabled: saved.enabled !== false,
  };
};

const CHART_SETTINGS_KEY = 'mscr-chart-settings';
// 네이버 해외주식은 로이터 코드를 쓴다 — 나스닥 상장은 `.O`가 붙고, NYSE·AMEX(ARCA 포함)는 티커 그대로다.
const naverStockUrl = (ticker: string, market: string | null): string =>
  marketInfo().key === 'kr'
    ? `https://stock.naver.com/domestic/stock/${ticker}/price`
    : `https://stock.naver.com/worldstock/stock/${market === 'NASDAQ' ? `${ticker}.O` : ticker}/price`;
// alpha-square 실시간 소스에서 고를 수 있는 주기 — 로컬 소스는 이 목록 대신 위쪽 range 버튼(3m~max)을 쓴다.
const FREQ_OPTIONS: { value: ChartFreq; label: string }[] = [
  { value: 'minute-1', label: '1분' },
  { value: 'minute-3', label: '3분' },
  { value: 'minute-5', label: '5분' },
  { value: 'minute-15', label: '15분' },
  { value: 'minute-30', label: '30분' },
  { value: 'minute-60', label: '1시간' },
  { value: 'day', label: '일봉' },
];
const FREQ_VALUES = FREQ_OPTIONS.map(option => option.value);
// alpha-square 페이지당 한도(백엔드 CANDLE_PAGE_LIMIT)를 처음부터 채워서 요청한다. "더보기"는 이만큼씩
// 늘려 백엔드가 과거 방향으로 페이지를 이어붙이게 하고, 상한(백엔드 CANDLE_BARS_MAX)에서 멈춘다.
const ALPHASQUARE_DEFAULT_COUNT = 1000;
const ALPHASQUARE_COUNT_STEP = 1000;
const ALPHASQUARE_MAX_COUNT = 5000;
type ChartSettings = { range: string; source: ChartSource; freq: ChartFreq; enabled: string[]; config: ChartIndicatorParams; plots: ChartPlotSpec[]; logScale: boolean; showParams: boolean };
const loadChartSettings = (): ChartSettings => {
  try {
    const saved = JSON.parse(localStorage.getItem(CHART_SETTINGS_KEY) || 'null');
    return {
      range: typeof saved?.range === 'string' ? saved.range : '1y',
      source: saved?.source === 'alphasquare' ? 'alphasquare' : 'local',
      freq: FREQ_VALUES.includes(saved?.freq) ? saved.freq : 'minute-5',
      enabled: Array.isArray(saved?.enabled) ? saved.enabled : defaultEnabled,
      config: { ...defaultConfig, ...saved?.config },
      plots: Array.isArray(saved?.plots) ? saved.plots.slice(0, MAX_PLOTS).map(normalizePlot) : [],
      logScale: saved?.logScale === true,
      showParams: typeof saved?.showParams === 'boolean' ? saved.showParams : true,
    };
  } catch {
    return { range: '1y', source: 'local', freq: 'minute-5', enabled: defaultEnabled, config: defaultConfig, plots: [], logScale: false, showParams: true };
  }
};

export default function TickerDetail({ ticker, tickers, onSelect, light }: { ticker: string | null; tickers: string[]; onSelect: SelectTicker; light: boolean }) {
  // 매매 계획은 국내 전용(KIS 주문)이라 미국 모드에서는 버튼을 내린다. 차트 실시간(alpha-square)은
  // 미국 종목도 제공되므로 양쪽 모두 쓴다.
  const krOnly = marketInfo().key === 'kr';
  const [detail, setDetail] = useState<Instrument | null>(null);
  const [chart, setChart] = useState<BarsResponse | null>(null);
  const [range, setRange] = useState(() => loadChartSettings().range);
  const [source, setSource] = useState<ChartSource>(() => loadChartSettings().source);
  const [freq, setFreq] = useState<ChartFreq>(() => loadChartSettings().freq);
  // 실시간 소스에서 한 번에 가져올 봉수 — "더보기"로 늘어난다. 설정에 영속하지 않고 종목·소스·주기가
  // 바뀌면 기본값(페이지당 한도)으로 되돌아간다.
  const [count, setCount] = useState(ALPHASQUARE_DEFAULT_COUNT);
  useEffect(() => { setCount(ALPHASQUARE_DEFAULT_COUNT); }, [ticker, source, freq]);
  const [enabled, setEnabled] = useState(() => loadChartSettings().enabled);
  const [config, setConfig] = useState(() => loadChartSettings().config);
  const [plots, setPlots] = useState(() => loadChartSettings().plots);
  const [logScale, setLogScale] = useState(() => loadChartSettings().logScale);
  const [definitions, setDefinitions] = useState<IndicatorDefinition[]>([]);
  const [showParams, setShowParams] = useState(() => loadChartSettings().showParams);
  const [lists, setLists] = useState<Watchlist[]>([]);
  const [listId, setListId] = useState<number | null>(null);
  const [watchMessage, setWatchMessage] = useState('');
  const [plan, setPlan] = useState<PositionPlan | null>(null);
  const [measure, setMeasure] = useState<MeasureMode>('off');
  const [viewportLastBar, setViewportLastBar] = useState<ChartBar | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const loadLists = useCallback(() => {
    if (!ticker) return;
    api.watchlists(ticker).then(rows => {
      setLists(rows);
      setListId(current => current != null && rows.some(row => row.id === current) ? current : rows.find(row => row.contains)?.id ?? rows[0]?.id ?? null);
    }).catch(() => undefined);
  }, [ticker]);
  useEffect(() => { if (ticker) api.instrument(ticker).then(setDetail).catch(() => setDetail(null)); }, [ticker]);
  // 색·판만 바꾼 경우까지 다시 불러오지 않도록, 서버가 실제로 쓰는 값(id·수식)만 문자열로 굳혀 의존성으로 쓴다.
  const plotQuery = useMemo(() => {
    const active = plots.filter(item => item.enabled && item.formula.trim());
    return active.length ? JSON.stringify(active.map(({ id, formula }) => ({ id, formula }))) : '';
  }, [plots]);
  const suggestions = useMemo(() => chartSuggestions(definitions), [definitions]);
  // 서버는 수식 오류를 그 지표에만 붙여 돌려준다 — 나머지 지표와 봉은 그대로 그려진다.
  const plotErrors = useMemo(() => Object.fromEntries((chart?.plots ?? []).filter(plot => plot.error).map(plot => [plot.id, plot.error as string])), [chart]);
  // 수식은 한 글자씩 타이핑되므로 그대로 두면 매 키 입력이 요청이 되고, 완성되지 않은 수식의
  // 오류가 깜빡인다. 잠깐 멈춘 뒤에만 다시 불러온다.
  const [settledPlotQuery, setSettledPlotQuery] = useState(plotQuery);
  useEffect(() => { const timer = setTimeout(() => setSettledPlotQuery(plotQuery), 400); return () => clearTimeout(timer); }, [plotQuery]);
  const loadChart = useCallback(() => { if (ticker) api.bars(ticker, range, source, freq, count, enabled, config, settledPlotQuery).then(setChart).catch(() => setChart(null)); }, [ticker, range, source, freq, count, enabled, config, settledPlotQuery]);
  useEffect(loadChart, [loadChart]);
  useEffect(() => { localStorage.setItem(CHART_SETTINGS_KEY, JSON.stringify({ range, source, freq, enabled, config, plots, logScale, showParams })); }, [range, source, freq, enabled, config, plots, logScale, showParams]);
  useEffect(() => { api.indicators().then(setDefinitions).catch(() => undefined); }, []);
  useEffect(loadLists, [loadLists]);
  useEffect(() => { window.addEventListener('mscr-watchlist-changed', loadLists); return () => window.removeEventListener('mscr-watchlist-changed', loadLists); }, [loadLists]);
  // 값이 같으면 그대로 둔다. 드래그로 저장할 때마다 이벤트가 돌아와 새 객체로 바뀌면 차트가 불필요하게 다시 그려진다.
  const readPlan = useCallback(() => setPlan(current => {
    const next = ticker ? loadPositionPlans()[ticker] ?? null : null;
    return JSON.stringify(current) === JSON.stringify(next) ? current : next;
  }), [ticker]);
  useEffect(readPlan, [readPlan]);
  // 계획 화면에서 계획을 차트로 보내면 같은 종목을 보고 있어도 블록을 다시 읽어야 한다.
  useEffect(() => { window.addEventListener(POSITION_EVENT, readPlan); return () => window.removeEventListener(POSITION_EVENT, readPlan); }, [readPlan]);
  const savePlan = useCallback((next: PositionPlan | null) => { setPlan(next); if (ticker) storePositionPlan(ticker, next); }, [ticker]);
  useEffect(() => {
    const navigate = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      // 다른 탭이 보이는 동안에도 이 컴포넌트는 display:none 으로 남아 있으므로 화면에 떠 있을 때만 반응한다.
      if (!rootRef.current?.offsetParent) return;
      const target = event.target as HTMLElement | null;
      if (target?.isContentEditable || (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return;
      const position = ticker ? tickers.indexOf(ticker) : -1;
      const next = event.key === 'ArrowLeft' ? position - 1 : position + 1;
      if (position < 0 || next < 0 || next >= tickers.length) return;
      event.preventDefault();
      onSelect(tickers[next], tickers);
    };
    window.addEventListener('keydown', navigate);
    return () => window.removeEventListener('keydown', navigate);
  }, [ticker, tickers, onSelect]);
  // 종목이 붙기 전에만 머리말을 보인다 — 선택된 뒤에는 차트가 화면 높이를 다 써야 한다.
  if (!ticker) return <div className="page">
    <ViewHeader title="종목 상세" lede="어느 화면에서든 종목을 고르면 열립니다. 차트·지표·보유·계획을 한 화면에서 봅니다." />
    <div className="panel empty">위쪽 검색창에 종목명이나 코드를 넣거나, 다른 화면에서 종목을 클릭하세요.</div>
  </div>;
  if (!detail) return <div className="panel empty">종목 정보를 불러오는 중…</div>;
  const toggle = (key: string) => setEnabled(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key]);
  const addPlot = () => setPlots(current => current.length >= MAX_PLOTS ? current : [...current, { id: `plot-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, label: `지표 ${current.length + 1}`, formula: '', pane: 'price', style: 'line', color: PLOT_COLORS[current.length % PLOT_COLORS.length], enabled: true }]);
  const updatePlot = (id: string, patch: Partial<ChartPlotSpec>) => setPlots(current => current.map(item => item.id === id ? { ...item, ...patch } : item));
  const removePlot = (id: string) => setPlots(current => current.filter(item => item.id !== id));
  // 우측 시세 표. 세 번째 원소는 설명이 필요한 항목의 툴팁.
  const quoteRows: [string, string, string?][] = [
    ['시가', money(detail.quote.open as number)],
    ['고가', money(detail.quote.high as number)],
    ['저가', money(detail.quote.low as number)],
    ['거래량', Number(detail.quote.volume || 0).toLocaleString()],
    ['거래대금', money(detail.quote.value as number)],
    ['시가총액', money(detail.fundamental.market_cap)],
    ['PER', ratio(detail.fundamental.per)],
    ['PBR', ratio(detail.fundamental.pbr)],
    ['가중수익률', detail.quote.weighted_return == null ? '—' : `${Number(detail.quote.weighted_return).toFixed(1)}%`, '최근 3·6·9·12개월 누적수익률 가중평균(0.4/0.2/0.2/0.2)'],
  ];
  const index = ticker ? tickers.indexOf(ticker) : -1;
  const hasPrev = index > 0;
  const hasNext = index >= 0 && index < tickers.length - 1;
  const watched = lists.find(list => list.id === listId)?.contains ?? false;
  // 보유 중이면 평단이 진입가다 — 이미 잡은 포지션의 손절·청산을 그대로 그려 볼 수 있어야 한다.
  // 보유 중이 아니면 지금 차트에 실제로 표시된(줌·스크롤 반영) 영역의 마지막 봉 종가를 쓴다.
  // 차트를 아직 못 불러왔을 때만 시세 종가로 대체한다.
  const basePrice = detail.position?.avg_cost || viewportLastBar?.close || Number(detail.quote.close) || 0;
  const freshPlan = () => defaultPlan(basePrice, detail.kind, detail.position?.quantity ?? 0);
  const toggleWatch = async () => {
    try {
      if (watched && listId != null) await api.deleteWatchlistItem(listId, ticker);
      else await api.saveWatchlistItem({ watchlist_id: listId, ticker });
      setWatchMessage('');
      window.dispatchEvent(new Event('mscr-watchlist-changed'));
    } catch (error) {
      setWatchMessage(error instanceof Error ? error.message : '관심종목을 바꿀 수 없습니다.');
    }
  };

  return <div className="detail-grid" ref={rootRef}><div className="detail-main">
    <div className="panel quote-bar">
      <div className="pager">
        <button className="btn btn--ghost btn--sm btn--icon" disabled={!hasPrev} onClick={() => hasPrev && onSelect(tickers[index - 1], tickers)} aria-label="이전 종목 (←)" title={hasPrev ? `이전: ${tickers[index - 1]} (←)` : '목록의 첫 종목입니다'}>◀</button>
        {tickers.length > 1 && <span className="count mono" title="← → 키로 이동">{index >= 0 ? index + 1 : '—'}/{tickers.length}</span>}
        <button className="btn btn--ghost btn--sm btn--icon" disabled={!hasNext} onClick={() => hasNext && onSelect(tickers[index + 1], tickers)} aria-label="다음 종목 (→)" title={hasNext ? `다음: ${tickers[index + 1]} (→)` : '목록의 마지막 종목입니다'}>▶</button>
      </div>
      <div className="quote-id">
        <strong>{detail.name}</strong>
        <a className="mono muted" href={naverStockUrl(detail.ticker, detail.market)} target="_blank" rel="noopener noreferrer">{detail.ticker}</a>
        <span className="quote-tag">{detail.kind.toUpperCase()} · {detail.market || detail.category || 'ETF'}</span>
      </div>
      <strong className="quote-price mono">{money(detail.quote.close as number)}</strong>
      <strong className={`quote-change ${(detail.quote.change_pct as number) >= 0 ? 'up' : 'down'}`}>{detail.quote.change_pct == null ? '—' : `${Number(detail.quote.change_pct).toFixed(2)}%`}</strong>
      <div className="toolbar toolbar--tight push">
        {watchMessage && <span className="subtle">{watchMessage}</span>}
        {lists.length > 1 && <select className="w-sm" value={listId ?? ''} aria-label="관심목록 선택" onChange={event => setListId(Number(event.target.value))}>{lists.map(list => <option key={list.id} value={list.id}>{list.name}</option>)}</select>}
        <button className="btn btn--ghost btn--sm" aria-pressed={watched} onClick={toggleWatch} title={watched ? '관심목록에서 빼기' : '관심목록에 담기'}>{watched ? '★ 관심' : '☆ 관심'}</button>
      </div>
    </div>
    <div className="panel chart-box">
      <div className="toolbar"><div className="segmented">{(['local', 'alphasquare'] as const).map(item => <button key={item} className="btn" aria-pressed={source === item} onClick={() => setSource(item)} title={item === 'alphasquare' ? 'alphasquare.co.kr 비공식 API로 분봉까지 봅니다(수정주가 아님, 참고용)' : '로컬 DB(일봉 EOD) 기준'}>{item === 'local' ? '로컬' : '실시간'}</button>)}</div><div className="segmented">{source === 'local' ? ['3m', '6m', '1y', '3y', 'max'].map(item => <button key={item} className="btn" aria-pressed={range === item} onClick={() => setRange(item)}>{item}</button>) : FREQ_OPTIONS.map(option => <button key={option.value} className="btn" aria-pressed={freq === option.value} onClick={() => setFreq(option.value)}>{option.label}</button>)}</div>{source === 'alphasquare' && <button className="btn btn--ghost btn--sm" onClick={loadChart} title="alpha-square에서 최신 캔들을 다시 불러옵니다(자동 갱신 없음)">새로고침</button>}{source === 'alphasquare' && <button className="btn btn--ghost btn--sm" disabled={count >= ALPHASQUARE_MAX_COUNT} onClick={() => setCount(current => Math.min(ALPHASQUARE_MAX_COUNT, current + ALPHASQUARE_COUNT_STEP))} title={count >= ALPHASQUARE_MAX_COUNT ? `한 번에 가져올 수 있는 최대 봉수(${ALPHASQUARE_MAX_COUNT})에 닿았습니다` : 'alpha-square에서 더 과거의 캔들을 이어붙여 불러옵니다'}>이전 데이터 더보기</button>}<button className="btn btn--ghost" aria-pressed={logScale} onClick={() => setLogScale(current => !current)} title="가격 축을 로그 눈금으로 바꿉니다 — 등락률이 같으면 같은 높이로 보여 오래된 구간과 최근 구간의 움직임을 나란히 비교할 수 있습니다. 거래량과 보조판은 일반 눈금 그대로입니다.">로그</button>{krOnly && <button className="btn btn--ghost" aria-pressed={!!plan} disabled={!plan && basePrice <= 0} onClick={() => savePlan(plan ? null : freshPlan())} title="진입·손절·청산 가격을 차트에 블록으로 그립니다">포지션</button>}<button className="btn btn--ghost" aria-pressed={measure === 'bars'} onClick={() => setMeasure(current => current === 'bars' ? 'off' : 'bars')} title="차트에서 봉 두 개를 클릭하면 그 사이 구간을 봉 개수·가격 변화로 표시합니다">봉구간 측정</button><button className="btn btn--ghost" aria-pressed={measure === 'lines'} onClick={() => setMeasure(current => current === 'lines' ? 'off' : 'lines')} title="차트에서 가격 두 곳을 클릭하면 가로선 두 개를 긋고 그 상하폭을 금액·비율로 표시합니다">선구간 측정</button><span className="subtle push" title={source === 'alphasquare' ? 'alphasquare.co.kr의 비공식 내부 API입니다 — 공식 데이터가 아니므로 참고용으로만 활용하세요. 수정주가가 아니며, 자동 갱신 없이 새로고침 버튼으로만 다시 불러옵니다.' : undefined}>{source === 'alphasquare' ? `실시간(비공식) · ${FREQ_OPTIONS.find(option => option.value === freq)?.label ?? freq}` : chart?.adjusted ? '수정주가' : krOnly ? 'KRX 원주가' : '수정주가(Massive)'} · {chart?.bars.length || 0} bars</span></div>
      <div className="chart-canvas"><TickerChart data={chart} light={light} plan={plan} kind={detail.kind} onPlanChange={savePlan} measure={measure} plotStyles={plots} logScale={logScale} onLastBarChange={setViewportLastBar} /></div>
    </div></div><aside className="panel sidebar scroll">{plan && <PositionPlanner plan={plan} ticker={detail.ticker} name={detail.name} kind={detail.kind} onChange={savePlan} onReset={() => savePlan(freshPlan())} onClose={() => savePlan(null)} />}<div className="toolbar"><div className="section-title">지표 설정</div><button className="btn btn--quiet btn--sm push" aria-expanded={showParams} onClick={() => setShowParams(current => !current)}>{showParams ? '숨기기' : '표시'}</button></div>
    {showParams && <>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('ma')} onChange={() => toggle('ma')} />이동평균</label><div className="parameter-inputs">{config.maPeriods.map((period, index) => <input key={index} aria-label={`이동평균 기간 ${index + 1}`} type="number" min="1" value={period} onChange={event => setConfig(current => ({ ...current, maPeriods: current.maPeriods.map((value, position) => position === index ? Number(event.target.value) : value) }))} />)}</div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('rsi')} onChange={() => toggle('rsi')} />RSI</label><input aria-label="RSI 기간" type="number" min="1" value={config.rsiPeriod} onChange={event => setConfig(current => ({ ...current, rsiPeriod: Number(event.target.value) }))} /></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('macd')} onChange={() => toggle('macd')} />MACD</label><div className="parameter-inputs"><input aria-label="MACD 단기" type="number" min="1" value={config.macdFast} onChange={event => setConfig(current => ({ ...current, macdFast: Number(event.target.value) }))} /><input aria-label="MACD 장기" type="number" min="1" value={config.macdSlow} onChange={event => setConfig(current => ({ ...current, macdSlow: Number(event.target.value) }))} /><input aria-label="MACD 시그널" type="number" min="1" value={config.macdSignal} onChange={event => setConfig(current => ({ ...current, macdSignal: Number(event.target.value) }))} /></div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('bb')} onChange={() => toggle('bb')} />Bollinger</label><div className="parameter-inputs"><input aria-label="볼린저 기간" type="number" min="1" value={config.bbPeriod} onChange={event => setConfig(current => ({ ...current, bbPeriod: Number(event.target.value) }))} /><input aria-label="볼린저 배수" type="number" min="0.1" step="0.1" value={config.bbK} onChange={event => setConfig(current => ({ ...current, bbK: Number(event.target.value) }))} /></div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('volume_ma')} onChange={() => toggle('volume_ma')} />거래량 이평</label><input aria-label="거래량 이평 기간" type="number" min="1" value={config.volumeMaPeriod} onChange={event => setConfig(current => ({ ...current, volumeMaPeriod: Number(event.target.value) }))} /></div>
    <div className="toolbar"><div className="section-title">수식 지표</div><button className="btn btn--quiet btn--sm push" disabled={plots.length >= MAX_PLOTS} onClick={addPlot} title={plots.length >= MAX_PLOTS ? `한 차트에 최대 ${MAX_PLOTS}개까지 얹을 수 있습니다` : '스크리너와 같은 수식으로 지표를 만들어 차트에 얹습니다'}>+ 추가</button></div>
    {!plots.length && <p className="subtle">스크리너와 같은 수식을 써서 원하는 지표를 직접 그립니다. 예: <code>obv_ratio(close, volume, 20)</code>, <code>close / sma(close, 60) - 1</code>, <code>close &gt; sma(close, 20)</code>. 저장해 둔 사용자 지표도 이름으로 부를 수 있습니다.</p>}
    {plots.map(spec => <div className="indicator-control plot-row" key={spec.id}>
      <div className="plot-row__head">
        <label className="check"><input type="checkbox" checked={spec.enabled} onChange={() => updatePlot(spec.id, { enabled: !spec.enabled })} aria-label={`${spec.label || '수식 지표'} 표시`} /></label>
        <input className="plot-row__label" aria-label="지표 이름" placeholder="이름" value={spec.label} onChange={event => updatePlot(spec.id, { label: event.target.value })} />
        <input type="color" aria-label="지표 색" value={spec.color} onChange={event => updatePlot(spec.id, { color: event.target.value })} />
        <button className="btn btn--quiet btn--sm btn--icon" aria-label="지표 삭제" title="이 지표를 지웁니다" onClick={() => removePlot(spec.id)}>×</button>
      </div>
      <FormulaInput value={spec.formula} onChange={next => updatePlot(spec.id, { formula: next })} suggestions={suggestions} ariaLabel="지표 수식" placeholder="예: obv_ratio(close, volume, 20)" />
      <div className="parameter-inputs">
        <select aria-label="표시 위치" value={spec.pane} onChange={event => updatePlot(spec.id, { pane: event.target.value as ChartPlotPane })} title="가격판은 캔들 위에, 보조판은 아래 별도 칸에 그립니다. 같은 보조판을 고르면 한 칸에 겹칩니다.">{PANE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
        <select aria-label="선 모양" value={spec.style} onChange={event => updatePlot(spec.id, { style: event.target.value as ChartPlotStyleName })}>{STYLE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      </div>
      {plotErrors[spec.id] && <p className="msg" data-tone="error">{plotErrors[spec.id]}</p>}
    </div>)}
    </>}
    <div className="section-title">시세</div><table className="table table--kv"><tbody>{quoteRows.map(([label, value, hint]) => <tr key={label}><td title={hint}>{label}</td><td>{value}</td></tr>)}</tbody></table>{detail.position && <><div className="section-title">보유</div><div className="msg">{detail.position.quantity}주 · 평균 {money(detail.position.avg_cost)}<br />평가손익 <b>{money(detail.position.unrealized)}</b></div></>}</aside></div>;
}
