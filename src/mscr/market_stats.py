from __future__ import annotations

import bisect
from datetime import date as date_cls
from typing import Any

import numpy as np
import pandas as pd

from .db import db_session

LIMIT_PCT = 29.5
NEW_EXTREME_WINDOW = 250
VOLUME_SURGE_WINDOW = 20
LOOKBACK_TRADING_DAYS = 280  # NEW_EXTREME_WINDOW + 결측/거래정지 여유분
RANK_SIZE = 50


def available_dates(path=None) -> list[str]:
    with db_session(path) as db:
        rows = db.execute("SELECT DISTINCT date FROM daily_bars WHERE source='krx_snapshot' ORDER BY date").fetchall()
    return [row[0] for row in rows]


def _nearest_date(dates: list[str], target: str) -> str:
    idx = bisect.bisect_left(dates, target)
    candidates = [d for d in (dates[idx] if idx < len(dates) else None, dates[idx - 1] if idx > 0 else None) if d]
    target_day = date_cls.fromisoformat(target)
    return min(candidates, key=lambda d: abs((date_cls.fromisoformat(d) - target_day).days))


def _clean(value: Any) -> Any:
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if value is None or value is pd.NaT:
        return None
    return value


def _row_dict(row: pd.Series, keys: list[str]) -> dict[str, Any]:
    return {key: _clean(row.get(key)) for key in keys}


def _quantiles(series: pd.Series) -> dict[str, float | None]:
    series = series.dropna()
    if series.empty:
        return {"q1": None, "median": None, "q3": None}
    return {"q1": _clean(series.quantile(0.25)), "median": _clean(series.quantile(0.5)), "q3": _clean(series.quantile(0.75))}


def _breadth(valid: pd.DataFrame, all_today: pd.DataFrame) -> dict[str, Any]:
    return {
        "count": int(len(all_today)),
        "up": int((valid["change_pct"] > 0).sum()),
        "down": int((valid["change_pct"] < 0).sum()),
        "flat": int((valid["change_pct"] == 0).sum()),
        "limit_up": int((valid["change_pct"] >= LIMIT_PCT).sum()),
        "limit_down": int((valid["change_pct"] <= -LIMIT_PCT).sum()),
        "new_high": int(valid["new_high"].fillna(False).sum()),
        "new_low": int(valid["new_low"].fillna(False).sum()),
        "halted": int(all_today["halted"].sum()),
    }


