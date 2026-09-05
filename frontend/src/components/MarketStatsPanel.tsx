import { useEffect, useState } from 'react';
import { api, MarketBreadth, MarketRankRow, MarketStats } from '../lib/api';
import { compactVolume, ratio, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';

const changeClass = (value: number | null | undefined) => value == null ? '' : value > 0 ? 'change-up' : value < 0 ? 'change-down' : '';
const fmtPct2 = (value: number | null | undefined) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const fmtRatioX = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(1)}배`;
const fmtCount = (value: number) => value.toLocaleString('ko-KR');

function BreadthCard({ label, data }: { label: string; data: MarketBreadth }) {
  const decided = data.up + data.down + data.flat;
  const upPct = decided ? (data.up / decided) * 100 : 0;
  const downPct = decided ? (data.down / decided) * 100 : 0;
  return <div className="stat-card">
    <label>{label} · {fmtCount(data.count)}종목</label>
    <div className="breadth-bar"><span className="breadth-up" style={{ width: `${upPct}%` }} /><span className="breadth-down" style={{ width: `${downPct}%` }} /></div>
    <div className="subtle">상승 {data.up} · 하락 {data.down} · 보합 {data.flat}</div>
    <div className="subtle">상한가 {data.limit_up} · 하한가 {data.limit_down} · 거래정지 {data.halted}</div>
    <div className="subtle">52주 신고가 {data.new_high} · 신저가 {data.new_low}</div>
  </div>;
}

function RankTable({ title, rows, onSelect, valueLabel, valueOf }: { title: string; rows: MarketRankRow[]; onSelect: SelectTicker; valueLabel: string; valueOf: (row: MarketRankRow) => string }) {
  const tickers = rows.map(row => row.ticker);
  return <div className="panel" style={{ padding: 14, minWidth: 0 }}>
    <div className="section-title" style={{ margin: '0 0 10px' }}>{title} <span className="badge">{rows.length}</span></div>
    <div className="rank-scroll">
      <table className="metric-table rank-table">
        <thead><tr><td>종목</td><td>종가</td><td>등락률</td><td>{valueLabel}</td></tr></thead>
        <tbody>
          {rows.map(row => <tr key={row.ticker} className="rank-row" onClick={() => onSelect(row.ticker, tickers)}>
            <td>{row.name}<span className="subtle mono"> {row.ticker}</span></td>
            <td className="mono">{won(row.close)}</td>
            <td className={`mono ${changeClass(row.change_pct)}`}>{fmtPct2(row.change_pct)}</td>
            <td className="mono">{valueOf(row)}</td>
          </tr>)}
          {!rows.length && <tr><td colSpan={4} className="subtle">데이터 없음</td></tr>}
        </tbody>
      </table>
    </div>
  </div>;
}

export default function MarketStatsPanel({ onSelect }: { onSelect: SelectTicker }) {
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState('');
  const [stats, setStats] = useState<MarketStats | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.marketStatsDates().then(({ dates }) => {
      setDates(dates);
      if (dates.length) setDate(dates[dates.length - 1]);
    }).catch(error => setStatus(error instanceof Error ? error.message : '수집된 날짜 목록을 불러올 수 없습니다.'));
  }, []);

  useEffect(() => {
    if (!date) return;
    setLoading(true);
    api.marketStats(date)
      .then(result => { setStats(result); setStatus(result.date !== date ? `${date}는 휴장일이라 가장 가까운 거래일(${result.date})로 이동했습니다.` : ''); })
      .catch(error => { setStats(null); setStatus(error instanceof Error ? error.message : '시장 통계를 불러올 수 없습니다.'); })
      .finally(() => setLoading(false));
  }, [date]);

  return <div className="scroll market-stats" style={{ padding: 18, height: '100%' }}>
    <div className="toolbar">
      <div className="section-title" style={{ margin: 0 }}>날짜 선택</div>
      <input type="date" className="control" value={date} min={dates[0]} max={dates[dates.length - 1]} onChange={event => setDate(event.target.value)} />
      {loading && <span className="subtle">조회 중…</span>}
      {!loading && status && <span className="subtle">{status}</span>}
    </div>
    {!stats && !loading && <div className="empty">{status || '날짜를 선택하면 그 날의 시장 통계를 보여줍니다.'}</div>}
    {stats && <>
      <div className="toolbar" style={{ marginTop: 10 }}>
        <span className="badge">기준일 {stats.date}</span>
        <span className="subtle">전체 {fmtCount(stats.counts.total)} · 코스피 {fmtCount(stats.counts.kospi)} · 코스닥 {fmtCount(stats.counts.kosdaq)} · 주식 {fmtCount(stats.counts.stock)} · ETF {fmtCount(stats.counts.etf)}</span>
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>시장 브레스</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
        <BreadthCard label="전체" data={stats.breadth.all} />
        <BreadthCard label="코스피" data={stats.breadth.kospi} />
        <BreadthCard label="코스닥" data={stats.breadth.kosdaq} />
        <BreadthCard label="ETF" data={stats.breadth.etf} />
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>거래 규모</div>
      <div className="card-grid">
        <div className="stat-card"><label>거래대금 합계</label><strong>{compactVolume(stats.volume.value_sum)}원</strong>{stats.volume.value_sum_prev != null && <div className="subtle">전일 {compactVolume(stats.volume.value_sum_prev)}원</div>}</div>
        <div className="stat-card"><label>거래량 합계</label><strong>{compactVolume(stats.volume.volume_sum)}주</strong></div>
        <div className="stat-card"><label>코스피 시가총액</label><strong>{compactVolume(stats.volume.market_cap_sum.KOSPI)}원</strong></div>
        <div className="stat-card"><label>코스닥 시가총액</label><strong>{compactVolume(stats.volume.market_cap_sum.KOSDAQ)}원</strong></div>
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>Top 랭킹 (주식)</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(2,1fr)', alignItems: 'start' }}>
        <RankTable title="거래대금 상위" rows={stats.rankings.value_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.value)}원`} />
        <RankTable title="거래량 급증 상위" rows={stats.rankings.volume_surge_top} onSelect={onSelect} valueLabel="20일 평균 대비" valueOf={row => fmtRatioX(row.vol_ratio)} />
        <RankTable title="등락률 상위" rows={stats.rankings.gainers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.value)}원`} />
        <RankTable title="등락률 하위" rows={stats.rankings.losers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.value)}원`} />
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>ETF 통계</div>
      <div className="card-grid" style={{ gridTemplateColumns: 'repeat(2,1fr)', alignItems: 'start' }}>
        <RankTable title="거래대금 상위 (ETF)" rows={stats.etf_rankings.value_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.value)}원`} />
        <RankTable title="NAV 괴리율 상위 (ETF)" rows={stats.etf_rankings.premium_top} onSelect={onSelect} valueLabel="NAV 괴리율" valueOf={row => fmtPct2(row.premium_pct)} />
        <RankTable title="등락률 상위 (ETF)" rows={stats.etf_rankings.gainers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.value)}원`} />
        <RankTable title="등락률 하위 (ETF)" rows={stats.etf_rankings.losers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactVolume(row.value)}원`} />
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>시가총액 구간별 통계 (주식)</div>
      <div className="panel" style={{ padding: 14 }}>
        <table className="metric-table">
          <thead><tr><td>구간</td><td>종목수</td><td>평균 등락률</td><td>거래대금 합계</td></tr></thead>
          <tbody>
            {stats.sectors.map(band => <tr key={band.category}>
              <td>{band.category}</td>
              <td className="mono">{fmtCount(band.count)}</td>
              <td className={`mono ${changeClass(band.avg_change_pct)}`}>{fmtPct2(band.avg_change_pct)}</td>
              <td className="mono">{compactVolume(band.value_sum)}원</td>
            </tr>)}
            {!stats.sectors.length && <tr><td colSpan={4} className="subtle">해당 날짜의 시가총액 데이터가 없습니다</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="section-title" style={{ marginTop: 22 }}>밸류에이션 분포 (주식)</div>
      <div className="card-grid">
        <div className="stat-card"><label>PER 중앙값</label><strong>{ratio(stats.valuation.per.median)}</strong></div>
        <div className="stat-card"><label>PER 1분위~3분위</label><strong>{ratio(stats.valuation.per.q1)} ~ {ratio(stats.valuation.per.q3)}</strong></div>
        <div className="stat-card"><label>PBR 중앙값</label><strong>{ratio(stats.valuation.pbr.median)}</strong></div>
        <div className="stat-card"><label>PBR 1분위~3분위</label><strong>{ratio(stats.valuation.pbr.q1)} ~ {ratio(stats.valuation.pbr.q3)}</strong></div>
        <div className="stat-card"><label>평균 배당수익률</label><strong>{stats.valuation.div_avg == null ? '—' : `${stats.valuation.div_avg.toFixed(2)}%`}</strong></div>
      </div>
    </>}
  </div>;
}
