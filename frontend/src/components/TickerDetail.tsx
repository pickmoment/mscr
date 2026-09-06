import { useCallback, useEffect, useRef, useState } from 'react';
import { api, BarsResponse, ChartBar, ChartIndicatorParams, Instrument, Watchlist } from '../lib/api';
import TickerChart from './TickerChart';
import { ratio, won } from '../lib/format';
import PositionPlanner from './PositionPlanner';
import { defaultPlan, loadPositionPlans, POSITION_EVENT, PositionPlan, storePositionPlan } from '../lib/position';
import { SelectTicker } from '../lib/nav';
import ViewHeader from './ViewHeader';

const defaultConfig: ChartIndicatorParams = { maPeriods: [5, 20, 60], rsiPeriod: 14, macdFast: 12, macdSlow: 26, macdSignal: 9, bbPeriod: 20, bbK: 2, volumeMaPeriod: 50 };
const defaultEnabled = ['ma', 'rsi', 'macd', 'bb', 'volume_ma'];
const CHART_SETTINGS_KEY = 'mscr-chart-settings';
type ChartSettings = { range: string; enabled: string[]; config: ChartIndicatorParams; showParams: boolean };
const loadChartSettings = (): ChartSettings => {
  try {
    const saved = JSON.parse(localStorage.getItem(CHART_SETTINGS_KEY) || 'null');
    return { range: typeof saved?.range === 'string' ? saved.range : '1y', enabled: Array.isArray(saved?.enabled) ? saved.enabled : defaultEnabled, config: { ...defaultConfig, ...saved?.config }, showParams: typeof saved?.showParams === 'boolean' ? saved.showParams : true };
  } catch {
    return { range: '1y', enabled: defaultEnabled, config: defaultConfig, showParams: true };
  }
};