def compute(date: str, path=None) -> dict[str, Any]:
    try:
        date_cls.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)") from exc
    dates = available_dates(path)
    if not dates:
        raise ValueError("수집된 시세 데이터가 없습니다")
    resolved = date if date in dates else _nearest_date(dates, date)
    idx = dates.index(resolved)
    window_dates = dates[max(0, idx - LOOKBACK_TRADING_DAYS + 1): idx + 1]
    start_date = window_dates[0]
    prev_date = window_dates[-2] if len(window_dates) > 1 else None

    with db_session(path) as db:
        instruments = pd.DataFrame([dict(r) for r in db.execute(
            "SELECT ticker,name,kind,market,category FROM instruments WHERE is_preferred=0 AND is_spac=0"
        ).fetchall()])
        bars = pd.DataFrame([dict(r) for r in db.execute(
            "SELECT ticker,date,open,high,low,close,volume,value,nav,halted FROM daily_bars WHERE source='krx_snapshot' AND date>=? AND date<=?",
            (start_date, resolved),
        ).fetchall()])
        fundamentals = pd.DataFrame([dict(r) for r in db.execute(
            # snapshots_fundamental은 수집 시점의 as_of를 KRX 원본 포맷(YYYYMMDD, 대시 없음)으로 저장한다.
            "SELECT ticker,per,pbr,market_cap,div FROM snapshots_fundamental WHERE date=?", (resolved.replace("-", ""),)
        ).fetchall()])

    if instruments.empty or bars.empty:
        raise ValueError("해당 날짜의 시세 데이터가 없습니다")
    bars = bars.merge(instruments, on="ticker", how="inner")

    # 거래정지일은 open=high=low=volume=0으로 기록되어, 그대로 롤링에 섞이면 52주 저가·거래량
    # 평균이 0에 끌려간다. 정지일을 제거한 시계열로 롤링 지표를 만들고 당일 스냅샷에 붙인다.
    clean = bars[bars["halted"] == 0].sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = clean.groupby("ticker", sort=False)
    clean["prev_close"] = grouped["close"].shift(1)
    clean["roll_high"] = grouped["high"].transform(lambda s: s.rolling(NEW_EXTREME_WINDOW, min_periods=NEW_EXTREME_WINDOW).max())
    clean["roll_low"] = grouped["low"].transform(lambda s: s.rolling(NEW_EXTREME_WINDOW, min_periods=NEW_EXTREME_WINDOW).min())
    clean["prior_vol_avg"] = grouped["volume"].transform(lambda s: s.shift(1).rolling(VOLUME_SURGE_WINDOW, min_periods=VOLUME_SURGE_WINDOW).mean())
    today_clean = clean.loc[clean["date"] == resolved, ["ticker", "prev_close", "roll_high", "roll_low", "prior_vol_avg"]]

    today = bars[bars["date"] == resolved].copy()
    today["halted"] = today["halted"].astype(bool)
    if not fundamentals.empty:
        today = today.merge(fundamentals, on="ticker", how="left")
    else:
        for col in ("per", "pbr", "market_cap", "div"):
            today[col] = np.nan

    active = today[~today["halted"]].merge(today_clean, on="ticker", how="left")
    active["change_pct"] = np.where(active["prev_close"] > 0, (active["close"] / active["prev_close"] - 1) * 100, np.nan)
    active["vol_ratio"] = np.where(active["prior_vol_avg"] > 0, active["volume"] / active["prior_vol_avg"], np.nan)
    active["new_high"] = active["high"] >= active["roll_high"]
    active["new_low"] = active["low"] <= active["roll_low"]
    active["premium_pct"] = np.where((active["kind"] == "etf") & (active["nav"] > 0), (active["close"] / active["nav"] - 1) * 100, np.nan)

    breadth = {
        "all": _breadth(active, today),
        "kospi": _breadth(active[active["market"] == "KOSPI"], today[today["market"] == "KOSPI"]),
        "kosdaq": _breadth(active[active["market"] == "KOSDAQ"], today[today["market"] == "KOSDAQ"]),
        "etf": _breadth(active[active["kind"] == "etf"], today[today["kind"] == "etf"]),
    }

    prev_value_sum = None
    if prev_date:
        prev_rows = bars[bars["date"] == prev_date]
        prev_value_sum = _clean(prev_rows["value"].fillna(0).sum()) if not prev_rows.empty else None
    volume = {
        "value_sum": _clean(today["value"].fillna(0).sum()),
        "value_sum_prev": prev_value_sum,
        "volume_sum": _clean(today["volume"].fillna(0).sum()),
        "market_cap_sum": {market: _clean(today.loc[today["market"] == market, "market_cap"].fillna(0).sum()) for market in ("KOSPI", "KOSDAQ")},
    }

    rank_cols = ["ticker", "name", "market", "close", "change_pct", "value", "volume", "vol_ratio", "premium_pct"]

    def _top(frame: pd.DataFrame, by: str, ascending: bool) -> list[dict[str, Any]]:
        subset = frame.dropna(subset=[by]).sort_values(by, ascending=ascending).head(RANK_SIZE)
        return [_row_dict(row, rank_cols) for _, row in subset.iterrows()]

    # ETF는 별도 "ETF 통계" 섹션에서 다루므로, 일반 Top 랭킹은 주식만 대상으로 한다.
    stocks = active[active["kind"] == "stock"]
    rankings = {
        "value_top": _top(stocks, "value", False),
        "volume_surge_top": _top(stocks, "vol_ratio", False),
        "gainers_top": _top(stocks, "change_pct", False),
        "losers_top": _top(stocks, "change_pct", True),
    }

    etf = active[active["kind"] == "etf"].assign(abs_premium=lambda f: f["premium_pct"].abs())
    etf_rankings = {
        "value_top": _top(etf, "value", False),
        "gainers_top": _top(etf, "change_pct", False),
        "losers_top": _top(etf, "change_pct", True),
        "premium_top": _top(etf, "abs_premium", False),
    }

    # instruments.category/base_index는 이 데이터셋에서 항상 비어 있어(주식 provider가 업종 분류를
    # 채우지 않음) 업종별 통계 대신 실제로 채워지는 시가총액 구간으로 시장 폭을 나눠 보여준다.
    sectors = []
    capped = stocks[stocks["market_cap"] > 0]
    if not capped.empty:
        bands = [("대형주 (1조원 이상)", 1e12, float("inf")), ("중형주 (1천억~1조원)", 1e11, 1e12), ("소형주 (1천억원 미만)", 0, 1e11)]
        for label, low, high in bands:
            group = capped[(capped["market_cap"] >= low) & (capped["market_cap"] < high)]
            if group.empty:
                continue
            sectors.append({
                "category": label,
                "count": int(len(group)),
                "avg_change_pct": _clean(group["change_pct"].mean()),
                "value_sum": _clean(group["value"].fillna(0).sum()),
            })

    valuation = {
        "per": _quantiles(stocks.loc[stocks["per"] > 0, "per"]),
        "pbr": _quantiles(stocks.loc[stocks["pbr"] > 0, "pbr"]),
        "div_avg": _clean(stocks.loc[stocks["div"] > 0, "div"].mean()),
    }

    counts = {
        "total": int(len(today)),
        "stock": int((today["kind"] == "stock").sum()),
        "etf": int((today["kind"] == "etf").sum()),
        "kospi": int((today["market"] == "KOSPI").sum()),
        "kosdaq": int((today["market"] == "KOSDAQ").sum()),
    }

    return {
        "date": resolved,
        "requested_date": date,
        "prev_date": prev_date,
        "counts": counts,
        "breadth": breadth,
        "volume": volume,
        "rankings": rankings,
        "etf_rankings": etf_rankings,
        "sectors": sectors,
        "valuation": valuation,
    }
