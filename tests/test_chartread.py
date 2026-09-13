"""차트를 말로 옮기는 계층 — 구간·스윙·수평선·사건.

여기서 지키는 약속은 두 가지다. (1) 구간은 창 전체를 빈틈없이 덮고 서로 겹치지 않는다.
(2) 구간 라벨은 그 구간의 등락률과 같은 방향을 가리킨다. 둘 중 하나라도 깨지면 읽는 쪽은
라벨도 숫자도 못 믿는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mscr import chartread, market, query
from mscr.db import db_session, init_db


def _frame(close, high=None, low=None, volume=None, halted=None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    dates = pd.bdate_range("2025-01-01", periods=len(close)).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "date": dates,
        "open": np.r_[close[0], close[:-1]],
        "high": close * 1.01 if high is None else np.asarray(high, dtype=float),
        "low": close * 0.99 if low is None else np.asarray(low, dtype=float),
        "close": close,
        "volume": np.full(len(close), 1000.0) if volume is None else np.asarray(volume, dtype=float),
        "value": close * 1000.0,
        "halted": np.zeros(len(close), dtype=int) if halted is None else np.asarray(halted, dtype=int),
    })


# --- 스윙 ---------------------------------------------------------------

def test_swings_confirm_an_extreme_only_after_price_retraces_past_the_threshold():
    high = np.array([10, 11, 12, 13, 12, 11, 10, 11, 12, 13, 14], dtype=float)
    pivots = chartread.swings(high, high - 1, np.full(len(high), 2.0))
    assert [(item["pos"], item["kind"], item["price"]) for item in pivots] == [
        (0, "low", 9.0), (3, "high", 13.0), (6, "low", 9.0), (10, "high", 14.0)]
    # 마지막 극값은 되돌림을 아직 못 봤다 — "확정된 고점"으로 말하면 안 된다.
    assert [item["confirmed"] for item in pivots] == [True, True, True, False]


def test_swings_ignore_wiggles_smaller_than_the_threshold():
    high = np.array([10, 11, 12, 13, 12, 11, 10, 11, 12, 13, 14], dtype=float)
    assert len(chartread.swings(high, high - 1, np.full(len(high), 2.0))) == 4
    # 되돌림이 한 번도 기준을 못 넘으면 구조가 통째로 비는 게 맞다 — 없는 파동을 지어내지 않는다.
    assert chartread.swings(high, high - 1, np.full(len(high), 6.0)) == []


# --- 구간 ---------------------------------------------------------------

def _segments_of(close, **kwargs):
    return chartread.read(_frame(close), **kwargs)["segments"]


def test_segments_cover_the_window_without_gaps_or_overlaps():
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 400)))
    payload = chartread.read(_frame(close), window=250)
    segments = payload["segments"]
    assert segments[0]["from"] == payload["window"]["from"]
    assert segments[-1]["to"] == payload["window"]["to"]
    assert all(first["to"] == second["from"] for first, second in zip(segments, segments[1:]))


@pytest.mark.parametrize("seed", range(8))
def test_segment_labels_never_contradict_their_own_change(seed):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.025, 400)))
    for segment in _segments_of(close, window=250):
        if segment["kind"] == "상승":
            assert segment["change"] > 0
        elif segment["kind"] == "하락":
            assert segment["change"] < 0


def test_a_clean_trend_is_one_segment():
    assert [segment["kind"] for segment in _segments_of(np.linspace(100, 200, 120))] == ["상승"]


def test_a_stall_at_the_end_is_reported_by_the_box_not_by_the_segments():
    """구간은 스윙 구조가 있어야 끊긴다. 스윙조차 없는 조용한 꼬리는 앞 구간에 붙고, 대신 `box`가 말한다.

    둘을 따로 계산하는 이유가 이것이다 — 구간만 보고 "상승 중"이라고 옮기면 두 달째 멈춰 선 걸 놓친다.
    """
    close = np.r_[np.linspace(100, 160, 90), 160 + np.tile([0.0, 1.0, -1.0, 0.5], 15)]
    payload = chartread.read(_frame(close))
    assert [segment["kind"] for segment in payload["segments"]] == ["상승"]
    assert payload["box"]["exists"] and payload["box"]["bars"] >= 60


def test_the_swing_threshold_does_not_collapse_where_atr_is_not_available_yet():
    # 창 앞쪽 13봉은 ATR14가 비어 있다. 그 자리를 0으로 채우면 문턱이 무너져 가짜 스윙이 쏟아진다.
    rng = np.random.default_rng(3)
    assert [segment["kind"] for segment in _segments_of(100 + rng.normal(0, 0.3, 120))] == ["횡보"]


# --- 수평선·박스 --------------------------------------------------------

def test_levels_cluster_nearby_swings_and_drop_the_ones_touched_once():
    pivots = [{"pos": 0, "kind": "high", "price": 100.0}, {"pos": 5, "kind": "high", "price": 101.0},
              {"pos": 9, "kind": "low", "price": 80.0}]
    levels = chartread.levels(pivots, [f"d{index}" for index in range(10)], close=90.0, tolerance=2.0)
    assert levels == [{"price": 100.5, "role": "저항", "touches": 2, "last": "d5", "distance": 0.1167}]


def test_levels_keep_the_nearest_two_when_everything_is_far_away():
    pivots = [{"pos": 0, "kind": "low", "price": 10.0}, {"pos": 1, "kind": "low", "price": 10.2},
              {"pos": 2, "kind": "high", "price": 20.0}, {"pos": 3, "kind": "high", "price": 20.4}]
    levels = chartread.levels(pivots, ["a", "b", "c", "d"], close=100.0, tolerance=1.0)
    assert [item["price"] for item in levels] == [20.2, 10.1]


def test_box_is_the_longest_recent_stretch_that_stays_inside_the_band():
    close = np.r_[np.full(40, 50.0), 100 + np.tile([0.0, 2.0, -2.0, 1.0], 10)]
    box = chartread.box(close * 1.01, close * 0.99, close, max_width=0.12, min_bars=15)
    # 박스는 마지막 봉에서 뒤로 넓히다 폭이 터지는 자리에서 멈춘다 — 급등 이전은 끌고 오지 않는다.
    assert box["exists"] and box["bars"] == 40 and box["low"] > 90


def test_box_does_not_exist_when_price_is_still_moving():
    close = np.linspace(100, 200, 60)
    assert chartread.box(close * 1.01, close * 0.99, close, max_width=0.12, min_bars=15)["exists"] is False


# --- 사건 ---------------------------------------------------------------

def test_events_report_gaps_and_whether_they_were_filled():
    close = np.r_[np.full(30, 100.0), np.full(30, 130.0)]
    frame = _frame(close)
    frame.loc[30, "open"] = 130.0            # 100 → 130 갭상승, 이후 되돌아오지 않는다
    payload = chartread.read(frame, window=60)
    gaps = [item for item in payload["events"] if item["kind"] == "갭상승"]
    assert gaps and gaps[0]["filled"] is False and gaps[0]["gap_pct"] == pytest.approx(0.3)


def test_events_flag_volume_spikes_against_the_prior_twenty_days():
    volume = np.full(60, 1000.0)
    volume[45] = 5000.0
    payload = chartread.read(_frame(np.linspace(100, 120, 60), volume=volume), window=60)
    spikes = [item for item in payload["events"] if item["kind"] == "거래량급증"]
    assert [item["vol_ratio20"] for item in spikes] == [5.0]


def test_volume_block_reports_where_the_traded_volume_sits_against_price():
    close = np.linspace(100, 120, 60)
    volume = np.full(60, 1000.0)
    volume[40:45] = 20000.0  # 거래량이 창의 아래쪽 가격대에 몰린 상승 — 매물대가 현재가 밑이다
    payload = chartread.read(_frame(close, volume=volume), window=60)
    assert payload["volume"]["vwma_spread20"] < 0
    assert chartread.read(_frame(close), window=60)["volume"]["vwma_spread20"] == pytest.approx(0, abs=5e-4)


# --- 전처리·조립 --------------------------------------------------------

def test_halted_bars_are_excluded_but_still_counted():
    halted = np.zeros(60, dtype=int)
    halted[[10, 11]] = 1
    payload = chartread.read(_frame(np.linspace(100, 120, 60), halted=halted), window=60)
    assert payload["bars_available"] == 58 and payload["halted_bars"] == 2


def test_the_window_starts_after_an_unadjusted_price_break():
    # 액면병합으로 종가가 하루 만에 5배 — 그 이전 구간을 끌고 오면 이평선과 스윙이 전부 어긋난다.
    close = np.r_[np.full(40, 100.0), np.full(40, 500.0)]
    payload = chartread.read(_frame(close), window=250)
    assert payload["price_jump_flag"] is True and payload["bars_available"] == 40


def test_reading_needs_enough_bars_to_have_a_structure():
    with pytest.raises(ValueError, match="봉이 모자랍니다"):
        chartread.read(_frame(np.linspace(100, 110, 12)))


# --- query 계층 ---------------------------------------------------------

@pytest.fixture()
def store(monkeypatch, tmp_path):
    path = tmp_path / "chart.db"
    init_db(path)
    monkeypatch.setattr("mscr.db.DB_PATH", path)
    close = np.r_[np.linspace(1000, 1400, 60), np.linspace(1400, 1200, 40)]
    frame = _frame(close)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,region,market,is_preferred,is_spac,first_seen,last_seen,delisted)"
                   " VALUES('005930','삼성전자','stock','KR','KOSPI',0,0,'2025-01-01','2025-05-22',0)")
        db.executemany(
            "INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted)"
            " VALUES('005930',?,'krx_snapshot',?,?,?,?,?,?,NULL,?)",
            [(row.date, row.open, row.high, row.low, row.close, row.volume, row.value, row.halted)
             for row in frame.itertuples()])
    return path


def test_query_chart_describes_the_active_market_ticker(store):
    with market.use("kr"):
        payload = query.chart("005930", window=100)
        assert payload["name"] == "삼성전자" and payload["currency"] == "KRW"
        assert payload["window"]["bars"] == 100
        assert [segment["kind"] for segment in payload["segments"]] == ["상승", "하락"]
        assert json_safe(payload)
        with pytest.raises(ValueError, match="등록되지 않은"):
            query.chart("000001")


def _candles(monkeypatch, frame: pd.DataFrame) -> None:
    from mscr.providers import alphasquare

    monkeypatch.setattr(alphasquare.AlphaSquareProvider, "_resolve", lambda self, ticker: 1)
    monkeypatch.setattr(alphasquare.AlphaSquareProvider, "candles", lambda self, ticker, freq, count=1000: frame)


def test_query_chart_reads_alphasquare_candles_and_says_so(store, monkeypatch):
    close = np.linspace(1000, 1300, 80)
    _candles(monkeypatch, _frame(close)[["date", "open", "high", "low", "close", "volume"]])
    with market.use("kr"):
        payload = query.chart("005930", window=60, source="alphasquare")
    # 수정주가가 아니라는 사실은 원천과 함께 반드시 따라다녀야 한다.
    assert payload["source"] == "alphasquare" and payload["freq"] == "day" and payload["adjusted"] is False
    assert payload["segments"][-1]["kind"] == "상승"


def test_query_chart_turns_minute_epochs_back_into_local_wall_clock(store, monkeypatch):
    stamps = pd.date_range("2026-09-11 09:00", periods=40, freq="5min")
    frame = _frame(np.linspace(1000, 1100, 40))
    frame["date"] = (stamps.view("int64") // 10 ** 9).astype("int64")
    _candles(monkeypatch, frame[["date", "open", "high", "low", "close", "volume"]])
    with market.use("kr"):
        payload = query.chart("005930", window=40, source="alphasquare", freq="minute-5")
    assert payload["freq"] == "minute-5"
    assert payload["window"] == {"from": "2026-09-11 09:00", "to": "2026-09-11 12:15", "bars": 40}


def test_query_chart_rejects_sources_and_frequencies_it_cannot_read(store):
    with market.use("kr"):
        with pytest.raises(ValueError, match="지원하지 않는 원천"):
            query.chart("005930", source="yahoo")
        with pytest.raises(ValueError, match="지원하지 않는 주기"):
            query.chart("005930", source="alphasquare", freq="week")


def test_query_chart_prefers_cached_adjusted_bars_when_they_cover_the_window(store):
    with market.use("kr"):
        assert query.chart("005930", window=40)["adjusted"] is False
    with db_session(store) as db:
        db.executemany(
            "INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted)"
            " VALUES('005930',?,'adjusted',?,?,?,?,?,?,NULL,0)",
            [(row.date, row.open, row.high, row.low, row.close, row.volume, row.value)
             for row in _frame(np.linspace(500, 700, 100)).itertuples()])
    with market.use("kr"):
        # 화면 차트가 그리는 계열과 같은 것을 설명해야 한다 — 수정주가가 캐시돼 있으면 그걸 쓴다.
        assert query.chart("005930", window=40)["adjusted"] is True


def json_safe(payload) -> bool:
    import json
    json.loads(query.dumps(payload))
    return True
