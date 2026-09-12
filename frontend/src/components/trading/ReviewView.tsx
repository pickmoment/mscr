import { useEffect, useMemo, useState } from 'react';
import { api, ReviewPlanResult, ReviewStats } from '../../lib/api';
import { SelectTicker } from '../../lib/nav';
import { money } from '../../lib/format';
import ViewHeader from '../ViewHeader';
import Term from '../Term';
import { EquitySpark, pctPoint, reviewColumns, rMultiple, rTone } from './shared';

/** 결산. 계획대로 들어가고 나왔는지를 체결 기록으로 되짚는다. */
export default function ReviewView({ onSelect }: { onSelect: SelectTicker }) {
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [results, setResults] = useState<ReviewPlanResult[]>([]);
  const [message, setMessage] = useState('');
  const load = () => {
    api.reviewStats().then(setStats).catch(error => setMessage(error instanceof Error ? error.message : '결산 조회 실패'));
    api.reviewPlans().then(setResults).catch(() => setResults([]));
  };
  useEffect(load, []);
  // 체결을 동기화하면 실현 R이 달라진다. 화면을 다시 열지 않아도 반영되게 이벤트로 다시 읽는다.
  useEffect(() => { window.addEventListener('mscr-trades-changed', load); return () => window.removeEventListener('mscr-trades-changed', load); }, []);
  const tickers = useMemo(() => results.map(row => row.ticker), [results]);

  return <div className="page stack stack--lg">
    <ViewHeader
      title="복기"
      lede={<>실제 체결만으로 계획별 성과를 <Term id="r" /> 단위로 결산합니다. 모의 실행은 들어오지 않습니다.</>}
    />
    <section className="panel autoplan-panel">
      <p className="subtle">체결 기록을 계획과 맞춰 본 결과입니다. <Term id="r">1R</Term>은 계획의 진입가와 손절가 차이라, 실현 R이 +1이면 걸었던 위험만큼 벌었다는 뜻입니다.</p>
      {message && <div className="msg" data-tone="error">{message}</div>}
      {stats && <div className="stack">
        <div className="grid grid--auto">
          <div className="stat-card"><label>거래 수</label><strong>{stats.trades.toLocaleString('ko-KR')}</strong><span className="subtle">승 {stats.wins} · 패 {stats.losses} · 보유 중 {stats.open.count}</span></div>
          <div className="stat-card"><label>승률</label><strong>{pctPoint(stats.win_rate, 1)}</strong><span className="subtle">평균 이익 {rMultiple(stats.avg_win_r)} · 평균 손실 {rMultiple(stats.avg_loss_r)}</span></div>
          <div className="stat-card"><label>기대 R</label><strong className={rTone(stats.expectancy_r)}>{rMultiple(stats.expectancy_r)}</strong><span className="subtle">거래 한 건당 평균 R</span></div>
          <div className="stat-card"><label>합계 R</label><strong className={rTone(stats.total_r)}>{rMultiple(stats.total_r)}</strong><span className="subtle">미실현 {rMultiple(stats.open.total_open_r)}</span></div>
          <div className="stat-card"><label>프로핏팩터</label><strong>{stats.profit_factor == null ? '—' : stats.profit_factor.toFixed(2)}</strong><span className="subtle">총이익 ÷ 총손실</span></div>
          <div className="stat-card"><label>최대 연속 손실</label><strong>{stats.max_consecutive_losses}회</strong><span className="subtle">연달아 진 횟수의 최대</span></div>
          <div className="stat-card"><label>최대 낙폭 R</label><strong className={stats.max_drawdown_r ? 'down' : undefined}>{rMultiple(stats.max_drawdown_r)}</strong><span className="subtle">누적 R 고점 대비 최대 하락</span></div>
          <div className="stat-card"><label>평균 보유일</label><strong>{stats.avg_days_held == null ? '—' : `${stats.avg_days_held.toFixed(1)}일`}</strong><span className="subtle">진입일부터 청산일까지</span></div>
          <div className="stat-card"><label>평균 진입 <Term id="slippage" /></label><strong>{pctPoint(stats.avg_entry_slippage_pct)}</strong><span className="subtle">양수면 계획가보다 불리하게 체결</span></div>
        </div>
        {stats.equity_curve.length >= 2 && <div className="stack">
          <div className="section-title">누적 R</div>
          <EquitySpark points={stats.equity_curve} />
        </div>}
        {stats.warnings.length > 0 && <div className="msg autoplan-warnings" data-tone="warn">
          <b>확인하세요</b>
          <ul>{stats.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
        </div>}
        <div className="grid grid--2">
          <div className="stack">
            <div className="section-title">셋업별</div>
            <table className="table table--nowrap">
              <thead><tr><th>셋업</th><th className="num">거래</th><th className="num">승률</th><th className="num">평균 R</th><th className="num">합계 R</th></tr></thead>
              <tbody>{stats.by_setup.map(group => <tr key={group.setup || '—'}>
                <td>{group.setup || '태그 없음'}</td>
                <td className="num">{group.trades}</td>
                <td className="num">{pctPoint(group.win_rate, 1)}</td>
                <td className={`num ${rTone(group.avg_r)}`}>{rMultiple(group.avg_r)}</td>
                <td className={`num ${rTone(group.total_r)}`}>{rMultiple(group.total_r)}</td>
              </tr>)}</tbody>
            </table>
            {!stats.by_setup.length && <div className="empty">셋업 태그가 붙은 청산 거래가 없습니다.</div>}
          </div>
          <div className="stack">
            <div className="section-title">월별</div>
            <table className="table table--nowrap">
              <thead><tr><th>월</th><th className="num">거래</th><th className="num">승률</th><th className="num">평균 R</th><th className="num">합계 R</th></tr></thead>
              <tbody>{stats.by_month.map(group => <tr key={group.month || '—'}>
                <td className="mono">{group.month || '—'}</td>
                <td className="num">{group.trades}</td>
                <td className="num">{pctPoint(group.win_rate, 1)}</td>
                <td className={`num ${rTone(group.avg_r)}`}>{rMultiple(group.avg_r)}</td>
                <td className={`num ${rTone(group.total_r)}`}>{rMultiple(group.total_r)}</td>
              </tr>)}</tbody>
            </table>
            {!stats.by_month.length && <div className="empty">청산된 거래가 없습니다.</div>}
          </div>
        </div>
      </div>}
      <div className="section-title">계획별 결과 <span className="badge">{results.length}</span></div>
      <div className="subtle">MAE R·MFE R은 <Term id="mae_mfe" />입니다. 보유 기간 중 가장 불리했던 지점과 가장 유리했던 지점을 R로 잰 값입니다.</div>
      <div className="autoplan-scroll">
        <table className="table table--nowrap">
          <thead><tr>{reviewColumns.map(column => <th key={column.label} className={column.num ? 'num' : undefined} title={column.title}>{column.label}</th>)}</tr></thead>
          <tbody>{results.map(row => <tr key={row.plan_id}>
            <td className="mono">{row.entry_date || '—'}</td>
            <td><button className="btn btn--quiet btn--sm mono" onClick={() => onSelect(row.ticker, tickers)} title="종목 상세로 이동합니다">{row.ticker}</button> <span className="subtle">{row.ticker_name || ''}</span></td>
            <td>{row.name}</td>
            <td>{row.setup ? <span className="chip">{row.setup}</span> : <span className="subtle">—</span>}</td>
            <td><span className="badge" data-tone={row.status === 'open' ? 'live' : 'ok'}>{row.status === 'open' ? '보유 중' : '청산'}</span></td>
            <td className="num">{money(row.entry_price)} <span className="subtle">{row.entry_slippage_pct == null ? '' : `(${row.entry_slippage_pct >= 0 ? '+' : ''}${row.entry_slippage_pct.toFixed(2)}%)`}</span></td>
            <td className={`num ${rTone(row.realized_r)}`}>{rMultiple(row.realized_r)}</td>
            <td className={`num ${rTone(row.open_r)}`}>{rMultiple(row.open_r)}</td>
            <td className="num down">{rMultiple(row.mae_r)}</td>
            <td className="num up">{rMultiple(row.mfe_r)}</td>
            <td className="num">{row.days_held == null ? '—' : `${row.days_held}일`}</td>
          </tr>)}</tbody>
        </table>
        {!results.length && <div className="empty">체결된 계획이 없습니다. 계획이 진입 체결되면 여기에 결과가 쌓입니다.</div>}
      </div>
    </section>
  </div>;
}
