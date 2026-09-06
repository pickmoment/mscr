import { useCallback, useEffect, useRef, useState } from 'react';
import { api, Meta } from './lib/api';
import ScreenerView from './components/ScreenerView';
import TickerDetail from './components/TickerDetail';
import TickerSearch from './components/TickerSearch';
import PortfolioPanel from './components/PortfolioPanel';
import IndicatorManager from './components/IndicatorManager';
import SettingsPanel from './components/SettingsPanel';
import MarketStatsPanel from './components/MarketStatsPanel';
import MarketLivePanel from './components/MarketLivePanel';
import WatchlistPanel from './components/WatchlistPanel';
import BriefPanel from './components/BriefPanel';
import HelpPanel from './components/HelpPanel';
import { TradingProvider } from './components/trading/TradingContext';
import PlansView from './components/trading/PlansView';
import OrdersView from './components/trading/OrdersView';
import RiskView from './components/trading/RiskView';
import ReviewView from './components/trading/ReviewView';
import { SelectTicker } from './lib/nav';

type Theme = 'dark' | 'light';
export type ViewKey = 'brief' | 'screener' | 'stats' | 'live' | 'watchlist' | 'plans' | 'orders'
  | 'portfolio' | 'risk' | 'review' | 'indicators' | 'settings' | 'help' | 'detail';
type AreaKey = 'brief' | 'explore' | 'operate' | 'tools';

// 영역은 "지금 무엇을 하는 중인가"고, 하위 탭은 그 안의 작업 순서다. 운용 하위 탭 순서가 곧 실제 절차다.
const areas: { key: AreaKey; label: string; views: { key: ViewKey; label: string }[] }[] = [
  { key: 'brief', label: '브리핑', views: [{ key: 'brief', label: '오늘의 브리핑' }] },
  { key: 'explore', label: '탐색', views: [{ key: 'screener', label: '스크리너' }, { key: 'stats', label: '시장 통계' }, { key: 'live', label: '현재 시황' }] },
  { key: 'operate', label: '운용', views: [{ key: 'watchlist', label: '관심종목' }, { key: 'plans', label: '계획' }, { key: 'orders', label: '주문 실행' }, { key: 'portfolio', label: '포트폴리오' }, { key: 'risk', label: '리스크' }, { key: 'review', label: '복기' }] },
  { key: 'tools', label: '도구', views: [{ key: 'indicators', label: '지표 관리' }, { key: 'settings', label: '설정' }, { key: 'help', label: '도움말' }] },
];
const areaKeys = areas.map(area => area.key);
const areaOf = (view: ViewKey): AreaKey | null => areas.find(area => area.views.some(item => item.key === view))?.key ?? null;
const labelOf = (view: ViewKey): string => areas.flatMap(area => area.views).find(item => item.key === view)?.label ?? '이전 화면';

const initialTheme = (): Theme => {
  const saved = localStorage.getItem('mscr-theme');
  if (saved === 'dark' || saved === 'light') return saved;
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
};
// 'detail'은 저장하지 않는다 — 선택 종목이 없는 채로 빈 상세를 열고 시작하게 된다.
const initialView = (): ViewKey => {
  const saved = localStorage.getItem('mscr-view');
  return saved && saved !== 'detail' && areaOf(saved as ViewKey) ? saved as ViewKey : 'brief';
};

const SunIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2" /><path d="M12 2.6v2.2M12 19.2v2.2M4.4 4.4l1.6 1.6M18 18l1.6 1.6M2.6 12h2.2M19.2 12h2.2M4.4 19.6 6 18M18 6l1.6-1.6" /></svg>;
const MoonIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20.5 14.2A8.6 8.6 0 0 1 9.8 3.5a8.6 8.6 0 1 0 10.7 10.7Z" /></svg>;

