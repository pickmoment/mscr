"""시장 모드(한국/미국) 격리와 Massive 수집 경로."""

from __future__ import annotations

import pandas as pd
import pytest

from mscr import market
from mscr.db import db_session, init_db
from mscr.ingest import run_ingest
from mscr.providers.massive import MassiveError, MassiveProvider


@pytest.fixture()
def isolated_home(monkeypatch, tmp_path):
    """저장된 기본 시장·자격증명이 실제 홈 디렉터리를 건드리지 않게 격리한다."""
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    monkeypatch.delenv("MSCR_MARKET", raising=False)
    return tmp_path


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "market.db"
    init_db(path)
    return path


def _seed(path, ticker: str, region: str, source: str, market_name: str, dates: list[str], close: float = 100.0) -> None:
    with db_session(path) as db:
        db.execute(
            "INSERT INTO instruments(ticker,name,kind,region,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock',?,?,0,0,?,?,0)",
            (ticker, f"{ticker} 이름", region, market_name, dates[0], dates[-1]))
        db.executemany(
            "INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,NULL,0)",
            [(ticker, day, source, close, close, close, close, 1000, 1000.0 * close, ) for day in dates])


def test_resolve_and_ticker_rules():
    assert market.resolve("us").region == "US" and market.resolve("KR").key == "kr"
    with pytest.raises(ValueError):
        market.resolve("jp")
    assert market.region_of("005930") == "KR" and market.region_of("AAPL") == "US"
    with market.use("us"):
        assert market.clean_ticker("brk.b") == "BRK.B"
        with pytest.raises(ValueError):
            market.clean_ticker("AAPL$")
    with market.use("kr"):
        assert market.clean_ticker(" 005930 ") == "005930"
        for wrong in ("AAPL", "5930"):
            with pytest.raises(ValueError):
                market.clean_ticker(wrong)


def test_active_market_is_scoped_to_the_context():
    with market.use("us"):
        assert market.bar_source() == "massive_snapshot"
        with market.use("kr"):
            assert market.bar_source() == "krx_snapshot"
        assert market.bar_source() == "massive_snapshot"


# --- Massive 프로바이더 ---------------------------------------------------

