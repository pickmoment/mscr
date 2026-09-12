import type { Time } from 'lightweight-charts';
export type ScreenSpec = { universe: { kinds: string[]; markets: string[]; exclude_preferred: boolean; exclude_spac: boolean; exclude_halted: boolean; min_bars: number }; formula: string; sort: { formula: string; dir: 'asc' | 'desc' }; limit: number; as_of_offset: number };
export type ScreenRow = { ticker: string; name: string; kind: string; market: string | null; close: number | null; change_pct: number | null; volume: number | null; value: number | null; market_cap: number | null; per: number | null; pbr: number | null; bars_available: number; price_jump_flag: number; halted: number; weighted_return: number | null; _sort?: number | null };
export type Meta = { as_of: string | null; instrument_count: { stock: number; etf: number }; bars_rows: number; last_ingest_at: string | null; data_ready: boolean };
export type Instrument = { ticker: string; name: string; kind: string; market: string | null; category: string | null; base_index: string | null; as_of: string | null; quote: Record<string, number | boolean | null>; fundamental: Record<string, number | null>; bars_available: number; position: Position | null; etf: { nav: number | null; premium_pct: number | null; tracking_error: number | null; top_holdings: { name: string; weight: number }[] | null } | null };
export type InstrumentHit = { ticker: string; name: string; kind: string; market: string | null };
export type ChartPoint = { time: Time; value: number };
export type ChartBar = { time: Time; open: number; high: number; low: number; close: number; volume: number; halted: boolean };
export type ChartIndicatorParams = { maPeriods: number[]; rsiPeriod: number; macdFast: number; macdSlow: number; macdSignal: number; bbPeriod: number; bbK: number; volumeMaPeriod: number };
export type ChartSource = 'local' | 'alphasquare';
// 차트에 얹는 수식 지표. 수식은 스크리너와 같은 언어라 내장 함수도 저장해 둔 사용자 지표도 쓸 수 있다.
export type ChartPlotPane = 'price' | 'volume' | 'sub1' | 'sub2' | 'sub3';
export type ChartPlotStyleName = 'line' | 'dashed' | 'histogram';
export type ChartPlotSpec = { id: string; label: string; formula: string; pane: ChartPlotPane; style: ChartPlotStyleName; color: string; enabled: boolean };
// 서버는 계산 결과만 돌려준다 — 색·판 같은 표시 설정은 클라이언트가 들고 id로 짝을 맞춘다.
export type ChartPlot = { id: string; error: string | null; points: ChartPoint[] };
export type ChartFreq = 'minute-1' | 'minute-3' | 'minute-5' | 'minute-15' | 'minute-30' | 'minute-60' | 'day';
export type BarsResponse = { ticker: string; adjusted: boolean; price_jump_flag: boolean; bars: ChartBar[]; overlays: Record<string, ChartPoint[]>; rsi?: ChartPoint[]; macd?: Record<string, ChartPoint[]>; bb?: Record<string, ChartPoint[]>; volume_ma?: ChartPoint[]; plots?: ChartPlot[]; source: ChartSource; freq: ChartFreq | null };
export type Position = { ticker: string; name: string; quantity: number; cost: number; avg_cost: number; last_close: number | null; market_value: number; unrealized: number; unrealized_pct: number | null; day_change: number; weight: number; stale: boolean };
export type PortfolioData = { positions: Position[]; total_market_value: number; total_cost: number; total_unrealized: number; total_unrealized_pct: number | null; total_realized: number; total_day_change: number; cash_krw: number; total_assets: number; stale: boolean };
export type Trade = { id: number; ticker: string; name: string | null; side: 'buy' | 'sell'; trade_date: string; quantity: number; price: number; fee: number; tax: number; memo: string | null };
export type IndicatorParameter = { name: string; default: number; min: number | null; max: number | null; integer: boolean };
export type IndicatorDefinition = { id: number | null; key: string; label: string; unit: string; formula: string | null; parameters: IndicatorParameter[]; enabled: boolean; builtin: boolean; series: boolean; kind: 'number' | 'bool' | 'function'; created_at: string | null; updated_at: string | null };
export type TradingStatus = { enabled: boolean; env: string | null; account_masked: string | null; source: 'env' | 'file' | null; reason: string | null; active_env: 'paper' | 'real'; accounts: { paper: string | null; real: string | null } };
export type BrokerCredential = { app_key: string; app_secret: string; account: string; env: 'paper' | 'real' };
export type TradePlanPhase = 'waiting_entry' | 'holding' | 'tp1_done' | 'trailing' | 'closed';
export type TradePlanLeg = 'entry' | 'stop' | 'tp1' | 'tp2' | 'trailing';
export type TradePlan = { id: number; name: string; ticker: string; side: 'buy' | 'sell'; quantity: number; order_type: 'limit' | 'market'; limit_price: number | null; entry_price: number; stop_price: number; tp1_price: number; tp1_ratio: number; tp2_price: number; tp2_ratio: number; tp3_trailing_pct: number; enabled: boolean; setup: string | null; note: string | null; updated_at: string };
export type PlanProposalRequest = { ticker: string; side: 'buy' | 'sell'; entry_price: number; max_investment: number; max_loss: number };
export type PlanCandidate = { stop_atr_multiple: number; stop_distance: number; stop_price: number; rejected: string | null; quantity: number; invested?: number; max_loss_krw?: number; loss_budget_used?: number; leg_quantities?: number[]; tp1_price?: number; tp1_ratio?: number; tp2_price?: number; tp2_ratio?: number; tp3_trailing_pct?: number; reach_tp1_prob?: number; reach_tp2_prob?: number; baseline_expectancy_r?: number; breakeven_tp1_prob?: number };
export type PlanProposal = { ticker: string; name: string; side: 'buy' | 'sell'; entry_price: number; as_of: string | null; reference_close: number | null; atr: number; atr_pct: number; max_investment: number; max_loss: number; binding: 'max_loss' | 'max_investment'; sample: { observations: number; horizon_days: number }; candidates: PlanCandidate[]; recommended: number; recommendation_reason: string; warnings: string[]; risk_budget: RiskBudget; plan: Omit<TradePlan, 'id' | 'name' | 'note' | 'setup' | 'updated_at'> };
export type PlanEvaluation = { plan_id: number; name: string; ticker: string; side: string; phase: TradePlanPhase; next_leg: TradePlanLeg | null; triggered: boolean; reason: string; as_of: string | null; close: number | null; order_side: 'buy' | 'sell' | null; order_quantity: number | null };
export type BrokerOrder = { id: number | null; plan_id: number | null; plan_name: string | null; leg: TradePlanLeg | null; as_of: string | null; ticker: string; side: string; quantity: number; order_type: string; limit_price: number | null; status: string; env: string; broker_order_id: string | null; filled_quantity: number; filled_price: number | null; fee: number; tax: number; trade_id: number | null; message: string | null; requested_at: string; updated_at: string };
export type PlanSimulationLeg = { leg: TradePlanLeg; date: string; price: number; quantity: number };
export type PlanSimulation = { plan_id: number; name: string; ticker: string; side: string; start: string; end: string; bars: number; entry_price: number; stop_price: number; r_unit: number; phase: TradePlanPhase; entered: boolean; legs: PlanSimulationLeg[]; realized_krw: number | null; realized_r: number | null; open_quantity: number | null; open_r: number | null; total_r: number | null; last_close: number | null; warnings: string[] };
export type ReconcileRow = { ticker: string; name: string; local_quantity: number; broker_quantity: number; quantity_diff: number; local_avg_cost: number | null; broker_avg_cost: number | null; matched: boolean };
export type ReconcileResult = { env: string; account_masked: string | null; positions: ReconcileRow[]; mismatched: number; local_cash_krw: number; broker_cash_krw: number };
export type AppSettings = { mscr_home: string; db_path: string; schema_version: number; credential_paths: { krx: string; kis: string }; request_delay_sec: number; request_delay_source: 'default' | 'env' | 'file'; krx: { mode: 'openapi' | 'idpw' | 'anonymous'; source: 'env' | 'file' | null; openapi_key_masked: string | null; krx_id_masked: string | null; stored: string[] }; kis: TradingStatus; ingest_defaults: { days: number; force: boolean; source: 'krx' | 'fdr' | 'alphasquare' }; data: { as_of: string | null; bars_rows: number; instrument_count: { stock: number; etf: number }; last_ingest_at: string | null } };
export type IngestStatus = { running: boolean; source?: 'krx' | 'fdr' | 'alphasquare'; days?: number; force?: boolean; started_at?: string; finished_at?: string | null; processed?: number; total?: number | null; current_day?: string | null; ok?: boolean | null; error?: string | null };
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
export type LiveMarketStat = { upper_limit: number; up: number; same: number; down: number; lower_limit: number };
export type LiveTrendingRow = { code: string; name: string; market: string | null; count: number };
export type LiveThemeRow = { theme_id: number | null; theme: string | null; big_theme: string | null; stock_count: number; returns: number | null; rank: number; rank_change: number; up_count: number; down_count: number; even_count: number; date: string | null };
export type LiveNewsRow = { dt: string | null; source: string | null; title: string; summary: string | null; link: string };
export type LiveIssueRow = { dt: string | null; title: string; link: string; source: string | null };
export type LiveFeaturedRow = { code: string; name: string; close: number | null; returns: number | null; volume: number | null; volume_valued: number | null; net: number | null };
export type LiveFeaturedSection = { label: string; rows: LiveFeaturedRow[]; error?: string };
export type LiveThemeStock = { code: string; name: string; market: string | null };
export type LiveIndustryStock = { code: string; name: string | null; close: number | null; returns: number | null };
export type LiveIndustryRow = { industry: string; count: number; up: number; down: number; flat: number; avg_returns: number | null; marketcap_sum: number; stocks: LiveIndustryStock[] };
export type MarketLiveOverview = {
  breadth: { kospi?: LiveMarketStat; kosdaq?: LiveMarketStat };
  trending: LiveTrendingRow[];
  theme_leaders: LiveThemeRow[];
  news: LiveNewsRow[];
  featured: Record<string, LiveFeaturedSection>;
  net_flows: Record<string, LiveFeaturedSection>;
  industries: LiveIndustryRow[];
  issues: LiveIssueRow[];
  errors: Record<string, string>;
};
export type Watchlist = { id: number; name: string; created_at: string; updated_at: string; item_count: number; contains: boolean };
export type WatchlistRow = { ticker: string; name: string; kind: string | null; market: string | null; delisted: boolean; memo: string | null; target_price: number | null; added_price: number | null; added_at: string; as_of: string | null; close: number | null; volume: number | null; value: number | null; halted: boolean; change_pct: number | null; target_gap_pct: number | null; since_added_pct: number | null; market_cap: number | null; per: number | null; pbr: number | null; stale: boolean };
export type WatchlistSummary = { count: number; up: number; down: number; flat: number; avg_change_pct: number | null; reached_target: number; stale: boolean };
export type WatchlistDetail = { id: number; name: string; updated_at: string; as_of: string | null; rows: WatchlistRow[]; summary: WatchlistSummary };
export type WatchlistItemPayload = { watchlist_id?: number | null; ticker: string; memo?: string | null; target_price?: number | null };
export type WatchlistItemsAction = { action: 'delete' | 'move' | 'copy'; tickers: string[]; target_id?: number | null };

