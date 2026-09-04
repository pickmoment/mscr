from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests

from ..config import REQUEST_RETRIES, request_delay

BASE_URL = "https://api.alphasquare.co.kr"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://alphasquare.co.kr/",
    "Accept": "application/json",
}
RETRYABLE_STATUS = {403, 429, 502, 503, 504}
KST = "Asia/Seoul"
FEATURED_FACTORS = (
    ("returns_top", "상승률 상위"),
    ("returns_bottom", "하락률 상위"),
    ("volume_valued_top", "거래대금 상위"),
    ("new_high_price", "신고가"),
    ("upper_limit_price", "상한가"),
    ("supervised", "관리종목"),
)


def _http_get(session: requests.Session, path: str, params: dict[str, Any], delay: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES + 1):
        try:
            response = session.get(f"{BASE_URL}{path}", params=params, headers=HEADERS, timeout=10)
            if response.status_code >= 400:
                if response.status_code in RETRYABLE_STATUS and attempt < REQUEST_RETRIES:
                    last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
                    time.sleep(delay * (attempt + 1))
                    continue
                response.raise_for_status()
            time.sleep(delay)
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES:
                time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"alpha-square 요청 실패: {last_error}") from last_error


def _special_stocks(session: requests.Session, factor: str, wait: float, market: str = "all", limit: int = 20) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit, "offset": 0, "exchange": "UNIFIED", "type_specs": "CS"}
    params["market"] = ["kospi", "kosdaq"] if market == "all" else market
    payload = _http_get(session, f"/data/v2/special-stocks/by/{factor}", params, wait)
    return [
        {"code": item.get("code"), "name": item.get("ko_name"), "close": item.get("close"), "returns": item.get("returns"), "volume": item.get("volume"), "volume_valued": item.get("volume_valued")}
        for item in (payload.get("data") or [])
    ]


def theme_stocks(theme_id: int, delay: float | None = None) -> list[dict[str, Any]]:
    """테마(`theme_id`)에 속한 종목 목록을 가져온다. 시황 화면에서 테마를 펼쳤을 때 쓴다."""
    session = requests.Session()
    wait = request_delay() if delay is None else delay
    payload = _http_get(session, f"/theme/v2/themes/{theme_id}/stocks", {}, wait)
    return [{"code": item.get("code"), "name": item.get("ko_name"), "market": item.get("market")} for item in (payload or [])]


def market_overview(delay: float | None = None) -> dict[str, Any]:
    """alpha-square 실시간 엔드포인트로 현재 시황 스냅샷을 가져온다.

    종목 단위 폴백(AlphaSquareProvider)과 달리 로컬 DB나 유니버스가 필요 없다. 섹션마다
    독립적으로 호출하며, 한 섹션이 실패해도 나머지는 그대로 반환하고 실패한 섹션은
    `errors`에 이유를 남긴다(수동 새로고침 버튼으로만 호출되는 대시보드라 부분 실패를
    감수하는 편이, 전체를 에러로 만드는 것보다 낫다).
    """
    session = requests.Session()
    wait = request_delay() if delay is None else delay
    result: dict[str, Any] = {"breadth": {}, "trending": [], "theme_leaders": [], "news": [], "issues": [], "featured": {}, "errors": {}}

    def _section(name: str, fn: Any) -> None:
        try:
            result[name] = fn()
        except Exception as exc:
            result["errors"][name] = str(exc)

    def _breadth() -> dict[str, Any]:
        return {market: _http_get(session, "/data/v3/prices/returns-group-count", {"market": market}, wait) for market in ("kospi", "kosdaq")}

    def _trending() -> list[dict[str, Any]]:
        payload = _http_get(session, "/stock/v2/trendings", {"limit": 20}, wait)
        return [{"code": item.get("code"), "name": item.get("ko_name"), "market": item.get("market"), "count": item.get("count")} for item in (payload.get("data") or [])]

    def _theme_leaders() -> list[dict[str, Any]]:
        payload = _http_get(session, "/theme/v2/leader-board", {}, wait)
        rows = []
        for item in payload.get("data") or []:
            theme, big_theme, stats = item.get("theme") or {}, item.get("big_theme") or {}, item.get("stats") or {}
            rows.append({
                "theme_id": theme.get("id"), "theme": theme.get("name"), "big_theme": big_theme.get("name"), "stock_count": item.get("stock_count"),
                "returns": stats.get("returns"), "rank": stats.get("rank"), "rank_change": stats.get("rank_change"),
                "up_count": stats.get("up_count"), "down_count": stats.get("down_count"), "even_count": stats.get("even_count"), "date": stats.get("date"),
            })
        return rows

    def _news() -> list[dict[str, Any]]:
        payload = _http_get(session, "/data/v3/issue/market-news", {"limit": 20}, wait)
        return [{"dt": item.get("dt"), "source": item.get("source"), "title": item.get("title"), "summary": item.get("summary"), "link": item.get("link")} for item in (payload.get("data") or [])]

    def _issues() -> list[dict[str, Any]]:
        payload = _http_get(session, "/data/v2/issue/market", {"issue_type": "market", "limit": 20}, wait)
        return [{"dt": item.get("dt"), "title": item.get("title"), "link": item.get("link"), "source": item.get("source")} for item in (payload or [])]

    def _featured() -> dict[str, Any]:
        """factor마다 독립적으로 실패를 허용한다 — 하나가 막혀도 나머지 특징종목은 그대로 보여준다."""
        sections: dict[str, Any] = {}
        for factor, label in FEATURED_FACTORS:
            try:
                sections[factor] = {"label": label, "rows": _special_stocks(session, factor, wait)}
            except Exception as exc:
                sections[factor] = {"label": label, "rows": [], "error": str(exc)}
        return sections

    for name, fn in (("breadth", _breadth), ("trending", _trending), ("theme_leaders", _theme_leaders), ("news", _news), ("issues", _issues), ("featured", _featured)):
        _section(name, fn)
    return result