class _FakeMassive(MassiveProvider):
    """HTTP만 갈아 끼운다 — 응답 해석·페이지네이션은 실제 코드를 그대로 태운다."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[str] = []
        self.delay = 0.0
        self.api_key = "test"

    def _get(self, path_or_url, params=None):
        self.calls.append(path_or_url)
        payload = self.responses.get(path_or_url)
        if payload is None:
            raise MassiveError(f"no stub for {path_or_url}")
        return payload


def _grouped(day: str, results):
    return {f"/v2/aggs/grouped/locale/us/market/stocks/{day}": {"status": "OK", "results": results}}


def test_daily_snapshot_maps_fields_and_approximates_traded_value():
    provider = _FakeMassive(_grouped("2026-09-10", [
        {"T": "AAPL", "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.0, "v": 1000, "vw": 102.0},
        {"T": "spy", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10, "vw": None},
    ]))
    frame = provider.daily_snapshot("2026-09-10").set_index("ticker")

    assert frame.loc["AAPL", "close"] == 104.0 and frame.loc["AAPL", "high"] == 105.0
    # 거래대금은 따로 오지 않아 거래량×VWAP으로 근사하고, VWAP이 없으면 종가로 대신한다.
    assert frame.loc["AAPL", "value"] == 1000 * 102.0
    assert frame.loc["SPY", "value"] == 10 * 1.5


def test_universe_classifies_etfs_and_folds_exchange_codes():
    provider = _FakeMassive({"/v3/reference/tickers": {"status": "OK", "results": [
        {"ticker": "AAPL", "name": "Apple Inc.", "type": "CS", "primary_exchange": "XNAS"},
        {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "type": "ETF", "primary_exchange": "ARCX"},
        {"ticker": "XYZ", "name": "Some Acquisition Corp", "type": "CS", "primary_exchange": "ZZZZ"},
    ], "next_url": None}})
    frame = provider.universe().set_index("ticker")

    assert frame.loc["AAPL", "kind"] == "stock" and frame.loc["AAPL", "market"] == "NASDAQ"
    assert frame.loc["SPY", "kind"] == "etf" and frame.loc["SPY", "market"] == "NYSE"
    # 미국은 종목명 말고 SPAC 표시가 없어 이름으로 근사한다. 모르는 거래소는 OTHER로 접는다.
    assert frame.loc["XYZ", "is_spac"] == 1 and frame.loc["XYZ", "market"] == "OTHER"


def test_latest_trading_day_walks_back_over_empty_days(monkeypatch):
    monkeypatch.setattr("mscr.providers.massive.eastern_today", lambda: pd.Timestamp("2026-09-12").date())
    provider = _FakeMassive({**_grouped("2026-09-12", []), **_grouped("2026-09-11", [{"T": "AAPL", "c": 1.0, "v": 1}])})

    assert provider.latest_trading_day() == "2026-09-11"


# --- 수집 ---------------------------------------------------------------

class _IngestProvider(_FakeMassive):
    def __init__(self, days: dict[str, list[dict]], universe: pd.DataFrame):
        super().__init__({})
        self.days = days
        self._universe = universe

    def latest_trading_day(self):
        return max(self.days)

    def daily_snapshot(self, day):
        return MassiveProvider.daily_snapshot(_FakeMassive(_grouped(day, self.days.get(day, []))), day)

    def universe(self):
        return self._universe


def _ingest_provider():
    universe = pd.DataFrame([
        {"ticker": "AAPL", "name": "Apple Inc.", "kind": "stock", "market": "NASDAQ", "category": "CS", "is_preferred": 0, "is_spac": 0},
        {"ticker": "SPY", "name": "SPDR S&P 500", "kind": "etf", "market": "NYSE", "category": "ETF", "is_preferred": 0, "is_spac": 0},
    ])
    days = {
        "2026-09-10": [{"T": "AAPL", "o": 99.0, "h": 101.0, "l": 98.0, "c": 100.0, "v": 1000, "vw": 100.0}],
        "2026-09-11": [{"T": "AAPL", "o": 100.0, "h": 103.0, "l": 100.0, "c": 102.0, "v": 1200, "vw": 101.0},
                       {"T": "SPY", "o": 500.0, "h": 505.0, "l": 499.0, "c": 504.0, "v": 900, "vw": 502.0}],
    }
    return _IngestProvider(days, universe)


def test_us_ingest_stores_bars_universe_and_presets_under_the_us_market(store):
    with market.use("us"):
        run_ingest(days=2, provider=_ingest_provider(), path=store)

    with db_session(store) as db:
        bars = db.execute("SELECT ticker,date,source,close,value FROM daily_bars ORDER BY date,ticker").fetchall()
        instruments = db.execute("SELECT ticker,kind,region,market FROM instruments ORDER BY ticker").fetchall()
        runs = {row["kind"]: row["status"] for row in db.execute("SELECT kind,status FROM ingest_runs")}
        screens = db.execute("SELECT name,region FROM screens").fetchall()

    assert {row["source"] for row in bars} == {"massive_snapshot"}
    assert [(row["ticker"], row["date"]) for row in bars] == [("AAPL", "2026-09-10"), ("AAPL", "2026-09-11"), ("SPY", "2026-09-11")]
    # 미국 티커는 6자리 0채움을 하지 않는다.
    assert [(row["ticker"], row["kind"], row["region"]) for row in instruments] == [("AAPL", "stock", "US"), ("SPY", "etf", "US")]
    assert runs == {"us_bars": "ok", "us_universe": "ok"}
    assert screens and {row["region"] for row in screens} == {"US"}


def test_us_ingest_skips_days_already_collected(store):
    provider = _ingest_provider()
    with market.use("us"):
        run_ingest(days=2, provider=provider, path=store)
        before = len(provider.calls)
        run_ingest(days=2, provider=provider, path=store)

    with db_session(store) as db:
        assert db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 3
    assert len(provider.calls) == before  # 재실행이 같은 날짜를 다시 요청하지 않는다


def test_unsupported_source_for_the_market_is_rejected(store):
    with market.use("us"), pytest.raises(ValueError, match="지원하지 않는 소스"):
        run_ingest(source="krx", path=store)
    with market.use("kr"), pytest.raises(ValueError, match="지원하지 않는 소스"):
        run_ingest(source="massive", path=store)


# --- 화면 격리 -----------------------------------------------------------

SPEC = {
    "universe": {"kinds": ["stock"], "markets": [], "exclude_preferred": False, "exclude_spac": False, "exclude_halted": False, "min_bars": 1},
    "formula": "close > 0",
    "sort": {"formula": "close", "dir": "desc"},
    "limit": 100,
}


def test_screen_only_sees_the_active_market(store):
    days = ["2026-09-10", "2026-09-11"]
    _seed(store, "005930", "KR", "krx_snapshot", "KOSPI", days, 70000.0)
    _seed(store, "AAPL", "US", "massive_snapshot", "NASDAQ", days, 100.0)
    from mscr.screener import run

    with market.use("kr"):
        assert [row["ticker"] for row in run(SPEC, store)] == ["005930"]
    with market.use("us"):
        assert [row["ticker"] for row in run(SPEC, store)] == ["AAPL"]


def test_screen_rejects_exchanges_from_the_other_market(store):
    from mscr.screener import run

    with market.use("us"), pytest.raises(ValueError, match="invalid universe"):
        run(SPEC | {"universe": SPEC["universe"] | {"markets": ["KOSPI"]}}, store)


def test_portfolio_splits_positions_and_cash_by_market(store):
    days = ["2026-09-11"]
    _seed(store, "005930", "KR", "krx_snapshot", "KOSPI", days, 70000.0)
    _seed(store, "AAPL", "US", "massive_snapshot", "NASDAQ", days, 100.0)
    with db_session(store) as db:
        db.executemany(
            "INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,memo,created_at) VALUES(?,?,?,?,?,0,0,NULL,datetime('now'))",
            [("005930", "buy", "2026-09-11", 10, 60000.0), ("AAPL", "buy", "2026-09-11", 5, 90.0)])
        db.execute("INSERT INTO settings(key,value) VALUES('cash_krw','1000000')")
        db.execute("INSERT INTO settings(key,value) VALUES('cash_usd','500')")
    from mscr.portfolio import snapshot

    with market.use("kr"):
        korea = snapshot(store)
    with market.use("us"):
        usa = snapshot(store)

    assert [row["ticker"] for row in korea["positions"]] == ["005930"] and korea["cash"] == 1000000.0 and korea["currency"] == "KRW"
    assert [row["ticker"] for row in usa["positions"]] == ["AAPL"] and usa["cash"] == 500.0 and usa["currency"] == "USD"
    assert usa["total_market_value"] == 500.0  # 5주 × 100달러


def test_watchlists_are_scoped_to_the_active_market(store):
    days = ["2026-09-11"]
    _seed(store, "005930", "KR", "krx_snapshot", "KOSPI", days, 70000.0)
    _seed(store, "AAPL", "US", "massive_snapshot", "NASDAQ", days, 100.0)
    from mscr import watchlist

    with market.use("kr"):
        korea = watchlist.create_watchlist("관심", path=store)
        watchlist.save_item(korea["id"], "005930", path=store)
    with market.use("us"):
        usa = watchlist.create_watchlist("US 관심", path=store)
        watchlist.save_item(usa["id"], "AAPL", path=store)
        assert [row["name"] for row in watchlist.list_watchlists(path=store)] == ["US 관심"]
        # 다른 시장의 목록은 보이지도, 열리지도 않는다.
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            watchlist.snapshot(korea["id"], path=store)
        with pytest.raises(ValueError, match="등록되지 않은"):
            watchlist.save_item(usa["id"], "005930", path=store)
        with pytest.raises(ValueError, match="다른 시장 모드"):
            watchlist.create_watchlist("관심", path=store)

    with market.use("kr"):
        assert [row["name"] for row in watchlist.list_watchlists(path=store)] == ["관심"]
        assert [row["ticker"] for row in watchlist.snapshot(korea["id"], path=store)["rows"]] == ["005930"]


# --- 웹 요청 컨텍스트 -----------------------------------------------------

def _call_asgi(app, path: str, headers=None, query: str = ""):
    """HTTP 클라이언트 없이 ASGI 앱을 직접 호출한다(미들웨어 동작만 확인하면 되므로)."""
    import asyncio

    scope = {
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "headers": [(key.encode(), value.encode()) for key, value in (headers or {}).items()],
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next((item["status"] for item in sent if item["type"] == "http.response.start"), None)
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, body


def _middleware(seen: list[str]):
    from mscr.api.app import MarketContextMiddleware

    async def endpoint(scope, receive, send):
        seen.append(market.active().key)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return MarketContextMiddleware(endpoint)


def test_request_market_comes_from_header_or_query(isolated_home):
    seen: list[str] = []
    app = _middleware(seen)

    assert _call_asgi(app, "/api/meta", {"x-market": "us"})[0] == 200
    assert _call_asgi(app, "/api/meta", query="market=us")[0] == 200
    assert _call_asgi(app, "/api/meta")[0] == 200
    assert seen == ["us", "us", "kr"]
    # 요청이 끝나면 컨텍스트는 원래대로 돌아간다.
    assert market.active().key == "kr"


def test_unknown_market_is_rejected_and_kr_only_paths_are_blocked(isolated_home):
    seen: list[str] = []
    app = _middleware(seen)

    assert _call_asgi(app, "/api/meta", {"x-market": "jp"})[0] == 422
    status, body = _call_asgi(app, "/api/trading/plans", {"x-market": "us"})
    assert status == 409 and "한국 주식 모드" in body.decode()
    assert _call_asgi(app, "/api/trading/plans", {"x-market": "kr"})[0] == 200
    assert seen == ["kr"]  # 미국 모드 요청은 라우트까지 내려가지 않는다


def test_default_market_is_persisted_for_later_sessions(isolated_home):
    assert market.stored_default() == "kr"
    market.save_default("us")
    assert market.stored_default() == "us"
    assert market.active().key == "us"
