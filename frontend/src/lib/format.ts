import { marketInfo } from './market';

// 금액 표기는 시장 모드를 따른다 — 한국은 원 단위 정수, 미국은 달러 소수 둘째 자리.
export const money = (value: number | null | undefined) => {
  if (value == null) return '—';
  if (marketInfo().currency === 'USD') return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `${Math.round(value).toLocaleString('ko-KR')}원`;
};
// 주식 수·거래량처럼 통화가 아닌 큰 수. 한국은 억·만, 미국은 B·M·K로 접는다.
export const compactVolume = (value: number | null | undefined) => {
  if (value == null) return '—';
  if (marketInfo().currency === 'USD') {
    if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
    if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
    if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
    return Math.round(value).toLocaleString('en-US');
  }
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)}억`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}만`;
  return Math.round(value).toLocaleString('ko-KR');
};
// 거래대금·시가총액처럼 접어서 보여 주는 금액. 접은 단위 뒤에 통화 표시를 붙인다.
export const compactMoney = (value: number | null | undefined) => {
  if (value == null) return '—';
  return marketInfo().currency === 'USD' ? `$${compactVolume(value)}` : `${compactVolume(value)}원`;
};
export const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`;
export const ratio = (value: number | null | undefined) => value == null ? '—' : value.toFixed(2);
