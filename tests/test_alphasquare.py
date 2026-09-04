import pandas as pd

from mscr.db import db_session, init_db
from mscr.providers.alphasquare import AlphaSquareProvider, market_overview


def _kst_close_ms(day: str) -> int:
    target = pd.Timestamp(day).as_unit("ns")
    return int((target.tz_localize("Asia/Seoul") + pd.Timedelta(hours=15, minutes=30)).tz_convert("UTC").timestamp() * 1000)


def test_resolve_caches_stock_id_and_skips_second_lookup(tmp_path):
    path = tmp_path / "alphasquare.db"
    init_db(path)
    with db_session(path) as db:
        provider = AlphaSquareProvider(db, delay=0)
        calls = []
        provider._get = lambda path_, params: calls.append(params) or {"005930": {"id": 123, "type": "stock"}}

        first = provider._resolve("005930")
        provider._get = lambda path_, params: (_ for _ in ()).throw(AssertionError("should not re-query a cached ticker"))
        second = provider._resolve("005930")

    assert first == 123
    assert second == 123
    assert len(calls) == 1


def test_resolve_returns_none_for_unknown_ticker(tmp_path):
    path = tmp_path / "alphasquare.db"
    init_db(path)
    with db_session(path) as db:
        provider = AlphaSquareProvider(db, delay=0)
        provider._get = lambda path_, params: {}

        assert provider._resolve("999999") is None
        row = db.execute("SELECT 1 FROM alphasquare_ticker_map WHERE ticker='999999'").fetchone()
    assert row is None


def test_range_bars_keeps_only_bars_within_the_requested_korean_calendar_window(tmp_path):
    path = tmp_path / "alphasquare.db"
    init_db(path)
    with db_session(path) as db:
        provider = AlphaSquareProvider(db, delay=0)
        page = [
            [_kst_close_ms("2026-08-27"), 90, 95, 85, 92, 5],
            [_kst_close_ms("2026-08-28"), 92, 100, 90, 98, 8],
            [_kst_close_ms("2026-08-31"), 98, 110, 90, 105, 10],
        ]
        provider._get = lambda path_, params: {"data": page}

        bars = provider._range_bars(123, "2026-08-28", "2026-08-31")

    assert {bar["date"] for bar in bars} == {"2026-08-28", "2026-08-31"}
    matched = next(bar for bar in bars if bar["date"] == "2026-08-31")
    assert matched == {"date": "2026-08-31", "open": 98, "high": 110, "low": 90, "close": 105, "volume": 10, "value": 1050}


def test_range_bars_paginates_backward_when_a_page_is_full(tmp_path):
    path = tmp_path / "alphasquare.db"
    init_db(path)
    with db_session(path) as db:
        provider = AlphaSquareProvider(db, delay=0)
        newest_page = [[_kst_close_ms("2026-08-31"), 98, 110, 90, 105, 10]] + [
            [_kst_close_ms("2026-08-30") - i, 1, 1, 1, 1, 1] for i in range(999)
        ]
        older_page = [[_kst_close_ms("2026-08-01"), 50, 55, 45, 52, 3]]
        pages = [newest_page, older_page]

        def fake_get(path_, params):
            return {"data": pages.pop(0)}

        provider._get = fake_get

        bars = provider._range_bars(123, "2026-08-01", "2026-08-31")

    assert not pages
    assert {"2026-08-31", "2026-08-01"} <= {bar["date"] for bar in bars}


def test_history_skips_tickers_that_fail_to_resolve(tmp_path):
    path = tmp_path / "alphasquare.db"
    init_db(path)
    with db_session(path) as db:
        provider = AlphaSquareProvider(db, delay=0)
        provider._resolve = lambda ticker: {"005930": 123}.get(ticker)
        provider._range_bars = lambda stock_id, start, end: [
            {"date": "2026-08-31", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10}
        ]

        frame = provider.history(["005930", "999999"], "2026-08-31", "2026-08-31")

    assert frame["ticker"].tolist() == ["005930"]
    assert frame.iloc[0]["close"] == 105


_LIVE_RESPONSES = {
    "/data/v3/prices/returns-group-count": {"upper_limit": 3, "up": 400, "same": 20, "down": 300, "lower_limit": 1},
    "/stock/v2/trendings": {"data": [{"code": "005930", "ko_name": "삼성전자", "market": "KOSPI", "count": 999}]},
    "/theme/v2/leader-board": {"data": [{
        "theme": {"id": 1, "name": "반도체"}, "big_theme": {"id": 10, "name": "IT"}, "stock_count": 30,
        "stats": {"date": "2026-09-03", "returns": 4.5, "rank": 1, "rank_change": 2, "up_count": 20, "down_count": 5, "even_count": 5},
    }]},
    "/data/v3/issue/market-news": {"data": [{"dt": "2026-09-03", "source": "연합뉴스", "title": "코스피 상승", "summary": "요약", "link": "https://example.com/news"}]},
    "/data/v2/issue/market": [{"dt": "2026-09-03", "category": "market", "title": "이슈", "link": "https://example.com/issue", "source": "속보", "type": "market"}],
    "/data/v2/special-stocks/by/returns_top": {"data": [{"code": "005930", "ko_name": "삼성전자", "close": 70000.0, "returns": 5.5, "volume": 1000.0, "volume_valued": 700000.0}]},
}


