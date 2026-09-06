import { useEffect, useState } from 'react';
import { api, PortfolioData, Trade } from '../lib/api';
import { compactVolume, pct, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';
import TickerSearch from './TickerSearch';
import ViewHeader from './ViewHeader';
export default function PortfolioPanel({ onSelect }: { onSelect: SelectTicker }) {
  const [data, setData] = useState<PortfolioData | null>(null); const [trades, setTrades] = useState<Trade[]>([]); const [cash, setCash] = useState(''); const [form, setForm] = useState({ ticker: '', side: 'buy', trade_date: new Date().toISOString().slice(0,10), quantity: '', price: '', fee: '0', tax: '', memo: '' }); const [message, setMessage] = useState('');
  const refresh = () => { api.portfolio().then(setData).catch(() => undefined); api.trades().then(setTrades).catch(() => undefined); };
  useEffect(refresh, []);
  const submitTrade = async (event: React.FormEvent) => {
    event.preventDefault();
    const ticker = form.ticker.trim();
    // 검색으로 고르지 않고 직접 친 값도 그대로 들어오므로, 보내기 전에 형식을 본다.
    if (!/^\d{6}$/.test(ticker)) { setMessage('종목코드 6자리를 입력하세요.'); return; }
    try {
      await api.addTrade({ ticker, side: form.side as 'buy' | 'sell', trade_date: form.trade_date, quantity: Number(form.quantity), price: Number(form.price), fee: Number(form.fee), tax: Number(form.tax || 0), memo: form.memo || null });
      setMessage('거래가 추가되었습니다.');
      refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : '거래 추가 실패'); }
  };
  const removeTrade = (trade: Trade) => {
    if (!window.confirm(`${trade.trade_date} ${trade.name || trade.ticker} ${trade.quantity}주 거래를 삭제합니다. 계속할까요?`)) return;
    api.deleteTrade(trade.id).then(refresh).catch(error => setMessage(error.message));
  };
  const updateCash = async () => { try { await api.cash(Number(cash)); setMessage('현금을 저장했습니다.'); refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : '현금 저장 실패'); } };
  if (!data) return <div className="panel empty">포트폴리오를 불러오는 중…</div>;
  // 카드마다 "이 숫자가 어디서 나온 값인지"를 한 줄로 붙인다.
  const stats: [string, string, string][] = [
    ['총 평가금액', won(data.total_market_value), '보유 종목 현재가 × 수량'],
    ['총 매입금액', won(data.total_cost), '실제 매수에 쓴 금액'],
    ['평가손익', won(data.total_unrealized), '평가금액 − 매입금액'],
    ['수익률', pct(data.total_unrealized_pct), '매입금액 대비'],
    ['당일 손익', won(data.total_day_change), '전일 종가 대비'],
    ['실현손익', won(data.total_realized), '매도로 확정된 손익'],
    ['현금', won(data.cash_krw), '직접 입력한 값'],
    ['총자산', won(data.total_assets), '평가금액 + 현금'],
  ];
  // 티커·구분은 입력 방식이 달라 따로 그리고, 나머지는 라벨과 타입만 다른 같은 입력이라 돌려 만든다.
  const tradeFields: [Exclude<keyof typeof form, 'ticker' | 'side'>, string, string][] = [
    ['trade_date', '거래일', 'date'], ['quantity', '수량', 'number'], ['price', '가격', 'number'],
    ['fee', '수수료', 'number'], ['tax', '세금', 'number'], ['memo', '메모', 'text'],
  ];
  return <div className="stack stack--lg page">
    <ViewHeader
      title="포트폴리오"
      lede={<>실제 보유 수량·평단과 현금을 기록하는 원장입니다. 리스크·복기·브리핑의 총자산이 이 값에서 나옵니다.</>}
    />
    <div className="grid grid--4">
      {stats.map(([label, value, note]) => <div className="stat-card" key={label}>
        <label>{label}</label>
        <strong className={label.includes('손익') ? (value.startsWith('-') ? 'down' : 'up') : ''}>{value}</strong>
        <span className="subtle">{note}</span>
      </div>)}
    </div>
    <div className="panel panel--tight stack">
      <div className="toolbar">
        <div className="section-title">보유 종목</div>
        {data.stale && <span className="badge" data-tone="warn">일부 시세 오래됨</span>}
      </div>
      <table className="table table--rows">
        <thead><tr>{['종목', '수량', '평균단가', '현재가', '평가금액', '평가손익', '수익률', '당일손익', '비중'].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>
          {data.positions.map(position => <tr key={position.ticker} onClick={() => onSelect(position.ticker, data.positions.map(item => item.ticker))}>
            <td><b>{position.name}</b><span className="muted mono"> {position.ticker}</span></td>
            <td className="num">{position.quantity}</td>
            <td className="num">{won(position.avg_cost)}</td>
            <td className="num">{won(position.last_close)}</td>
            <td className="num">{won(position.market_value)}</td>
            <td className={position.unrealized >= 0 ? 'num up' : 'num down'}>{won(position.unrealized)}</td>
            <td className="num">{pct(position.unrealized_pct)}</td>
            <td className="num">{won(position.day_change)}</td>
            <td className="num">{pct(position.weight)}</td>
          </tr>)}
        </tbody>
      </table>
      {!data.positions.length && <div className="empty empty--inline">아직 보유 종목이 없습니다. 아래 거래 내역에 매수를 입력하거나, 주문 실행 화면에서 체결을 동기화하세요.</div>}
    </div>
    <div className="panel panel--tight stack">
      <div className="section-title">거래 내역</div>
      {/* 값을 넣고 나면 placeholder가 사라져 무엇을 넣었는지 알 수 없으므로 라벨을 남긴다. */}
      <form className="form-grid" onSubmit={submitTrade}>
        <label>종목<TickerSearch value={form.ticker} ariaLabel="거래 종목 검색" placeholder="종목명 또는 코드"
          onChange={next => setForm(current => ({ ...current, ticker: next.toUpperCase() }))}
          onPick={hit => setForm(current => ({ ...current, ticker: hit.ticker }))} /></label>
        <label>구분<select value={form.side} onChange={e => setForm(current => ({ ...current, side: e.target.value }))}>
          <option value="buy">매수</option>
          <option value="sell">매도</option>
        </select></label>
        {tradeFields.map(([key, label, type]) => <label key={key}>{label}<input type={type} required={key === 'quantity' || key === 'price'}
          value={form[key]} onChange={e => setForm(current => ({ ...current, [key]: e.target.value }))} /></label>)}
        <div className="toolbar"><button className="btn btn--primary">거래 추가</button></div>
      </form>
      <div className="toolbar toolbar--tight">
        <input className="w-md" type="number" placeholder={`현금 ${compactVolume(data.cash_krw)}`} value={cash} onChange={e => setCash(e.target.value)} />
        <button className="btn btn--ghost" onClick={updateCash}>현금 저장</button>
        {message && <span className="msg">{message}</span>}
      </div>
      <table className="table">
        <thead><tr>{[['거래일', ''], ['구분', ''], ['종목', ''], ['체결', 'num'], ['수수료', 'num'], ['세금', 'num'], ['메모', ''], ['', '']].map(([label, cls], index) => <th key={index} className={cls}>{label}</th>)}</tr></thead>
        <tbody>
          {trades.map(trade => <tr key={trade.id}>
            <td>{trade.trade_date}</td>
            <td className={trade.side === 'buy' ? 'up' : 'down'}>{trade.side === 'buy' ? '매수' : '매도'}</td>
            <td>{trade.name || trade.ticker}</td>
            <td className="num">{trade.quantity}주 × {won(trade.price)}</td>
            <td className="num">{won(trade.fee)}</td>
            <td className="num">{won(trade.tax)}</td>
            <td>{trade.memo || '—'}</td>
            <td><button className="btn btn--danger btn--sm" onClick={() => removeTrade(trade)}>삭제</button></td>
          </tr>)}
        </tbody>
      </table>
    </div>
  </div>;
}
