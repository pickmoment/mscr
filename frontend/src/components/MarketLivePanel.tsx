import { Fragment, useEffect, useState } from 'react';
import { api, LiveFeaturedRow, LiveFeaturedSection, LiveIndustryRow, LiveIssueRow, LiveMarketStat, LiveNewsRow, LiveThemeRow, LiveThemeStock, LiveTrendingRow, MarketLiveOverview } from '../lib/api';
import { compactVolume, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';

const fmtPct2 = (value: number | null | undefined) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const changeClass = (value: number | null | undefined) => value == null ? '' : value > 0 ? 'change-up' : value < 0 ? 'change-down' : '';
const fmtCount = (value: number) => value.toLocaleString('ko-KR');
const fmtDt = (value: string | null | undefined) => {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};

function BreadthCard({ label, data }: { label: string; data: LiveMarketStat | undefined }) {
  if (!data) return <div className="stat-card"><label>{label}</label><div className="subtle">데이터 없음</div></div>;
  const total = data.up + data.same + data.down;
  const upPct = total ? (data.up / total) * 100 : 0;
  const downPct = total ? (data.down / total) * 100 : 0;
  return <div className="stat-card">
    <label>{label} · {fmtCount(total)}종목</label>
    <div className="breadth-bar"><span className="breadth-up" style={{ width: `${upPct}%` }} /><span className="breadth-down" style={{ width: `${downPct}%` }} /></div>
    <div className="subtle">상승 {fmtCount(data.up)} · 보합 {fmtCount(data.same)} · 하락 {fmtCount(data.down)}</div>
    <div className="subtle">상한가 {data.upper_limit} · 하한가 {data.lower_limit}</div>
  </div>;
}

function TrendingTable({ rows, onSelect }: { rows: LiveTrendingRow[]; onSelect: SelectTicker }) {
  const tickers = rows.map(row => row.code);
  return <div className="panel" style={{ padding: 14, minWidth: 0 }}>
    <div className="section-title" style={{ margin: '0 0 10px' }}>인기 종목 <span className="badge">{rows.length}</span></div>
    <div className="rank-scroll">
      <table className="metric-table rank-table">
        <thead><tr><td>#</td><td>종목</td><td>시장</td><td>조회수</td></tr></thead>
        <tbody>
          {rows.map((row, idx) => <tr key={row.code} className="rank-row" onClick={() => onSelect(row.code, tickers)}>
            <td className="mono">{idx + 1}</td>
            <td>{row.name}<span className="subtle mono"> {row.code}</span></td>
            <td className="subtle">{row.market || '—'}</td>
            <td className="mono">{fmtCount(row.count)}</td>
          </tr>)}
          {!rows.length && <tr><td colSpan={4} className="subtle">데이터 없음</td></tr>}
        </tbody>
      </table>
    </div>
  </div>;
}

type ThemeStocksState = 'loading' | 'error' | LiveThemeStock[];

function ThemeTable({ rows, onSelect }: { rows: LiveThemeRow[]; onSelect: SelectTicker }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [stocksByTheme, setStocksByTheme] = useState<Record<number, ThemeStocksState>>({});

  const toggle = (themeId: number | null) => {
    if (themeId == null) return;
    if (expanded === themeId) { setExpanded(null); return; }
    setExpanded(themeId);
    if (stocksByTheme[themeId]) return;
    setStocksByTheme(current => ({ ...current, [themeId]: 'loading' }));
    api.themeStocks(themeId)
      .then(result => setStocksByTheme(current => ({ ...current, [themeId]: result.stocks })))
      .catch(() => setStocksByTheme(current => ({ ...current, [themeId]: 'error' })));
  };

  return <div className="panel" style={{ padding: 14, minWidth: 0 }}>
    <div className="section-title" style={{ margin: '0 0 10px' }}>테마 수익률 순위 <span className="badge">{rows.length}</span></div>
    <div className="rank-scroll">
      <table className="metric-table rank-table">
        <thead><tr><td>순위</td><td>테마</td><td>대분류</td><td>수익률</td><td>상승/하락/보합</td></tr></thead>
        <tbody>
          {rows.map(row => <Fragment key={row.rank}>
            <tr className="rank-row" onClick={() => toggle(row.theme_id)}>
              <td className="mono">{row.rank}{row.rank_change !== 0 && <span className="subtle"> ({row.rank_change > 0 ? '▲' : '▼'}{Math.abs(row.rank_change)})</span>}</td>
              <td>{row.theme || '—'}<span className="subtle mono"> {row.stock_count}종목</span></td>
              <td className="subtle">{row.big_theme || '—'}</td>
              <td className={`mono ${changeClass(row.returns)}`}>{fmtPct2(row.returns)}</td>
              <td className="subtle">{row.up_count}↑ {row.down_count}↓ {row.even_count}=</td>
            </tr>
            {expanded === row.theme_id && <tr>
              <td colSpan={5} style={{ paddingTop: 0, paddingBottom: 10 }}>
                {row.theme_id != null && stocksByTheme[row.theme_id] === 'loading' && <span className="subtle">종목 불러오는 중…</span>}
                {row.theme_id != null && stocksByTheme[row.theme_id] === 'error' && <span className="subtle">종목을 불러오지 못했습니다.</span>}
                {row.theme_id != null && Array.isArray(stocksByTheme[row.theme_id]) && <div className="theme-stock-chips">
                  {(stocksByTheme[row.theme_id] as LiveThemeStock[]).map(stock => <button type="button" key={stock.code} className="theme-stock-chip" onClick={() => onSelect(stock.code, (stocksByTheme[row.theme_id as number] as LiveThemeStock[]).map(item => item.code))}>{stock.name}<span className="subtle mono"> {stock.code}</span></button>)}
                  {(stocksByTheme[row.theme_id] as LiveThemeStock[]).length === 0 && <span className="subtle">종목 없음</span>}
                </div>}
              </td>
            </tr>}
          </Fragment>)}
          {!rows.length && <tr><td colSpan={5} className="subtle">데이터 없음</td></tr>}
        </tbody>
      </table>
    </div>
  </div>;
}

function FeaturedTable({ factor, section, onSelect, valueLabel, valueOf }: { factor: string; section: LiveFeaturedSection; onSelect: SelectTicker; valueLabel: string; valueOf: (row: LiveFeaturedRow) => string }) {
  const tickers = section.rows.map(row => row.code);
  return <div className="panel" style={{ padding: 14, minWidth: 0 }}>
    <div className="section-title" style={{ margin: '0 0 10px' }}>{section.label}{section.error && <span className="subtle"> · 조회 실패</span>}</div>
    <div className="rank-scroll">
      <table className="metric-table rank-table">
        <thead><tr><td>종목</td><td>종가</td><td>등락률</td><td>{valueLabel}</td></tr></thead>
        <tbody>
          {section.rows.map(row => <tr key={`${factor}-${row.code}`} className="rank-row" onClick={() => onSelect(row.code, tickers)}>
            <td>{row.name}<span className="subtle mono"> {row.code}</span></td>
            <td className="mono">{won(row.close)}</td>
            <td className={`mono ${changeClass(row.returns)}`}>{fmtPct2(row.returns)}</td>
            <td className="mono">{valueOf(row)}</td>
          </tr>)}
          {!section.rows.length && <tr><td colSpan={4} className="subtle">{section.error ? '조회 실패' : '데이터 없음'}</td></tr>}
        </tbody>
      </table>
    </div>
  </div>;
}

function IndustryTable({ rows, onSelect }: { rows: LiveIndustryRow[]; onSelect: SelectTicker }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const toggle = (industry: string) => setExpanded(current => current === industry ? null : industry);

  return <div className="panel" style={{ padding: 14 }}>
    <div className="section-title" style={{ margin: '0 0 10px' }}>업종현황 <span className="badge">{rows.length}</span></div>
    <div className="rank-scroll">
      <table className="metric-table rank-table">
        <thead><tr><td>업종</td><td>종목수</td><td>평균 등락률</td><td>상승/하락/보합</td><td>시총 합계</td></tr></thead>
        <tbody>
          {rows.map(row => <Fragment key={row.industry}>
            <tr className="rank-row" onClick={() => toggle(row.industry)}>
              <td>{row.industry}</td>
              <td className="mono">{row.count}</td>
              <td className={`mono ${changeClass(row.avg_returns)}`}>{fmtPct2(row.avg_returns)}</td>
              <td className="subtle">{row.up}↑ {row.down}↓ {row.flat}=</td>
              <td className="mono">{compactVolume(row.marketcap_sum * 1e8)}원</td>
            </tr>
            {expanded === row.industry && <tr>
              <td colSpan={5} style={{ paddingTop: 0, paddingBottom: 10 }}>
                <div className="theme-stock-chips">
                  {row.stocks.map(stock => <button type="button" key={stock.code} className={`theme-stock-chip ${changeClass(stock.returns)}`} onClick={() => onSelect(stock.code, row.stocks.map(item => item.code))}>{stock.name || stock.code}<span className="subtle mono"> {stock.code}</span> {fmtPct2(stock.returns)}</button>)}
                  {!row.stocks.length && <span className="subtle">종목 없음</span>}
                </div>
              </td>
            </tr>}
          </Fragment>)}
          {!rows.length && <tr><td colSpan={5} className="subtle">데이터 없음</td></tr>}
        </tbody>
      </table>
    </div>
  </div>;
}

function LinkList({ title, rows }: { title: string; rows: (LiveNewsRow | LiveIssueRow)[] }) {
  return <div className="panel" style={{ padding: 14, minWidth: 0 }}>
    <div className="section-title" style={{ margin: '0 0 10px' }}>{title} <span className="badge">{rows.length}</span></div>
    <ul className="link-list">
      {rows.map((row, idx) => <li key={idx}>
        <a href={row.link} target="_blank" rel="noreferrer">{row.title}</a>
        <div className="subtle">{[row.source, fmtDt(row.dt)].filter(Boolean).join(' · ')}</div>
      </li>)}
      {!rows.length && <li className="subtle">데이터 없음</li>}
    </ul>
  </div>;
}

export default function MarketLivePanel({ onSelect }: { onSelect: SelectTicker }) {
  const [overview, setOverview] = useState<MarketLiveOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api.marketLive()
      .then(result => { setOverview(result); setUpdatedAt(new Date().toLocaleTimeString('ko-KR')); setStatus(''); })
      .catch(error => setStatus(error instanceof Error ? error.message : '현재 시황을 불러올 수 없습니다.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return <div className="scroll market-stats" style={{ padding: 18, height: '100%' }}>
    <div className="toolbar">
      <div className="section-title" style={{ margin: 0 }}>현재 시황</div>
      <button type="button" className="ghost" disabled={loading} onClick={load}>{loading ? '조회 중…' : '새로고침'}</button>
      {updatedAt && <span className="subtle">갱신 {updatedAt}</span>}
      {status && <span className="subtle">{status}</span>}
    </div>
    <div className="subtle" style={{ marginTop: 4 }}>alphasquare.co.kr의 비공식 내부 API를 사용합니다 — 공식 데이터가 아니므로 참고용으로만 활용하세요. 자동 갱신 없이 새로고침 버튼으로만 조회합니다.</div>

    {!overview && !loading && <div className="empty">새로고침을 눌러 현재 시황을 불러오세요.</div>}

    {overview && <>
      <div className="section-title" style={{ marginTop: 22 }}>시장 등락 현황{overview.errors.breadth && <span className="subtle"> · 조회 실패: {overview.errors.breadth}</span>}</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(2,1fr)' }}>
        <BreadthCard label="코스피" data={overview.breadth.kospi} />
        <BreadthCard label="코스닥" data={overview.breadth.kosdaq} />
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>인기 종목 · 테마{(overview.errors.trending || overview.errors.theme_leaders) && <span className="subtle"> · 조회 실패: {overview.errors.trending || overview.errors.theme_leaders}</span>}</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(2,1fr)', alignItems: 'start' }}>
        <TrendingTable rows={overview.trending} onSelect={onSelect} />
        <ThemeTable rows={overview.theme_leaders} onSelect={onSelect} />
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>특징 종목</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', alignItems: 'start' }}>
        {Object.entries(overview.featured).map(([factor, section]) => <FeaturedTable key={factor} factor={factor} section={section} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.volume_valued)}원`} />)}
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>주체별 순매매{overview.errors.net_flows && <span className="subtle"> · 조회 실패: {overview.errors.net_flows}</span>}</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', alignItems: 'start' }}>
        {Object.entries(overview.net_flows).map(([factor, section]) => <FeaturedTable key={factor} factor={factor} section={section} onSelect={onSelect} valueLabel="순매매" valueOf={row => row.net == null ? '—' : `${row.net > 0 ? '+' : ''}${compactVolume(row.net)}원`} />)}
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>업종현황{overview.errors.industries && <span className="subtle"> · 조회 실패: {overview.errors.industries}</span>}</div>
      <div className="subtle" style={{ marginBottom: 10 }}>전종목 업종 집계 API가 없어 시가총액 상위 종목 표본으로 근사한 값입니다. 소형주 비중이 큰 업종은 실제와 차이가 있을 수 있습니다.</div>
      <IndustryTable rows={overview.industries} onSelect={onSelect} />

      <div className="section-title" style={{ marginTop: 22 }}>시장 뉴스 · 이슈{(overview.errors.news || overview.errors.issues) && <span className="subtle"> · 조회 실패: {overview.errors.news || overview.errors.issues}</span>}</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(2,1fr)', alignItems: 'start' }}>
        <LinkList title="시장 뉴스" rows={overview.news} />
        <LinkList title="시장 이슈" rows={overview.issues} />
      </div>
    </>}
  </div>;
}
