from __future__ import annotations

import math
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
NET_FLOW_FACTORS = (
    ("individual_volume_valued_net_buy_top", "개인 순매수 상위"),
    ("individual_volume_valued_net_sell_top", "개인 순매도 상위"),
    ("institutional_volume_valued_net_buy_top", "기관 순매수 상위"),
    ("institutional_volume_valued_net_sell_top", "기관 순매도 상위"),
    ("foreigner_volume_valued_net_buy_top", "외국인 순매수 상위"),
    ("foreigner_volume_valued_net_sell_top", "외국인 순매도 상위"),
)
INDUSTRY_SAMPLE_SIZE = 300  # 업종현황 전용 엔드포인트가 없어 시가총액 상위 표본으로 근사한다
CANDLE_FREQS = ("minute-1", "minute-3", "minute-5", "minute-15", "minute-30", "minute-60", "day")  # 종목상세 실시간 차트가 고를 수 있는 주기
CANDLE_PAGE_LIMIT = 1000  # alpha-square 캔들 API가 요청 한 번에 주는 최대 봉수(서버가 강제하는 상한)
CANDLE_BARS_DEFAULT = CANDLE_PAGE_LIMIT  # 실시간 차트를 처음 열 때 한 번에 가져오는 봉수 — 페이지당 한도를 그대로 채운다
CANDLE_BARS_MAX = 5000  # "이전 데이터 더보기"로 늘릴 수 있는 총 봉수 상한(페이지 5장) — 비공식 API 호출이 무한정 커지지 않게 막는다


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
    rows = []
    for item in payload.get("data") or []:
        # 주체별 순매매 factor는 개인/기관/외국인별로 net_vol_valued_<주체> 필드명이 다르다.
        # 어떤 주체 factor인지는 호출한 쪽이 이미 알고 있으므로, 여기서는 존재하는 필드를 그대로 뽑는다.
        net = next((value for key, value in item.items() if key.startswith("net_vol_valued_") and not key.endswith("_returns")), None)
        rows.append({
            "code": item.get("code"), "name": item.get("ko_name"), "close": item.get("close"), "returns": item.get("returns"),
            "volume": item.get("volume"), "volume_valued": item.get("volume_valued"), "net": net,
        })
    return rows


def _industry_overview(session: requests.Session, wait: float, sample_size: int = INDUSTRY_SAMPLE_SIZE) -> list[dict[str, Any]]:
    """업종별 등락 현황을 시가총액 상위 `sample_size` 종목 표본으로 근사한다.

    alpha-square에는 전종목 업종 집계 엔드포인트가 없어, 시가총액순 특징종목 조회로 받은
    표본을 업종별로 묶어 평균 등락률·상승/하락 종목수를 계산한다. 전수조사가 아니므로
    소형주 비중이 큰 업종은 실제와 오차가 있을 수 있다.
    """
    params: dict[str, Any] = {"limit": sample_size, "offset": 0, "exchange": "UNIFIED", "type_specs": "CS", "market": ["kospi", "kosdaq"]}
    payload = _http_get(session, "/data/v2/special-stocks/by/marketcap", params, wait)
    groups: dict[str, dict[str, Any]] = {}
    for item in payload.get("data") or []:
        industry = item.get("industry") or "기타"
        group = groups.setdefault(industry, {"industry": industry, "count": 0, "up": 0, "down": 0, "flat": 0, "returns_sum": 0.0, "marketcap_sum": 0.0, "stocks": []})
        group["count"] += 1
        returns = item.get("returns")
        if returns is not None:
            group["returns_sum"] += returns
            if returns > 0: group["up"] += 1
            elif returns < 0: group["down"] += 1
            else: group["flat"] += 1
        marketcap = item.get("marketcap")
        if marketcap is not None:
            group["marketcap_sum"] += marketcap
        # marketcap factor는 이미 시가총액 내림차순으로 오므로, 추가 정렬 없이 그대로 쌓으면
        # 업종 안에서도 대형주가 먼저 나온다.
        group["stocks"].append({"code": item.get("code"), "name": item.get("ko_name"), "close": item.get("close"), "returns": returns})
    rows = [
        {"industry": g["industry"], "count": g["count"], "up": g["up"], "down": g["down"], "flat": g["flat"], "avg_returns": g["returns_sum"] / g["count"] if g["count"] else None, "marketcap_sum": g["marketcap_sum"], "stocks": g["stocks"]}
        for g in groups.values()
    ]
    rows.sort(key=lambda row: row["avg_returns"] if row["avg_returns"] is not None else -math.inf, reverse=True)
    return rows


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
    result: dict[str, Any] = {"breadth": {}, "trending": [], "theme_leaders": [], "news": [], "issues": [], "featured": {}, "net_flows": {}, "industries": [], "errors": {}}

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

    def _factor_group(factors: tuple[tuple[str, str], ...]) -> dict[str, Any]:
        """factor마다 독립적으로 실패를 허용한다 — 하나가 막혀도 같은 그룹의 나머지는 그대로 보여준다."""
        sections: dict[str, Any] = {}
        for factor, label in factors:
            try:
                sections[factor] = {"label": label, "rows": _special_stocks(session, factor, wait)}
            except Exception as exc:
                sections[factor] = {"label": label, "rows": [], "error": str(exc)}
        return sections

    def _featured() -> dict[str, Any]:
        return _factor_group(FEATURED_FACTORS)

    def _net_flows() -> dict[str, Any]:
        return _factor_group(NET_FLOW_FACTORS)

    def _industries() -> list[dict[str, Any]]:
        return _industry_overview(session, wait)

    for name, fn in (("breadth", _breadth), ("trending", _trending), ("theme_leaders", _theme_leaders), ("news", _news), ("issues", _issues), ("featured", _featured), ("net_flows", _net_flows), ("industries", _industries)):
        _section(name, fn)
    return result


