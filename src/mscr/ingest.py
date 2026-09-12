from __future__ import annotations

import json
import sys
import time
from datetime import timedelta

import pandas as pd

from . import market
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
    "박스 돌파 예정": {"universe": {"kinds": ["stock"], "markets": ["KOSPI", "KOSDAQ"], "exclude_preferred": False, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(value, 20) > 3000000000 and close / rolling_max(high, 60) >= 0.96 and sma(close, 60) > sma(close, 120) and (rolling_max(high, 20) - rolling_min(low, 20)) / close < 0.08 and (rolling_max(high, 20) - rolling_min(low, 20)) / close >= 0.03 and (rolling_max(high, 20) - close) / close <= 0.02 and close >= 1000", "sort": {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}, "limit": 5},
}


# 미국 프리셋은 달러 기준 금액·최저가를 쓰고, 재무 스냅샷(PER·시가총액)을 쓰지 않는다 —
# Massive 무료 플랜에서 전종목 재무를 받으려면 종목마다 개별 호출이 필요해 수집하지 않는다.
US_PRESETS = {
    "거래량 급증 (미국)": {"universe": {"kinds": ["stock", "etf"], "markets": [], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "prior_avg_ratio(volume, 20) >= 3 and value >= 50000000 and close >= 5", "sort": {"formula": "prior_avg_ratio(volume, 20)", "dir": "desc"}, "limit": 500},
    "52주 신고가 근접 (미국)": {"universe": {"kinds": ["stock", "etf"], "markets": [], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "close / rolling_max(high, 250) - 1 >= -0.03 and value >= 30000000", "sort": {"formula": "close / rolling_max(high, 250) - 1", "dir": "desc"}, "limit": 500},
    "골든크로스 (미국)": {"universe": {"kinds": ["stock", "etf"], "markets": [], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "crosses_above(sma(close, 5), sma(close, 20)) and value >= 10000000", "sort": {"formula": "value", "dir": "desc"}, "limit": 500},
    "과매도 반등 후보 (미국)": {"universe": {"kinds": ["stock", "etf"], "markets": [], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "rsi(close, 14) <= 30 and returns(close, 20) <= -0.15 and sma(value, 20) >= 20000000", "sort": {"formula": "rsi(close, 14)", "dir": "asc"}, "limit": 500},
    "미너비니 추세 템플릿 (미국)": {"universe": {"kinds": ["stock"], "markets": [], "exclude_preferred": True, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(close, 20) > sma(close, 60) and returns(close, 120) >= 0 and returns(close, 250) >= 0 and close / rolling_min(low, 250) - 1 >= 0.3 and close / rolling_max(high, 250) - 1 >= -0.25 and value >= 10000000 and close >= 5", "sort": {"formula": "returns(close, 120)", "dir": "desc"}, "limit": 500},
    "3R 목표 후보 (미국)": {"universe": {"kinds": ["stock"], "markets": [], "exclude_preferred": False, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(value, 20) > 30000000 and close / rolling_max(high, 60) >= 0.96 and sma(close, 60) > sma(close, 120) and close >= 5", "sort": {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}, "limit": 5},
    "박스 조임 후보 (미국)": {"universe": {"kinds": ["stock"], "markets": [], "exclude_preferred": False, "exclude_spac": True, "exclude_halted": True, "min_bars": 250}, "formula": "sma(value, 20) > 30000000 and close / rolling_max(high, 60) >= 0.96 and sma(close, 60) > sma(close, 120) and (rolling_max(high, 20) - rolling_min(low, 20)) / close < 0.08 and (rolling_max(high, 20) - rolling_min(low, 20)) / close >= 0.03 and close >= 5", "sort": {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}, "limit": 5},
}


def _text(value):
    return None if pd.isna(value) else str(value)


def _num(value):
    return None if pd.isna(value) else float(value)


def _today():
    return pd.Timestamp.now().date()


def _normalize_ticker(value, mkt) -> str:
    """KRX는 6자리 숫자로 0을 채우고, 미국은 대문자 티커를 그대로 쓴다."""
    text = str(value or "").strip()
    return text.zfill(6) if mkt.region == market.KR.region else text.upper()


def _flag(row, key: str, fallback: int) -> int:
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    return int(value)


def _upsert_universe(db, frame: pd.DataFrame, as_of: str, kind: str | None, mkt) -> None:
    """`kind`가 None이면 프레임의 kind 컬럼을 쓴다(미국 유니버스는 주식·ETF가 한 응답에 섞여 온다)."""
    if frame.empty:
        return
    rows = []
    for row in frame.to_dict("records"):
        ticker = _normalize_ticker(row.get("ticker"), mkt)
        name = _text(row.get("name")) or ticker
        row_kind = kind or str(row.get("kind") or "stock")
        rows.append((
            ticker, name, row_kind, mkt.region, _text(row.get("market")), _text(row.get("category")), _text(row.get("base_index")),
            _flag(row, "is_preferred", int(mkt.region == market.KR.region and row_kind == "stock" and not ticker.endswith("0"))),
            _flag(row, "is_spac", int("스팩" in name)), as_of, as_of,
        ))
    db.executemany("""
      INSERT INTO instruments(ticker,name,kind,region,market,category,base_index,is_preferred,is_spac,first_seen,last_seen,delisted)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,0)
      ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, kind=excluded.kind, region=excluded.region, market=COALESCE(excluded.market,instruments.market), category=COALESCE(excluded.category,instruments.category), base_index=COALESCE(excluded.base_index,instruments.base_index), is_preferred=excluded.is_preferred, is_spac=excluded.is_spac, last_seen=excluded.last_seen, delisted=0
    """, rows)


def _mark_delisted(db, as_of: str, kinds, region: str) -> None:
    """이번 유니버스 응답에 없던 종목은 상장폐지로 표시한다. 다른 시장의 종목은 건드리지 않는다."""
    for kind in kinds:
        db.execute("UPDATE instruments SET delisted=1 WHERE last_seen < ? AND kind=? AND region=?", (as_of, kind, region))


def _store_bars(db, frame: pd.DataFrame, day: str, mkt=None) -> int:
    mkt = mkt or market.active()
    if frame.empty:
        return 0
    rows = []
    for row in frame.to_dict("records"):
        ticker = _normalize_ticker(row.get("ticker"), mkt)
        close = _num(row.get("close")); open_ = _num(row.get("open")); high = _num(row.get("high")); low = _num(row.get("low")); volume = _num(row.get("volume")); value = _num(row.get("value"))
        halted = int(close is not None and close > 0 and open_ == high == low == volume == 0)
        rows.append((ticker, day, mkt.bar_source, open_, high, low, close, volume, value, _num(row.get("nav")), halted))
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


def _seed_presets(db, mkt=None) -> None:
    mkt = mkt or market.active()
    presets = US_PRESETS if mkt.region == market.US.region else PRESETS
    now = pd.Timestamp.now().isoformat(timespec="seconds")
    for name, spec in presets.items():
        db.execute("INSERT INTO screens(name,region,spec,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(name) DO NOTHING", (name, mkt.region, json.dumps(spec, ensure_ascii=False), now, now))


def latest_trading_day(provider=None, mkt=None) -> str:
    """전체 ingest 없이 데이터 제공처가 실제로 발표한 최신 거래일만 조회한다."""
    mkt = mkt or market.active()
    if mkt.region == market.US.region:
        from .providers.massive import MassiveProvider

        try:
            return (provider or MassiveProvider()).latest_trading_day()
        except Exception as exc:
            raise RuntimeError(f"Massive 최근 거래일 조회에 실패했습니다: {exc}") from exc
    provider = provider or KRXProvider()
    try:
        as_of = provider.latest_trading_day()
    except Exception as exc:
        raise RuntimeError(f"KRX 최근 거래일 조회에 실패했습니다: {exc}. API 키와 해당 서비스 이용 승인 상태를 확인하세요.") from exc
    return pd.Timestamp(as_of).strftime("%Y-%m-%d")

def run_ingest(days: int = 400, force: bool = False, provider=None, source: str | None = None, path=None, on_progress=None, mkt=None) -> None:
    """`on_progress`는 각 거래일 처리 후 (idx, total, day, stock_rows, etf_rows)로 호출된다. 웹 UI가 백그라운드 진행 상황을 폴링하는 데 쓴다."""
    mkt = mkt or market.active()
    source = source or mkt.default_ingest_source
    if source not in mkt.ingest_sources:
        raise ValueError(f"{mkt.label} 주식 모드에서 지원하지 않는 소스입니다: {source} (가능: {', '.join(mkt.ingest_sources)})")
    if mkt.region == market.US.region:
        _run_ingest_massive(days=days, force=force, provider=provider, path=path, on_progress=on_progress)
        return
    if source == "fdr":
        _run_ingest_fdr_stocks(force=force, path=path)
        return
    if source == "alphasquare":
        _run_ingest_alphasquare(days=days, force=force, path=path)
        return
    provider = provider or KRXProvider()
    try:
        as_of = provider.latest_trading_day()
    except Exception as exc:
        raise RuntimeError(f"KRX 최근 거래일 조회에 실패했습니다: {exc}. API 키와 해당 서비스 이용 승인 상태를 확인하세요.") from exc
    end = pd.Timestamp(as_of).date()
    # trading_days는 양 끝을 포함하므로 days를 그대로 빼면 창이 days+1 캘린더일이 된다.
    # days=1이 "가장 최근 거래일 하루"가 되도록 하나 적게 뺀다.
    trading_days = sorted(provider.trading_days(end - timedelta(days=days - 1), end), reverse=True)
    with db_session(path) as db:
        _upsert_universe(db, provider.stock_universe(as_of), as_of, "stock", market.KR)
        _upsert_universe(db, provider.etf_universe(as_of), as_of, "etf", market.KR)
        _mark_delisted(db, as_of, ("stock", "etf"), market.KR.region)
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
                        count = _store_bars(db, frame, day, market.KR)
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
        _seed_presets(db, market.KR)


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
        count = _store_bars(db, matched, day, market.KR)
        _record_run(db, day, "stock", "ok", count, int((time.monotonic() - started) * 1000))
    print(f"[fdr] {day} stock={count} ({time.monotonic() - started:.1f}s)", file=sys.stderr)


def _run_ingest_alphasquare(days: int = 400, force: bool = False, path=None) -> None:
    """alpha-square를 로컬 유니버스 티커별로 하나씩 호출해 최근 `days`일의 시세 공백을 채운다.

    전종목을 한 번에 반환하는 벌크 엔드포인트는 없지만, 종목 하나당 날짜 구간은 한 번에
    받아올 수 있어 다른 소스와 동일하게 --days로 수집 기간을 지정한다. KRX/pykrx/FDR이
    모두 비었을 때만 쓰는 최후 폴백으로 의도했다.
    """
    from .providers.alphasquare import AlphaSquareProvider

    with db_session(path) as db:
        # 로컬에 이미 저장된 최신 거래일이 아니라 오늘 날짜를 기준으로 삼는다. KRX가 아직
        # 오늘자를 게시하지 않아 로컬 최신일이 하루 이상 뒤처진 상태가 alpha-square를 쓰는
        # 바로 그 상황이므로, 로컬 최신일을 기준으로 삼으면 정작 필요한 최신 날짜를 영영
        # 요청하지 못한다.
        end = _today()
        # bdate_range는 양 끝을 포함하므로 days를 그대로 빼면 창이 days+1 캘린더일이 된다.
        # days=1이 "오늘 하루"가 되도록 하나 적게 뺀다.
        start = end - timedelta(days=days - 1)
        all_days = [ts.strftime("%Y-%m-%d") for ts in pd.bdate_range(start, end)]
        provider = AlphaSquareProvider(db)
        for kind in ("stock", "etf"):
            target_days = [day for day in all_days if not _already_done(db, day, kind, force)]
            if not target_days:
                print(f"[alphasquare] {kind} 이미 모두 수집됨 (--force로 재수집)", file=sys.stderr)
                continue
            tickers = [row[0] for row in db.execute("SELECT ticker FROM instruments WHERE kind=? AND delisted=0", (kind,)).fetchall()]
            if not tickers:
                print(f"[alphasquare] {kind} 유니버스가 비어 있어 건너뜁니다", file=sys.stderr)
                continue
            started = time.monotonic()
            frame = provider.history(tickers, target_days[0], target_days[-1])
            counts: dict[str, int] = {}
            if not frame.empty:
                for day, group in frame[frame["date"].isin(target_days)].groupby("date"):
                    counts[day] = _store_bars(db, group.drop(columns="date"), day, market.KR)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            for day in target_days:
                count = counts.get(day, 0)
                _record_run(db, day, kind, "ok" if count else "holiday", count, elapsed_ms)
            print(f"[alphasquare] {kind} {target_days[0]}~{target_days[-1]} {sum(counts.values())}행 ({time.monotonic() - started:.1f}s)", file=sys.stderr)


def _run_ingest_massive(days: int = 30, force: bool = False, provider=None, path=None, on_progress=None) -> None:
    """Massive의 "일별 시장 요약"으로 거래일마다 미국 전종목 일봉을 한 번씩 받아 채운다.

    무료 플랜은 분당 5회 제한이라 호출 간 지연이 길다(기본 12초). `--days`를 늘릴수록 그만큼
    오래 걸리므로, 매일 돌릴 때는 최근 며칠만 확인하는 쪽이 낫다.
    """
    from .providers.massive import MassiveError, MassiveProvider

    mkt = market.US
    bars_kind, universe_kind = mkt.ingest_kinds
    try:
        provider = provider or MassiveProvider()
        as_of = provider.latest_trading_day()
    except MassiveError as exc:
        raise RuntimeError(str(exc)) from exc
    end = pd.Timestamp(as_of).date()
    # trading_days는 양 끝을 포함한다. days=1이 "가장 최근 거래일 하루"가 되도록 하나 적게 뺀다.
    trading_days = sorted(provider.trading_days(end - timedelta(days=days - 1), end), reverse=True)
    with db_session(path) as db:
        if not _already_done(db, as_of, universe_kind, force):
            started = time.monotonic()
            try:
                universe = provider.universe()
                _upsert_universe(db, universe, as_of, None, mkt)
                _mark_delisted(db, as_of, ("stock", "etf"), mkt.region)
                _record_run(db, as_of, universe_kind, "ok", len(universe), int((time.monotonic() - started) * 1000))
                print(f"[massive] universe {len(universe)}종목 ({time.monotonic() - started:.1f}s)", file=sys.stderr)
            except Exception as exc:
                _record_run(db, as_of, universe_kind, "failed", 0, int((time.monotonic() - started) * 1000), str(exc))
                raise RuntimeError(f"Massive 종목 목록 조회에 실패했습니다: {exc}") from exc
        for idx, ts in enumerate(trading_days, 1):
            day = pd.Timestamp(ts).strftime("%Y-%m-%d")
            if _already_done(db, day, bars_kind, force):
                if on_progress is not None:
                    on_progress(idx, len(trading_days), day, 0, 0)
                continue
            started = time.monotonic(); count = 0
            try:
                frame = provider.daily_snapshot(day)
                if frame.empty:
                    _record_run(db, day, bars_kind, "holiday", 0, int((time.monotonic() - started) * 1000))
                else:
                    count = _store_bars(db, frame, day, mkt)
                    _record_run(db, day, bars_kind, "ok", count, int((time.monotonic() - started) * 1000))
            except Exception as exc:
                _record_run(db, day, bars_kind, "failed", 0, int((time.monotonic() - started) * 1000), str(exc))
            print(f"[massive {idx}/{len(trading_days)}] {day} bars={count} {time.monotonic() - started:.1f}s", file=sys.stderr)
            if on_progress is not None:
                on_progress(idx, len(trading_days), day, count, 0)
        _seed_presets(db, mkt)
