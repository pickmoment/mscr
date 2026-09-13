"""기계가 읽는 조회·편집 계층 — `mscr query …` CLI와 AI 에이전트 스킬이 쓴다.

화면(FastAPI)과 CLI가 같은 라이브러리 함수를 보게 두고, 여기서는 JSON으로 내보낼 수 있는
형태로만 다듬는다. 서버를 띄우지 않아도 되고, 출력이 사람이 읽는 표가 아니라 항상 하나의
JSON 객체라 파싱이 필요 없다.

모든 함수는 현재 시장 모드(`market.active()`)를 따른다 — 호출부가 `--market`으로 정한다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from . import market, market_stats, portfolio, signals, watchlist
from .config import DB_PATH, MSCR_HOME, SCHEMA_VERSION
from .db import db_session
from .dynamic import BUILTIN_CATALOG, BUILTIN_FUNCTIONS, SCALAR_NAMES, SCREEN_NAMES, SERIES_NAMES, custom_definitions, ticker_snapshot, validate_formula
from .market import bar_source
from .screener import FIELDS

# 에이전트가 한 번에 삼키기 좋은 크기. --limit 으로 올릴 수 있다.
DEFAULT_SCREEN_LIMIT = 20
DEFAULT_BARS = 120
SCREEN_COLUMNS = ("ticker", "name", "kind", "market", "close", "change_pct", "value", "market_cap", "bars_available", "_sort")


def clean(value: Any) -> Any:
    """numpy·pandas 타입과 NaN을 JSON이 받는 값으로 낮춘다. NaN·inf는 null이 된다."""
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def dumps(payload: Any) -> str:
    return json.dumps(clean(payload), ensure_ascii=False)


# --- 상태·카탈로그 -------------------------------------------------------

def status() -> dict[str, Any]:
    """지금 어느 시장을 보고 있고 데이터가 어디까지 쌓였는지. 다른 조회 전에 먼저 본다."""
    from .broker.kis import broker_status
    from .providers.krx import krx_status
    from .providers.massive import massive_status

    mkt = market.active()
    with db_session() as db:
        counts = {row["kind"]: row["n"] for row in db.execute("SELECT kind,COUNT(*) n FROM instruments WHERE region=? AND delisted=0 GROUP BY kind", (mkt.region,))}
        bars = db.execute("SELECT COUNT(*) FROM daily_bars WHERE source=?", (mkt.bar_source,)).fetchone()[0]
        as_of = db.execute("SELECT MAX(date) FROM daily_bars WHERE source=?", (mkt.bar_source,)).fetchone()[0]
        days = db.execute("SELECT COUNT(DISTINCT date) FROM daily_bars WHERE source=?", (mkt.bar_source,)).fetchone()[0]
        placeholders = ",".join("?" * len(mkt.ingest_kinds))
        last_ingest = db.execute(f"SELECT MAX(ran_at) FROM ingest_runs WHERE status='ok' AND kind IN ({placeholders})", mkt.ingest_kinds).fetchone()[0]
        failures = [dict(row) for row in db.execute(
            f"SELECT date,kind,error FROM ingest_runs WHERE status='failed' AND kind IN ({placeholders}) ORDER BY date DESC LIMIT 5", mkt.ingest_kinds)]
        version = db.execute("PRAGMA user_version").fetchone()[0]
    credentials = {"massive": massive_status()["mode"]} if mkt.region == market.US.region else {"krx": krx_status()["mode"], "kis": "enabled" if broker_status()["enabled"] else "disabled"}
    return {
        "market": mkt.as_dict(), "as_of": as_of, "trading_days": days, "bars_rows": bars,
        "instruments": {"stock": counts.get("stock", 0), "etf": counts.get("etf", 0)},
        "last_ingest_at": last_ingest, "recent_failures": failures,
        "credentials": credentials, "home": str(MSCR_HOME), "db": str(DB_PATH),
        "schema_version": version, "expected_schema_version": SCHEMA_VERSION,
    }


def markets() -> dict[str, Any]:
    return {"active": market.active().key, "default": market.stored_default(), "markets": [item.as_dict() for item in market.MARKETS.values()]}


def fields() -> dict[str, Any]:
    """스크린 수식에 쓸 수 있는 이름 전부. 수식을 만들기 전에 한 번 읽는다."""
    return {
        "series": sorted(SERIES_NAMES),
        "scalars": sorted(SCALAR_NAMES),
        "labels": {key: {"label": spec.label_ko, "unit": spec.unit, "kind": spec.kind} for key, spec in FIELDS.items()},
        "functions": BUILTIN_CATALOG,
        "custom": [{"key": item["key"], "label": item["label"], "formula": item["formula"],
                    "parameters": [parameter["name"] for parameter in item["parameters"]]} for item in custom_definitions()],
        "note": "시리즈는 마지막 봉 값으로, 스칼라는 그대로 평가된다. and/or/not 과 비교식을 조합한다.",
    }


# --- 종목 ---------------------------------------------------------------

def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    needle = str(query or "").strip()
    if not needle:
        return []
    like, prefix = f"%{needle}%", f"{needle}%"
    with db_session() as db:
        rows = db.execute(
            "SELECT ticker,name,kind,market FROM instruments "
            "WHERE region=? AND delisted=0 AND (ticker LIKE ? OR name LIKE ?) "
            "ORDER BY CASE WHEN ticker=? THEN 0 WHEN name=? THEN 1 WHEN ticker LIKE ? THEN 2 WHEN name LIKE ? THEN 3 ELSE 4 END, name LIMIT ?",
            (market.region(), like, like, needle, needle, prefix, prefix, max(1, min(int(limit), 50)))).fetchall()
    return [dict(row) for row in rows]


def quote(ticker: str) -> dict[str, Any]:
    """한 종목의 최신 시세·재무·계산 지표. 외부 API는 건드리지 않고 로컬 DB만 읽는다."""
    code = market.clean_ticker(ticker)
    with db_session() as db:
        item = db.execute("SELECT * FROM instruments WHERE ticker=? AND region=?", (code, market.region())).fetchone()
        if not item:
            raise ValueError(f"등록되지 않은 종목입니다: {code} (현재 시장: {market.active().label})")
        item = dict(item)
        bar = db.execute(f"SELECT * FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        fundamental = db.execute("SELECT * FROM snapshots_fundamental WHERE ticker=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    bar = dict(bar) if bar else {}
    fundamental = dict(fundamental) if fundamental else {}
    metric = ticker_snapshot(code)
    return {
        "ticker": code, "name": item["name"], "kind": item["kind"], "market": item["market"],
        "currency": market.active().currency, "delisted": bool(item["delisted"]),
        "as_of": metric.get("as_of") or bar.get("date"),
        "quote": {key: bar.get(key) for key in ("open", "high", "low", "close", "volume", "value", "nav")}
                 | {"change_pct": metric.get("change_pct"), "weighted_return": metric.get("weighted_return"), "halted": bool(bar.get("halted"))},
        "fundamental": {key: fundamental.get(key) for key in ("market_cap", "shares", "per", "pbr", "eps", "bps", "div", "dps")},
        "bars_available": metric.get("bars_available", 0),
        "position": next((row for row in portfolio.snapshot()["positions"] if row["ticker"] == code), None),
    }


def bars(ticker: str, days: int = DEFAULT_BARS) -> dict[str, Any]:
    """최근 일봉 OHLCV. 지표는 붙이지 않는다 — 필요하면 screen 수식으로 계산하는 쪽이 싸다."""
    code = market.clean_ticker(ticker)
    with db_session() as db:
        rows = db.execute(
            f"SELECT date,open,high,low,close,volume,value,halted FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date DESC LIMIT ?",
            (code, max(1, min(int(days), 2000)))).fetchall()
    return {"ticker": code, "currency": market.active().currency, "count": len(rows), "bars": [dict(row) for row in reversed(rows)]}


# --- 스크리너 -----------------------------------------------------------

def build_spec(formula: str, sort: str | None = None, direction: str = "desc", kinds: list[str] | None = None,
               markets_filter: list[str] | None = None, limit: int = DEFAULT_SCREEN_LIMIT, min_bars: int = 250,
               exclude_preferred: bool = True, exclude_spac: bool = True, exclude_halted: bool = True,
               as_of_offset: int = 0) -> dict[str, Any]:
    return {
        "universe": {
            "kinds": kinds or ["stock"], "markets": markets_filter or [],
            "exclude_preferred": exclude_preferred, "exclude_spac": exclude_spac,
            "exclude_halted": exclude_halted, "min_bars": int(min_bars),
        },
        "formula": formula,
        "sort": {"formula": sort or "value", "dir": direction},
        "limit": int(limit), "as_of_offset": int(as_of_offset),
    }


def screen(spec: dict[str, Any], columns: list[str] | None = None) -> dict[str, Any]:
    """스크린 수식을 현재 시장 전 종목에 돌린다. 유니버스가 크면 수십 초가 걸릴 수 있다."""
    from .screener import run

    rows = run(spec)
    keys = list(columns) if columns else list(SCREEN_COLUMNS)
    trimmed = [{key: row.get(key) for key in keys} for row in rows] if keys else rows
    return {"market": market.active().key, "as_of": rows[0].get("as_of") if rows else None,
            "count": len(rows), "spec": spec, "rows": trimmed}


def presets() -> list[dict[str, Any]]:
    with db_session() as db:
        return [{"id": row["id"], "name": row["name"], "updated_at": row["updated_at"], "spec": json.loads(row["spec"])}
                for row in db.execute("SELECT id,name,spec,updated_at FROM screens WHERE region=? ORDER BY name", (market.region(),))]


def preset_spec(name: str) -> dict[str, Any]:
    match = next((item for item in presets() if item["name"] == name), None)
    if match is None:
        raise ValueError(f"프리셋을 찾을 수 없습니다: {name} (현재 시장: {market.active().label})")
    return match["spec"]


def save_preset(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """프리셋을 저장하거나 덮어쓴다. 이름은 두 시장을 통틀어 유일해야 한다."""
    functions = BUILTIN_FUNCTIONS | {item["key"] for item in custom_definitions(enabled_only=False)}
    validate_formula(spec["formula"], SCREEN_NAMES, functions)
    validate_formula(str((spec.get("sort") or {}).get("formula", "close")), SCREEN_NAMES, functions)
    now = datetime.now().isoformat(timespec="seconds")
    with db_session() as db:
        clash = db.execute("SELECT id,region FROM screens WHERE name=?", (name,)).fetchone()
        if clash and clash["region"] != market.region():
            raise ValueError("다른 시장 모드에 같은 이름의 프리셋이 있습니다")
        db.execute("INSERT INTO screens(name,region,spec,created_at,updated_at) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET spec=excluded.spec,updated_at=excluded.updated_at",
                   (name, market.region(), json.dumps(spec, ensure_ascii=False), now, now))
        row = db.execute("SELECT id FROM screens WHERE name=?", (name,)).fetchone()
    return {"id": row[0], "name": name, "region": market.region(), "updated_at": now, "spec": spec}


def delete_preset(name: str) -> dict[str, Any]:
    with db_session() as db:
        removed = db.execute("DELETE FROM screens WHERE name=? AND region=?", (name, market.region())).rowcount
    if not removed:
        raise ValueError(f"프리셋을 찾을 수 없습니다: {name}")
    return {"deleted": name}


# --- 시장·브리핑·신호 ----------------------------------------------------

def stats(date: str | None = None) -> dict[str, Any]:
    dates = market_stats.available_dates()
    if not dates:
        raise ValueError("수집된 시세 데이터가 없습니다. 먼저 `mscr ingest`를 실행하세요.")
    return market_stats.compute(date or dates[-1])


def brief(date: str | None = None) -> dict[str, Any]:
    from . import brief as brief_module

    return brief_module.build(date)


def signal_history(ticker: str, limit: int = 20) -> list[dict[str, Any]]:
    return signals.signal_history(market.clean_ticker(ticker), limit)


def signal_diff(name: str, date: str | None = None) -> dict[str, Any]:
    preset = next((item for item in presets() if item["name"] == name), None)
    if preset is None:
        raise ValueError(f"프리셋을 찾을 수 없습니다: {name}")
    result = signals.diff(preset["id"], date)
    return {"preset": name} | result


# --- 관심종목·포트폴리오 --------------------------------------------------

def watchlists() -> list[dict[str, Any]]:
    return watchlist.list_watchlists()


def _watchlist_id(name: str | None) -> int | None:
    if name is None:
        return None
    for item in watchlist.list_watchlists():
        if item["name"] == name or str(item["id"]) == str(name):
            return int(item["id"])
    raise ValueError(f"관심목록을 찾을 수 없습니다: {name}")


def watchlist_detail(name: str | None = None) -> dict[str, Any]:
    lists = watchlist.list_watchlists()
    if not lists:
        return {"id": None, "name": None, "rows": [], "summary": {"count": 0}}
    return watchlist.snapshot(_watchlist_id(name) if name else int(lists[0]["id"]))


def watchlist_create(name: str) -> dict[str, Any]:
    return watchlist.create_watchlist(name)


def watchlist_add(ticker: str, name: str | None = None, memo: str | None = None, target_price: float | None = None) -> dict[str, Any]:
    return watchlist.save_item(_watchlist_id(name), ticker, memo=memo, target_price=target_price)


def watchlist_remove(ticker: str, name: str | None = None) -> dict[str, Any]:
    resolved = _watchlist_id(name)
    if resolved is None:
        lists = watchlist.list_watchlists()
        if not lists:
            raise ValueError("관심목록이 없습니다")
        resolved = int(lists[0]["id"])
    watchlist.delete_item(resolved, ticker)
    return {"watchlist_id": resolved, "removed": market.clean_ticker(ticker)}


def positions() -> dict[str, Any]:
    return portfolio.snapshot()
