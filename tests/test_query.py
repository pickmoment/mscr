"""`mscr query` JSON 계층 — AI 에이전트 스킬이 이 표면을 그대로 쓴다."""

from __future__ import annotations

import json

import pytest

from mscr import market, query
from mscr.db import db_session, init_db


@pytest.fixture()
def store(monkeypatch, tmp_path):
    """기본 DB 경로를 임시 파일로 돌려 실제 ~/.mscr 데이터를 건드리지 않는다."""
    path = tmp_path / "query.db"
    init_db(path)
    monkeypatch.setattr("mscr.db.DB_PATH", path)
    with db_session(path) as db:
        db.executemany(
            "INSERT INTO instruments(ticker,name,kind,region,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,?,?,?,0,0,'2026-09-01','2026-09-10',0)",
            [("005930", "삼성전자", "stock", "KR", "KOSPI"), ("AAPL", "Apple Inc.", "stock", "US", "NASDAQ")])
        rows = []
        for day, close in (("2026-09-09", 100.0), ("2026-09-10", 110.0)):
            rows.append(("005930", day, "krx_snapshot", close, close, close, close, 1000, 1000.0 * close))
            rows.append(("AAPL", day, "massive_snapshot", close, close, close, close, 1000, 1000.0 * close))
        db.executemany(
            "INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,NULL,0)", rows)
    return path


def test_clean_drops_non_finite_numbers_so_output_is_valid_json():
    payload = query.clean({"a": float("nan"), "b": float("inf"), "c": [1.5, True], "d": None})
    assert payload == {"a": None, "b": None, "c": [1.5, True], "d": None}
    assert json.loads(query.dumps(payload)) == payload


def test_status_and_fields_describe_the_active_market(store):
    with market.use("us"):
        status = query.status()
        assert status["market"]["key"] == "us" and status["market"]["currency"] == "USD"
        assert status["as_of"] == "2026-09-10" and status["instruments"]["stock"] == 1
        assert set(status["credentials"]) == {"massive"}
    fields = query.fields()
    # 에이전트가 수식을 쓰기 전에 읽는 카탈로그 — 시리즈·스칼라·함수가 모두 들어 있어야 한다.
    assert "close" in fields["series"] and "market_cap" in fields["scalars"]
    assert {item["key"] for item in fields["functions"]} >= {"sma", "rsi", "atr", "rolling_max"}


def test_lookups_are_scoped_to_the_active_market(store):
    with market.use("kr"):
        assert [row["ticker"] for row in query.search("삼성")] == ["005930"]
        assert query.quote("005930")["quote"]["close"] == 110.0
        assert query.bars("005930", days=1)["bars"][0]["date"] == "2026-09-10"
        with pytest.raises(ValueError, match="등록되지 않은"):
            query.quote("000001")
    with market.use("us"):
        assert [row["ticker"] for row in query.search("Apple")] == ["AAPL"]
        assert query.quote("AAPL")["currency"] == "USD"
        # 다른 시장 종목은 형식부터 걸린다.
        with pytest.raises(ValueError):
            query.quote("005930")


def test_screen_trims_columns_and_reports_the_spec(store):
    spec = query.build_spec("close > 0", sort="close", min_bars=1, limit=5)
    with market.use("kr"):
        result = query.screen(spec)
    assert result["count"] == 1 and result["as_of"] == "2026-09-10"
    # 기본 출력은 요약 열만 — 에이전트가 읽을 분량을 줄인다.
    assert set(result["rows"][0]) == set(query.SCREEN_COLUMNS)
    assert result["rows"][0]["ticker"] == "005930"
    assert result["spec"]["universe"]["min_bars"] == 1

    with market.use("kr"):
        full = query.screen(spec, columns=["ticker", "close"])
    assert full["rows"] == [{"ticker": "005930", "close": 110.0}]


def test_presets_round_trip_and_refuse_cross_market_names(store):
    spec = query.build_spec("close > 0", min_bars=1)
    with market.use("kr"):
        saved = query.save_preset("내 프리셋", spec)
        assert saved["region"] == "KR"
        assert [item["name"] for item in query.presets()] == ["내 프리셋"]
        assert query.preset_spec("내 프리셋")["formula"] == "close > 0"
    with market.use("us"):
        # 이름은 두 시장을 통틀어 유일하다. 다른 시장 프리셋을 덮어쓰지 않는다.
        assert query.presets() == []
        with pytest.raises(ValueError, match="다른 시장 모드"):
            query.save_preset("내 프리셋", spec)
    with market.use("kr"):
        assert query.delete_preset("내 프리셋") == {"deleted": "내 프리셋"}
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            query.preset_spec("내 프리셋")


def test_bad_formula_is_rejected_before_the_preset_is_stored(store):
    with market.use("kr"), pytest.raises(ValueError):
        query.save_preset("나쁜 수식", query.build_spec("close > __import__('os')"))
    with market.use("kr"):
        assert query.presets() == []


def test_watchlist_writes_go_through_the_active_market(store):
    with market.use("kr"):
        created = query.watchlist_create("에이전트")
        query.watchlist_add("005930", "에이전트", memo="편입 이유", target_price=150.0)
        detail = query.watchlist_detail("에이전트")
        assert [row["ticker"] for row in detail["rows"]] == ["005930"]
        # 편입 시점 종가가 남아 편입 후 성과를 추적할 수 있다.
        assert detail["rows"][0]["added_price"] == 110.0 and detail["rows"][0]["memo"] == "편입 이유"
        assert query.watchlist_remove("005930", "에이전트") == {"watchlist_id": created["id"], "removed": "005930"}
        assert query.watchlist_detail("에이전트")["rows"] == []
    with market.use("us"):
        # 한국 목록은 미국 모드에서 보이지 않는다.
        assert query.watchlists() == []
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            query.watchlist_add("AAPL", "에이전트")
