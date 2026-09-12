"""Massive(massive.com) 미국 주식 시세 프로바이더.

전종목 일봉은 "일별 시장 요약"(`/v2/aggs/grouped/...`) 한 번의 호출로 하루치를 통째로 받는다.
KRX 수집이 거래일마다 두 번(주식·ETF) 호출하는 것과 같은 모양이라, 수집 루프는 그대로 두고
소스만 갈아 끼운다.

유니버스는 `/v3/reference/tickers`를 커서 페이지네이션으로 훑어 종목명·거래소·종류를 채운다.

무료 플랜은 분당 5회 제한이라 호출 사이에 기본 12초를 쉰다(`massive_request_delay_sec`로 조절).
유료 플랜이면 0으로 낮추면 된다.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..config import REQUEST_RETRIES
from ..credentials import clear, load, mask, update

CREDENTIAL_NAME = "massive_credentials"
BASE_URL = os.environ.get("MSCR_MASSIVE_BASE_URL") or "https://api.massive.com"
SIGNUP_URL = "https://massive.com/dashboard/keys"
# 무료 플랜(분당 5회) 기준. 유료 플랜이면 설정에서 0으로 낮춘다.
DEFAULT_DELAY_SEC = 12.0
EASTERN = ZoneInfo("America/New_York")
LATEST_DAY_LOOKBACK = 10

# primary_exchange는 ISO 10383 MIC로 온다. 화면·필터에서 쓰는 이름으로 접어 준다.
EXCHANGES = {
    "XNAS": "NASDAQ", "XNGS": "NASDAQ", "XNMS": "NASDAQ", "XNCM": "NASDAQ",
    "XNYS": "NYSE", "ARCX": "NYSE", "XASE": "AMEX", "BATS": "CBOE", "XCBO": "CBOE", "EDGX": "CBOE",
}
# ETF·ETN·펀드류는 kind='etf'로, 나머지는 주식으로 본다.
ETF_TYPES = {"ETF", "ETN", "ETV", "ETS", "FUND", "BASKET"}
PREFERRED_TYPES = {"PFD", "PFDPF"}


class MassiveError(RuntimeError):
    pass


def massive_credentials() -> dict[str, str]:
    stored = load(CREDENTIAL_NAME)
    return {"api_key": os.environ.get("MASSIVE_API_KEY") or str(stored.get("api_key") or "")}


def massive_status() -> dict[str, Any]:
    resolved = massive_credentials()["api_key"]
    source = ("env" if os.environ.get("MASSIVE_API_KEY") else "file") if resolved else None
    return {
        "mode": "apikey" if resolved else "anonymous", "source": source,
        "api_key_masked": mask(resolved), "stored": sorted(load(CREDENTIAL_NAME)),
        "signup_url": SIGNUP_URL, "request_delay_sec": massive_delay(),
    }


def save_massive_credentials(api_key: str | None = None) -> dict[str, Any]:
    update(CREDENTIAL_NAME, {"api_key": api_key})
    return massive_status()


def clear_massive_credentials() -> dict[str, Any]:
    clear(CREDENTIAL_NAME)
    return massive_status()


def massive_delay() -> float:
    raw = os.environ.get("MSCR_MASSIVE_DELAY_SEC") or load("settings").get("massive_request_delay_sec")
    try:
        return min(60.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_DELAY_SEC


def save_massive_delay(seconds: float) -> float:
    from ..credentials import save

    value = min(60.0, max(0.0, float(seconds)))
    save("settings", load("settings") | {"massive_request_delay_sec": value})
    return value


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def eastern_today() -> date:
    return pd.Timestamp.now(tz=EASTERN).date()


class MassiveProvider:
    """일별 시장 요약과 종목 목록만 쓰는 최소 구현. 실시간·틱 엔드포인트는 쓰지 않는다."""

    def __init__(self, api_key: str | None = None, delay: float | None = None):
        self.api_key = api_key or massive_credentials()["api_key"]
        if not self.api_key:
            raise MassiveError(f"Massive API 키가 없습니다. MASSIVE_API_KEY 환경변수나 `mscr config massive --api-key`로 설정하세요 (발급: {SIGNUP_URL}).")
        self.delay = massive_delay() if delay is None else max(0.0, float(delay))
        self.session = requests.Session()
        self.session.headers.update({"authorization": f"Bearer {self.api_key}", "accept": "application/json"})
        self._last_call = 0.0

    def _sleep(self) -> None:
        remaining = self.delay - (time.monotonic() - self._last_call)
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}{path_or_url}"
        last_error: Exception | None = None
        for attempt in range(REQUEST_RETRIES):
            self._sleep()
            try:
                response = self.session.get(url, params=params, timeout=30)
                self._last_call = time.monotonic()
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(1.0 * (attempt + 1))
                continue
            if response.status_code == 429:
                # 분당 한도에 걸렸다. 설정한 지연이 짧은 것이므로 넉넉히 쉬고 다시 시도한다.
                last_error = MassiveError("요청 한도 초과(429)")
                time.sleep(max(self.delay, 15.0) * (attempt + 1))
                continue
            if response.status_code == 401:
                raise MassiveError(f"Massive API 키가 거부되었습니다(401). 키를 다시 확인하세요 (발급: {SIGNUP_URL}).")
            if response.status_code == 403:
                raise MassiveError(f"플랜에서 허용하지 않는 요청입니다(403): {response.text[:200]}")
            if response.status_code >= 400:
                last_error = MassiveError(f"Massive 호출 실패: {response.status_code} {response.text[:200]}")
                if response.status_code < 500:
                    raise last_error
                time.sleep(1.0 * (attempt + 1))
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                raise MassiveError(f"Massive 응답을 해석할 수 없습니다: {response.text[:200]}") from exc
            if str(payload.get("status", "")).upper() == "ERROR":
                raise MassiveError(f"Massive 오류: {payload.get('error') or payload}")
            return payload
        raise MassiveError(f"Massive 통신 실패: {last_error}")

    def _paged(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        payload = self._get(path, params)
        while True:
            for item in payload.get("results") or []:
                yield item
            next_url = payload.get("next_url")
            if not next_url:
                return
            payload = self._get(next_url)

    def daily_snapshot(self, day: str) -> pd.DataFrame:
        """하루치 전종목 일봉. `day`는 YYYY-MM-DD. 휴장일이면 빈 프레임을 돌려준다."""
        payload = self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{day}", {"adjusted": "true"})
        rows = []
        for item in payload.get("results") or []:
            ticker = str(item.get("T") or "").strip().upper()
            if not ticker:
                continue
            close, volume, vwap = _number(item.get("c")), _number(item.get("v")), _number(item.get("vw"))
            # Massive는 거래대금을 따로 주지 않는다. 거래량×VWAP(없으면 종가)로 근사한다.
            reference = vwap if vwap and vwap > 0 else close
            rows.append({
                "ticker": ticker, "open": _number(item.get("o")), "high": _number(item.get("h")),
                "low": _number(item.get("l")), "close": close, "volume": volume,
                "value": (volume * reference) if volume is not None and reference else None,
            })
        return pd.DataFrame(rows, columns=["ticker", "open", "high", "low", "close", "volume", "value"])

    def latest_trading_day(self) -> str:
        """데이터가 실제로 게시된 최신 날짜. 미 동부 오늘부터 거슬러 올라가며 확인한다."""
        today = eastern_today()
        for back in range(LATEST_DAY_LOOKBACK):
            day = today - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            try:
                frame = self.daily_snapshot(day.isoformat())
            except MassiveError as exc:
                # 무료 플랜은 당일 데이터를 주지 않을 수 있다. 하루 더 거슬러 올라가 본다.
                if "403" not in str(exc):
                    raise
                continue
            if not frame.empty:
                return day.isoformat()
        raise MassiveError(f"최근 {LATEST_DAY_LOOKBACK}일 안에 데이터가 있는 거래일을 찾지 못했습니다.")

    def trading_days(self, start: date, end: date) -> list[pd.Timestamp]:
        """미국 시장 휴일 달력은 따로 받지 않는다. 평일을 후보로 돌리고, 데이터가 빈 날을 휴장으로 기록한다."""
        return list(pd.bdate_range(start, end))

    def universe(self) -> pd.DataFrame:
        """상장 중인 미국 주식·ETF 목록. 한 페이지 1000건씩 커서로 넘긴다."""
        rows = []
        for item in self._paged("/v3/reference/tickers", {"market": "stocks", "active": "true", "limit": 1000}):
            ticker = str(item.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            type_code = str(item.get("type") or "").upper()
            name = str(item.get("name") or ticker)
            rows.append({
                "ticker": ticker, "name": name,
                "kind": "etf" if type_code in ETF_TYPES else "stock",
                "market": EXCHANGES.get(str(item.get("primary_exchange") or "").upper(), "OTHER"),
                "category": type_code or None,
                "is_preferred": int(type_code in PREFERRED_TYPES),
                # 미국은 종목명으로만 SPAC을 가릴 수 있다. 정확한 분류가 아니라 근사다.
                "is_spac": int("acquisition corp" in name.lower()),
            })
        return pd.DataFrame(rows, columns=["ticker", "name", "kind", "market", "category", "is_preferred", "is_spac"])
