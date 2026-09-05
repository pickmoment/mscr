import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { api, Meta, ScreenRow } from './lib/api';
import ScreenerPanel from './components/ScreenerPanel';
import ScreenerGrid from './components/ScreenerGrid';
import TickerDetail from './components/TickerDetail';
import PortfolioPanel from './components/PortfolioPanel';
import IndicatorManager from './components/IndicatorManager';
import TradingPanel from './components/TradingPanel';
import SettingsPanel from './components/SettingsPanel';
import MarketStatsPanel from './components/MarketStatsPanel';
import MarketLivePanel from './components/MarketLivePanel';
import WatchlistPanel from './components/WatchlistPanel';
import BriefPanel from './components/BriefPanel';
import { SelectTicker } from './lib/nav';

type Theme = 'dark' | 'light';
type TabKey = 'brief' | 'screener' | 'detail' | 'watchlist' | 'stats' | 'live' | 'portfolio' | 'trading' | 'indicators' | 'settings';

// 좌측은 탐색, 우측 묶음은 운용, 꼬리는 도구. 그룹 경계에만 구분선을 넣는다.
const groups: { key: 'explore' | 'operate' | 'tools'; tabs: { key: TabKey; label: string }[] }[] = [
  { key: 'explore', tabs: [{ key: 'brief', label: '브리핑' }, { key: 'screener', label: '스크리너' }, { key: 'detail', label: '종목 상세' }, { key: 'stats', label: '시장 통계' }, { key: 'live', label: '현재 시황' }] },
  { key: 'operate', tabs: [{ key: 'watchlist', label: '관심종목' }, { key: 'portfolio', label: '포트폴리오' }, { key: 'trading', label: '트레이딩' }] },
  { key: 'tools', tabs: [{ key: 'indicators', label: '지표 관리' }, { key: 'settings', label: '설정' }] },
];
const order: TabKey[] = groups.flatMap(group => group.tabs.map(tab => tab.key));

const initialTheme = (): Theme => {
  const saved = localStorage.getItem('mscr-theme');
  if (saved === 'dark' || saved === 'light') return saved;
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
};

const SunIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2" /><path d="M12 2.6v2.2M12 19.2v2.2M4.4 4.4l1.6 1.6M18 18l1.6 1.6M2.6 12h2.2M19.2 12h2.2M4.4 19.6 6 18M18 6l1.6-1.6" /></svg>;
const MoonIcon = () => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20.5 14.2A8.6 8.6 0 0 1 9.8 3.5a8.6 8.6 0 1 0 10.7 10.7Z" /></svg>;

export default function App() {
  const [tab, setTab] = useState<TabKey>('brief');
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [siblings, setSiblings] = useState<string[]>([]);
  const [rows, setRows] = useState<ScreenRow[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [tradesVersion, setTradesVersion] = useState(0);
  const tablist = useRef<HTMLElement>(null);

  // 차트·그리드는 렌더 중에 readTokens()로 실제 토큰 값을 읽는다. 자식 effect가 부모 effect보다
  // 먼저 돌기 때문에 data-theme을 effect에서 바꾸면 자식이 직전 테마 색을 집어간다. 렌더 시점에
  // 미리 반영해 둔다 — 외부 DOM 상태에 대한 멱등 쓰기다.
  if (document.documentElement.dataset.theme !== theme) document.documentElement.dataset.theme = theme;
  useEffect(() => { api.meta().then(setMeta).catch(() => undefined); }, []);
  useEffect(() => {
    localStorage.setItem('mscr-theme', theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f3f6fa' : '#0b0e13');
  }, [theme]);
  useEffect(() => { const bump = () => setTradesVersion(current => current + 1); window.addEventListener('mscr-trades-changed', bump); return () => window.removeEventListener('mscr-trades-changed', bump); }, []);

  const selectTicker: SelectTicker = (ticker, related) => { setSelectedTicker(ticker); setSiblings(related.includes(ticker) ? related : [ticker]); setTab('detail'); };

  // 탭바는 표준 tablist 규약을 따른다. 좌우 화살표로 탭을 옮기고 포커스도 함께 이동한다.
  const onTabKeyDown = useCallback((event: React.KeyboardEvent) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    const next = step ? order[(order.indexOf(tab) + step + order.length) % order.length]
      : event.key === 'Home' ? order[0]
      : event.key === 'End' ? order[order.length - 1]
      : null;
    if (!next) return;
    event.preventDefault();
    setTab(next);
    requestAnimationFrame(() => tablist.current?.querySelector<HTMLButtonElement>(`#tab-${next}`)?.focus());
  }, [tab]);

  const panel = (key: TabKey, node: React.ReactNode) => <section
    key={key}
    role="tabpanel"
    id={`panel-${key}`}
    aria-labelledby={`tab-${key}`}
    // 숨긴 탭도 마운트를 유지해 스크롤·선택 상태를 보존한다. inert로 포커스만 차단한다.
    inert={tab !== key}
    style={{ display: tab === key ? 'block' : 'none' }}
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
      <button
        className="btn btn--ghost btn--icon"
        onClick={() => setTheme(current => current === 'dark' ? 'light' : 'dark')}
        title={`${theme === 'dark' ? '라이트' : '다크'} 테마로 전환`}
        aria-label={`${theme === 'dark' ? '라이트' : '다크'} 테마로 전환`}
      >{theme === 'dark' ? <SunIcon /> : <MoonIcon />}</button>
    </header>

    <nav className="tabs" role="tablist" aria-label="화면 전환" ref={tablist} onKeyDown={onTabKeyDown}>
      {groups.map((group, index) => <Fragment key={group.key}>
        {index > 0 && <span className={group.key === 'tools' ? 'tab-spacer' : 'tab-sep'} aria-hidden="true" />}
        {group.tabs.map(item => <button
          key={item.key}
          id={`tab-${item.key}`}
          className="tab"
          role="tab"
          type="button"
          aria-selected={tab === item.key}
          aria-controls={`panel-${item.key}`}
          tabIndex={tab === item.key ? 0 : -1}
          onClick={() => setTab(item.key)}
        >{item.label}</button>)}
      </Fragment>)}
    </nav>

    <main className="content">
      {panel('brief', <BriefPanel onSelect={selectTicker} onOpenTab={setTab} />)}
      {panel('screener', <div className="split"><ScreenerPanel onResults={setRows} onSelect={selectTicker} /><ScreenerGrid rows={rows} onSelect={selectTicker} light={theme === 'light'} /></div>)}
      {panel('detail', <TickerDetail ticker={selectedTicker} tickers={siblings} onSelect={selectTicker} light={theme === 'light'} />)}
      {panel('watchlist', <WatchlistPanel onSelect={selectTicker} />)}
      {panel('stats', <MarketStatsPanel onSelect={selectTicker} />)}
      {panel('live', <MarketLivePanel onSelect={selectTicker} />)}
      {panel('portfolio', <PortfolioPanel key={tradesVersion} onSelect={selectTicker} />)}
      {panel('trading', <TradingPanel onSelect={selectTicker} />)}
      {panel('indicators', <IndicatorManager />)}
      {panel('settings', <SettingsPanel />)}
    </main>
  </div>;
}
