from __future__ import annotations

import math

import pytest

from mscr.autoplan import MIN_QUANTITY, SPLIT, STOP_MULTIPLES, TARGET_MULTIPLES, _STATS_CACHE, _tick_size, propose
from mscr.db import db_session, init_db
from mscr.trading import _leg_quantities, evaluate_plans, save_plan


def _synthetic_bars(db, ticker: str, count: int, start: float, amplitude: float, period: int, drift: float) -> None:
    """상승·하락 구간이 모두 나오는 결정론적 지그재그. excursion 통계가 한쪽으로 쏠리지 않는다."""
    for index in range(count):
        day = f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}"
        price = start * (1 + amplitude * math.sin(2 * math.pi * index / period)) + drift * index
        span = price * 0.02
        db.execute(
            "INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, day, "krx_snapshot", price, price + span / 2, price - span / 2, price, 1000, 1_000_000, None, 0))


# 호가 단위 구간마다 하나씩. 변동성이 가격에 비례하므로 어느 구간에서도 손절폭이 1틱보다 넓다.
PRICE_BANDS = (1_500.0, 3_000.0, 12_000.0, 30_000.0, 120_000.0, 300_000.0, 600_000.0)


def _band_ticker(price: float) -> str:
    return f"9{PRICE_BANDS.index(price):05d}"


@pytest.fixture()
def store(tmp_path):
    _STATS_CACHE.clear()
    path = tmp_path / "autoplan.db"
    init_db(path)
    with db_session(path) as db:
        def add(ticker: str, label: str, start: float, amplitude: float, period: int, drift: float) -> None:
            db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock','KOSPI',0,0,'2026-01-01','2026-12-31',0)", (ticker, label))
            _synthetic_bars(db, ticker, 200, start=start, amplitude=amplitude, period=period, drift=drift)

        for index in range(20):
            add(f"{index:06d}", f"종목{index}", 1000.0 + index * 10, 0.05 + index * 0.005, 18 + index, 0.3 * (index % 3 - 1))
        for index, price in enumerate(PRICE_BANDS):
            add(_band_ticker(price), f"가격대{index}", price, 0.06, 21 + index, price * 0.0003 * (index % 3 - 1))
    return path


def test_quantity_respects_both_caps_and_reports_the_binding_one(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        assert candidate["invested"] <= 5_000_000
        assert candidate["max_loss_krw"] <= 200_000
        assert candidate["quantity"] == min(int(5_000_000 // 1000.0), int(200_000 // candidate["stop_distance"]))
    assert result["binding"] == "max_loss"

    tight = propose("000000", "buy", 1000.0, 12_000, 12_000, path=store)
    assert tight["binding"] == "max_investment"
    assert tight["candidates"][tight["recommended"]]["quantity"] == 12


def test_max_loss_is_never_exceeded_by_the_stop_leg(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        # 진입 직후 손절이면 전량이 손절폭만큼 손실이다. 이 값이 한도를 넘으면 사이징이 틀린 것이다.
        assert candidate["quantity"] * candidate["stop_distance"] <= 200_000 + 1e-9


def test_leg_quantities_are_integers_summing_to_plan_quantity(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        legs = candidate["leg_quantities"]
        assert all(isinstance(value, int) and value >= 1 for value in legs)
        assert sum(legs) == candidate["quantity"]
        # 엔진이 비율로 되돌려 계산해도 같은 정수가 나와야 한다.
        assert list(_leg_quantities(candidate["quantity"], candidate["tp1_ratio"], candidate["tp2_ratio"])) == legs


def test_ratios_satisfy_the_plan_schema_constraints(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        assert 0 < candidate["tp1_ratio"] < 1 and 0 < candidate["tp2_ratio"] < 1
        assert candidate["tp1_ratio"] + candidate["tp2_ratio"] < 1
        assert candidate["tp3_trailing_pct"] > 0


def test_every_usable_candidate_saves_and_evaluates(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    saved = 0
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        plan = dict(result["plan"], name=f"자동-{candidate['stop_atr_multiple']}", quantity=candidate["quantity"],
                    stop_price=candidate["stop_price"], tp1_price=candidate["tp1_price"], tp1_ratio=candidate["tp1_ratio"],
                    tp2_price=candidate["tp2_price"], tp2_ratio=candidate["tp2_ratio"], tp3_trailing_pct=candidate["tp3_trailing_pct"])
        save_plan(plan, path=store)
        saved += 1
    assert saved >= 3
    evaluations = evaluate_plans(path=store)
    assert len(evaluations) == saved
    for evaluation in evaluations:
        assert evaluation["phase"] == "waiting_entry"
        assert float(evaluation["order_quantity"]).is_integer()


def test_price_ordering_matches_side(store):
    long_side = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in long_side["candidates"]:
        if candidate["rejected"]: continue
        assert candidate["stop_price"] < 1000.0 < candidate["tp1_price"] < candidate["tp2_price"]
    short_side = propose("000000", "sell", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in short_side["candidates"]:
        if candidate["rejected"]: continue
        assert candidate["stop_price"] > 1000.0 > candidate["tp1_price"] > candidate["tp2_price"]


def test_target_distances_are_multiples_of_the_stop_distance(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    m1, m2 = TARGET_MULTIPLES
    tick = result["tick_size"]
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        # 목표가는 손절폭의 배수를 호가 단위로 맞춘 값이므로 반 틱까지 벗어날 수 있다.
        assert abs(candidate["tp1_price"] - 1000.0 - m1 * candidate["stop_distance"]) <= tick / 2 + 1e-9
        assert abs(candidate["tp2_price"] - 1000.0 - m2 * candidate["stop_distance"]) <= tick / 2 + 1e-9
        assert abs(candidate["stop_distance"] - candidate["stop_atr_multiple"] * result["atr"]) <= tick + 1e-9


def test_recommended_candidate_is_usable_and_matches_plan(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    chosen = result["candidates"][result["recommended"]]
    assert chosen["rejected"] is None
    assert result["recommendation_reason"]
    assert result["plan"]["quantity"] == chosen["quantity"]
    assert result["plan"]["stop_price"] == chosen["stop_price"]
    assert result["plan"]["order_type"] == "limit"
    assert result["plan"]["limit_price"] == result["plan"]["entry_price"] == 1000.0


def test_thin_loss_budget_is_rejected_with_the_required_amount(store):
    with pytest.raises(ValueError, match="최대 손실 금액이"):
        propose("000000", "buy", 1000.0, 5_000_000, 1.0, path=store)


def test_candidates_below_minimum_quantity_are_rejected_not_dropped(store):
    unit = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)["atr"]
    # 손절폭 2·ATR까지는 3주가 되고 그보다 넓으면 안 되는 손실 한도.
    result = propose("000000", "buy", 1000.0, 5_000_000, MIN_QUANTITY * 2.0 * unit, path=store)
    assert len(result["candidates"]) == len(STOP_MULTIPLES)
    rejected = [item for item in result["candidates"] if item["rejected"]]
    assert rejected and all("3분할" in item["rejected"] for item in rejected)
    assert all(item["quantity"] < MIN_QUANTITY for item in rejected)
    assert all(item["stop_atr_multiple"] > 2.0 for item in rejected)


def test_probabilities_and_expectancy_are_within_bounds(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    m1, m2 = TARGET_MULTIPLES
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        assert 0.0 <= candidate["reach_tp2_prob"] <= candidate["reach_tp1_prob"] <= 1.0
        assert 0.0 <= candidate["breakeven_tp1_prob"] <= 1.0
        # 기대값은 전량 두 목표 도달과 전량 손절 사이에 있어야 한다.
        assert -1.0 <= candidate["baseline_expectancy_r"] <= m1 + m2
        assert math.isfinite(candidate["baseline_expectancy_r"])


def test_wider_stop_means_fewer_shares_and_farther_targets(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    usable = [item for item in result["candidates"] if item["rejected"] is None]
    for previous, current in zip(usable, usable[1:]):
        assert current["stop_distance"] > previous["stop_distance"]
        assert current["quantity"] <= previous["quantity"]
        assert current["tp1_price"] > previous["tp1_price"]
        assert current["reach_tp1_prob"] <= previous["reach_tp1_prob"]


def test_statistics_are_cached_per_snapshot_date(store):
    propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    assert len(_STATS_CACHE) == 1
    propose("000001", "buy", 1010.0, 5_000_000, 200_000, path=store)
    assert len(_STATS_CACHE) == 1, "ATR 단위 통계는 종목과 무관하므로 재계산하면 안 된다"


def test_input_validation(store):
    with pytest.raises(ValueError, match="종목코드"):
        propose("12345", "buy", 1000.0, 5_000_000, 200_000, path=store)
    with pytest.raises(ValueError, match="등록되지 않은"):
        propose("999999", "buy", 1000.0, 5_000_000, 200_000, path=store)
    with pytest.raises(ValueError, match="매매 구분"):
        propose("000000", "hold", 1000.0, 5_000_000, 200_000, path=store)
    with pytest.raises(ValueError, match="진입가는"):
        propose("000000", "buy", 0.0, 5_000_000, 200_000, path=store)
    with pytest.raises(ValueError, match="최대 손실 금액이 최대 투자 금액보다"):
        propose("000000", "buy", 1000.0, 100_000, 200_000, path=store)
    with pytest.raises(ValueError, match="1주도 살 수 없습니다"):
        propose("000000", "buy", 200_000.0, 100_000, 50_000, path=store)


def test_split_weights_follow_the_configured_shape(store):
    result = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        quantity = candidate["quantity"]
        assert candidate["leg_quantities"][0] == round(quantity * SPLIT[0])
        assert candidate["leg_quantities"][1] == round(quantity * SPLIT[1])


def test_prices_land_on_krx_tick_boundaries(store):
    for entry in PRICE_BANDS:
        result = propose(_band_ticker(entry), "buy", entry, 1_000_000_000, 900_000_000, path=store)
        tick = result["tick_size"]
        assert tick == _tick_size(entry, "stock")
        usable = [item for item in result["candidates"] if item["rejected"] is None]
        assert usable, entry
        for candidate in usable:
            for key in ("stop_price", "tp1_price", "tp2_price"):
                assert candidate[key] % tick == pytest.approx(0.0, abs=1e-6), (entry, key, candidate[key], tick)


def test_tick_alignment_never_pushes_loss_past_the_cap(store):
    # 손절가 정렬은 진입가 쪽으로만 가야 한다. 반대로 가면 정렬만으로 한도가 깨진다.
    for band in PRICE_BANDS:
        entry = band - 1.0  # 호가 경계에서 살짝 벗어난 진입가
        result = propose(_band_ticker(band), "buy", entry, 1_000_000_000, 30_000_000, path=store)
        usable = [item for item in result["candidates"] if item["rejected"] is None]
        assert usable, band
        for candidate in usable:
            assert candidate["stop_price"] >= entry - candidate["stop_atr_multiple"] * result["atr"]
            assert candidate["quantity"] * candidate["stop_distance"] <= 30_000_000 + 1e-9


def test_short_side_stop_aligns_away_from_the_stop_not_into_it(store):
    result = propose(_band_ticker(120_000.0), "sell", 120_000.0, 1_000_000_000, 30_000_000, path=store)
    usable = [item for item in result["candidates"] if item["rejected"] is None]
    assert usable
    for candidate in usable:
        assert candidate["stop_price"] <= 120_000.0 + candidate["stop_atr_multiple"] * result["atr"]
        assert candidate["quantity"] * candidate["stop_distance"] <= 30_000_000 + 1e-9


def test_etf_keeps_the_five_won_tick(store):
    with db_session(store) as db:
        db.execute("UPDATE instruments SET kind='etf' WHERE ticker='000000'")
    result = propose("000000", "buy", 1_000.0, 5_000_000, 200_000, path=store)
    assert result["tick_size"] == 5.0
    for candidate in result["candidates"]:
        if candidate["rejected"]: continue
        assert candidate["stop_price"] % 5 == pytest.approx(0.0, abs=1e-6)


def test_tick_size_ladder_boundaries():
    assert [_tick_size(price, "stock") for price in (1_999, 2_000, 4_999, 5_000, 19_999, 20_000, 49_999, 50_000, 199_999, 200_000, 499_999, 500_000)] == \
           [1.0, 5.0, 5.0, 10.0, 10.0, 50.0, 50.0, 100.0, 100.0, 500.0, 500.0, 1_000.0]
    assert _tick_size(1_000_000, "etf") == 5.0


def test_entry_off_tick_is_warned(store):
    ticker = _band_ticker(120_000.0)
    result = propose(ticker, "buy", 120_050.0, 1_000_000_000, 30_000_000, path=store)
    assert any("호가 단위" in warning for warning in result["warnings"])
    aligned = propose(ticker, "buy", 120_000.0, 1_000_000_000, 30_000_000, path=store)
    assert not any("호가 단위" in warning for warning in aligned["warnings"])


def test_binding_constraint_is_always_named_in_the_warnings(store):
    loss_bound = propose("000000", "buy", 1000.0, 5_000_000, 200_000, path=store)
    assert loss_bound["binding"] == "max_loss"
    assert "최대 손실 금액이 수량을 결정" in loss_bound["warnings"][0]
    budget_bound = propose("000000", "buy", 1000.0, 12_000, 12_000, path=store)
    assert budget_bound["binding"] == "max_investment"
    assert "최대 투자 금액이 수량을 결정" in budget_bound["warnings"][0]
