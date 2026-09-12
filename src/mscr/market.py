"""시장 모드(한국/미국). 두 시장을 섞어서 보지 않고 한 번에 하나만 켜서 쓴다.

한 프로세스가 두 시장을 모두 다루므로 "지금 어느 시장인가"는 전역 변수가 아니라 컨텍스트
변수로 들고 다닌다. 웹 요청은 미들웨어가 요청마다 세팅하고, CLI는 `--market`으로, 백그라운드
작업은 시작한 쪽의 시장을 스레드 안에서 다시 세팅해 이어받는다(`inherit`).

시장이 갈라지는 지점은 세 곳이다.
- 일봉 저장소: `daily_bars.source`가 시장별로 다르다(krx_snapshot / massive_snapshot).
- 유니버스: `instruments.region`으로 나눠 담고, 거래소 이름(KOSPI/NASDAQ…)도 시장별로 다르다.
- 기능 범위: 실주문·자동 실행·현재 시황은 국내 전용이라 미국 모드에서는 막는다.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class Market:
    key: str                        # 'kr' | 'us'
    label: str
    region: str                     # instruments.region
    bar_source: str                 # daily_bars.source
    currency: str
    exchanges: tuple[str, ...]      # 유니버스 필터에 쓰는 거래소 이름
    ingest_sources: tuple[str, ...]
    ingest_kinds: tuple[str, ...]   # ingest_runs.kind (시장끼리 겹치지 않게 둔다)
    default_ingest_days: int
    cash_key: str                   # settings 테이블의 현금 잔고 키
    trading: bool                   # 계획·주문 실행·자동 실행·리스크·복기
    live_overview: bool             # 현재 시황(국내 전용 소스)
    fundamentals: bool              # PER·PBR·시가총액 스냅샷

    @property
    def default_ingest_source(self) -> str:
        return self.ingest_sources[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "region": self.region, "currency": self.currency,
            "exchanges": list(self.exchanges), "ingest_sources": list(self.ingest_sources),
            "default_ingest_days": self.default_ingest_days, "trading": self.trading,
            "live_overview": self.live_overview, "fundamentals": self.fundamentals,
        }


KR = Market(
    key="kr", label="한국", region="KR", bar_source="krx_snapshot", currency="KRW",
    exchanges=("KOSPI", "KOSDAQ", "KONEX"), ingest_sources=("krx", "fdr", "alphasquare"),
    ingest_kinds=("stock", "etf", "fundamental"), default_ingest_days=400, cash_key="cash_krw",
    trading=True, live_overview=True, fundamentals=True,
)
US = Market(
    key="us", label="미국", region="US", bar_source="massive_snapshot", currency="USD",
    exchanges=("NASDAQ", "NYSE", "AMEX", "CBOE", "OTHER"), ingest_sources=("massive",),
    ingest_kinds=("us_bars", "us_universe"), default_ingest_days=30, cash_key="cash_usd",
    trading=False, live_overview=False, fundamentals=False,
)
MARKETS: dict[str, Market] = {KR.key: KR, US.key: US}
DEFAULT_KEY = KR.key

_active: ContextVar[str] = ContextVar("mscr_market", default="")


def resolve(key: Any) -> Market:
    market = MARKETS.get(str(key or "").strip().lower())
    if market is None:
        raise ValueError(f"지원하지 않는 시장입니다: {key} (가능: {', '.join(MARKETS)})")
    return market


def stored_default() -> str:
    """환경변수 > 설정 파일 > 'kr' 순. 컨텍스트가 비었을 때만 본다."""
    from .credentials import load

    for candidate in (os.environ.get("MSCR_MARKET"), load("settings").get("market")):
        if str(candidate or "").strip().lower() in MARKETS:
            return str(candidate).strip().lower()
    return DEFAULT_KEY


def save_default(key: str) -> Market:
    from .credentials import load, save

    market = resolve(key)
    save("settings", load("settings") | {"market": market.key})
    return market


def active() -> Market:
    return MARKETS.get(_active.get()) or MARKETS[stored_default()]


def set_active(key: Any) -> Token[str]:
    return _active.set(resolve(key).key)


def reset(token: Token[str]) -> None:
    _active.reset(token)


@contextmanager
def use(key: Any) -> Iterator[Market]:
    token = set_active(key)
    try:
        yield active()
    finally:
        reset(token)


def inherit() -> str:
    """백그라운드 스레드로 넘길 현재 시장 키. 스레드 안에서 `use(key)`로 다시 세운다."""
    return active().key


def bar_source() -> str:
    return active().bar_source


def region() -> str:
    return active().region


def region_of(ticker: str) -> str:
    """종목코드만 보고 시장을 판별한다. KRX는 6자리 숫자, 미국은 영문자를 포함한다.

    매매 기록(`trades`)처럼 시장 구분 컬럼이 없는 테이블을 모드별로 나눠 볼 때 쓴다.
    """
    return KR.region if str(ticker or "").isdigit() else US.region


def clean_ticker(ticker: Any, market: Market | None = None) -> str:
    """입력받은 종목코드를 현재 시장 형식으로 정규화한다."""
    market = market or active()
    cleaned = str(ticker or "").strip().upper()
    if market.key == KR.key:
        if len(cleaned) != 6 or not cleaned.isdigit():
            raise ValueError("종목코드는 6자리 숫자여야 합니다")
        return cleaned
    if not cleaned or len(cleaned) > 12 or not all(char.isalnum() or char in ".-" for char in cleaned):
        raise ValueError("미국 종목 티커 형식이 아닙니다")
    return cleaned


def require_trading() -> Market:
    market = active()
    if not market.trading:
        raise PermissionError(f"{market.label} 주식 모드에서는 계획·주문 실행·자동 실행을 지원하지 않습니다. 한국 주식 모드로 전환하세요.")
    return market


def require_live_overview() -> Market:
    market = active()
    if not market.live_overview:
        raise PermissionError(f"{market.label} 주식 모드에서는 현재 시황을 지원하지 않습니다(국내 전용 데이터 소스).")
    return market
