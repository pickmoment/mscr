PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS instruments (
  ticker TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  market TEXT,
  category TEXT,
  base_index TEXT,
  is_preferred INTEGER NOT NULL DEFAULT 0,
  is_spac INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  delisted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_bars (
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,
  source TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, value REAL,
  nav REAL,
  halted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (ticker, date, source)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(date, source);

CREATE TABLE IF NOT EXISTS bars_coverage (
  ticker TEXT NOT NULL,
  source TEXT NOT NULL,
  earliest_attempted TEXT NOT NULL,
  PRIMARY KEY (ticker, source)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS alphasquare_ticker_map (
  ticker TEXT PRIMARY KEY,
  stock_id INTEGER NOT NULL,
  resolved_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS snapshots_fundamental (
  ticker TEXT NOT NULL, date TEXT NOT NULL,
  bps REAL, per REAL, pbr REAL, eps REAL, div REAL, dps REAL,
  market_cap REAL, shares REAL,
  PRIMARY KEY (ticker, date)
) WITHOUT ROWID;


CREATE TABLE IF NOT EXISTS indicator_definitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT 'number',
  formula TEXT NOT NULL,
  parameters TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  spec TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screen_runs (
  screen_id INTEGER NOT NULL REFERENCES screens(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  matched INTEGER NOT NULL DEFAULT 0,
  ran_at TEXT NOT NULL,
  PRIMARY KEY (screen_id, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS screen_signals (
  screen_id INTEGER NOT NULL REFERENCES screens(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  rank INTEGER NOT NULL,
  score REAL,
  close REAL,
  PRIMARY KEY (screen_id, date, ticker)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_screen_signals_ticker ON screen_signals(ticker, date);

CREATE TABLE IF NOT EXISTS watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_items (
  watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  memo TEXT,
  target_price REAL,
  added_price REAL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (watchlist_id, ticker)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_watchlist_items_ticker ON watchlist_items(ticker);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  side TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  quantity REAL NOT NULL CHECK (quantity > 0),
  price REAL NOT NULL CHECK (price >= 0),
  fee REAL NOT NULL DEFAULT 0,
  tax REAL NOT NULL DEFAULT 0,
  memo TEXT,
  created_at TEXT NOT NULL,
  broker_order_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker, trade_date, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_broker_daily ON trades(broker_order_id, trade_date) WHERE broker_order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_runs (
  date TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  rows INTEGER NOT NULL DEFAULT 0,
  elapsed_ms INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  ran_at TEXT NOT NULL,
  PRIMARY KEY (date, kind)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS trade_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  ticker TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity REAL NOT NULL CHECK (quantity > 0),
  order_type TEXT NOT NULL CHECK (order_type IN ('limit','market')),
  limit_price REAL,
  entry_price REAL NOT NULL CHECK (entry_price > 0),
  stop_price REAL NOT NULL CHECK (stop_price > 0),
  tp1_price REAL NOT NULL CHECK (tp1_price > 0),
  tp1_ratio REAL NOT NULL CHECK (tp1_ratio > 0 AND tp1_ratio < 1),
  tp2_price REAL NOT NULL CHECK (tp2_price > 0),
  tp2_ratio REAL NOT NULL CHECK (tp2_ratio > 0 AND tp2_ratio < 1),
  tp3_trailing_pct REAL NOT NULL CHECK (tp3_trailing_pct > 0),
  enabled INTEGER NOT NULL DEFAULT 1,
  setup TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS broker_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER REFERENCES trade_plans(id) ON DELETE SET NULL,
  leg TEXT,
  as_of TEXT,
  ticker TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity REAL NOT NULL,
  order_type TEXT NOT NULL,
  limit_price REAL,
  status TEXT NOT NULL,
  env TEXT NOT NULL,
  broker_order_id TEXT,
  filled_quantity REAL NOT NULL DEFAULT 0,
  filled_price REAL,
  fee REAL NOT NULL DEFAULT 0,
  tax REAL NOT NULL DEFAULT 0,
  trade_id INTEGER REFERENCES trades(id) ON DELETE SET NULL,
  message TEXT,
  payload TEXT,
  requested_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_broker_orders_plan ON broker_orders(plan_id, requested_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_broker_orders_daily ON broker_orders(env, broker_order_id, substr(requested_at, 1, 10)) WHERE broker_order_id IS NOT NULL;
