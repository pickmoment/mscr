from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Iterable

from .db import db_session
from .dynamic import evaluate_context, screen_context

JOB_NAME = "signals"
STREAK_LOOKBACK = 60


def start_job(screen_ids: list[int] | None = None, days: int = 1, force: bool = False) -> dict[str, Any]:
    """신호 로그 적재를 백그라운드로 돌린다. 날짜마다 전 유니버스를 다시 계산하므로 요청 스레드에서
    직접 돌리면 응답이 막힌다."""
    from . import jobs

    return jobs.start(JOB_NAME, lambda progress: capture(screen_ids=screen_ids, offsets=range(days), force=force, on_progress=progress), {"days": days, "force": force})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def trading_days(path=None) -> list[str]:
    with db_session(path) as db:
        return [row[0] for row in db.execute("SELECT DISTINCT date FROM daily_bars WHERE source='krx_snapshot' ORDER BY date").fetchall()]


def _screen_rows(db, screen_ids: list[int] | None) -> list[dict[str, Any]]:
    rows = db.execute("SELECT id,name,spec FROM screens ORDER BY name").fetchall()
    wanted = {int(value) for value in screen_ids} if screen_ids else None
    return [{"id": row["id"], "name": row["name"], "spec": json.loads(row["spec"])} for row in rows if wanted is None or row["id"] in wanted]


def capture(screen_ids: list[int] | None = None, offsets: Iterable[int] = (0,), force: bool = False, path=None,
            on_progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    """저장된 프리셋을 기준일별로 실행해 신호를 남긴다.

    `offsets`는 최신 거래일에서 거슬러 올라간 거래일 수다(0이면 최신일). 이미 수집한 (프리셋, 날짜)는
    `force`가 아니면 건너뛰므로 매일 실행해도 새 날짜만 계산한다. 신호가 0건인 날도 `screen_runs`에
    남겨 '후보 없음'과 '수집 안 함'을 구분한다."""
    days = trading_days(path)
    if not days:
        return {"screens": 0, "dates": 0, "rows": 0, "skipped": 0}
    wanted: list[tuple[int, str]] = []
    for offset in sorted({int(value) for value in offsets}):
        if 0 <= offset < len(days):
            wanted.append((offset, days[-1 - offset]))
    with db_session(path) as db:
        screens = _screen_rows(db, screen_ids)
        done = {(row["screen_id"], row["date"]) for row in db.execute("SELECT screen_id,date FROM screen_runs").fetchall()}
    todo = [(screen, [(offset, date) for offset, date in wanted if force or (screen["id"], date) not in done]) for screen in screens]
    total = sum(len(items) for _, items in todo)
    skipped = len(screens) * len(wanted) - total
    processed = rows_written = 0
    for screen, items in todo:
        if not items:
            continue
        context = screen_context(screen["spec"], path)
        for offset, date in items:
            matched = evaluate_context(context, offset)
            with db_session(path) as db:
                db.execute("DELETE FROM screen_signals WHERE screen_id=? AND date=?", (screen["id"], date))
                db.executemany(
                    "INSERT INTO screen_signals(screen_id,date,ticker,rank,score,close) VALUES(?,?,?,?,?,?)",
                    [(screen["id"], date, row["ticker"], index, row.get("_sort"), row.get("close")) for index, row in enumerate(matched, start=1)])
                db.execute("INSERT INTO screen_runs(screen_id,date,matched,ran_at) VALUES(?,?,?,?) ON CONFLICT(screen_id,date) DO UPDATE SET matched=excluded.matched, ran_at=excluded.ran_at",
                           (screen["id"], date, len(matched), _now()))
            rows_written += len(matched)
            processed += 1
            if on_progress:
                on_progress(processed, total, f"{screen['name']} {date}")
    return {"screens": len({screen["id"] for screen, items in todo if items}), "dates": processed, "rows": rows_written, "skipped": skipped}


def captured_dates(screen_id: int, path=None) -> list[str]:
    with db_session(path) as db:
        return [row[0] for row in db.execute("SELECT date FROM screen_runs WHERE screen_id=? ORDER BY date", (int(screen_id),)).fetchall()]


def coverage(path=None) -> list[dict[str, Any]]:
    """프리셋별 신호 로그 적재 상태. 로그가 없는 프리셋도 0으로 나온다."""
    with db_session(path) as db:
        rows = db.execute("""
            SELECT s.id, s.name, COUNT(r.date) days, MIN(r.date) first_date, MAX(r.date) last_date,
                   COALESCE(SUM(r.matched), 0) signals
            FROM screens s LEFT JOIN screen_runs r ON r.screen_id = s.id
            GROUP BY s.id, s.name ORDER BY s.name""").fetchall()
    return [dict(row) for row in rows]


def _rows_on(db, screen_id: int, date: str) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        "SELECT g.ticker, g.rank, g.score, g.close, i.name, i.market FROM screen_signals g LEFT JOIN instruments i ON i.ticker=g.ticker WHERE g.screen_id=? AND g.date=? ORDER BY g.rank",
        (int(screen_id), date)).fetchall()]


