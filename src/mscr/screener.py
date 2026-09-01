from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    label_ko: str
    unit: str
    kind: str

_FIELDS = {
    "close": ("종가", "krw", "number"), "open": ("시가", "krw", "number"), "high": ("고가", "krw", "number"), "low": ("저가", "krw", "number"),
    "volume": ("거래량", "count", "number"), "value": ("거래대금", "krw", "number"), "change_pct": ("등락률", "pct", "number"),
    "market_cap": ("시가총액", "krw", "number"), "shares": ("상장주식수", "count", "number"), "per": ("PER", "x", "number"), "pbr": ("PBR", "x", "number"), "eps": ("EPS", "krw", "number"), "bps": ("BPS", "krw", "number"), "div": ("배당수익률", "pct", "number"),
    "bars_available": ("유효 봉 수", "count", "number"), "halted": ("거래정지", "bool", "bool"), "price_jump_flag": ("가격 급변", "bool", "bool"), "weighted_return": ("가중수익률", "pct", "number"),
}
FIELDS = {key: FieldSpec(*value) for key, value in _FIELDS.items()}




def run(spec: dict[str, Any], path=None) -> list[dict[str, Any]]:
    from .dynamic import run_screen

    return run_screen(spec, path)