export default function TickerDetail({ ticker, tickers, onSelect, light }: { ticker: string | null; tickers: string[]; onSelect: SelectTicker; light: boolean }) {
  const [detail, setDetail] = useState<Instrument | null>(null);
  const [chart, setChart] = useState<BarsResponse | null>(null);
  const [range, setRange] = useState(() => loadChartSettings().range);
  const [enabled, setEnabled] = useState(() => loadChartSettings().enabled);
  const [config, setConfig] = useState(() => loadChartSettings().config);
  const [showParams, setShowParams] = useState(() => loadChartSettings().showParams);
  const [lists, setLists] = useState<Watchlist[]>([]);
  const [listId, setListId] = useState<number | null>(null);
  const [watchMessage, setWatchMessage] = useState('');
  const [plan, setPlan] = useState<PositionPlan | null>(null);
  const [measuring, setMeasuring] = useState(false);
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
  useEffect(() => { if (ticker) api.bars(ticker, range, enabled, config).then(setChart).catch(() => setChart(null)); }, [ticker, range, enabled, config]);
  useEffect(() => { localStorage.setItem(CHART_SETTINGS_KEY, JSON.stringify({ range, enabled, config, showParams })); }, [range, enabled, config, showParams]);
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
  const latestRsi = chart?.rsi?.at(-1)?.value;
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
        <div className="section-title">{detail.kind.toUpperCase()} · {detail.market || detail.category || 'ETF'}</div>
        <div className="name"><strong>{detail.name}</strong><a className="mono muted" href={`https://stock.naver.com/domestic/stock/${detail.ticker}/price`} target="_blank" rel="noopener noreferrer">{detail.ticker}</a></div>
      </div>
      <strong className="quote-price mono">{won(detail.quote.close as number)}</strong>
      <strong className={`quote-change ${(detail.quote.change_pct as number) >= 0 ? 'up' : 'down'}`}>{detail.quote.change_pct == null ? '—' : `${Number(detail.quote.change_pct).toFixed(2)}%`}</strong>
      <div className="quote-facts">
        <span>거래대금 {won(detail.quote.value as number)}</span>
        <span>RSI({config.rsiPeriod}) {ratio(latestRsi)}</span>
        <span title="최근 3·6·9·12개월 누적수익률 가중평균(0.4/0.2/0.2/0.2)">가중수익률 {detail.quote.weighted_return == null ? '—' : `${Number(detail.quote.weighted_return).toFixed(1)}%`}</span>
      </div>
      <div className="toolbar toolbar--tight push">
        {watchMessage && <span className="subtle">{watchMessage}</span>}
        {lists.length > 1 && <select className="w-md" value={listId ?? ''} aria-label="관심목록 선택" onChange={event => setListId(Number(event.target.value))}>{lists.map(list => <option key={list.id} value={list.id}>{list.name}</option>)}</select>}
        <button className="btn btn--ghost" aria-pressed={watched} onClick={toggleWatch} title={watched ? '관심목록에서 빼기' : '관심목록에 담기'}>{watched ? '★ 관심' : '☆ 관심'}</button>
        <span className="badge">{detail.as_of || '—'} 기준</span>
      </div>
    </div>
    <div className="panel chart-box">
      <div className="toolbar"><div className="segmented">{['3m','6m','1y','3y','max'].map(item => <button key={item} className="btn" aria-pressed={range === item} onClick={() => setRange(item)}>{item}</button>)}</div><button className="btn btn--ghost" aria-pressed={!!plan} disabled={!plan && basePrice <= 0} onClick={() => savePlan(plan ? null : freshPlan())} title="진입·손절·청산 가격을 차트에 블록으로 그립니다">포지션</button><button className="btn btn--ghost" aria-pressed={measuring} onClick={() => setMeasuring(current => !current)} title="차트에서 봉 두 개를 클릭하면 그 사이 구간을 봉 개수·가격 변화로 표시합니다">구간 측정</button><span className="subtle push">{chart?.adjusted ? '수정주가' : 'KRX 원주가'} · {chart?.bars.length || 0} bars</span></div>
      <div className="chart-canvas"><TickerChart data={chart} light={light} plan={plan} kind={detail.kind} onPlanChange={savePlan} measuring={measuring} onLastBarChange={setViewportLastBar} /></div>
    </div></div><aside className="panel sidebar scroll">{plan && <PositionPlanner plan={plan} ticker={detail.ticker} name={detail.name} kind={detail.kind} onChange={savePlan} onReset={() => savePlan(freshPlan())} onClose={() => savePlan(null)} />}<div className="toolbar"><div className="section-title">지표 설정</div><button className="btn btn--quiet btn--sm push" aria-expanded={showParams} onClick={() => setShowParams(current => !current)}>{showParams ? '숨기기' : '표시'}</button></div>
    {showParams && <>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('ma')} onChange={() => toggle('ma')} />이동평균</label><div className="parameter-inputs">{config.maPeriods.map((period, index) => <input key={index} aria-label={`이동평균 기간 ${index + 1}`} type="number" min="1" value={period} onChange={event => setConfig(current => ({ ...current, maPeriods: current.maPeriods.map((value, position) => position === index ? Number(event.target.value) : value) }))} />)}</div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('rsi')} onChange={() => toggle('rsi')} />RSI</label><input aria-label="RSI 기간" type="number" min="1" value={config.rsiPeriod} onChange={event => setConfig(current => ({ ...current, rsiPeriod: Number(event.target.value) }))} /></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('macd')} onChange={() => toggle('macd')} />MACD</label><div className="parameter-inputs"><input aria-label="MACD 단기" type="number" min="1" value={config.macdFast} onChange={event => setConfig(current => ({ ...current, macdFast: Number(event.target.value) }))} /><input aria-label="MACD 장기" type="number" min="1" value={config.macdSlow} onChange={event => setConfig(current => ({ ...current, macdSlow: Number(event.target.value) }))} /><input aria-label="MACD 시그널" type="number" min="1" value={config.macdSignal} onChange={event => setConfig(current => ({ ...current, macdSignal: Number(event.target.value) }))} /></div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('bb')} onChange={() => toggle('bb')} />Bollinger</label><div className="parameter-inputs"><input aria-label="볼린저 기간" type="number" min="1" value={config.bbPeriod} onChange={event => setConfig(current => ({ ...current, bbPeriod: Number(event.target.value) }))} /><input aria-label="볼린저 배수" type="number" min="0.1" step="0.1" value={config.bbK} onChange={event => setConfig(current => ({ ...current, bbK: Number(event.target.value) }))} /></div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('volume_ma')} onChange={() => toggle('volume_ma')} />거래량 이평</label><input aria-label="거래량 이평 기간" type="number" min="1" value={config.volumeMaPeriod} onChange={event => setConfig(current => ({ ...current, volumeMaPeriod: Number(event.target.value) }))} /></div>
    </>}
    <div className="section-title">시세</div><table className="table table--kv"><tbody>{[['시가',won(detail.quote.open as number)],['고가',won(detail.quote.high as number)],['저가',won(detail.quote.low as number)],['거래량',Number(detail.quote.volume || 0).toLocaleString()],['시가총액',won(detail.fundamental.market_cap)],['PER',ratio(detail.fundamental.per)],['PBR',ratio(detail.fundamental.pbr)]].map(([label,value]) => <tr key={label}><td>{label}</td><td>{value}</td></tr>)}</tbody></table>{detail.position && <><div className="section-title">보유</div><div className="msg">{detail.position.quantity}주 · 평균 {won(detail.position.avg_cost)}<br />평가손익 <b>{won(detail.position.unrealized)}</b></div></>}</aside></div>;
}