def diff(screen_id: int, date: str | None = None, path=None) -> dict[str, Any]:
    """기준일과 그 직전 수집일 사이의 신규·이탈 종목. 수집된 날짜만 비교하므로 수집을 건너뛴 날이
    있어도 '어제와의 비교'가 아니라 '직전 수집일과의 비교'가 된다."""
    with db_session(path) as db:
        dates = [row[0] for row in db.execute("SELECT date FROM screen_runs WHERE screen_id=? ORDER BY date", (int(screen_id),)).fetchall()]
        if not dates:
            return {"screen_id": int(screen_id), "date": None, "previous": None, "entered": [], "exited": [], "held": []}
        target = date if date in dates else dates[-1]
        index = dates.index(target)
        previous = dates[index - 1] if index > 0 else None
        current_rows = _rows_on(db, screen_id, target)
        previous_rows = _rows_on(db, screen_id, previous) if previous else []
    previous_tickers = {row["ticker"] for row in previous_rows}
    current_tickers = {row["ticker"] for row in current_rows}
    return {
        "screen_id": int(screen_id), "date": target, "previous": previous,
        "entered": [row for row in current_rows if row["ticker"] not in previous_tickers],
        "held": [row for row in current_rows if row["ticker"] in previous_tickers],
        "exited": [row for row in previous_rows if row["ticker"] not in current_tickers],
    }


def streaks(screen_id: int, date: str | None = None, path=None) -> dict[str, dict[str, Any]]:
    """기준일 신호 종목별 연속 등장 일수. 1이면 신규 진입이다."""
    with db_session(path) as db:
        dates = [row[0] for row in db.execute("SELECT date FROM screen_runs WHERE screen_id=? ORDER BY date DESC LIMIT ?", (int(screen_id), STREAK_LOOKBACK)).fetchall()]
        if not dates:
            return {}
        target = date if date in dates else dates[0]
        window = dates[dates.index(target):]
        rows = db.execute(
            f"SELECT date,ticker FROM screen_signals WHERE screen_id=? AND date IN ({','.join('?' * len(window))})",
            [int(screen_id), *window]).fetchall()
    by_date: dict[str, set[str]] = {day: set() for day in window}
    for row in rows:
        by_date[row["date"]].add(row["ticker"])
    result: dict[str, dict[str, Any]] = {}
    for ticker in by_date[target]:
        days = 0
        first = target
        for day in window:
            if ticker not in by_date[day]:
                break
            days += 1
            first = day
        result[ticker] = {"days": days, "first_date": first, "truncated": days == len(window) and len(window) == STREAK_LOOKBACK}
    return result


def signal_history(ticker: str, limit: int = 50, path=None) -> list[dict[str, Any]]:
    """한 종목이 어떤 프리셋에 언제 걸렸는지. 계획의 셋업 태그를 채울 때 쓴다."""
    with db_session(path) as db:
        rows = db.execute(
            "SELECT g.date, g.screen_id, s.name, g.rank FROM screen_signals g JOIN screens s ON s.id=g.screen_id WHERE g.ticker=? ORDER BY g.date DESC, s.name LIMIT ?",
            (str(ticker), max(1, min(int(limit), 500)))).fetchall()
    return [dict(row) for row in rows]
