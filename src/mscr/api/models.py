from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

class ScreenRequest(BaseModel):
    universe: dict[str, Any] = Field(default_factory=dict)
    formula: str = Field(min_length=1, max_length=2000)
    sort: dict[str, Any] = Field(default_factory=lambda: {"formula": "close", "dir": "desc"})
    limit: int = 500
    as_of_offset: int = Field(default=0, ge=0, le=250)

class ScreenSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    spec: ScreenRequest

class IndicatorParameter(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    default: float
    min: float | None = None
    max: float | None = None
    integer: bool = True


class IndicatorDefinitionRequest(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)
    label: str = Field(min_length=1, max_length=80)
    unit: Literal["number", "krw", "count", "ratio", "pct", "x"] = "number"
    formula: str = Field(min_length=1, max_length=500)
    parameters: list[IndicatorParameter] = Field(default_factory=list, max_length=10)
    enabled: bool = True

class TradeRequest(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    trade_date: date
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    fee: float = Field(default=0, ge=0)
    tax: float = Field(default=0, ge=0)
    memo: str | None = None

class CashRequest(BaseModel):
    """현금 잔고. 통화는 현재 시장 모드를 따른다(한국=원, 미국=달러)."""
    cash: float = Field(ge=0)

class TradePlanRequest(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=60)
    ticker: str = Field(pattern=r"^\d{6}$")
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    order_type: Literal["limit", "market"]
    limit_price: float | None = Field(default=None, ge=0)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    tp1_price: float = Field(gt=0)
    tp1_ratio: float = Field(gt=0, lt=1)
    tp2_price: float = Field(gt=0)
    tp2_ratio: float = Field(gt=0, lt=1)
    tp3_trailing_pct: float = Field(gt=0)
    enabled: bool = True
    setup: str | None = Field(default=None, max_length=60)
    note: str | None = None


class RiskLimitRequest(BaseModel):
    risk_per_trade_pct: float = Field(gt=0, le=100)
    max_portfolio_heat_pct: float = Field(gt=0, le=100)


class SignalCaptureRequest(BaseModel):
    days: int = Field(default=1, ge=1, le=1000)
    force: bool = False
    screen_ids: list[int] | None = None


class BacktestProtocol(BaseModel):
    entry: Literal["next_open", "breakout"] = "next_open"
    trigger_window: int = Field(default=5, ge=1, le=60)
    trigger_buffer_pct: float = Field(default=0.1, ge=0, le=10)
    stop_mode: Literal["atr", "box"] = "atr"
    atr_multiple: float = Field(default=2.0, gt=0, le=10)
    atr_period: int = Field(default=14, ge=2, le=250)
    box_lookback: int = Field(default=20, ge=2, le=250)
    box_buffer_atr: float = Field(default=0.25, ge=0, le=5)
    target_r: float = Field(default=3.0, gt=0, le=20)
    horizon_days: int = Field(default=60, ge=1, le=500)
    cost_pct: float = Field(default=0.25, ge=0, le=5)
    top_n: int | None = Field(default=5, ge=1, le=500)
    non_overlap: bool = True


class BacktestRequest(BaseModel):
    screen_id: int
    protocol: BacktestProtocol = Field(default_factory=BacktestProtocol)

class PlanProposalRequest(BaseModel):
    ticker: str = Field(pattern=r"^\d{6}$")
    side: Literal["buy", "sell"] = "buy"
    entry_price: float = Field(gt=0)
    max_investment: float = Field(gt=0)
    max_loss: float = Field(gt=0)

class TradeRunRequest(BaseModel):
    dry_run: bool = True
    plan_ids: list[int] | None = None


class BrokerCredentialRequest(BaseModel):
    app_key: str = Field(min_length=1, max_length=200)
    app_secret: str = Field(min_length=1, max_length=400)
    account: str = Field(min_length=1, max_length=20)
    env: Literal["paper", "real"] = "paper"


class GuardRequest(BaseModel):
    daily_entry_limit: int | None = Field(default=None, ge=0, le=1000)
    daily_notional_limit_krw: int | None = Field(default=None, ge=0)
    require_paper_first: bool | None = None


class KillSwitchRequest(BaseModel):
    engaged: bool
    reason: str = Field(default="화면에서 정지", max_length=200)


class PanicRequest(BaseModel):
    reason: str = Field(default="화면에서 긴급 정지", max_length=200)


class ActiveEnvRequest(BaseModel):
    env: Literal["paper", "real"]


class KRXCredentialRequest(BaseModel):
    openapi_key: str | None = Field(default=None, max_length=200)
    krx_id: str | None = Field(default=None, max_length=100)
    krx_pw: str | None = Field(default=None, max_length=200)


class PreferenceRequest(BaseModel):
    request_delay_sec: float = Field(ge=0, le=10)
    massive_request_delay_sec: float | None = Field(default=None, ge=0, le=60)


class IngestRunRequest(BaseModel):
    days: int = Field(default=400, gt=0, le=3650)
    force: bool = False
    # 시장 모드마다 쓸 수 있는 소스가 다르다(한국 krx/fdr/alphasquare, 미국 massive).
    source: Literal["krx", "fdr", "alphasquare", "massive"] | None = None


class MarketRequest(BaseModel):
    market: Literal["kr", "us"]


class MassiveCredentialRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=200)


class WatchlistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class WatchlistItemRequest(BaseModel):
    watchlist_id: int | None = None
    # 한국 6자리 코드와 미국 티커를 모두 받는다. 시장별 형식 검사는 watchlist 쪽에서 한다.
    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,12}$")
    memo: str | None = Field(default=None, max_length=500)
    target_price: float | None = Field(default=None, gt=0)


class WatchlistBulkRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    tickers: list[str] = Field(min_length=1, max_length=2000)


class WatchlistItemsActionRequest(BaseModel):
    action: Literal["delete", "move", "copy"]
    tickers: list[str] = Field(min_length=1, max_length=2000)
    target_id: int | None = None


class BriefSettingsRequest(BaseModel):
    screen_ids: list[int] | None = None
