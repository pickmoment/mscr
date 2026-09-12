import { useEffect, useState } from 'react';
import { api, MarketBreadth, MarketRankRow, MarketStats } from '../lib/api';
import { compactMoney, compactVolume, ratio, money } from '../lib/format';
import { SelectTicker } from '../lib/nav';
import ViewHeader from './ViewHeader';

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
    {/* 상승/하락 비율은 데이터에서 계산되는 값이라 인라인 width가 유일한 표현 수단 */}
    <div className="breadth-bar"><span className="breadth-up" style={{ width: `${upPct}%` }} /><span className="breadth-down" style={{ width: `${downPct}%` }} /></div>
    <div className="subtle">상승 {data.up} · 하락 {data.down} · 보합 {data.flat}</div>
    <div className="subtle">상한가 {data.limit_up} · 하한가 {data.limit_down} · 거래정지 {data.halted}</div>
    <div className="subtle">52주 신고가 {data.new_high} · 신저가 {data.new_low}</div>
  </div>;
}

function RankTable({ title, rows, onSelect, valueLabel, valueOf }: { title: string; rows: MarketRankRow[]; onSelect: SelectTicker; valueLabel: string; valueOf: (row: MarketRankRow) => string }) {
  const tickers = rows.map(row => row.ticker);
  return <div className="panel panel--tight">
    <div className="section-title">{title} <span className="badge">{rows.length}</span></div>
    <div className="table-scroll table-scroll--rank">
      <table className="table table--rows">
        <thead><tr><th>종목</th><th className="num">종가</th><th className="num">등락률</th><th className="num">{valueLabel}</th></tr></thead>
        <tbody>
          {rows.map(row => <tr key={row.ticker} onClick={() => onSelect(row.ticker, tickers)}>
            <td>{row.name}<span className="subtle mono"> {row.ticker}</span></td>
            <td className="num">{money(row.close)}</td>
            <td className={`num ${changeClass(row.change_pct)}`}>{fmtPct2(row.change_pct)}</td>
            <td className="num">{valueOf(row)}</td>
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

  // 그 거래일에 종목이 실제로 있는 거래소만 그린다(미국의 '기타' 분류처럼 빈 칸을 만들지 않는다).
  const listedExchanges = stats ? stats.exchanges.filter(name => (stats.counts.by_market[name] || 0) > 0) : [];

  return <div className="page">
    <ViewHeader
      title="시장 통계"
      lede={<>로컬에 수집된 일봉으로 계산한 <b>특정 거래일</b>의 시장 전체 통계입니다. 날짜를 골라 과거도 볼 수 있습니다.</>}
      actions={<>
        <label className="check">기준일<input type="date" className="w-md" value={date} min={dates[0]} max={dates[dates.length - 1]} onChange={event => setDate(event.target.value)} /></label>
        {loading && <span className="progress-note"><span className="spinner" aria-hidden="true" />조회 중…</span>}
      </>}
    />
    {/* 통계가 남아 있으면 휴장일 안내(경고), 비어 있으면 조회 실패(에러) */}
    {!loading && status && <div className="msg" data-tone={stats ? 'warn' : 'error'}>{status}</div>}
    {!stats && !loading && <div className="empty">{status || '날짜를 선택하면 그 날의 시장 통계를 보여줍니다.'}</div>}
    {stats && <>
      <div className="toolbar">
        <span className="badge">기준일 {stats.date}</span>
        <span className="subtle">전체 {fmtCount(stats.counts.total)}{listedExchanges.map(name => ` · ${name} ${fmtCount(stats.counts.by_market[name])}`).join('')} · 주식 {fmtCount(stats.counts.stock)} · ETF {fmtCount(stats.counts.etf)}</span>
      </div>

      <div className="section-title">시장 브레스</div>
      <div className="grid grid--4">
        <BreadthCard label="전체" data={stats.breadth.all} />
        {listedExchanges.map(name => <BreadthCard key={name} label={name} data={stats.breadth.by_market[name]} />)}
        <BreadthCard label="ETF" data={stats.breadth.etf} />
      </div>

      <div className="section-title">거래 규모</div>
      <div className="grid grid--auto">
        <div className="stat-card"><label>거래대금 합계</label><strong>{compactMoney(stats.volume.value_sum)}</strong>{stats.volume.value_sum_prev != null && <div className="subtle">전일 {compactMoney(stats.volume.value_sum_prev)}</div>}</div>
        <div className="stat-card"><label>거래량 합계</label><strong>{compactVolume(stats.volume.volume_sum)}주</strong></div>
        {listedExchanges.map(name => <div className="stat-card" key={name}><label>{name} 시가총액</label><strong>{compactMoney(stats.volume.market_cap_sum[name])}</strong></div>)}
      </div>

      <div className="section-title">Top 랭킹 (주식)</div>
      <div className="grid grid--2">
        <RankTable title="거래대금 상위" rows={stats.rankings.value_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactMoney(row.value)}`} />
        <RankTable title="거래량 급증 상위" rows={stats.rankings.volume_surge_top} onSelect={onSelect} valueLabel="20일 평균 대비" valueOf={row => fmtRatioX(row.vol_ratio)} />
        <RankTable title="등락률 상위" rows={stats.rankings.gainers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactMoney(row.value)}`} />
        <RankTable title="등락률 하위" rows={stats.rankings.losers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactMoney(row.value)}`} />
      </div>

      <div className="section-title">ETF 통계</div>
      <div className="grid grid--2">
        <RankTable title="거래대금 상위 (ETF)" rows={stats.etf_rankings.value_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactMoney(row.value)}`} />
        <RankTable title="NAV 괴리율 상위 (ETF)" rows={stats.etf_rankings.premium_top} onSelect={onSelect} valueLabel="NAV 괴리율" valueOf={row => fmtPct2(row.premium_pct)} />
        <RankTable title="등락률 상위 (ETF)" rows={stats.etf_rankings.gainers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactMoney(row.value)}`} />
        <RankTable title="등락률 하위 (ETF)" rows={stats.etf_rankings.losers_top} onSelect={onSelect} valueLabel="거래대금" valueOf={row => `${compactMoney(row.value)}`} />
      </div>

      <div className="section-title">시가총액 구간별 통계 (주식)</div>
      <div className="panel panel--tight">
        <table className="table">
          <thead><tr><th>구간</th><th className="num">종목수</th><th className="num">평균 등락률</th><th className="num">거래대금 합계</th></tr></thead>
          <tbody>
            {stats.sectors.map(band => <tr key={band.category}>
              <td>{band.category}</td>
              <td className="num">{fmtCount(band.count)}</td>
              <td className={`num ${changeClass(band.avg_change_pct)}`}>{fmtPct2(band.avg_change_pct)}</td>
              <td className="num">{compactMoney(band.value_sum)}</td>
            </tr>)}
            {!stats.sectors.length && <tr><td colSpan={4} className="subtle">해당 날짜의 시가총액 데이터가 없습니다</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="section-title">밸류에이션 분포 (주식)</div>
      <div className="grid grid--auto">
        <div className="stat-card"><label>PER 중앙값</label><strong>{ratio(stats.valuation.per.median)}</strong></div>
        <div className="stat-card"><label>PER 1분위~3분위</label><strong>{ratio(stats.valuation.per.q1)} ~ {ratio(stats.valuation.per.q3)}</strong></div>
        <div className="stat-card"><label>PBR 중앙값</label><strong>{ratio(stats.valuation.pbr.median)}</strong></div>
        <div className="stat-card"><label>PBR 1분위~3분위</label><strong>{ratio(stats.valuation.pbr.q1)} ~ {ratio(stats.valuation.pbr.q3)}</strong></div>
        <div className="stat-card"><label>평균 배당수익률</label><strong>{stats.valuation.div_avg == null ? '—' : `${stats.valuation.div_avg.toFixed(2)}%`}</strong></div>
      </div>
    </>}
  </div>;
}
