from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, SCHEMA_VERSION, MSCR_HOME


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


ADDED_COLUMNS = {
    "trades": {"broker_order_id": "TEXT"},
    "instruments": {"region": "TEXT NOT NULL DEFAULT 'KR'"},
    "screens": {"region": "TEXT NOT NULL DEFAULT 'KR'"},
    "watchlists": {"region": "TEXT NOT NULL DEFAULT 'KR'"},
    "trade_plans": {
        "entry_price": "REAL NOT NULL DEFAULT 0",
        "stop_price": "REAL NOT NULL DEFAULT 0",
        "tp1_price": "REAL NOT NULL DEFAULT 0",
        "tp1_ratio": "REAL NOT NULL DEFAULT 0",
        "tp2_price": "REAL NOT NULL DEFAULT 0",
        "tp2_ratio": "REAL NOT NULL DEFAULT 0",
        "tp3_trailing_pct": "REAL NOT NULL DEFAULT 0",
        "setup": "TEXT",
    },
    "broker_orders": {
        "leg": "TEXT",
        "as_of": "TEXT",
        "org_no": "TEXT",
        "origin": "TEXT NOT NULL DEFAULT 'manual'",
        "trigger_price": "REAL",
        "replaces_order_id": "INTEGER",
    },
}
DROPPED_COLUMNS = {"trade_plans": ("condition", "max_fills")}
DROPPED_INDEXES = ("idx_trades_broker", "idx_broker_orders_bid")


def init_db(path: Path | str | None = DB_PATH) -> None:
    target = Path(path) if path is not None else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with connect(target) as connection:
        for table, columns in ADDED_COLUMNS.items():
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): continue
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, declaration in columns.items():
                if column not in existing: connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        for table, columns in DROPPED_COLUMNS.items():
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): continue
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for column in columns:
                if column in existing: connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        for index in DROPPED_INDEXES:
            connection.execute(f"DROP INDEX IF EXISTS {index}")
        connection.executescript((Path(__file__).with_name("schema.sql")).read_text())
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

@contextmanager
def db_session(path: Path | str | None = DB_PATH):
    target = Path(path) if path is not None else DB_PATH
    init_db(target)
    connection = connect(target)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