class AlphaSquareProvider:
    """alphasquare.co.kr 비공개 내부 API(api.alphasquare.co.kr)를 종목 단위로 호출하는 최후 폴백 소스.

    전종목을 한 번에 반환하는 엔드포인트가 없어 로컬 유니버스(instruments 테이블)의 티커를
    하나씩 순회하며 채운다. 티커 → 내부 stock-id 매핑은 alphasquare_ticker_map에 캐시해
    이후 호출에서 재조회 비용을 없앤다.
    """

    def __init__(self, db: Any, delay: float | None = None):
        self.db = db
        self.delay = request_delay() if delay is None else delay
        self.session = requests.Session()

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        return _http_get(self.session, path, params, self.delay)

    def _resolve(self, ticker: str) -> int | None:
        cached = self.db.execute("SELECT stock_id FROM alphasquare_ticker_map WHERE ticker=?", (ticker,)).fetchone()
        if cached:
            return int(cached[0])
        try:
            data = self._get("/data/v2/stock/details", {"code": ticker})
        except Exception:
            return None
        entry = data.get(ticker) if isinstance(data, dict) else None
        stock_id = entry.get("id") if entry else None
        if stock_id is None:
            return None
        self.db.execute(
            "INSERT INTO alphasquare_ticker_map(ticker,stock_id,resolved_at) VALUES(?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET stock_id=excluded.stock_id, resolved_at=excluded.resolved_at",
            (ticker, int(stock_id), pd.Timestamp.now().isoformat(timespec="seconds")),
        )
        return int(stock_id)

    def _range_bars(self, stock_id: int, start: str, end: str) -> list[dict[str, Any]]:
        """[start, end] 구간의 일봉을 반환한다. 캔들 API가 요청당 최대 1000봉만 주므로,
        구간이 그보다 길면(예: --days 3650) 과거 방향으로 페이지를 넘겨가며 이어붙인다."""
        end_ms = int((pd.Timestamp(end).as_unit("ns").tz_localize(KST) + pd.Timedelta(days=1)).tz_convert("UTC").timestamp() * 1000) - 1
        bars: dict[str, dict[str, Any]] = {}
        cursor = end_ms
        for _ in range(10):
            try:
                payload = self._get(f"/data/v3/prices/candles/{stock_id}", {"freq": "day", "limit": 1000, "end": cursor})
            except Exception:
                break
            page = [row for row in (payload.get("data") or []) if len(row) >= 6]
            if not page:
                break
            oldest_ms = min(int(row[0]) for row in page)
            for row in page:
                bar_date = pd.Timestamp(int(row[0]), unit="ms", tz="UTC").tz_convert(KST).strftime("%Y-%m-%d")
                if start <= bar_date <= end:
                    # alpha-square 캔들 API는 거래대금(value)을 주지 않는다. 종가×거래량 근사치로
                    # 채워, 스크리너의 유동성(value) 조건이 이 소스로 채운 날짜에서도 동작하게 한다.
                    bars[bar_date] = {"date": bar_date, "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5], "value": row[4] * row[5]}
            oldest_date = pd.Timestamp(oldest_ms, unit="ms", tz="UTC").tz_convert(KST).strftime("%Y-%m-%d")
            if oldest_date <= start or len(page) < 1000:
                break
            cursor = oldest_ms - 1
        return list(bars.values())

    def history(self, tickers: list[str], start: str, end: str) -> pd.DataFrame:
        """로컬 유니버스 티커를 하나씩 조회해 [start, end] 구간의 OHLCV를 채운다."""
        rows = []
        for ticker in tickers:
            stock_id = self._resolve(ticker)
            if stock_id is None:
                continue
            for bar in self._range_bars(stock_id, start, end):
                rows.append({"ticker": ticker, **bar})
        return pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume", "value"])
