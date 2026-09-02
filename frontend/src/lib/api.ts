export type ScreenSpec = { universe: { kinds: string[]; markets: string[]; exclude_preferred: boolean; exclude_spac: boolean; exclude_halted: boolean; min_bars: number }; formula: string; sort: { formula: string; dir: 'asc' | 'desc' }; limit: number; as_of_offset: number };
export type ScreenRow = { ticker: string; name: string; kind: string; market: string | null; close: number | null; change_pct: number | null; volume: number | null; value: number | null; market_cap: number | null; per: number | null; pbr: number | null; bars_available: number; price_jump_flag: number; halted: number; weighted_return: number | null; _sort?: number | null };
export type Meta = { as_of: string | null; instrument_count: { stock: number; etf: number }; bars_rows: number; last_ingest_at: string | null; data_ready: boolean };
export type Instrument = { ticker: string; name: string; kind: string; market: string | null; category: string | null; base_index: string | null; as_of: string | null; quote: Record<string, number | boolean | null>; fundamental: Record<string, number | null>; bars_available: number; position: Position | null; etf: { nav: number | null; premium_pct: number | null; tracking_error: number | null; top_holdings: { name: string; weight: number }[] | null } | null };
export type ChartPoint = { time: string; value: number };
export type ChartBar = { time: string; open: number; high: number; low: number; close: number; volume: number; halted: boolean };
export type ChartIndicatorParams = { maPeriods: number[]; rsiPeriod: number; macdFast: number; macdSlow: number; macdSignal: number; bbPeriod: number; bbK: number; volumeMaPeriod: number };
export type BarsResponse = { ticker: string; adjusted: boolean; price_jump_flag: boolean; bars: ChartBar[]; overlays: Record<string, ChartPoint[]>; rsi?: ChartPoint[]; macd?: Record<string, ChartPoint[]>; bb?: Record<string, ChartPoint[]>; volume_ma?: ChartPoint[] };
export type Position = { ticker: string; name: string; quantity: number; cost: number; avg_cost: number; last_close: number | null; market_value: number; unrealized: number; unrealized_pct: number | null; day_change: number; weight: number; stale: boolean };
export type PortfolioData = { positions: Position[]; total_market_value: number; total_cost: number; total_unrealized: number; total_unrealized_pct: number | null; total_realized: number; total_day_change: number; cash_krw: number; total_assets: number; stale: boolean };
export type Trade = { id: number; ticker: string; name: string | null; side: 'buy' | 'sell'; trade_date: string; quantity: number; price: number; fee: number; tax: number; memo: string | null };
export type IndicatorParameter = { name: string; default: number; min: number | null; max: number | null; integer: boolean };
export type IndicatorDefinition = { id: number | null; key: string; label: string; unit: string; formula: string | null; parameters: IndicatorParameter[]; enabled: boolean; builtin: boolean; series: boolean; kind: 'number' | 'bool' | 'function'; created_at: string | null; updated_at: string | null };
export type TradingStatus = { enabled: boolean; env: string | null; account_masked: string | null; source: 'env' | 'file' | null; reason: string | null; active_env: 'paper' | 'real'; accounts: { paper: string | null; real: string | null } };
export type BrokerCredential = { app_key: string; app_secret: string; account: string; env: 'paper' | 'real' };
export type TradePlanPhase = 'waiting_entry' | 'holding' | 'tp1_done' | 'trailing' | 'closed';
export type TradePlanLeg = 'entry' | 'stop' | 'tp1' | 'tp2' | 'trailing';
export type TradePlan = { id: number; name: string; ticker: string; side: 'buy' | 'sell'; quantity: number; order_type: 'limit' | 'market'; limit_price: number | null; entry_price: number; stop_price: number; tp1_price: number; tp1_ratio: number; tp2_price: number; tp2_ratio: number; tp3_trailing_pct: number; enabled: boolean; note: string | null; updated_at: string };
export type PlanProposalRequest = { ticker: string; side: 'buy' | 'sell'; entry_price: number; max_investment: number; max_loss: number };
export type PlanCandidate = { stop_atr_multiple: number; stop_distance: number; stop_price: number; rejected: string | null; quantity: number; invested?: number; max_loss_krw?: number; loss_budget_used?: number; leg_quantities?: number[]; tp1_price?: number; tp1_ratio?: number; tp2_price?: number; tp2_ratio?: number; tp3_trailing_pct?: number; reach_tp1_prob?: number; reach_tp2_prob?: number; baseline_expectancy_r?: number; breakeven_tp1_prob?: number };
export type PlanProposal = { ticker: string; name: string; side: 'buy' | 'sell'; entry_price: number; as_of: string | null; reference_close: number | null; atr: number; atr_pct: number; max_investment: number; max_loss: number; binding: 'max_loss' | 'max_investment'; sample: { observations: number; horizon_days: number }; candidates: PlanCandidate[]; recommended: number; recommendation_reason: string; warnings: string[]; plan: Omit<TradePlan, 'id' | 'name' | 'note' | 'updated_at'> };
export type PlanEvaluation = { plan_id: number; name: string; ticker: string; side: string; phase: TradePlanPhase; next_leg: TradePlanLeg | null; triggered: boolean; reason: string; as_of: string | null; close: number | null; order_side: 'buy' | 'sell' | null; order_quantity: number | null };
export type BrokerOrder = { id: number | null; plan_id: number | null; plan_name: string | null; leg: TradePlanLeg | null; as_of: string | null; ticker: string; side: string; quantity: number; order_type: string; limit_price: number | null; status: string; env: string; broker_order_id: string | null; filled_quantity: number; filled_price: number | null; fee: number; tax: number; trade_id: number | null; message: string | null; requested_at: string; updated_at: string };
export type AppSettings = { mscr_home: string; db_path: string; schema_version: number; credential_paths: { krx: string; kis: string }; request_delay_sec: number; request_delay_source: 'default' | 'env' | 'file'; krx: { mode: 'openapi' | 'idpw' | 'anonymous'; source: 'env' | 'file' | null; openapi_key_masked: string | null; krx_id_masked: string | null; stored: string[] }; kis: TradingStatus; data: { as_of: string | null; bars_rows: number; instrument_count: { stock: number; etf: number }; last_ingest_at: string | null } };
export type IngestStatus = { running: boolean; source?: 'krx' | 'fdr'; days?: number; force?: boolean; started_at?: string; finished_at?: string | null; processed?: number; total?: number | null; current_day?: string | null; ok?: boolean | null; error?: string | null };
export type MarketBreadth = { count: number; up: number; down: number; flat: number; limit_up: number; limit_down: number; new_high: number; new_low: number; halted: number };
export type MarketRankRow = { ticker: string; name: string; market: string | null; close: number | null; change_pct: number | null; value: number | null; volume: number | null; vol_ratio: number | null; premium_pct: number | null };
export type MarketCapBand = { category: string; count: number; avg_change_pct: number | null; value_sum: number | null };
export type MarketQuantiles = { q1: number | null; median: number | null; q3: number | null };
export type MarketStats = {
  date: string; requested_date: string; prev_date: string | null;
  counts: { total: number; stock: number; etf: number; kospi: number; kosdaq: number };
  breadth: { all: MarketBreadth; kospi: MarketBreadth; kosdaq: MarketBreadth; etf: MarketBreadth };
  volume: { value_sum: number | null; value_sum_prev: number | null; volume_sum: number | null; market_cap_sum: { KOSPI: number | null; KOSDAQ: number | null } };
  rankings: { value_top: MarketRankRow[]; volume_surge_top: MarketRankRow[]; gainers_top: MarketRankRow[]; losers_top: MarketRankRow[] };
  etf_rankings: { value_top: MarketRankRow[]; gainers_top: MarketRankRow[]; losers_top: MarketRankRow[]; premium_top: MarketRankRow[] };
  sectors: MarketCapBand[];
  valuation: { per: MarketQuantiles; pbr: MarketQuantiles; div_avg: number | null };
};
const detailMessage = (detail: unknown, status: number): string => {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) return detail.map((item: { loc?: (string | number)[]; msg?: string }) => `${(item.loc || []).filter(part => part !== 'body').join('.') || '요청'}: ${item.msg || '잘못된 값'}`).join(' / ');
  return `요청 실패 (HTTP ${status})`;
};
const REQUEST_TIMEOUT_MS = 15000;
// 전 유니버스 동적 계산은 조건에 따라 수십 초가 걸릴 수 있어 스크린 요청만 별도의 긴 상한을 쓴다.
const SCREEN_TIMEOUT_MS = 300000;
type RequestOptions = { timeoutMs?: number; signal?: AbortSignal };
const request = async <T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> => {
  const controller = new AbortController();
  const timeoutMs = options?.timeoutMs ?? REQUEST_TIMEOUT_MS;
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (options?.signal) {
    if (options.signal.aborted) onExternalAbort();
    else options.signal.addEventListener('abort', onExternalAbort, { once: true });
  }
  let response: Response;
  try {
    response = await fetch(path, { headers: { 'content-type': 'application/json' }, ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (timedOut) throw new Error(`서버 응답이 ${timeoutMs / 1000}초 넘게 없습니다. mscr 서버가 실행 중인지 확인하세요.`);
      throw new Error('요청을 취소했습니다.');
    }
    throw new Error('서버에 연결할 수 없습니다. mscr 서버가 실행 중인지, 포트가 맞는지 확인하세요.');
  } finally {
    clearTimeout(timer);
    options?.signal?.removeEventListener('abort', onExternalAbort);
  }
  if (!response.ok) throw new Error(detailMessage((await response.json().catch(() => ({}))).detail, response.status));
  return response.status === 204 ? undefined as T : response.json();
};
const barsPath = (ticker: string, range: string, indicators: string[], config: ChartIndicatorParams) => { const query = new URLSearchParams({ range, indicators: indicators.join(','), ma_periods: config.maPeriods.join(','), rsi_period: String(config.rsiPeriod), macd_fast: String(config.macdFast), macd_slow: String(config.macdSlow), macd_signal: String(config.macdSignal), bb_period: String(config.bbPeriod), bb_k: String(config.bbK), volume_ma_period: String(config.volumeMaPeriod) }); return `/api/instruments/${ticker}/bars?${query}`; };

export const api = {
  meta: () => request<Meta>('/api/meta'),
  screen: (spec: ScreenSpec, signal?: AbortSignal) => request<{ as_of: string | null; count: number; rows: ScreenRow[] }>('/api/screen', { method: 'POST', body: JSON.stringify(spec) }, { timeoutMs: SCREEN_TIMEOUT_MS, signal }),
  screens: () => request<{ id: number; name: string; spec: ScreenSpec; updated_at: string }[]>('/api/screens'),
  saveScreen: (name: string, spec: ScreenSpec) => request<{ id: number }>('/api/screens', { method: 'POST', body: JSON.stringify({ name, spec }) }),
  updateScreen: (id: number, name: string, spec: ScreenSpec) => request<{ id: number; name: string; updated_at: string }>(`/api/screens/${id}`, { method: 'PUT', body: JSON.stringify({ name, spec }) }),
  deleteScreen: (id: number) => request<void>(`/api/screens/${id}`, { method: 'DELETE' }),
  indicators: () => request<IndicatorDefinition[]>('/api/indicators'),
  saveIndicator: (indicator: Pick<IndicatorDefinition, 'key' | 'label' | 'unit' | 'formula' | 'parameters' | 'enabled'>) => request<{ id: number }>('/api/indicators', { method: 'POST', body: JSON.stringify(indicator) }),
  deleteIndicator: (id: number) => request<void>(`/api/indicators/${id}`, { method: 'DELETE' }),
  instrument: (ticker: string) => request<Instrument>(`/api/instruments/${ticker}`),
  bars: (ticker: string, range: string, indicators: string[], config: ChartIndicatorParams) => request<BarsResponse>(barsPath(ticker, range, indicators, config)),
  portfolio: () => request<PortfolioData>('/api/portfolio'),
  trades: () => request<Trade[]>('/api/trades'),
  addTrade: (trade: Omit<Trade, 'id' | 'name'>) => request<{ id: number }>('/api/trades', { method: 'POST', body: JSON.stringify(trade) }),
  deleteTrade: (id: number) => request<void>(`/api/trades/${id}`, { method: 'DELETE' }),
  cash: (cash_krw: number) => request<{ cash_krw: number }>('/api/settings/cash', { method: 'PUT', body: JSON.stringify({ cash_krw }) }),
  settings: () => request<AppSettings>('/api/settings'),
  saveKrxCredentials: (payload: { openapi_key?: string; krx_id?: string; krx_pw?: string }) => request<AppSettings>('/api/settings/krx', { method: 'PUT', body: JSON.stringify(payload) }),
  clearKrxCredentials: () => request<AppSettings>('/api/settings/krx', { method: 'DELETE' }),
  savePreferences: (payload: { request_delay_sec: number }) => request<AppSettings>('/api/settings/preferences', { method: 'PUT', body: JSON.stringify(payload) }),
  krxLatest: () => request<{ as_of: string }>('/api/ingest/krx-latest'),
  ingestStatus: () => request<IngestStatus>('/api/ingest/status'),
  runIngest: (payload: { days: number; force: boolean; source: 'krx' | 'fdr' }) => request<IngestStatus>('/api/ingest/run', { method: 'POST', body: JSON.stringify(payload) }),
  tradingStatus: () => request<TradingStatus>('/api/trading/status'),
  saveBrokerCredentials: (credential: BrokerCredential) => request<TradingStatus>('/api/trading/credentials', { method: 'PUT', body: JSON.stringify(credential) }),
  deleteBrokerCredentials: (env: 'paper' | 'real') => request<TradingStatus>(`/api/trading/credentials?env=${env}`, { method: 'DELETE' }),
  setActiveEnv: (env: 'paper' | 'real') => request<TradingStatus>('/api/trading/active-env', { method: 'PUT', body: JSON.stringify({ env }) }),
  tradePlans: () => request<TradePlan[]>('/api/trading/plans'),
  saveTradePlan: (plan: Omit<TradePlan, 'id' | 'updated_at'> & { id?: number }) => request<{ id: number }>('/api/trading/plans', { method: 'POST', body: JSON.stringify(plan) }),
  deleteTradePlan: (id: number) => request<void>(`/api/trading/plans/${id}`, { method: 'DELETE' }),
  proposePlan: (body: PlanProposalRequest) => request<PlanProposal>('/api/trading/plans/propose', { method: 'POST', body: JSON.stringify(body) }),
  evaluatePlans: () => request<PlanEvaluation[]>('/api/trading/evaluate'),
  runPlans: (body: { dry_run: boolean; plan_ids?: number[] }) => request<BrokerOrder[]>('/api/trading/run', { method: 'POST', body: JSON.stringify(body) }),
  syncOrders: () => request<BrokerOrder[]>('/api/trading/sync', { method: 'POST' }),
  tradeOrders: (limit = 200) => request<BrokerOrder[]>(`/api/trading/orders?limit=${limit}`),
  marketStatsDates: () => request<{ dates: string[] }>('/api/market-stats/dates'),
  marketStats: (date: string) => request<MarketStats>(`/api/market-stats?date=${date}`),
};
