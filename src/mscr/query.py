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

from . import chartread, market, market_stats, portfolio, signals, watchlist
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


BAR_COLUMNS = "date,open,high,low,close,volume,value,halted"
NOT_NULL = "open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL"


def _local_bars(db, code: str, needed: int) -> tuple[list[dict[str, Any]], bool]:
    """화면 차트와 같은 가격 계열을 고른다 — 한국은 캐시된 수정주가가 창을 덮을 때만 그걸 쓴다.

    `source='adjusted'` 행은 사용자가 화면에서 그 종목 차트를 연 적이 있을 때만 쌓인다. 여기서는
    KRX를 새로 호출하지 않고 있는 것만 본다 — 조회 명령이 조용히 외부 API를 때리면 안 된다.
    미국(Massive) 일봉은 이미 수정주가라 스냅샷이 곧 수정주가다.
    """
    snapshot = [dict(row) for row in db.execute(
        f"SELECT {BAR_COLUMNS} FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date DESC LIMIT ?",
        (code, needed)).fetchall()]
    if market.active().region != market.KR.region:
        return snapshot, bool(snapshot)
    adjusted = [dict(row) for row in db.execute(
        f"SELECT {BAR_COLUMNS} FROM daily_bars WHERE ticker=? AND source='adjusted' AND {NOT_NULL} ORDER BY date DESC LIMIT ?",
        (code, needed)).fetchall()]
    if adjusted and snapshot and len(adjusted) >= len(snapshot) and adjusted[0]["date"] == snapshot[0]["date"]:
        return adjusted, True
    return snapshot, False


def _alphasquare_bars(db, code: str, freq: str, needed: int) -> pd.DataFrame:
    """alpha-square 비공식 API에서 캔들을 직접 받는다 — 화면의 `실시간` 탭과 같은 경로다.

    로컬 일봉과 다른 점이 셋이다. (1) 장중인 오늘 봉이 들어온다, (2) 분봉을 볼 수 있다,
    (3) **수정주가가 아니다**. 셋째 때문에 분할·병합 지점이 그대로 절벽으로 남는데, 화면은 사람이
    그 절벽을 눈으로 보고 걸러 낸다. 말로 옮길 때는 그럴 수 없으므로 로컬과 같은 전처리를 걸어
    단절 이전을 잘라내고 `price_jump_flag`로 알린다.
    """
    from .providers.alphasquare import CANDLE_BARS_MAX, CANDLE_FREQS, AlphaSquareProvider

    if freq not in CANDLE_FREQS:
        raise ValueError(f"지원하지 않는 주기입니다: {freq} (가능: {', '.join(CANDLE_FREQS)})")
    frame = AlphaSquareProvider(db).candles(code, freq, count=min(needed, CANDLE_BARS_MAX))
    if frame.empty:
        raise ValueError(f"alpha-square에서 캔들을 받지 못했습니다: {code} ({freq})")
    if freq != "day":
        # 분봉 시각은 장 시간대 벽시계를 UTC epoch초로 인코딩해 온다(화면이 그대로 읽게). 되돌린다.
        frame = frame.assign(date=pd.to_datetime(frame["date"], unit="s").dt.strftime("%Y-%m-%d %H:%M"))
    return frame.assign(value=frame["close"] * frame["volume"], halted=0)


def chart(ticker: str, window: int = chartread.DEFAULT_WINDOW, source: str = "local", freq: str = "day",
          swing_factor: float = chartread.SWING_FACTOR, with_sketch: bool = False) -> dict[str, Any]:
    """차트를 그리지 않고 설명할 수 있게 만든 구조 요약 — 구간·스윙·수평선·사건.

    같은 250봉을 `bars`로 주면 40KB인데 여기서는 4KB 안팎이고, 줄어든 대신 "3개월 횡보 뒤
    6월부터 상승" 같은 시간 축 정보가 남는다. 무엇을 읽을지는 `chartread`의 정의가 정한다 —
    정의하지 않은 모양은 여기 나오지 않는다.

    `source`는 화면 차트의 `로컬`/`실시간` 토글과 같다. `local`은 로컬 DB 일봉(EOD, 오프라인),
    `alphasquare`는 비공식 API로 분봉까지 보되 네트워크를 탄다.
    """
    code = market.clean_ticker(ticker)
    if source not in {"local", "alphasquare"}:
        raise ValueError(f"지원하지 않는 원천입니다: {source} (가능: local, alphasquare)")
    needed = max(1, int(window)) + chartread.WARMUP
    with db_session() as db:
        item = db.execute("SELECT name,kind,market FROM instruments WHERE ticker=? AND region=?", (code, market.region())).fetchone()
        if not item:
            raise ValueError(f"등록되지 않은 종목입니다: {code} (현재 시장: {market.active().label})")
        if source == "alphasquare":
            frame, adjusted = _alphasquare_bars(db, code, freq, needed), False
        else:
            rows, adjusted = _local_bars(db, code, needed)
            if not rows:
                raise ValueError(f"일봉이 없습니다: {code} — 먼저 `mscr ingest`로 수집하세요.")
            frame = pd.DataFrame(list(reversed(rows)))
    return {"ticker": code, "name": item["name"], "kind": item["kind"], "market": item["market"],
            "currency": market.active().currency, "source": source,
            "freq": freq if source == "alphasquare" else "day", "adjusted": adjusted} | chartread.read(
        frame, window=int(window), swing_factor=float(swing_factor), with_sketch=with_sketch)


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
