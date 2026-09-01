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
    cash_krw: float = Field(ge=0)

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
    note: str | None = None

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


class ActiveEnvRequest(BaseModel):
    env: Literal["paper", "real"]


class KRXCredentialRequest(BaseModel):
    openapi_key: str | None = Field(default=None, max_length=200)
    krx_id: str | None = Field(default=None, max_length=100)
    krx_pw: str | None = Field(default=None, max_length=200)


class PreferenceRequest(BaseModel):
    request_delay_sec: float = Field(ge=0, le=10)