def _fake_special_stocks(session, path, params, delay):
    if path.startswith("/data/v2/special-stocks/by/"):
        return _LIVE_RESPONSES.get(path, {"data": []})
    return _LIVE_RESPONSES[path]


def test_market_overview_aggregates_all_sections(monkeypatch):
    monkeypatch.setattr("mscr.providers.alphasquare._http_get", _fake_special_stocks)

    overview = market_overview(delay=0)

    assert overview["errors"] == {}
    assert overview["breadth"]["kospi"]["up"] == 400 and overview["breadth"]["kosdaq"]["up"] == 400
    assert overview["trending"] == [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "count": 999}]
    assert overview["theme_leaders"][0]["theme"] == "반도체" and overview["theme_leaders"][0]["returns"] == 4.5
    assert overview["theme_leaders"][0]["theme_id"] == 1
    assert overview["news"][0]["title"] == "코스피 상승"
    assert overview["issues"][0]["title"] == "이슈"
    assert overview["featured"]["returns_top"]["label"] == "상승률 상위"
    assert overview["featured"]["returns_top"]["rows"][0]["name"] == "삼성전자"
    assert overview["featured"]["returns_bottom"]["rows"] == []


def test_market_overview_keeps_other_sections_when_one_fails(monkeypatch):
    def fake_get(session, path, params, delay):
        if path == "/theme/v2/leader-board":
            raise RuntimeError("boom")
        return _fake_special_stocks(session, path, params, delay)

    monkeypatch.setattr("mscr.providers.alphasquare._http_get", fake_get)

    overview = market_overview(delay=0)

    assert overview["theme_leaders"] == []
    assert "theme_leaders" in overview["errors"]
    assert overview["trending"] and overview["news"] and overview["issues"]


def test_market_overview_keeps_other_featured_factors_when_one_fails(monkeypatch):
    def fake_get(session, path, params, delay):
        if path == "/data/v2/special-stocks/by/supervised":
            raise RuntimeError("boom")
        return _fake_special_stocks(session, path, params, delay)

    monkeypatch.setattr("mscr.providers.alphasquare._http_get", fake_get)

    overview = market_overview(delay=0)

    assert "featured" not in overview["errors"]
    assert overview["featured"]["supervised"]["rows"] == []
    assert "error" in overview["featured"]["supervised"]
    assert overview["featured"]["returns_top"]["rows"][0]["code"] == "005930"


def test_theme_stocks_maps_response_fields(monkeypatch):
    from mscr.providers.alphasquare import theme_stocks

    monkeypatch.setattr(
        "mscr.providers.alphasquare._http_get",
        lambda session, path, params, delay: [{"code": "005930", "ko_name": "삼성전자", "market": "kospi", "is_alive": True}] if path == "/theme/v2/themes/512/stocks" else [],
    )

    stocks = theme_stocks(512, delay=0)

    assert stocks == [{"code": "005930", "name": "삼성전자", "market": "kospi"}]


def test_market_live_route_delegates_to_market_overview(monkeypatch):
    from mscr.api import routes

    sentinel = {"breadth": {}, "trending": [], "theme_leaders": [], "news": [], "issues": [], "errors": {}}
    monkeypatch.setattr("mscr.providers.alphasquare.market_overview", lambda: sentinel)

    assert routes.get_market_live() is sentinel


def test_market_live_theme_stocks_route_delegates_to_theme_stocks(monkeypatch):
    from mscr.api import routes

    monkeypatch.setattr("mscr.providers.alphasquare.theme_stocks", lambda theme_id: [{"code": "005930", "name": "삼성전자", "market": "kospi"}] if theme_id == 512 else [])

    assert routes.get_market_live_theme_stocks(512) == {"stocks": [{"code": "005930", "name": "삼성전자", "market": "kospi"}]}


def test_market_live_theme_stocks_route_wraps_failures(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from mscr.api import routes

    def boom(theme_id):
        raise RuntimeError("network down")

    monkeypatch.setattr("mscr.providers.alphasquare.theme_stocks", boom)

    with pytest.raises(HTTPException) as excinfo:
        routes.get_market_live_theme_stocks(512)
    assert excinfo.value.status_code == 502
