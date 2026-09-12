"""관심종목 목록과 편입 종목을 관리한다.

목록은 이름으로 구분하고, 편입 시점의 종가(`added_price`)를 함께 남겨 편입 후 성과를 추적한다.
시세는 EOD 스냅샷(`daily_bars.source=f'{bar_source()}'`)의 최근 2개 봉만 읽으므로 목록 크기에
비례하는 인덱스 조회로 끝난다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import market
from .db import db_session
from .market import bar_source

NAME_LIMIT = 60
MEMO_LIMIT = 500
DEFAULT_NAME = "관심종목"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_name(name: Any) -> str:
    cleaned = str(name or "").strip()
    if not 1 <= len(cleaned) <= NAME_LIMIT: raise ValueError(f"목록 이름은 1~{NAME_LIMIT}자여야 합니다")
    return cleaned


def _clean_ticker(ticker: Any) -> str:
    return market.clean_ticker(ticker)


def _clean_memo(memo: Any) -> str | None:
    if memo is None: return None
    cleaned = str(memo).strip()
    if len(cleaned) > MEMO_LIMIT: raise ValueError(f"메모는 {MEMO_LIMIT}자 이하여야 합니다")
    return cleaned or None


def _clean_target(target: Any) -> float | None:
    if target is None or str(target).strip() == "": return None
    try:
        value = float(target)
    except (TypeError, ValueError):
        raise ValueError("목표가가 올바르지 않습니다") from None
    if value <= 0: raise ValueError("목표가는 0보다 커야 합니다")
    return value


def _require_watchlist(db: sqlite3.Connection, watchlist_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM watchlists WHERE id=? AND region=?", (watchlist_id, market.region())).fetchone()
    if row is None: raise ValueError("관심목록을 찾을 수 없습니다")
    return row


def _require_free_name(db: sqlite3.Connection, name: str, watchlist_id: int | None = None) -> None:
    """목록 이름은 시장을 가로질러 유일하다(테이블 UNIQUE 제약). 다른 시장과 부딪히면 그렇다고 알려준다."""
    row = db.execute("SELECT id,region FROM watchlists WHERE name=?", (name,)).fetchone()
    if row is None or row["id"] == watchlist_id: return
    if row["region"] != market.region(): raise ValueError("다른 시장 모드에 같은 이름의 관심목록이 있습니다")
    raise ValueError("같은 이름의 관심목록이 있습니다")


def _default_watchlist_id(db: sqlite3.Connection) -> int:
    mkt = market.active()
    row = db.execute("SELECT id FROM watchlists WHERE region=? ORDER BY id LIMIT 1", (mkt.region,)).fetchone()
    if row: return int(row[0])
    now = _now()
    name = DEFAULT_NAME if mkt.region == market.KR.region else f"{DEFAULT_NAME} ({mkt.label})"
    return int(db.execute("INSERT INTO watchlists(name,region,created_at,updated_at) VALUES(?,?,?,?)", (name, mkt.region, now, now)).lastrowid)


def list_watchlists(ticker: str | None = None, path=None) -> list[dict[str, Any]]:
    """목록과 편입 종목 수를 반환한다. `ticker`를 주면 목록별 편입 여부(`contains`)를 함께 채운다."""
    target = _clean_ticker(ticker) if ticker else None
    with db_session(path) as db:
        rows = db.execute(
            "SELECT w.id,w.name,w.created_at,w.updated_at,"
            "(SELECT COUNT(*) FROM watchlist_items i WHERE i.watchlist_id=w.id) item_count,"
            "(SELECT COUNT(*) FROM watchlist_items i WHERE i.watchlist_id=w.id AND i.ticker=?) hit "
            "FROM watchlists w WHERE w.region=? ORDER BY w.name",
            (target or "", market.region())).fetchall()
    return [{"id": row["id"], "name": row["name"], "created_at": row["created_at"], "updated_at": row["updated_at"], "item_count": row["item_count"], "contains": bool(target and row["hit"])} for row in rows]


def create_watchlist(name: str, path=None) -> dict[str, Any]:
    cleaned = _clean_name(name)
    now = _now()
    with db_session(path) as db:
        _require_free_name(db, cleaned)
        watchlist_id = int(db.execute("INSERT INTO watchlists(name,region,created_at,updated_at) VALUES(?,?,?,?)", (cleaned, market.region(), now, now)).lastrowid)
    return {"id": watchlist_id, "name": cleaned, "created_at": now, "updated_at": now, "item_count": 0, "contains": False}


def rename_watchlist(watchlist_id: int, name: str, path=None) -> dict[str, Any]:
    cleaned = _clean_name(name)
    now = _now()
    with db_session(path) as db:
        _require_watchlist(db, watchlist_id)
        _require_free_name(db, cleaned, watchlist_id)
        db.execute("UPDATE watchlists SET name=?,updated_at=? WHERE id=?", (cleaned, now, watchlist_id))
    return {"id": watchlist_id, "name": cleaned, "updated_at": now}


def delete_watchlist(watchlist_id: int, path=None) -> None:
    with db_session(path) as db:
        _require_watchlist(db, watchlist_id)
        db.execute("DELETE FROM watchlists WHERE id=?", (watchlist_id,))


def save_item(watchlist_id: int | None, ticker: str, memo: Any = None, target_price: Any = None, path=None) -> dict[str, Any]:
    """종목을 목록에 편입하거나 메모·목표가를 갱신한다. 편입 시점 종가와 편입일은 재편입 전까지 유지된다."""
    code = _clean_ticker(ticker)
    note = _clean_memo(memo)
    target = _clean_target(target_price)
    now = _now()
    with db_session(path) as db:
        if not db.execute("SELECT 1 FROM instruments WHERE ticker=? AND region=?", (code, market.region())).fetchone(): raise ValueError("등록되지 않은 종목코드입니다")
        resolved = _default_watchlist_id(db) if watchlist_id is None else int(_require_watchlist(db, watchlist_id)["id"])
        added_price = db.execute(f"SELECT close FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        db.execute(
            "INSERT INTO watchlist_items(watchlist_id,ticker,memo,target_price,added_price,added_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(watchlist_id,ticker) DO UPDATE SET memo=excluded.memo,target_price=excluded.target_price",
            (resolved, code, note, target, added_price[0] if added_price else None, now))
        db.execute("UPDATE watchlists SET updated_at=? WHERE id=?", (now, resolved))
    return {"watchlist_id": resolved, "ticker": code, "memo": note, "target_price": target}


def create_from_tickers(name: str, tickers: list[str], path=None) -> dict[str, Any]:
    """새 목록을 만들고 종목을 한 번에 편입한다. 등록되지 않은 종목코드는 건너뛰고 목록에 남기지 않는다."""
    cleaned = _clean_name(name)
    codes = list(dict.fromkeys(_clean_ticker(ticker) for ticker in tickers))
    if not codes: raise ValueError("담을 종목이 없습니다")
    now = _now()
    with db_session(path) as db:
        _require_free_name(db, cleaned)
        placeholders = ",".join("?" * len(codes))
        known = {row[0] for row in db.execute(f"SELECT ticker FROM instruments WHERE region=? AND ticker IN ({placeholders})", [market.region(), *codes]).fetchall()}
        added = [code for code in codes if code in known]
        if not added: raise ValueError("등록된 종목이 하나도 없어 목록을 만들지 않았습니다")
        prices = {row[0]: row[1] for row in db.execute(
            f"SELECT ticker,(SELECT close FROM daily_bars b WHERE b.ticker=i.ticker AND b.source='{bar_source()}' ORDER BY b.date DESC LIMIT 1) FROM instruments i WHERE i.ticker IN ({placeholders})",
            codes).fetchall()}
        watchlist_id = int(db.execute("INSERT INTO watchlists(name,region,created_at,updated_at) VALUES(?,?,?,?)", (cleaned, market.region(), now, now)).lastrowid)
        db.executemany(
            "INSERT INTO watchlist_items(watchlist_id,ticker,memo,target_price,added_price,added_at) VALUES(?,?,NULL,NULL,?,?)",
            [(watchlist_id, code, prices.get(code), now) for code in added])
    return {"id": watchlist_id, "name": cleaned, "added": len(added), "skipped": [code for code in codes if code not in known]}


def delete_item(watchlist_id: int, ticker: str, path=None) -> None:
    code = _clean_ticker(ticker)
    with db_session(path) as db:
        _require_watchlist(db, watchlist_id)
        if not db.execute("DELETE FROM watchlist_items WHERE watchlist_id=? AND ticker=?", (watchlist_id, code)).rowcount: raise ValueError("편입된 종목을 찾을 수 없습니다")
        db.execute("UPDATE watchlists SET updated_at=? WHERE id=?", (_now(), watchlist_id))


def _clean_tickers(tickers: list[str]) -> list[str]:
    codes = list(dict.fromkeys(_clean_ticker(ticker) for ticker in tickers))
    if not codes: raise ValueError("선택된 종목이 없습니다")
    return codes


def delete_items(watchlist_id: int, tickers: list[str], path=None) -> dict[str, Any]:
    """선택한 종목을 목록에서 뺀다. 목록에 없던 종목은 조용히 무시한다."""
    codes = _clean_tickers(tickers)
    with db_session(path) as db:
        _require_watchlist(db, watchlist_id)
        removed = db.execute(
            f"DELETE FROM watchlist_items WHERE watchlist_id=? AND ticker IN ({','.join('?' * len(codes))})",
            [watchlist_id, *codes]).rowcount
        db.execute("UPDATE watchlists SET updated_at=? WHERE id=?", (_now(), watchlist_id))
    return {"affected": removed, "skipped": []}


def transfer_items(watchlist_id: int, target_id: int, tickers: list[str], keep_source: bool = False, path=None) -> dict[str, Any]:
    """선택한 종목을 다른 목록으로 옮기거나(`keep_source=False`) 복사한다.

    메모·목표가·편입가·편입일을 그대로 가져가므로 복사본도 원본과 같은 기준으로 성과를 본다.
    대상 목록에 이미 있는 종목은 덮어쓰지 않고 건너뛰며, 옮기기에서도 원본을 남긴다."""
    codes = _clean_tickers(tickers)
    if watchlist_id == target_id: raise ValueError("같은 목록으로는 옮기거나 복사할 수 없습니다")
    now = _now()
    with db_session(path) as db:
        _require_watchlist(db, watchlist_id)
        _require_watchlist(db, target_id)
        placeholders = ",".join("?" * len(codes))
        rows = db.execute(
            f"SELECT ticker,memo,target_price,added_price,added_at FROM watchlist_items WHERE watchlist_id=? AND ticker IN ({placeholders})",
            [watchlist_id, *codes]).fetchall()
        taken = {row[0] for row in db.execute(
            f"SELECT ticker FROM watchlist_items WHERE watchlist_id=? AND ticker IN ({placeholders})",
            [target_id, *codes]).fetchall()}
        moved = [dict(row) for row in rows if row["ticker"] not in taken]
        if moved:
            db.executemany(
                "INSERT INTO watchlist_items(watchlist_id,ticker,memo,target_price,added_price,added_at) VALUES(?,?,?,?,?,?)",
                [(target_id, row["ticker"], row["memo"], row["target_price"], row["added_price"], row["added_at"]) for row in moved])
            if not keep_source:
                db.executemany("DELETE FROM watchlist_items WHERE watchlist_id=? AND ticker=?", [(watchlist_id, row["ticker"]) for row in moved])
            db.execute("UPDATE watchlists SET updated_at=? WHERE id IN (?,?)", (now, watchlist_id, target_id))
    return {"affected": len(moved), "skipped": sorted(taken)}


def _percent(numerator: float | None, denominator: float | None) -> float | None:
    if not numerator or not denominator: return None
    return (numerator / denominator - 1) * 100


def snapshot(watchlist_id: int, path=None) -> dict[str, Any]:
    """목록 편입 종목의 최신 시세·목표가 괴리·편입 후 수익률을 계산한다."""
    with db_session(path) as db:
        watchlist = _require_watchlist(db, watchlist_id)
        items = db.execute(
            "SELECT w.ticker,w.memo,w.target_price,w.added_price,w.added_at,i.name,i.kind,i.market,i.delisted "
            "FROM watchlist_items w LEFT JOIN instruments i ON i.ticker=w.ticker WHERE w.watchlist_id=? ORDER BY w.added_at,w.ticker",
            (watchlist_id,)).fetchall()
        rows = []
        for item in items:
            bars = db.execute(f"SELECT date,close,volume,value,halted FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date DESC LIMIT 2", (item["ticker"],)).fetchall()
            fundamental = db.execute("SELECT market_cap,per,pbr FROM snapshots_fundamental WHERE ticker=? ORDER BY date DESC LIMIT 1", (item["ticker"],)).fetchone()
            latest = dict(bars[0]) if bars else {}
            previous = dict(bars[1]) if len(bars) > 1 else {}
            close = latest.get("close")
            rows.append({
                "ticker": item["ticker"], "name": item["name"] or item["ticker"], "kind": item["kind"], "market": item["market"], "delisted": bool(item["delisted"]),
                "memo": item["memo"], "target_price": item["target_price"], "added_price": item["added_price"], "added_at": item["added_at"],
                "as_of": latest.get("date"), "close": close, "volume": latest.get("volume"), "value": latest.get("value"), "halted": bool(latest.get("halted")),
                "change_pct": _percent(close, previous.get("close")),
                "target_gap_pct": _percent(item["target_price"], close),
                "since_added_pct": _percent(close, item["added_price"]),
                "market_cap": fundamental["market_cap"] if fundamental else None,
                "per": fundamental["per"] if fundamental else None,
                "pbr": fundamental["pbr"] if fundamental else None,
                "stale": close is None,
            })
    changes = [row["change_pct"] for row in rows if row["change_pct"] is not None]
    return {
        "id": watchlist["id"], "name": watchlist["name"], "updated_at": watchlist["updated_at"],
        "as_of": max((row["as_of"] for row in rows if row["as_of"]), default=None),
        "rows": rows,
        "summary": {
            "count": len(rows),
            "up": sum(1 for value in changes if value > 0),
            "down": sum(1 for value in changes if value < 0),
            "flat": sum(1 for value in changes if value == 0),
            "avg_change_pct": sum(changes) / len(changes) if changes else None,
            "reached_target": sum(1 for row in rows if row["target_gap_pct"] is not None and row["target_gap_pct"] <= 0),
            "stale": any(row["stale"] for row in rows),
        },
    }
