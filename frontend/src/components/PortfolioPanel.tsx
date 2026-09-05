import { useEffect, useState } from 'react';
import { api, PortfolioData, Trade } from '../lib/api';
import { compactVolume, pct, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';
export default function PortfolioPanel({ onSelect }: { onSelect: SelectTicker }) {
  const [data, setData] = useState<PortfolioData | null>(null); const [trades, setTrades] = useState<Trade[]>([]); const [cash, setCash] = useState(''); const [form, setForm] = useState({ ticker: '', side: 'buy', trade_date: new Date().toISOString().slice(0,10), quantity: '', price: '', fee: '0', tax: '', memo: '' }); const [message, setMessage] = useState('');
  const refresh = () => { api.portfolio().then(setData).catch(() => undefined); api.trades().then(setTrades).catch(() => undefined); };
  useEffect(refresh, []);
  const submitTrade = async (event: React.FormEvent) => { event.preventDefault(); try { await api.addTrade({ ticker: form.ticker, side: form.side as 'buy' | 'sell', trade_date: form.trade_date, quantity: Number(form.quantity), price: Number(form.price), fee: Number(form.fee), tax: Number(form.tax || 0), memo: form.memo || null }); setMessage('거래가 추가되었습니다.'); refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : '거래 추가 실패'); } };
  const updateCash = async () => { try { await api.cash(Number(cash)); setMessage('현금을 저장했습니다.'); refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : '현금 저장 실패'); } };
  if (!data) return <div className="panel empty">포트폴리오를 불러오는 중…</div>;
  const stats = [['총 평가금액',won(data.total_market_value)],['총 매입금액',won(data.total_cost)],['평가손익',won(data.total_unrealized)],['수익률',pct(data.total_unrealized_pct)],['당일 손익',won(data.total_day_change)],['실현손익',won(data.total_realized)],['현금',won(data.cash_krw)],['총자산',won(data.total_assets)]];
  // 거래 입력 폼은 key 기반으로 매핑하므로, 폭만 필드별로 달리 준다.
  const tradeFields = [['ticker', '티커'], ['trade_date', '거래일'], ['quantity', '수량'], ['price', '가격'], ['fee', '수수료'], ['tax', '세금'], ['memo', '메모']];
  return <div className="stack stack--lg page">
    <div className="grid grid--4">
      {stats.map(([label, value]) => <div className="stat-card" key={label}>
        <label>{label}</label>
        <strong className={label.includes('손익') ? (String(value).startsWith('-') ? 'down' : 'up') : ''}>{value}</strong>
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
      {!data.positions.length && <div className="empty empty--inline">아직 보유 종목이 없습니다.</div>}
    </div>
    <div className="panel panel--tight stack">
      <div className="section-title">거래 내역</div>
      <form className="toolbar toolbar--tight" onSubmit={submitTrade}>
        {tradeFields.map(([key, placeholder]) => <input key={key} className={key === 'memo' || key === 'trade_date' ? 'w-md' : 'w-sm'} required={['ticker', 'quantity', 'price'].includes(key)} placeholder={placeholder}
          type={key === 'trade_date' ? 'date' : key === 'quantity' || key === 'price' || key === 'fee' || key === 'tax' ? 'number' : 'text'}
          value={form[key as keyof typeof form]} onChange={e => setForm(current => ({ ...current, [key]: e.target.value }))} />)}
        <select className="w-sm" value={form.side} onChange={e => setForm(current => ({ ...current, side: e.target.value }))}>
          <option value="buy">매수</option>
          <option value="sell">매도</option>
        </select>
        <button className="btn btn--primary">거래 추가</button>
      </form>
      <div className="toolbar toolbar--tight">
        <input className="w-md" type="number" placeholder={`현금 ${compactVolume(data.cash_krw)}`} value={cash} onChange={e => setCash(e.target.value)} />
        <button className="btn btn--ghost" onClick={updateCash}>현금 저장</button>
        {message && <span className="msg">{message}</span>}
      </div>
      <table className="table">
        <tbody>
          {trades.map(trade => <tr key={trade.id}>
            <td>{trade.trade_date}</td>
            <td className={trade.side === 'buy' ? 'up' : 'down'}>{trade.side === 'buy' ? '매수' : '매도'}</td>
            <td>{trade.name || trade.ticker}</td>
            <td className="num">{trade.quantity}주 × {won(trade.price)}</td>
            <td><button className="btn btn--danger btn--sm" onClick={() => api.deleteTrade(trade.id).then(refresh).catch(error => setMessage(error.message))}>삭제</button></td>
          </tr>)}
        </tbody>
      </table>
    </div>
  </div>;
}