export default function App() {
  const [view, setView] = useState<ViewKey>(initialView);
  const [lastArea, setLastArea] = useState<AreaKey>(() => areaOf(initialView()) ?? 'brief');
  const [returnView, setReturnView] = useState<ViewKey>('brief');
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [siblings, setSiblings] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [meta, setMeta] = useState<Meta | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [tradesVersion, setTradesVersion] = useState(0);
  const areaBar = useRef<HTMLElement>(null);
  const viewBar = useRef<HTMLElement>(null);

  // 차트·그리드는 렌더 중에 readTokens()로 실제 토큰 값을 읽는다. 자식 effect가 부모 effect보다
  // 먼저 돌기 때문에 data-theme을 effect에서 바꾸면 자식이 직전 테마 색을 집어간다. 렌더 시점에
  // 미리 반영해 둔다 — 외부 DOM 상태에 대한 멱등 쓰기다.
  if (document.documentElement.dataset.theme !== theme) document.documentElement.dataset.theme = theme;
  useEffect(() => { api.meta().then(setMeta).catch(() => undefined); }, []);
  useEffect(() => {
    localStorage.setItem('mscr-theme', theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f3f6fa' : '#0b0e13');
  }, [theme]);
  useEffect(() => { if (view !== 'detail') localStorage.setItem('mscr-view', view); }, [view]);
  useEffect(() => { const bump = () => setTradesVersion(current => current + 1); window.addEventListener('mscr-trades-changed', bump); return () => window.removeEventListener('mscr-trades-changed', bump); }, []);

  const openView = useCallback((next: ViewKey) => {
    setView(next);
    const area = areaOf(next);
    if (area) setLastArea(area);
  }, []);

  // 종목 상세는 탭이 아니라 어디서든 열리는 화면이다. 돌아갈 곳을 기억해 두고 연다.
  const selectTicker: SelectTicker = (ticker, related) => {
    setSelectedTicker(ticker);
    setSiblings(related.includes(ticker) ? related : [ticker]);
    setReturnView(current => view === 'detail' ? current : view);
    setView('detail');
  };

  // 두 줄 모두 표준 tablist 규약을 따른다. 좌우 화살표로 항목을 옮기고 포커스도 함께 이동한다.
  const stepKey = <T extends string>(event: React.KeyboardEvent, keys: T[], current: T): T | null => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    const index = keys.indexOf(current);
    if (step && index >= 0) return keys[(index + step + keys.length) % keys.length];
    if (event.key === 'Home') return keys[0];
    if (event.key === 'End') return keys[keys.length - 1];
    return null;
  };
  const onAreaKeyDown = (event: React.KeyboardEvent) => {
    const next = stepKey(event, areaKeys, lastArea);
    if (!next) return;
    event.preventDefault();
    openView(areas.find(area => area.key === next)!.views[0].key);
    requestAnimationFrame(() => areaBar.current?.querySelector<HTMLButtonElement>(`#area-${next}`)?.focus());
  };
  const currentViews = areas.find(area => area.key === lastArea)!.views;
  const onViewKeyDown = (event: React.KeyboardEvent) => {
    const next = stepKey(event, currentViews.map(item => item.key), view);
    if (!next) return;
    event.preventDefault();
    openView(next);
    requestAnimationFrame(() => viewBar.current?.querySelector<HTMLButtonElement>(`#view-${next}`)?.focus());
  };

  const panel = (key: ViewKey, node: React.ReactNode) => <section
    key={key}
    role="tabpanel"
    id={`panel-${key}`}
    {...(key === 'detail' ? { 'aria-label': '종목 상세' } : { 'aria-labelledby': `view-${key}` })}
    // 숨긴 화면도 마운트를 유지해 스크롤·선택 상태를 보존한다. inert로 포커스만 차단한다.
    inert={view !== key}
    style={{ display: view === key ? 'block' : 'none' }}
  >{node}</section>;

  const instruments = meta ? meta.instrument_count.stock + meta.instrument_count.etf : null;

  return <div className="app-shell">
    <header className="topbar">
      <img className="brand-mark" src="/icon.svg" alt="" width="30" height="30" />
      <div className="brand">mscr<small>MARKET SCREENER / KRX</small></div>
      <div className="status-strip">
        <span className="status-dot" data-live={meta != null} />
        <span>EOD</span>
        <b className="mono">{meta?.as_of || '데이터 없음'}</b>
        <span className="sep">·</span>
        <span>{instruments == null ? '연결 중' : <><b className="mono">{instruments.toLocaleString('ko-KR')}</b> 종목</>}</span>
      </div>
      <div className="push">
        <TickerSearch
          className="w-lg"
          ariaLabel="종목 검색"
          placeholder="종목명 · 코드 검색"
          value={query}
          onChange={setQuery}
          onPick={(hit, related) => { setQuery(''); selectTicker(hit.ticker, related); }}
        />
      </div>
      <button
        className="btn btn--ghost btn--icon"
        onClick={() => setTheme(current => current === 'dark' ? 'light' : 'dark')}
        title={`${theme === 'dark' ? '라이트' : '다크'} 테마로 전환`}
        aria-label={`${theme === 'dark' ? '라이트' : '다크'} 테마로 전환`}
      >{theme === 'dark' ? <SunIcon /> : <MoonIcon />}</button>
    </header>

    <nav className="areas" role="tablist" aria-label="영역 전환" ref={areaBar} onKeyDown={onAreaKeyDown}>
      {areas.map(area => <button
        key={area.key}
        id={`area-${area.key}`}
        className="area"
        role="tab"
        type="button"
        aria-selected={lastArea === area.key}
        aria-controls={`panel-${area.views[0].key}`}
        tabIndex={lastArea === area.key ? 0 : -1}
        // 이미 그 영역 안에 있으면 첫 화면으로 되돌리지 않는다.
        onClick={() => { if (areaOf(view) !== area.key) openView(area.views[0].key); }}
      >{area.label}</button>)}
    </nav>

    {view === 'detail'
      ? <div className="subtabs"><button className="btn btn--ghost btn--sm" onClick={() => openView(returnView)}>◀ {labelOf(returnView)}로 돌아가기</button></div>
      : currentViews.length > 1 && <nav className="subtabs" role="tablist" aria-label="화면 전환" ref={viewBar} onKeyDown={onViewKeyDown}>
        {currentViews.map(item => <button
          key={item.key}
          id={`view-${item.key}`}
          className="subtab"
          role="tab"
          type="button"
          aria-selected={view === item.key}
          aria-controls={`panel-${item.key}`}
          tabIndex={view === item.key ? 0 : -1}
          onClick={() => openView(item.key)}
        >{item.label}</button>)}
      </nav>}

    <main className="content">
      {panel('brief', <BriefPanel onSelect={selectTicker} onOpenView={openView} />)}
      {panel('screener', <ScreenerView onSelect={selectTicker} light={theme === 'light'} />)}
      {panel('detail', <TickerDetail ticker={selectedTicker} tickers={siblings} onSelect={selectTicker} light={theme === 'light'} />)}
      {panel('stats', <MarketStatsPanel onSelect={selectTicker} />)}
      {panel('live', <MarketLivePanel onSelect={selectTicker} />)}
      {panel('watchlist', <WatchlistPanel onSelect={selectTicker} />)}
      {/* 계획·주문·리스크·복기는 같은 데이터를 본다. Provider가 한 번만 읽어 네 화면이 나눠 쓴다. */}
      <TradingProvider>
        {panel('plans', <PlansView onSelect={selectTicker} />)}
        {panel('orders', <OrdersView />)}
        {panel('risk', <RiskView />)}
        {panel('review', <ReviewView onSelect={selectTicker} />)}
      </TradingProvider>
      {panel('portfolio', <PortfolioPanel key={tradesVersion} onSelect={selectTicker} />)}
      {panel('indicators', <IndicatorManager />)}
      {panel('settings', <SettingsPanel />)}
      {panel('help', <HelpPanel onOpenView={openView} />)}
    </main>
  </div>;
}
