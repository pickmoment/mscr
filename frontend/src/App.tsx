import { useEffect, useState } from 'react';
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

type Theme = 'dark' | 'light';

const initialTheme = (): Theme => {
  const saved = localStorage.getItem('mscr-theme');
  if (saved === 'dark' || saved === 'light') return saved;
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
};

export default function App() {
  const [tab, setTab] = useState<'screener' | 'detail' | 'portfolio' | 'trading' | 'indicators' | 'stats' | 'live' | 'settings'>('screener');
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [rows, setRows] = useState<ScreenRow[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [tradesVersion, setTradesVersion] = useState(0);
  useEffect(() => { api.meta().then(setMeta).catch(() => undefined); }, []);
  useEffect(() => { localStorage.setItem('mscr-theme', theme); }, [theme]);
  useEffect(() => { const bump = () => setTradesVersion(current => current + 1); window.addEventListener('mscr-trades-changed', bump); return () => window.removeEventListener('mscr-trades-changed', bump); }, []);
  const selectTicker = (ticker: string) => { setSelectedTicker(ticker); setTab('detail'); };
  return <div className="app-shell" data-theme={theme}>
    <header className="topbar"><img className="brand-mark" src="/icon.svg" alt="" width="34" height="34" /><div className="brand">mscr<small>MARKET SCREENER / KRX</small></div><div className="status"><span className="dot" />EOD 데이터 · {meta?.as_of || '데이터 없음'} · {meta ? `${meta.instrument_count.stock + meta.instrument_count.etf} 종목` : '연결 중'}</div><button className="theme-toggle" onClick={() => setTheme(current => current === 'dark' ? 'light' : 'dark')} aria-label={`${theme === 'dark' ? '라이트' : '다크'} 테마로 전환`}>{theme === 'dark' ? '라이트' : '다크'}</button></header>
    <nav className="tabs">{([['screener','스크리너'],['detail','종목 상세'],['stats','시장 통계'],['live','현재 시황'],['portfolio','포트폴리오'],['trading','트레이딩'],['indicators','지표 관리'],['settings','설정']] as const).map(([key, label]) => <button className={`tab ${tab === key ? 'active' : ''}`} onClick={() => setTab(key)} key={key}>{label}</button>)}</nav>
    <main className="content">
      <section style={{ display: tab === 'screener' ? 'block' : 'none', height: '100%' }}><div className="split"><ScreenerPanel onResults={setRows} /><ScreenerGrid rows={rows} onSelect={selectTicker} light={theme === 'light'} /></div></section>
      <section style={{ display: tab === 'detail' ? 'block' : 'none', height: '100%' }}><TickerDetail ticker={selectedTicker} tickers={rows.map(row => row.ticker)} onSelect={selectTicker} light={theme === 'light'} /></section>
      <section style={{ display: tab === 'stats' ? 'block' : 'none', height: '100%' }}><MarketStatsPanel onSelect={selectTicker} /></section>
      <section style={{ display: tab === 'live' ? 'block' : 'none', height: '100%' }}><MarketLivePanel onSelect={selectTicker} /></section>
      <section style={{ display: tab === 'portfolio' ? 'block' : 'none', height: '100%' }}><PortfolioPanel key={tradesVersion} onSelect={selectTicker} /></section>
      <section style={{ display: tab === 'trading' ? 'block' : 'none', height: '100%' }}><TradingPanel /></section>
      <section style={{ display: tab === 'indicators' ? 'block' : 'none', height: '100%' }}><IndicatorManager /></section>
      <section style={{ display: tab === 'settings' ? 'block' : 'none', height: '100%' }}><SettingsPanel /></section>
    </main>
  </div>;
}
