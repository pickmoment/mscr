from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import FinanceDataReader as fdr
import pandas as pd
import requests

from ..config import REQUEST_RETRIES, request_delay
from ..credentials import clear, load, mask, update

CREDENTIAL_NAME = "krx_credentials"
_stock_module: Any = None


def krx_credentials() -> dict[str, str]:
    stored = load(CREDENTIAL_NAME)
    return {
        "openapi_key": os.environ.get("KRX_OPENAPI_KEY") or str(stored.get("openapi_key") or ""),
        "krx_id": os.environ.get("KRX_ID") or str(stored.get("krx_id") or ""),
        "krx_pw": os.environ.get("KRX_PW") or str(stored.get("krx_pw") or ""),
    }


def krx_status() -> dict[str, Any]:
    resolved = krx_credentials()
    stored = load(CREDENTIAL_NAME)
    mode = "openapi" if resolved["openapi_key"] else ("idpw" if resolved["krx_id"] and resolved["krx_pw"] else "anonymous")
    if mode == "openapi": source = "env" if os.environ.get("KRX_OPENAPI_KEY") else "file"
    elif mode == "idpw": source = "env" if os.environ.get("KRX_ID") else "file"
    else: source = None
    return {"mode": mode, "source": source, "openapi_key_masked": mask(resolved["openapi_key"]), "krx_id_masked": mask(resolved["krx_id"], 2), "stored": sorted(stored)}


def save_krx_credentials(openapi_key: str | None = None, krx_id: str | None = None, krx_pw: str | None = None) -> dict[str, Any]:
    update(CREDENTIAL_NAME, {"openapi_key": openapi_key, "krx_id": krx_id, "krx_pw": krx_pw})
    return krx_status()


def clear_krx_credentials() -> dict[str, Any]:
    clear(CREDENTIAL_NAME)
    return krx_status()


def _stock() -> Any:
    """pykrx opens an anonymous KRX session at import time, so import it lazily."""
    global _stock_module
    if _stock_module is None:
        from pykrx import stock as module
        _stock_module = module
    return _stock_module

STOCK_RENAME = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume", "거래대금": "value", "등락률": "change_pct", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume", "Change": "change_pct"}
ETF_RENAME = {**STOCK_RENAME, "NAV": "nav", "IDX_IND_NM": "base_index", "IDX_NM": "base_index", "BASE_IDX_NM": "base_index"}
CAP_RENAME = {"종가": "close", "시가총액": "market_cap", "거래량": "volume", "거래대금": "value", "상장주식수": "shares"}
FUND_RENAME = {"BPS": "bps", "PER": "per", "PBR": "pbr", "EPS": "eps", "DIV": "div", "DPS": "dps"}
OPENAPI_BASE_URL = os.environ.get("KRX_OPENAPI_BASE_URL", "https://data-dbg.krx.co.kr/svc/apis")


