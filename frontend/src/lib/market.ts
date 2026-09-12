// 시장 모드(한국/미국). 서버와 같은 규칙을 화면에서도 들고 있어야 통화 표기·거래소 목록·
// 잠글 탭을 렌더 시점에 바로 정할 수 있다. 요청마다 X-Market 헤더로 서버에 함께 보낸다.
export type MarketKey = 'kr' | 'us';
export type MarketInfo = {
  key: MarketKey;
  label: string;
  region: string;
  currency: 'KRW' | 'USD';
  exchanges: string[];
  ingest_sources: string[];
  default_ingest_days: number;
  trading: boolean;       // 계획·주문 실행·자동 실행·리스크·복기
  live_overview: boolean; // 현재 시황
  fundamentals: boolean;  // PER·PBR·시가총액
};

const STORAGE_KEY = 'mscr-market';

// 서버 목록(/api/markets)이 도착하기 전에도 화면이 그려져야 해서 같은 값을 기본으로 들고 있는다.
const FALLBACK: Record<MarketKey, MarketInfo> = {
  kr: { key: 'kr', label: '한국', region: 'KR', currency: 'KRW', exchanges: ['KOSPI', 'KOSDAQ', 'KONEX'], ingest_sources: ['krx', 'fdr', 'alphasquare'], default_ingest_days: 400, trading: true, live_overview: true, fundamentals: true },
  us: { key: 'us', label: '미국', region: 'US', currency: 'USD', exchanges: ['NASDAQ', 'NYSE', 'AMEX', 'CBOE', 'OTHER'], ingest_sources: ['massive'], default_ingest_days: 30, trading: false, live_overview: false, fundamentals: false },
};

const registry: Record<MarketKey, MarketInfo> = { ...FALLBACK };
const isKey = (value: unknown): value is MarketKey => value === 'kr' || value === 'us';

let current: MarketKey = (() => {
  const saved = localStorage.getItem(STORAGE_KEY);
  return isKey(saved) ? saved : 'kr';
})();

export const currentMarket = (): MarketKey => current;
export const marketInfo = (key: MarketKey = current): MarketInfo => registry[key];
export const allMarkets = (): MarketInfo[] => [registry.kr, registry.us];

export const setMarket = (key: MarketKey): void => {
  current = key;
  localStorage.setItem(STORAGE_KEY, key);
};

/** 서버가 내려준 시장 정의로 기본값을 덮어쓴다(기능 범위가 서버에서 바뀌어도 화면이 따라간다). */
export const applyMarkets = (markets: MarketInfo[]): void => {
  for (const item of markets) if (isKey(item.key)) registry[item.key] = { ...registry[item.key], ...item };
};

export const marketLabel = (key: MarketKey = current): string => `${registry[key].label} 주식`;
