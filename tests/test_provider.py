import pandas as pd
import pytest
import requests

from mscr.providers import krx as krx_module
from mscr.providers.krx import KRXOpenAPIProvider, KRXProvider, STOCK_RENAME, fdr_stock_snapshot


def test_openapi_request_uses_auth_key_and_normalizes_daily_record(monkeypatch):
    monkeypatch.setenv("KRX_OPENAPI_KEY", "secret")
    provider = KRXOpenAPIProvider(delay=0)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"OutBlock_1": [{"ISU_SRT_CD": "005930", "ISU_ABBRV": "삼성전자", "TDD_CLSPRC": "70,500", "ACC_TRDVOL": "1,000"}]}

    class Session:
        def __init__(self):
            self.call = None

        def get(self, url, params, headers, timeout):
            self.call = (url, params, headers, timeout)
            return Response()

    provider.session = Session()
    records = provider._records("sto", "stk_bydd_trd", "20260831")
    frame = provider._daily_frame(records)
    assert provider.session.call[1] == {"basDd": "20260831"}
    assert provider.session.call[2] == {"AUTH_KEY": "secret"}
    assert frame.iloc[0]["ticker"] == "005930"
    assert frame.iloc[0]["close"] == 70500
    assert frame.iloc[0]["volume"] == 1000

def test_openapi_error_response_is_exposed(monkeypatch):
    monkeypatch.setenv("KRX_OPENAPI_KEY", "secret")
    provider = KRXOpenAPIProvider(delay=0)

    class Response:
        status_code = 401

        def raise_for_status(self):
            raise requests.HTTPError("401 Client Error")

        def json(self):
            return {"respCode": "401", "respMsg": "Unauthorized API Call"}

    class Session:
        def get(self, url, params, headers, timeout):
            return Response()

    provider.session = Session()
    with pytest.raises(RuntimeError, match="401: Unauthorized API Call"):
        provider._records("sto", "stk_bydd_trd", "20260831")

def test_etf_frame_maps_official_index_name(monkeypatch):
    monkeypatch.setenv("KRX_OPENAPI_KEY", "secret")
    frame = KRXOpenAPIProvider._daily_frame([{"ISU_CD": "069500", "ISU_NM": "KODEX 200", "TDD_CLSPRC": "35,000", "IDX_IND_NM": "코스피 200"}], etf=True)
    assert frame.iloc[0]["ticker"] == "069500"
    assert frame.iloc[0]["base_index"] == "코스피 200"

def test_history_normalizes_finance_data_reader_columns():
    frame = pd.DataFrame(
        {"Open": [100], "High": [110], "Low": [90], "Close": [105], "Volume": [1234], "Change": [0.05]},
        index=pd.to_datetime(["2026-08-31"]),
    )
    frame.index.name = "Date"

    result = KRXProvider._normalize(frame, STOCK_RENAME)

    assert result.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [100, 110, 90, 105, 1234]
    assert result.iloc[0]["date"] == "2026-08-31"

def test_minervini_preset_uses_supported_dynamic_fields():
    from mscr.ingest import PRESETS

    preset = PRESETS["미너비니 추세 템플릿 (근사)"]

    assert preset["universe"]["kinds"] == ["stock"]
    assert "sma(close, 20) > sma(close, 60)" in preset["formula"]
    assert "rolling_min(low, 250)" in preset["formula"]

def test_presets_are_executable_by_the_formula_engine():
    from mscr.dynamic import BUILTIN_FUNCTIONS, SCREEN_NAMES, validate_formula
    from mscr.ingest import PRESETS

    for preset in PRESETS.values():
        validate_formula(preset["formula"], SCREEN_NAMES, BUILTIN_FUNCTIONS)
        validate_formula(preset["sort"]["formula"], SCREEN_NAMES, BUILTIN_FUNCTIONS)

def test_three_r_preset_selects_the_quietest_names_by_sort():
    from mscr.ingest import PRESETS

    preset = PRESETS["3R 목표 후보"]

    # 절대 임계가 아니라 정렬+상한이 종목을 고른다. 임계로 바꾸면 고변동성 국면에서 후보가 0이 된다.
    assert preset["sort"] == {"formula": "atr(high, low, close, 14) / close", "dir": "asc"}
    assert preset["limit"] == 5
    # 우선주를 빼면 측정 기대값이 +0.50R에서 +0.38R로 떨어진다.
    assert preset["universe"]["exclude_preferred"] is False

def test_box_breakout_preset_is_the_box_squeeze_set_plus_the_near_top_clause():
    from mscr.ingest import PRESETS

    squeeze = PRESETS["박스 조임 후보"]
    breakout = PRESETS["박스 돌파 예정"]

    # 측정된 주장이 "조임 후보 조건 + 상단 근접" 하위집합이라는 것이므로, 두 프리셋이 갈라지면 문서의 비교가 무효가 된다.
    squeeze_clauses = {clause.strip() for clause in squeeze["formula"].split(" and ")}
    breakout_clauses = {clause.strip() for clause in breakout["formula"].split(" and ")}
    assert squeeze_clauses < breakout_clauses
    assert breakout_clauses - squeeze_clauses == {"(rolling_max(high, 20) - close) / close <= 0.02"}
    assert breakout["universe"] == squeeze["universe"]
    assert breakout["sort"] == squeeze["sort"] and breakout["limit"] == squeeze["limit"]

def _fdr_listing():
    return pd.DataFrame({
        "Code": ["005930", "000660"],
        "Open": [249000, 200000],
        "High": [260000, 210000],
        "Low": [246000, 195000],
        "Close": [260000, 205000],
        "Volume": [17009810, 500000],
        "Amount": [4647038997556, 100000000000],
    })

def _fdr_history(close):
    return pd.DataFrame({"Close": [257000, close]}, index=pd.to_datetime(["2026-08-28", "2026-08-31"]))

def test_fdr_stock_snapshot_cross_validates_reference_ticker_date(monkeypatch):
    monkeypatch.setattr(krx_module.fdr, "StockListing", lambda market: _fdr_listing())
    monkeypatch.setattr(krx_module.fdr, "DataReader", lambda ticker, start: _fdr_history(260000))

    day, frame = fdr_stock_snapshot()

    assert day == "2026-08-31"
    assert set(frame["ticker"]) == {"005930", "000660"}
    row = frame[frame["ticker"] == "005930"].iloc[0]
    assert [row["open"], row["high"], row["low"], row["close"], row["volume"], row["value"]] == [249000, 260000, 246000, 260000, 17009810, 4647038997556]

def test_fdr_stock_snapshot_rejects_mismatched_reference_close(monkeypatch):
    monkeypatch.setattr(krx_module.fdr, "StockListing", lambda market: _fdr_listing())
    monkeypatch.setattr(krx_module.fdr, "DataReader", lambda ticker, start: _fdr_history(999999))

    with pytest.raises(RuntimeError, match="일치하지 않아"):
        fdr_stock_snapshot()

def test_fdr_stock_snapshot_rejects_empty_listing(monkeypatch):
    monkeypatch.setattr(krx_module.fdr, "StockListing", lambda market: pd.DataFrame())

    with pytest.raises(RuntimeError, match="종목 목록"):
        fdr_stock_snapshot()
