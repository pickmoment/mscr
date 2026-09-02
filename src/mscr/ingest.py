from __future__ import annotations

import json
import sys
import time
from datetime import timedelta

import pandas as pd

from .db import db_session
from .providers.krx import KRXProvider, fdr_stock_snapshot

PRESETS = {
    "거래량 급증": {"universe": {"kinds": ["stock", "etf"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "prior_avg_ratio(volume, 20) >= 3 and value >= 5000000000 and close >= 1000", "sort": {"formula": "prior_avg_ratio(volume, 20)", "dir": "desc"}, "limit": 500},
    "52주 신고가 근접": {"universe": {"kinds": ["stock", "etf"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "close / rolling_max(high, 250) - 1 >= -0.03 and value >= 3000000000", "sort": {"formula": "close / rolling_max(high, 250) - 1", "dir": "desc"}, "limit": 500},
    "골든크로스": {"universe": {"kinds": ["stock", "etf"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "crosses_above(sma(close, 5), sma(close, 20)) and value >= 1000000000", "sort": {"formula": "value", "dir": "desc"}, "limit": 500},
    "과매도 반등 후보": {"universe": {"kinds": ["stock", "etf"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "rsi(close, 14) <= 30 and returns(close, 20) <= -0.15 and market_cap >= 100000000000", "sort": {"formula": "rsi(close, 14)", "dir": "asc"}, "limit": 500},
    "미너비니 추세 템플릿 (근사)": {"universe": {"kinds": ["stock"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(close, 20) > sma(close, 60) and returns(close, 120) >= 0 and returns(close, 250) >= 0 and close / rolling_min(low, 250) - 1 >= 0.3 and close / rolling_max(high, 250) - 1 >= -0.25 and value >= 1000000000 and close >= 1000", "sort": {"formula": "returns(close, 120)", "dir": "desc"}, "limit": 500},
    "3R 목표 후보": {"universe": {"kinds": ["stock"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": False, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(value, 20) > 3000000000 and close / rolling_max(high, 60) >= 0.96 and sma(close, 60) > sma(close, 120) and close >= 1000", "sort": {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}, "limit": 5},
    "3R 목표 후보 (저변동성 고정)": {"universe": {"kinds": ["stock"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": False, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "atr(high, low, close, 14) / close < 0.03 and sma(value, 20) > 3000000000 and close / rolling_max(high, 60) >= 0.96 and sma(close, 60) > sma(close, 120) and historical_volatility(close, 20) / historical_volatility(close, 60) > 0.9 and historical_volatility(close, 20) / historical_volatility(close, 60) < 1.45 and close >= 1000", "sort": {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}, "limit": 500},
    "박스 조임 후보": {"universe": {"kinds": ["stock"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": False, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(value, 20) > 3000000000 and close / rolling_max(high, 60) >= 0.96 and sma(close, 60) > sma(close, 120) and (rolling_max(high, 20) - rolling_min(low, 20)) / close < 0.08 and (rolling_max(high, 20) - rolling_min(low, 20)) / close >= 0.03 and close >= 1000", "sort": {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}, "limit": 5},
}


def _text(value):
    return None if pd.isna(value) else str(value)


def _num(value):
    return None if pd.isna(value) else float(value)


def _upsert_universe(db, frame: pd.DataFrame, as_of: str, kind: str) -> None:
    if frame.empty:
        return
    rows = []
    for row in frame.to_dict("records"):
        ticker = str(row.get("ticker", "")).zfill(6)
        name = _text(row.get("name")) or ticker
        rows.append((ticker, name, kind, _text(row.get("market")), _text(row.get("category")), _text(row.get("base_index")), int(kind == "stock" and not ticker.endswith("0")), int("스팩" in name), as_of, as_of))
    db.executemany("""
      INSERT INTO instruments(ticker,name,kind,market,category,base_index,is_preferred,is_spac,first_seen,last_seen,delisted)
      VALUES(?,?,?,?,?,?,?,?,?,?,0)
      ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, market=COALESCE(excluded.market,instruments.market), category=COALESCE(excluded.category,instruments.category), base_index=COALESCE(excluded.base_index,instruments.base_index), last_seen=excluded.last_seen, delisted=0
    """, rows)
    db.execute("UPDATE instruments SET delisted=1 WHERE last_seen < ? AND kind=?", (as_of, kind))


def _store_bars(db, frame: pd.DataFrame, day: str) -> int:
    if frame.empty:
        return 0
    rows = []
    for row in frame.to_dict("records"):
        ticker = str(row.get("ticker", "")).zfill(6)
        close = _num(row.get("close")); open_ = _num(row.get("open")); high = _num(row.get("high")); low = _num(row.get("low")); volume = _num(row.get("volume")); value = _num(row.get("value"))
        halted = int(close is not None and close > 0 and open_ == high == low == volume == 0)
        rows.append((ticker, day, "krx_snapshot", open_, high, low, close, volume, value, _num(row.get("nav")), halted))
    db.executemany("""
      INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(ticker,date,source) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,value=excluded.value,nav=excluded.nav,halted=excluded.halted
    """, rows)
    return len(rows)


def _record_run(db, day: str, kind: str, status: str, rows: int, elapsed_ms: int, error: str | None = None) -> None:
    db.execute("""INSERT INTO ingest_runs(date,kind,status,rows,elapsed_ms,error,ran_at) VALUES(?,?,?,?,?,?,datetime('now'))
      ON CONFLICT(date,kind) DO UPDATE SET status=excluded.status,rows=excluded.rows,elapsed_ms=excluded.elapsed_ms,error=excluded.error,ran_at=excluded.ran_at""", (day, kind, status, rows, elapsed_ms, error))


def _already_done(db, day: str, kind: str, force: bool) -> bool:
    if force:
        return False
    row = db.execute("SELECT status FROM ingest_runs WHERE date=? AND kind=?", (day, kind)).fetchone()
    return bool(row and row[0] in ("ok", "holiday"))


def _seed_presets(db) -> None:
    now = pd.Timestamp.now().isoformat(timespec="seconds")
    for name, spec in PRESETS.items():
        db.execute("INSERT INTO screens(name,spec,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(name) DO NOTHING", (name, json.dumps(spec, ensure_ascii=False), now, now))


def latest_trading_day(provider=None) -> str:
    """전체 ingest 없이 KRX가 실제로 발표한 최신 거래일만 조회한다."""
    provider = provider or KRXProvider()
    try:
        as_of = provider.latest_trading_day()
    except Exception as exc:
        raise RuntimeError(f"KRX 최근 거래일 조회에 실패했습니다: {exc}. API 키와 해당 서비스 이용 승인 상태를 확인하세요.") from exc
    return pd.Timestamp(as_of).strftime("%Y-%m-%d")

def run_ingest(days: int = 400, force: bool = False, provider=None, source: str = "krx", path=None, on_progress=None) -> None:
    """`on_progress`는 각 거래일 처리 후 (idx, total, day, stock_rows, etf_rows)로 호출된다. 웹 UI가 백그라운드 진행 상황을 폴링하는 데 쓴다."""
    if source == "fdr":
        _run_ingest_fdr_stocks(force=force, path=path)
        return
    if source != "krx":
        raise ValueError(f"지원하지 않는 소스입니다: {source}")
    provider = provider or KRXProvider()
    try:
        as_of = provider.latest_trading_day()
    except Exception as exc:
        raise RuntimeError(f"KRX 최근 거래일 조회에 실패했습니다: {exc}. API 키와 해당 서비스 이용 승인 상태를 확인하세요.") from exc
    end = pd.Timestamp(as_of).date()
    trading_days = sorted(provider.trading_days(end - timedelta(days=days), end), reverse=True)
    with db_session(path) as db:
        _upsert_universe(db, provider.stock_universe(as_of), as_of, "stock")
        _upsert_universe(db, provider.etf_universe(as_of), as_of, "etf")
        for idx, ts in enumerate(trading_days, 1):
            day = pd.Timestamp(ts).strftime("%Y-%m-%d")
            started = time.monotonic(); stock_rows = etf_rows = 0
            for kind, fn in (("stock", provider.stock_snapshot), ("etf", provider.etf_snapshot)):
                if _already_done(db, day, kind, force):
                    continue
                try:
                    frame = fn(day.replace("-", ""))
                    if frame.empty:
                        _record_run(db, day, kind, "holiday", 0, int((time.monotonic() - started) * 1000))
                    else:
                        count = _store_bars(db, frame, day)
                        if kind == "stock": stock_rows = count
                        else: etf_rows = count
                        _record_run(db, day, kind, "ok", count, int((time.monotonic() - started) * 1000))
                except Exception as exc:
                    _record_run(db, day, kind, "failed", 0, int((time.monotonic() - started) * 1000), str(exc))
            print(f"[{idx}/{len(trading_days)}] {day} stock={stock_rows} etf={etf_rows} {time.monotonic() - started:.1f}s", file=sys.stderr)
            if on_progress is not None:
                on_progress(idx, len(trading_days), day, stock_rows, etf_rows)
        if not _already_done(db, as_of, "fundamental", force):
            started = time.monotonic()
            try:
                caps = provider.cap_snapshot(as_of); funds = provider.fundamental_snapshot(as_of)
                if "ticker" in caps.columns: caps = caps.set_index("ticker")
                if "ticker" in funds.columns: funds = funds.set_index("ticker")
                merged = caps.join(funds, how="outer", lsuffix="_cap", rsuffix="_fund")
                rows = [(str(ticker).zfill(6), as_of, _num(row.get("bps")), _num(row.get("per")), _num(row.get("pbr")), _num(row.get("eps")), _num(row.get("div")), _num(row.get("dps")), _num(row.get("market_cap")), _num(row.get("shares"))) for ticker, row in merged.iterrows()]
                db.executemany("INSERT INTO snapshots_fundamental(ticker,date,bps,per,pbr,eps,div,dps,market_cap,shares) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker,date) DO UPDATE SET bps=excluded.bps,per=excluded.per,pbr=excluded.pbr,eps=excluded.eps,div=excluded.div,dps=excluded.dps,market_cap=excluded.market_cap,shares=excluded.shares", rows)
                _record_run(db, as_of, "fundamental", "ok", len(rows), int((time.monotonic() - started) * 1000))
            except Exception as exc:
                _record_run(db, as_of, "fundamental", "failed", 0, int((time.monotonic() - started) * 1000), str(exc))
        _seed_presets(db)


def _run_ingest_fdr_stocks(force: bool = False, path=None) -> None:
    started = time.monotonic()
    try:
        day, frame = fdr_stock_snapshot()
    except Exception as exc:
        raise RuntimeError(f"FinanceDataReader에서 종목 스냅샷을 가져오지 못했습니다: {exc}") from exc
    with db_session(path) as db:
        if _already_done(db, day, "stock", force):
            print(f"[fdr] {day} stock 이미 수집됨 (--force로 재수집)", file=sys.stderr)
            return
        known = {row[0] for row in db.execute("SELECT ticker FROM instruments WHERE kind='stock'").fetchall()}
        if not known:
            raise RuntimeError("종목 유니버스가 비어 있습니다. 먼저 KRX 소스로 `mscr ingest`를 한 번 실행하세요.")
        matched = frame[frame["ticker"].isin(known)]
        count = _store_bars(db, matched, day)
        _record_run(db, day, "stock", "ok", count, int((time.monotonic() - started) * 1000))
    print(f"[fdr] {day} stock={count} ({time.monotonic() - started:.1f}s)", file=sys.stderr)