def _field(row: dict[str, Any], *names: str) -> Any:
    values = {str(key).upper().replace("_", ""): value for key, value in row.items()}
    for name in names:
        key = name.upper().replace("_", "")
        if key in values:
            return values[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        text = str(value).replace(",", "").strip()
        return None if not text or text == "-" else float(text)
    except (TypeError, ValueError):
        return None


class KRXOpenAPIProvider:
    """Official KRX Open API adapter using AUTH_KEY header authentication."""

    def __init__(self, api_key: str | None = None, delay: float | None = None):
        self.api_key = api_key or krx_credentials()["openapi_key"]
        if not self.api_key:
            raise ValueError("KRX Open API 키가 필요합니다 (설정 화면 또는 KRX_OPENAPI_KEY)")
        self.delay = request_delay() if delay is None else delay
        self.session = requests.Session()

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(REQUEST_RETRIES + 1):
            try:
                result = fn(*args, **kwargs)
                time.sleep(self.delay)
                return result
            except Exception as exc:
                last_error = exc
                time.sleep(self.delay)
                if attempt < REQUEST_RETRIES:
                    time.sleep(2 ** (attempt * 2))
        raise RuntimeError(f"KRX Open API request failed after retries: {last_error}") from last_error

    def _records(self, category: str, endpoint: str, day: str) -> list[dict[str, Any]]:
        response = self.session.get(f"{OPENAPI_BASE_URL}/{category}/{endpoint}", params={"basDd": day.replace("-", "")}, headers={"AUTH_KEY": self.api_key}, timeout=30)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            try:
                payload = response.json()
            except ValueError:
                raise exc
            message = _field(payload, "respMsg", "_error_message", "message")
            code = _field(payload, "respCode") or getattr(response, "status_code", None)
            if message or code:
                detail = f"{code}: {message}" if code and message else str(message or code)
                raise RuntimeError(f"KRX Open API {detail}") from exc
            raise
        payload = response.json()
        if "OutBlock_1" not in payload:
            message = _field(payload, "respMsg", "_error_message", "message")
            code = _field(payload, "respCode")
            if message or code:
                detail = f"{code}: {message}" if code and message else str(message or code)
                raise RuntimeError(f"KRX Open API {detail}")
            return []
        return payload["OutBlock_1"] or []

    @staticmethod
    def _daily_frame(records: list[dict[str, Any]], etf: bool = False) -> pd.DataFrame:
        rows = []
        for record in records:
            ticker = _field(record, "ISU_SRT_CD", "ISU_CD", "TICKER")
            if not ticker:
                continue
            row = {"ticker": str(ticker).zfill(6), "name": _field(record, "ISU_ABBRV", "ISU_NM", "ISU_NAME"), "open": _number(_field(record, "TDD_OPNPRC", "OPNPRC")), "high": _number(_field(record, "TDD_HGPRC", "HGPRC")), "low": _number(_field(record, "TDD_LWPRC", "LWPRC")), "close": _number(_field(record, "TDD_CLSPRC", "CLSPRC")), "volume": _number(_field(record, "ACC_TRDVOL", "TRDVOL")), "value": _number(_field(record, "ACC_TRDVAL", "TRDVAL")), "change_pct": _number(_field(record, "FLUC_RT", "FLUCRT")), "market_cap": _number(_field(record, "MKTCAP", "MKTCAP_AMT")), "shares": _number(_field(record, "LIST_SHRS", "LISTED_SHRS"))}
            if etf:
                row.update({"nav": _number(_field(record, "NAV", "NAV_VAL")), "base_index": _field(record, "IDX_IND_NM", "IDX_NM", "BASE_IDX_NM")})
            rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def _base_frame(records: list[dict[str, Any]], market: str) -> pd.DataFrame:
        rows = []
        for record in records:
            ticker = _field(record, "ISU_SRT_CD", "ISU_CD", "TICKER")
            if ticker:
                rows.append({"ticker": str(ticker).zfill(6), "name": _field(record, "ISU_ABBRV", "ISU_NM", "ISU_NAME") or str(ticker), "market": market})
        return pd.DataFrame(rows, columns=["ticker", "name", "market"])

    def latest_trading_day(self) -> str:
        candidate = pd.Timestamp.now().normalize()
        for offset in range(14):
            day = (candidate - pd.Timedelta(days=offset)).strftime("%Y%m%d")
            if self._call(self._records, "sto", "stk_bydd_trd", day):
                return day
        raise RuntimeError("KRX Open API에서 최근 거래일을 찾지 못했습니다.")

    def trading_days(self, start: date, end: date) -> list[pd.Timestamp]:
        return list(pd.bdate_range(start, end))

    def stock_universe(self, as_of: str) -> pd.DataFrame:
        kospi = self._base_frame(self._call(self._records, "sto", "stk_isu_base_info", as_of), "KOSPI")
        kosdaq = self._base_frame(self._call(self._records, "sto", "ksq_isu_base_info", as_of), "KOSDAQ")
        result = pd.concat([kospi, kosdaq], ignore_index=True)
        if result.empty:
            result = self.stock_snapshot(as_of)
        return result[["ticker", "name", "market"]]

    def etf_universe(self, as_of: str) -> pd.DataFrame:
        frame = self._daily_frame(self._call(self._records, "etp", "etf_bydd_trd", as_of), etf=True)
        if frame.empty:
            return pd.DataFrame(columns=["ticker", "name", "category"])
        return frame.assign(category=None)[["ticker", "name", "category"]].drop_duplicates("ticker")

    def stock_snapshot(self, day: str) -> pd.DataFrame:
        kospi = self._daily_frame(self._call(self._records, "sto", "stk_bydd_trd", day))
        kosdaq = self._daily_frame(self._call(self._records, "sto", "ksq_bydd_trd", day))
        return pd.concat([kospi, kosdaq], ignore_index=True).drop_duplicates("ticker")

    def etf_snapshot(self, day: str) -> pd.DataFrame:
        return self._daily_frame(self._call(self._records, "etp", "etf_bydd_trd", day), etf=True)

    def cap_snapshot(self, day: str) -> pd.DataFrame:
        frame = self.stock_snapshot(day)
        return frame[["ticker", "close", "market_cap", "shares", "volume", "value"]]

    def fundamental_snapshot(self, day: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["ticker", "bps", "per", "pbr", "eps", "div", "dps"])

    def history(self, ticker: str, start: str, end: str, kind: str = "stock", adjusted: bool = True) -> pd.DataFrame:
        frame = fdr.DataReader(ticker, start, end)
        return KRXProvider._normalize(frame, STOCK_RENAME)


class KRXProvider:
    """Serialized pykrx compatibility provider used without an Open API key."""

    def __new__(cls, *args: Any, **kwargs: Any):
        if krx_credentials()["openapi_key"]:
            return KRXOpenAPIProvider(*args, **kwargs)
        return super().__new__(cls)

    def __init__(self, delay: float | None = None):
        self.delay = request_delay() if delay is None else delay

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(REQUEST_RETRIES + 1):
            try:
                result = fn(*args, **kwargs)
                time.sleep(self.delay)
                return result
            except Exception as exc:
                last_error = exc
                time.sleep(self.delay)
                if attempt < REQUEST_RETRIES:
                    time.sleep(2 ** (attempt * 2))
        raise last_error

    @staticmethod
    def _date(value: date | str | pd.Timestamp) -> str:
        return value if isinstance(value, str) else pd.Timestamp(value).strftime("%Y%m%d")

    def latest_trading_day(self) -> str: return self._call(_stock().get_nearest_business_day_in_a_week, prev=True)
    def trading_days(self, start: date, end: date) -> list[pd.Timestamp]: return list(self._call(_stock().get_previous_business_days, self._date(start), self._date(end)))
    def stock_universe(self, as_of: str) -> pd.DataFrame:
        names = self._call(_stock().get_market_price_change_by_ticker, as_of, as_of, market="ALL")
        kospi = set(self._call(_stock().get_market_ticker_list, as_of, market="KOSPI")); kosdaq = set(self._call(_stock().get_market_ticker_list, as_of, market="KOSDAQ"))
        if names is None or names.empty: return pd.DataFrame(columns=["ticker", "name", "market"])
        frame = names.reset_index().rename(columns={"티커": "ticker", "종목명": "name"})
        if "ticker" not in frame: frame = frame.rename(columns={frame.columns[0]: "ticker"})
        if "name" not in frame: frame["name"] = frame["ticker"]
        frame["ticker"] = frame["ticker"].astype(str).str.zfill(6); frame["market"] = frame["ticker"].map(lambda t: "KOSPI" if t in kospi else ("KOSDAQ" if t in kosdaq else None))
        return frame[["ticker", "name", "market"]]
    def etf_universe(self, as_of: str) -> pd.DataFrame:
        frame = self._call(fdr.StockListing, "ETF/KR").rename(columns={"Symbol": "ticker", "Name": "name", "Category": "category"}); frame["ticker"] = frame["ticker"].astype(str).str.zfill(6); return frame[["ticker", "name", "category"]].drop_duplicates("ticker")
    def stock_snapshot(self, day: str) -> pd.DataFrame: return self._normalize(self._call(_stock().get_market_ohlcv_by_ticker, day, market="ALL"), STOCK_RENAME)
    def etf_snapshot(self, day: str) -> pd.DataFrame: return self._normalize(self._call(_stock().get_etf_ohlcv_by_ticker, day), ETF_RENAME)
    def cap_snapshot(self, day: str) -> pd.DataFrame: return self._normalize(self._call(_stock().get_market_cap_by_ticker, day, market="ALL"), CAP_RENAME)
    def fundamental_snapshot(self, day: str) -> pd.DataFrame: return self._normalize(self._call(_stock().get_market_fundamental_by_ticker, day, market="ALL"), FUND_RENAME)
    def history(self, ticker: str, start: str, end: str, kind: str = "stock", adjusted: bool = True) -> pd.DataFrame:
        if kind == "etf": frame = self._call(_stock().get_etf_ohlcv_by_date, start.replace("-", ""), end.replace("-", ""), ticker)
        else: frame = self._call(_stock().get_market_ohlcv_by_date, start.replace("-", ""), end.replace("-", ""), ticker, freq="d", adjusted=adjusted)
        return self._normalize(frame, ETF_RENAME if kind == "etf" else STOCK_RENAME)

    @staticmethod
    def _normalize(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
        if frame is None or frame.empty: return pd.DataFrame()
        result = frame.rename(columns=mapping).reset_index()
        for source in ("날짜", "Date"):
            if source in result: result = result.rename(columns={source: "date"})
        if "index" in result and "date" not in result and "ticker" not in result: result = result.rename(columns={"index": "date"})
        if "ticker" not in result and "date" not in result: result = result.rename(columns={result.columns[0]: "ticker"})
        if "ticker" in result: result["ticker"] = result["ticker"].astype(str).str.zfill(6)
        if "date" in result: result["date"] = pd.to_datetime(result["date"]).dt.strftime("%Y-%m-%d")
        return result


def fdr_stock_snapshot() -> tuple[str, pd.DataFrame]:
    """FinanceDataReader 전종목 시세를 KRX Open API의 대체 소스로 사용한다.

    한 번의 호출로 전종목 OHLCV를 받아오지만 날짜 컬럼이 없으므로, 기준 종목(삼성전자)의
    최근 일별 시세와 종가를 대조해 실제 거래일을 확정한다. 종가가 어긋나면 어느 거래일의
    스냅샷인지 알 수 없으므로 예외를 던진다.
    """
    listing = fdr.StockListing("KRX")
    if listing.empty:
        raise RuntimeError("FinanceDataReader에서 종목 목록을 가져오지 못했습니다.")
    reference = listing.loc[listing["Code"] == "005930"]
    if reference.empty:
        raise RuntimeError("기준 종목(005930)을 스냅샷에서 찾을 수 없습니다.")
    reference_close = float(reference.iloc[0]["Close"])
    recent = fdr.DataReader("005930", (pd.Timestamp.now() - pd.Timedelta(days=14)).strftime("%Y-%m-%d"))
    if recent.empty:
        raise RuntimeError("기준 종목의 최근 일봉을 가져오지 못했습니다.")
    if abs(float(recent.iloc[-1]["Close"]) - reference_close) > 0.5:
        raise RuntimeError("전종목 스냅샷과 기준 종목 종가가 일치하지 않아 거래일을 확정할 수 없습니다.")
    day = recent.index[-1].strftime("%Y-%m-%d")
    frame = listing.rename(columns={"Code": "ticker", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume", "Amount": "value"})
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    return day, frame[["ticker", "open", "high", "low", "close", "volume", "value"]]