class AlphaSquareProvider:
    """alphasquare.co.kr 비공개 내부 API(api.alphasquare.co.kr)를 종목 단위로 호출하는 최후 폴백 소스.

    전종목을 한 번에 반환하는 엔드포인트가 없어 로컬 유니버스(instruments 테이블)의 티커를
    하나씩 순회하며 채운다. 티커 → 내부 stock-id 매핑은 alphasquare_ticker_map에 캐시해
    이후 호출에서 재조회 비용을 없앤다.
    """

    def __init__(self, db: Any, delay: float | None = None):
        from .. import market
        from zoneinfo import ZoneInfo

        self.db = db
        self.delay = request_delay() if delay is None else delay
        self.session = requests.Session()
        # 캔들 시각은 현재 시장의 장 시간대로 읽는다. 미국 분봉을 KST로 찍으면 09:30~16:00 장이
        # 새벽 22:30~05:00으로 밀려 보인다.
        self.tz = ZoneInfo(market.active().timezone)

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

    def candles(self, ticker: str, freq: str, count: int = CANDLE_BARS_DEFAULT) -> pd.DataFrame:
        """단일 종목의 최근 캔들을 alpha-square에서 직접 가져온다 — 로컬 DB(daily_bars)를 거치지 않는
        실시간 조회 경로다. `count`가 페이지당 한도(`CANDLE_PAGE_LIMIT`=1000)를 넘으면 `_range_bars`와
        같은 방식으로 과거 방향 페이지를 이어붙인다 — "이전 데이터 더보기"가 이 경로를 쓴다. 여러 페이지를
        모아도 지표(이동평균 등)는 호출부가 합쳐진 프레임 전체로 한 번에 계산하므로 병합 경계에서 끊기지
        않는다.

        분봉(`freq`가 `minute-`로 시작)의 시간은 그 시장 장 시간대(한국 KST·미국 ET)의 벽시계 값을
        그대로 UTC epoch초로 인코딩한다. lightweight-charts는 숫자 시간을 항상 UTC로 표시하므로, 그렇게
        해야 화면에 현지 장 시각이 그대로 보인다. 일봉은 기존 일봉 경로와 동일하게 날짜 문자열을 쓴다.

        `freq`가 `day`면 오늘 봉을 `_today_bar`로 보충한다 — alpha-square의 일봉 캔들 API는 장이 끝난
        완결된 거래일까지만 주고 장중인 오늘 봉은 절대 내려주지 않는다(실측 확인).
        """
        stock_id = self._resolve(ticker)
        if stock_id is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        rows: dict[int, list[Any]] = {}
        cursor = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
        while len(rows) < count:
            page_limit = min(CANDLE_PAGE_LIMIT, count - len(rows))
            payload = self._get(f"/data/v3/prices/candles/{stock_id}", {"freq": freq, "limit": page_limit, "end": cursor})
            page = [row for row in (payload.get("data") or []) if len(row) >= 6]
            if not page:
                break
            for row in page:
                rows[int(row[0])] = row
            oldest_ms = min(int(row[0]) for row in page)
            if len(page) < page_limit or oldest_ms >= cursor:
                break  # 짧은 페이지 또는 커서가 움직이지 않음 = 더 과거 데이터가 없다는 뜻
            cursor = oldest_ms - 1
        intraday = freq.startswith("minute")
        out = []
        for row in (rows[key] for key in sorted(rows)):
            local = pd.Timestamp(int(row[0]), unit="ms", tz="UTC").tz_convert(self.tz).tz_localize(None)
            date_value = int(local.value // 10**9) if intraday else local.strftime("%Y-%m-%d")
            out.append({"date": date_value, "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]})
        if freq == "day":
            today_bar = self._today_bar(stock_id)
            if today_bar is not None and (not out or out[-1]["date"] != today_bar["date"]):
                out.append(today_bar)
        return pd.DataFrame(out, columns=["date", "open", "high", "low", "close", "volume"])

    def _today_bar(self, stock_id: int) -> dict[str, Any] | None:
        """alpha-square 일봉 캔들 API는 완결된 거래일까지만 주므로, 장중인 오늘 봉은 1분봉을 따로 조회해
        오늘 날짜(장 시간대 기준)의 봉만 골라 시가·고가·저가·종가·거래량으로 직접 합성한다. 주말은 장이 없으니
        조회 자체를 건너뛴다(공휴일은 걸러도 1분봉이 비어 있어 그대로 None을 반환한다)."""
        now_local = pd.Timestamp.now(tz="UTC").tz_convert(self.tz)
        if now_local.weekday() >= 5:
            return None
        today = now_local.strftime("%Y-%m-%d")
        payload = self._get(f"/data/v3/prices/candles/{stock_id}", {"freq": "minute-1", "limit": CANDLE_PAGE_LIMIT, "end": int(now_local.timestamp() * 1000)})
        today_rows = [row for row in (payload.get("data") or []) if len(row) >= 6 and pd.Timestamp(int(row[0]), unit="ms", tz="UTC").tz_convert(self.tz).strftime("%Y-%m-%d") == today]
        if not today_rows:
            return None
        today_rows.sort(key=lambda row: row[0])
        return {"date": today, "open": today_rows[0][1], "high": max(row[2] for row in today_rows), "low": min(row[3] for row in today_rows), "close": today_rows[-1][4], "volume": sum(row[5] for row in today_rows)}

    def _range_bars(self, stock_id: int, start: str, end: str) -> list[dict[str, Any]]:
        """[start, end] 구간의 일봉을 반환한다. 캔들 API가 요청당 최대 1000봉만 주므로,
        구간이 그보다 길면(예: --days 3650) 과거 방향으로 페이지를 넘겨가며 이어붙인다."""
        end_ms = int((pd.Timestamp(end).as_unit("ns").tz_localize(self.tz) + pd.Timedelta(days=1)).tz_convert("UTC").timestamp() * 1000) - 1
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
                bar_date = pd.Timestamp(int(row[0]), unit="ms", tz="UTC").tz_convert(self.tz).strftime("%Y-%m-%d")
                if start <= bar_date <= end:
                    # alpha-square 캔들 API는 거래대금(value)을 주지 않는다. 종가×거래량 근사치로
                    # 채워, 스크리너의 유동성(value) 조건이 이 소스로 채운 날짜에서도 동작하게 한다.
                    bars[bar_date] = {"date": bar_date, "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5], "value": row[4] * row[5]}
            oldest_date = pd.Timestamp(oldest_ms, unit="ms", tz="UTC").tz_convert(self.tz).strftime("%Y-%m-%d")
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
