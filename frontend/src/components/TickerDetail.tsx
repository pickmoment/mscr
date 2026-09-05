import { useCallback, useEffect, useRef, useState } from 'react';
import { api, BarsResponse, ChartIndicatorParams, Instrument, Watchlist } from '../lib/api';
import TickerChart from './TickerChart';
import { ratio, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';

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
  if (!ticker) return <div className="panel empty">스크리너에서 종목을 선택하세요.</div>;
  if (!detail) return <div className="panel empty">종목 정보를 불러오는 중…</div>;
  const toggle = (key: string) => setEnabled(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key]);
  const latestRsi = chart?.rsi?.at(-1)?.value;
  const index = ticker ? tickers.indexOf(ticker) : -1;
  const hasPrev = index > 0;
  const hasNext = index >= 0 && index < tickers.length - 1;
  const watched = lists.find(list => list.id === listId)?.contains ?? false;
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

  return <div className="detail-grid" ref={rootRef}><div className="scroll">
    <div className="panel" style={{ padding: '10px 16px', marginBottom: 10 }}>
      <div className="toolbar" style={{ gap: 14 }}>
        <div className="toolbar" style={{ gap: 4 }}>
          <button className="ghost" style={{ padding: '4px 10px', minHeight: 28 }} disabled={!hasPrev} onClick={() => hasPrev && onSelect(tickers[index - 1], tickers)} aria-label="이전 종목 (←)" title={hasPrev ? `이전: ${tickers[index - 1]} (←)` : '목록의 첫 종목입니다'}>◀</button>
          {tickers.length > 1 && <span className="subtle mono" style={{ fontSize: 11 }} title="← → 키로 이동">{index >= 0 ? index + 1 : '—'}/{tickers.length}</span>}
          <button className="ghost" style={{ padding: '4px 10px', minHeight: 28 }} disabled={!hasNext} onClick={() => hasNext && onSelect(tickers[index + 1], tickers)} aria-label="다음 종목 (→)" title={hasNext ? `다음: ${tickers[index + 1]} (→)` : '목록의 마지막 종목입니다'}>▶</button>
        </div>
        <div>
          <div className="section-title" style={{ margin: 0, marginBottom: 2 }}>{detail.kind.toUpperCase()} · {detail.market || detail.category || 'ETF'}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}><strong style={{ fontSize: 16 }}>{detail.name}</strong><a className="mono muted" style={{ fontSize: 12 }} href={`https://stock.naver.com/domestic/stock/${detail.ticker}/price`} target="_blank" rel="noopener noreferrer">{detail.ticker}</a></div>
        </div>
        <strong className="mono" style={{ fontSize: 18 }}>{won(detail.quote.close as number)}</strong>
        <strong className={(detail.quote.change_pct as number) >= 0 ? 'red' : 'blue'}>{detail.quote.change_pct == null ? '—' : `${Number(detail.quote.change_pct).toFixed(2)}%`}</strong>
        <span className="subtle mono">거래대금 {won(detail.quote.value as number)}</span>
        <span className="subtle mono">RSI({config.rsiPeriod}) {ratio(latestRsi)}</span>
        <span className="subtle mono" title="최근 3·6·9·12개월 누적수익률 가중평균(0.4/0.2/0.2/0.2)">가중수익률 {detail.quote.weighted_return == null ? '—' : `${Number(detail.quote.weighted_return).toFixed(1)}%`}</span>
        <div className="toolbar" style={{ gap: 6, margin: 0, marginLeft: 'auto' }}>
          {watchMessage && <span className="subtle">{watchMessage}</span>}
          {lists.length > 1 && <select value={listId ?? ''} aria-label="관심목록 선택" onChange={event => setListId(Number(event.target.value))}>{lists.map(list => <option key={list.id} value={list.id}>{list.name}</option>)}</select>}
          <button className={watched ? 'primary' : 'ghost'} onClick={toggleWatch} title={watched ? '관심목록에서 빼기' : '관심목록에 담기'}>{watched ? '★ 관심' : '☆ 관심'}</button>
          <span className="badge">{detail.as_of || '—'} 기준</span>
        </div>
      </div>
    </div>
    <div className="panel chart-box"><div className="toolbar" style={{ marginBottom: 8 }}>{['3m','6m','1y','3y','max'].map(item => <button key={item} className={range === item ? 'primary' : 'ghost'} onClick={() => setRange(item)}>{item}</button>)}<span className="subtle" style={{ marginLeft: 'auto' }}>{chart?.adjusted ? '수정주가' : 'KRX 원주가'} · {chart?.bars.length || 0} bars</span></div><div style={{ height: 500 }}><TickerChart data={chart} light={light} /></div></div></div><aside className="panel scroll indicator-controls" style={{ padding: 18 }}><div className="toolbar" style={{ marginBottom: showParams ? 14 : 24 }}><div className="section-title" style={{ margin: 0 }}>PARAMETERS</div><button className="ghost" style={{ marginLeft: 'auto', padding: '3px 9px', minHeight: 24, fontSize: 11 }} onClick={() => setShowParams(current => !current)}>{showParams ? '숨기기' : '표시'}</button></div>
    {showParams && <>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('ma')} onChange={() => toggle('ma')} />이동평균</label><div className="parameter-inputs">{config.maPeriods.map((period, index) => <input key={index} aria-label={`이동평균 기간 ${index + 1}`} type="number" min="1" value={period} onChange={event => setConfig(current => ({ ...current, maPeriods: current.maPeriods.map((value, position) => position === index ? Number(event.target.value) : value) }))} />)}</div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('rsi')} onChange={() => toggle('rsi')} />RSI</label><input aria-label="RSI 기간" type="number" min="1" value={config.rsiPeriod} onChange={event => setConfig(current => ({ ...current, rsiPeriod: Number(event.target.value) }))} /></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('macd')} onChange={() => toggle('macd')} />MACD</label><div className="parameter-inputs"><input aria-label="MACD 단기" type="number" min="1" value={config.macdFast} onChange={event => setConfig(current => ({ ...current, macdFast: Number(event.target.value) }))} /><input aria-label="MACD 장기" type="number" min="1" value={config.macdSlow} onChange={event => setConfig(current => ({ ...current, macdSlow: Number(event.target.value) }))} /><input aria-label="MACD 시그널" type="number" min="1" value={config.macdSignal} onChange={event => setConfig(current => ({ ...current, macdSignal: Number(event.target.value) }))} /></div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('bb')} onChange={() => toggle('bb')} />Bollinger</label><div className="parameter-inputs"><input aria-label="볼린저 기간" type="number" min="1" value={config.bbPeriod} onChange={event => setConfig(current => ({ ...current, bbPeriod: Number(event.target.value) }))} /><input aria-label="볼린저 배수" type="number" min="0.1" step="0.1" value={config.bbK} onChange={event => setConfig(current => ({ ...current, bbK: Number(event.target.value) }))} /></div></div>
    <div className="indicator-control"><label className="check"><input type="checkbox" checked={enabled.includes('volume_ma')} onChange={() => toggle('volume_ma')} />거래량 이평</label><input aria-label="거래량 이평 기간" type="number" min="1" value={config.volumeMaPeriod} onChange={event => setConfig(current => ({ ...current, volumeMaPeriod: Number(event.target.value) }))} /></div>
    </>}
    <div className="section-title" style={{ marginTop: 24 }}>QUOTE</div><table className="metric-table"><tbody>{[['시가',won(detail.quote.open as number)],['고가',won(detail.quote.high as number)],['저가',won(detail.quote.low as number)],['거래량',Number(detail.quote.volume || 0).toLocaleString()],['시가총액',won(detail.fundamental.market_cap)],['PER',ratio(detail.fundamental.per)],['PBR',ratio(detail.fundamental.pbr)]].map(([label,value]) => <tr key={label}><td>{label}</td><td className="mono" style={{ textAlign: 'right' }}>{value}</td></tr>)}</tbody></table>{detail.position && <><div className="section-title" style={{ marginTop: 24 }}>POSITION</div><div className="notice">{detail.position.quantity}주 · 평균 {won(detail.position.avg_cost)}<br />평가손익 <b>{won(detail.position.unrealized)}</b></div></>}</aside></div>;
}
