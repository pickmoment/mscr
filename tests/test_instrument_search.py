from __future__ import annotations

import pytest

from mscr.api import routes
from mscr.db import db_session, init_db


_ROWS = [
    ("005930", "삼성전자", "stock", "KOSPI", 0),
    ("000660", "SK하이닉스", "stock", "KOSPI", 0),
    ("005935", "삼성전자우", "stock", "KOSPI", 0),
    ("900001", "폐지종목", "stock", "KOSDAQ", 1),
]


@pytest.fixture()
def store(monkeypatch, tmp_path):
    path = tmp_path / "search.db"
    init_db(path)
    with db_session(path) as db:
        db.executemany(
            "INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,?,?,0,0,'2026-08-01','2026-08-31',?)",
            _ROWS,
        )
    monkeypatch.setattr(routes, "db_session", lambda *args, **kwargs: db_session(path))
    return path


def test_ticker_exact_match_ranks_first(store):
    assert routes.search_instruments(q="005930", limit=12)[0]["ticker"] == "005930"


def test_name_prefix_matches_sorted_by_name(store):
    assert [row["ticker"] for row in routes.search_instruments(q="삼성", limit=12)] == ["005930", "005935"]


def test_delisted_excluded(store):
    assert routes.search_instruments(q="폐지", limit=12) == []


def test_blank_query_returns_empty(store):
    assert routes.search_instruments(q="   ", limit=12) == []