export type JobStatus = { running: boolean; started_at?: string; finished_at?: string | null; processed?: number; total?: number | null; current?: string | null; ok?: boolean | null; error?: string | null; days?: number; force?: boolean; result?: { screens: number; dates: number; rows: number; skipped: number } | null };
export type SignalRow = { ticker: string; name: string | null; market: string | null; rank: number; score: number | null; close: number | null; streak_days?: number | null };
export type SignalStreak = { days: number; first_date: string; truncated: boolean };
export type SignalDiff = { screen_id: number; date: string | null; previous: string | null; entered: SignalRow[]; held: SignalRow[]; exited: SignalRow[]; streaks: Record<string, SignalStreak> };
export type SignalCoverageRow = { id: number; name: string; days: number; first_date: string | null; last_date: string | null; signals: number };
export type SignalCoverage = { screens: SignalCoverageRow[]; trading_days: number };
export type SignalHistoryRow = { date: string; screen_id: number; name: string; rank: number };
export type RiskLimits = { risk_per_trade_pct: number; max_portfolio_heat_pct: number };
export type RiskBudget = { per_trade_krw: number; heat_limit_krw: number; remaining_krw: number; suggested_max_loss: number | null };
export type RiskPlanRow = { plan_id: number; name: string; ticker: string; side: string; setup: string | null; phase: string; enabled: boolean; entry_price: number; stop_price: number; close: number | null; state: 'open' | 'pending' | 'closed' | 'disabled'; quantity: number; risk_krw: number; initial_risk_krw: number; risk_pct: number | null };
export type RiskUnprotected = { ticker: string; name: string; quantity: number; market_value: number; weight: number };
export type RiskHeat = { as_of: string | null; equity: number; cash_krw: number; market_value: number; limits: RiskLimits; open_risk_krw: number; pending_risk_krw: number; total_risk_krw: number; heat_pct: number | null; open_heat_pct: number | null; budget: RiskBudget; over_limit: boolean; plans: RiskPlanRow[]; unprotected: RiskUnprotected[]; warnings: string[] };
export type BriefScreenRow = { ticker: string; name: string | null; market: string | null; rank: number; score: number | null; close: number | null; change_pct: number | null; streak_days: number | null };
export type BriefScreen = { screen_id: number; name: string; date: string | null; previous: string | null; baseline: boolean; matched: number; entered: BriefScreenRow[]; exited: BriefScreenRow[]; held: number; streak_leaders: BriefScreenRow[]; stale: boolean };
export type BriefPlan = { plan_id: number; name: string; ticker: string; ticker_name: string | null; side: string; setup: string | null; phase: string; triggered: boolean; reason: string; close: number | null; entry_price: number; stop_price: number; distance_pct: number | null; stop_distance_pct: number | null; near: boolean };
export type BriefWatchItem = { watchlist_id: number; watchlist: string; ticker: string; name: string | null; close: number | null; target_price: number | null; target_gap_pct: number | null };
export type BriefPosition = { ticker: string; name: string; quantity: number; avg_cost: number; last_close: number | null; unrealized_pct: number | null; weight_pct: number; plan_name: string | null; stop_price: number | null; stop_distance_pct: number | null; unprotected: boolean };
export type BriefData = { as_of: string | null; previous: string | null; tracked_screen_ids: number[] | null; screens: BriefScreen[]; plans: BriefPlan[]; watchlist: { lists: number; items: number; reached: BriefWatchItem[] }; positions: BriefPosition[]; heat: { heat_pct: number | null; total_risk_krw: number; open_risk_krw: number; pending_risk_krw: number; over_limit: boolean; remaining_krw: number; limits: RiskLimits }; warnings: string[] };
export type ReviewExit = { leg: string; date: string | null; quantity: number; price: number; slippage_pct: number | null; realized_krw: number; realized_r: number | null };
export type ReviewPlanResult = { plan_id: number; name: string; ticker: string; ticker_name: string | null; side: string; setup: string | null; status: 'open' | 'closed'; planned_entry: number; entry_price: number; entry_quantity: number; entry_date: string | null; entry_slippage_pct: number | null; stop_price: number; r_unit: number; exits: ReviewExit[]; realized_krw: number; realized_r: number | null; open_quantity: number; open_r: number | null; total_r: number | null; mae_r: number | null; mfe_r: number | null; days_held: number | null; exit_date: string | null };
export type ReviewGroup = { setup?: string; month?: string; trades: number; win_rate: number | null; avg_r: number | null; total_r: number | null };
export type ReviewStats = { trades: number; wins: number; losses: number; win_rate: number | null; avg_r: number | null; total_r: number | null; expectancy_r: number | null; profit_factor: number | null; avg_win_r: number | null; avg_loss_r: number | null; max_consecutive_losses: number; max_drawdown_r: number | null; avg_days_held: number | null; avg_entry_slippage_pct: number | null; equity_curve: { date: string; cumulative_r: number }[]; by_setup: ReviewGroup[]; by_month: ReviewGroup[]; open: { count: number; total_open_r: number | null }; warnings: string[] };
export type BacktestProtocol = { entry: 'next_open' | 'breakout'; trigger_window: number; trigger_buffer_pct: number; stop_mode: 'atr' | 'box'; atr_multiple: number; atr_period: number; box_lookback: number; box_buffer_atr: number; target_r: number; horizon_days: number; cost_pct: number; top_n: number | null; non_overlap: boolean };
export type BacktestResult = { screen_id: number; name: string; protocol: BacktestProtocol; period: { start: string | null; end: string | null; days: number }; signals: number; triggered: number; trades: number; trigger_rate: number | null; expectancy_r: number | null; stderr_r: number | null; win_rate: number | null; target_rate: number | null; stop_rate: number | null; timeout_rate: number | null; avg_days_held: number | null; avg_risk_pct: number | null; by_month: { month: string; trades: number; expectancy_r: number | null }[]; by_half: { label: string; trades: number; expectancy_r: number | null }[]; profitable_months: number; total_months: number; warnings: string[] };
export type ForwardReturns = { screen_id: number; name: string; signals: number; horizons: { days: number; count: number; mean_pct: number | null; median_pct: number | null; win_rate: number | null; p10_pct: number | null; p90_pct: number | null }[]; warnings: string[] };
const detailMessage = (detail: unknown, status: number): string => {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) return detail.map((item: { loc?: (string | number)[]; msg?: string }) => `${(item.loc || []).filter(part => part !== 'body').join('.') || '요청'}: ${item.msg || '잘못된 값'}`).join(' / ');
  return `요청 실패 (HTTP ${status})`;
};
const REQUEST_TIMEOUT_MS = 15000;
// 전 유니버스 동적 계산은 조건에 따라 수십 초가 걸릴 수 있어 스크린 요청만 별도의 긴 상한을 쓴다.
const SCREEN_TIMEOUT_MS = 300000;
// 검증은 신호 로그를 읽어 시뮬레이션만 돌리지만 신호가 수만 건이면 몇 초가 걸린다.
const BACKTEST_TIMEOUT_MS = 60000;
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
const barsPath = (ticker: string, range: string, source: ChartSource, freq: ChartFreq, count: number, indicators: string[], config: ChartIndicatorParams, plots: string) => { const query = new URLSearchParams({ range, source, freq, count: String(count), indicators: indicators.join(','), ma_periods: config.maPeriods.join(','), rsi_period: String(config.rsiPeriod), macd_fast: String(config.macdFast), macd_slow: String(config.macdSlow), macd_signal: String(config.macdSignal), bb_period: String(config.bbPeriod), bb_k: String(config.bbK), volume_ma_period: String(config.volumeMaPeriod), plots }); return `/api/instruments/${ticker}/bars?${query}`; };

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
  searchInstruments: (q: string) => request<InstrumentHit[]>(`/api/instruments/search?q=${encodeURIComponent(q)}`),
  bars: (ticker: string, range: string, source: ChartSource, freq: ChartFreq, count: number, indicators: string[], config: ChartIndicatorParams, plots: string) => request<BarsResponse>(barsPath(ticker, range, source, freq, count, indicators, config, plots)),
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
  runIngest: (payload: { days: number; force: boolean; source: 'krx' | 'fdr' | 'alphasquare' }) => request<IngestStatus>('/api/ingest/run', { method: 'POST', body: JSON.stringify(payload) }),
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
  simulatePlan: (planId: number, start: string, end: string) => request<PlanSimulation>(`/api/trading/plans/${planId}/simulate?start=${start}&end=${end}`),
  reconcile: () => request<ReconcileResult>('/api/trading/reconcile'),
  marketStatsDates: () => request<{ dates: string[] }>('/api/market-stats/dates'),
  marketStats: (date: string) => request<MarketStats>(`/api/market-stats?date=${date}`),
  marketLive: () => request<MarketLiveOverview>('/api/market-live'),
  themeStocks: (themeId: number) => request<{ stocks: LiveThemeStock[] }>(`/api/market-live/themes/${themeId}/stocks`),
  watchlists: (ticker?: string) => request<Watchlist[]>(`/api/watchlists${ticker ? `?ticker=${ticker}` : ''}`),
  createWatchlist: (name: string) => request<Watchlist>('/api/watchlists', { method: 'POST', body: JSON.stringify({ name }) }),
  createWatchlistFromTickers: (name: string, tickers: string[]) => request<{ id: number; name: string; added: number; skipped: string[] }>('/api/watchlists/bulk', { method: 'POST', body: JSON.stringify({ name, tickers }) }),
  renameWatchlist: (id: number, name: string) => request<{ id: number; name: string; updated_at: string }>(`/api/watchlists/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
  deleteWatchlist: (id: number) => request<void>(`/api/watchlists/${id}`, { method: 'DELETE' }),
  watchlistItems: (id: number) => request<WatchlistDetail>(`/api/watchlists/${id}/items`),
  saveWatchlistItem: (payload: WatchlistItemPayload) => request<{ watchlist_id: number; ticker: string }>('/api/watchlists/items', { method: 'POST', body: JSON.stringify(payload) }),
  watchlistItemsAction: (id: number, payload: WatchlistItemsAction) => request<{ affected: number; skipped: string[] }>(`/api/watchlists/${id}/items/actions`, { method: 'POST', body: JSON.stringify(payload) }),
  deleteWatchlistItem: (id: number, ticker: string) => request<void>(`/api/watchlists/${id}/items/${ticker}`, { method: 'DELETE' }),
  brief: (date?: string) => request<BriefData>(`/api/brief${date ? `?date=${date}` : ''}`),
  saveBriefScreens: (screen_ids: number[] | null) => request<BriefData>('/api/brief/settings', { method: 'PUT', body: JSON.stringify({ screen_ids }) }),
  signalCoverage: () => request<SignalCoverage>('/api/signals/coverage'),
  signalStatus: () => request<JobStatus>('/api/signals/status'),
  captureSignals: (payload: { days: number; force: boolean; screen_ids?: number[] }) => request<JobStatus>('/api/signals/capture', { method: 'POST', body: JSON.stringify(payload) }),
  signalDiff: (screenId: number, date?: string) => request<SignalDiff>(`/api/signals/diff?screen_id=${screenId}${date ? `&date=${date}` : ''}`),
  signalHistory: (ticker: string) => request<SignalHistoryRow[]>(`/api/signals/history/${ticker}`),
  riskHeat: () => request<RiskHeat>('/api/risk/heat'),
  saveRiskLimits: (payload: RiskLimits) => request<RiskHeat>('/api/risk/limits', { method: 'PUT', body: JSON.stringify(payload) }),
  reviewPlans: () => request<ReviewPlanResult[]>('/api/review/plans'),
  reviewStats: () => request<ReviewStats>('/api/review/stats'),
  backtest: (screen_id: number, protocol: BacktestProtocol) => request<BacktestResult>('/api/backtest', { method: 'POST', body: JSON.stringify({ screen_id, protocol }) }, { timeoutMs: BACKTEST_TIMEOUT_MS }),
  forwardReturns: (screenId: number, topN?: number) => request<ForwardReturns>(`/api/backtest/forward?screen_id=${screenId}${topN ? `&top_n=${topN}` : ''}`, undefined, { timeoutMs: BACKTEST_TIMEOUT_MS }),
};
