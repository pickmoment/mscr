from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

from ..config import MSCR_HOME, REQUEST_RETRIES
from ..credentials import clear as store_clear
from ..credentials import load as store_load
from ..credentials import path_for
from ..credentials import save as store_save

BASE_URL = {"real": "https://openapi.koreainvestment.com:9443", "paper": "https://openapivts.koreainvestment.com:29443"}
ORDER_TR = {("real", "buy"): "TTTC0012U", ("real", "sell"): "TTTC0011U", ("paper", "buy"): "VTTC0012U", ("paper", "sell"): "VTTC0011U"}
CCLD_TR = {"real": "TTTC0081R", "paper": "VTTC0081R"}
BALANCE_TR = {"real": "TTTC8434R", "paper": "VTTC8434R"}
REVISE_TR = {"real": "TTTC0013U", "paper": "VTTC0013U"}
PSBL_RVSECNCL_TR = "TTTC0084R"  # 주식정정취소가능주문조회 — 모의투자는 제공되지 않는다.
PRICE_TR = "FHKST01010100"
# 실시간 시세 웹소켓. 포트는 실전 21000 / 모의 31000이며 MSCR_KIS_WS_URL로 덮어쓸 수 있다.
WS_URL = {"real": "ws://ops.koreainvestment.com:21000", "paper": "ws://ops.koreainvestment.com:31000"}
WS_PATH = "/tryitout"
ORDER_DVSN = {"limit": "00", "market": "01"}
ACCOUNT_PATTERN = re.compile(r"^(\d{8})(?:-?(\d{2}))?$")
CREDENTIAL_NAME = "kis_credentials"
CREDENTIAL_PATH = path_for(CREDENTIAL_NAME)


def _load_store() -> dict[str, Any]:
    """Load stored KIS credentials, migrating the legacy single-account flat format (app_key/app_secret/account/env at the top level) into per-env slots in place."""
    stored = store_load(CREDENTIAL_NAME)
    legacy_key, legacy_secret, legacy_account = stored.get("app_key"), stored.get("app_secret"), stored.get("account")
    if not (legacy_key and legacy_secret and legacy_account):
        return stored
    legacy_env = str(stored.get("env") or "paper").strip().lower()
    if legacy_env not in BASE_URL:
        legacy_env = "paper"
    migrated = {key: value for key, value in stored.items() if key not in ("app_key", "app_secret", "account", "env")}
    if not isinstance(migrated.get(legacy_env), dict):
        migrated[legacy_env] = {"app_key": str(legacy_key), "app_secret": str(legacy_secret), "account": str(legacy_account)}
    migrated.setdefault("active_env", legacy_env)
    store_save(CREDENTIAL_NAME, migrated)
    return migrated


class KISError(RuntimeError):
    """Transport or authentication failure against the KIS Open API."""


@dataclass(frozen=True)
class OrderResult:
    broker_order_id: str | None
    status: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    org_no: str | None = None      # KRX_FWDG_ORD_ORGNO — 정정·취소에 원주문번호와 함께 필요하다.
    order_time: str | None = None


@dataclass(frozen=True)
class Fill:
    quantity: float
    price: float
    fee: float
    tax: float
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    remaining: float = 0.0
    org_no: str | None = None


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


class KISBroker:
    """한국투자증권 Open API 국내주식 현금주문 어댑터."""

    def __init__(self, env: str | None = None, app_key: str | None = None, app_secret: str | None = None, account: str | None = None):
        stored = _load_store()
        self.env = (env or os.environ.get("KIS_ENV") or stored.get("active_env") or "paper").strip().lower()
        if self.env not in BASE_URL:
            raise KISError("KIS 환경은 paper 또는 real 이어야 합니다")
        slot = stored.get(self.env) if isinstance(stored.get(self.env), dict) else {}
        self.app_key = app_key or os.environ.get("KIS_APP_KEY") or slot.get("app_key") or ""
        self.app_secret = app_secret or os.environ.get("KIS_APP_SECRET") or slot.get("app_secret") or ""
        account = (account or os.environ.get("KIS_ACCOUNT") or slot.get("account") or "").strip()
        matched = ACCOUNT_PATTERN.match(account)
        if not (self.app_key and self.app_secret and matched):
            raise KISError("앱키, 앱시크릿, 계좌번호(8자리 또는 8자리-2자리)가 필요합니다")
        self.cano, self.prod = matched.group(1), matched.group(2) or "01"
        self.base_url = BASE_URL[self.env]
        self.token_path = MSCR_HOME / f"kis_token_{self.env}.json"
        self.approval_path = MSCR_HOME / f"kis_approval_{self.env}.json"
        self.session = requests.Session()

    @property
    def account_masked(self) -> str:
        return f"{self.cano[:4]}****-{self.prod}"

    def _cached_token(self) -> str | None:
        try:
            cached = json.loads(self.token_path.read_text())
        except (OSError, ValueError):
            return None
        expires = str(cached.get("expires", ""))
        if not cached.get("token") or not expires:
            return None
        try:
            valid_until = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return str(cached["token"]) if valid_until - timedelta(minutes=10) > datetime.now() else None

    def _token(self) -> str:
        cached = self._cached_token()
        if cached:
            return cached
        response = self.session.post(f"{self.base_url}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}, timeout=20)
        if response.status_code != 200:
            raise KISError(f"KIS 토큰 발급 실패: {response.status_code} {response.text[:200]}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise KISError(f"KIS 토큰 응답에 access_token이 없습니다: {payload}")
        MSCR_HOME.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps({"token": token, "expires": payload.get("access_token_token_expired", "")}, ensure_ascii=False))
        os.chmod(self.token_path, 0o600)
        return str(token)

    def _call(self, method: str, path: str, tr_id: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self._token()}", "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id, "custtype": "P"}
        last_error: Exception | None = None
        for attempt in range(REQUEST_RETRIES + 1):
            try:
                response = self.session.request(method, f"{self.base_url}{path}", headers=headers, params=params, json=body, timeout=20)
                if response.status_code != 200:
                    raise KISError(f"KIS 호출 실패 [{tr_id}]: {response.status_code} {response.text[:200]}")
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < REQUEST_RETRIES:
                    time.sleep(2**attempt)
        raise KISError(f"KIS 통신 실패 [{tr_id}]: {last_error}")

    def submit_order(self, ticker: str, side: str, quantity: float, order_type: str, limit_price: float | None) -> OrderResult:
        if side not in ("buy", "sell"):
            raise KISError("side는 buy 또는 sell 이어야 합니다")
        if order_type not in ORDER_DVSN:
            raise KISError("order_type은 limit 또는 market 이어야 합니다")
        if order_type == "limit" and not limit_price:
            raise KISError("지정가 주문은 limit_price가 필요합니다")
        # KRX는 소수 주식이 없다. 절삭(int)하면 2.999…주가 2주로 깎여 잔량이 남으므로 반올림한다.
        order_qty = round(quantity)
        if order_qty <= 0:
            raise KISError(f"주문 수량이 0주 이하입니다: {quantity:g}")
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prod, "PDNO": ticker,
            "ORD_DVSN": ORDER_DVSN[order_type], "ORD_QTY": str(order_qty),
            "ORD_UNPR": str(int(limit_price)) if order_type == "limit" else "0",
            "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": "",
        }
        payload = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash", ORDER_TR[(self.env, side)], body=body)
        message = str(payload.get("msg1", "")).strip()
        if str(payload.get("rt_cd")) != "0":
            return OrderResult(None, "rejected", message or f"주문 거부 (msg_cd={payload.get('msg_cd')})", payload)
        output = payload.get("output") or {}
        return OrderResult(str(output.get("ODNO") or "") or None, "submitted", message or "주문 접수", payload,
                           org_no=str(output.get("KRX_FWDG_ORD_ORGNO") or "") or None, order_time=str(output.get("ORD_TMD") or "") or None)

    def order_fill(self, broker_order_id: str, order_date: str | None = None) -> Fill:
        day = (order_date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prod, "INQR_STRT_DT": day, "INQR_END_DT": day,
            "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "", "ODNO": broker_order_id, "INQR_DVSN_1": "", "EXCG_ID_DVSN_CD": "KRX",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        payload = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", CCLD_TR[self.env], params=params)
        if str(payload.get("rt_cd")) != "0":
            return Fill(0, 0, 0, 0, "rejected", payload)
        rows = [row for row in (payload.get("output1") or []) if str(row.get("odno", "")).lstrip("0") == broker_order_id.lstrip("0")]
        if not rows:
            return Fill(0, 0, 0, 0, "submitted", payload)
        row = rows[0]
        filled = _number(row.get("tot_ccld_qty"))
        remaining = _number(row.get("rmn_qty"))
        amount = _number(row.get("tot_ccld_amt"))
        price = _number(row.get("avg_prvs")) or (amount / filled if filled else 0.0)
        if str(row.get("cncl_yn", "")).upper() == "Y" and filled <= 0:
            status = "rejected"
        elif filled > 0 and remaining <= 0:
            status = "filled"
        elif filled > 0:
            status = "partial"
        else:
            status = "submitted"
        return Fill(filled, price, 0.0, 0.0, status, payload, remaining=remaining, org_no=str(row.get("ord_gno_brno") or "") or None)

    def revise_order(self, org_no: str, broker_order_id: str, quantity: float, order_type: str, limit_price: float | None, all_quantity: bool = True) -> OrderResult:
        """미체결 주문을 정정한다. 주문구분을 시장가로 바꾸면 남은 잔량이 즉시 체결된다."""
        return self._amend(org_no, broker_order_id, "01", quantity, order_type, limit_price, all_quantity)

    def cancel_order(self, org_no: str, broker_order_id: str, quantity: float = 0.0, all_quantity: bool = True) -> OrderResult:
        """미체결 잔량을 취소한다. 이미 체결된 수량은 취소되지 않는다."""
        return self._amend(org_no, broker_order_id, "02", quantity, "market", None, all_quantity)

    def _amend(self, org_no: str, broker_order_id: str, kind: str, quantity: float, order_type: str, limit_price: float | None, all_quantity: bool) -> OrderResult:
        if not org_no or not broker_order_id:
            raise KISError("정정·취소에는 원주문번호와 주문조직번호가 모두 필요합니다")
        if order_type not in ORDER_DVSN:
            raise KISError("order_type은 limit 또는 market 이어야 합니다")
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prod, "KRX_FWDG_ORD_ORGNO": org_no, "ORGN_ODNO": broker_order_id,
            "ORD_DVSN": ORDER_DVSN[order_type], "RVSE_CNCL_DVSN_CD": kind,
            "ORD_QTY": str(max(0, round(quantity))), "ORD_UNPR": str(int(limit_price)) if order_type == "limit" and limit_price else "0",
            "QTY_ALL_ORD_YN": "Y" if all_quantity else "N", "EXCG_ID_DVSN_CD": "KRX", "CNDT_PRIC": "",
        }
        payload = self._call("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", REVISE_TR[self.env], body=body)
        message = str(payload.get("msg1", "")).strip()
        if str(payload.get("rt_cd")) != "0":
            return OrderResult(None, "rejected", message or f"정정·취소 거부 (msg_cd={payload.get('msg_cd')})", payload)
        output = payload.get("output") or {}
        return OrderResult(str(output.get("ODNO") or "") or None, "submitted", message or ("취소 접수" if kind == "02" else "정정 접수"), payload,
                           org_no=str(output.get("KRX_FWDG_ORD_ORGNO") or "") or org_no, order_time=str(output.get("ORD_TMD") or "") or None)

    def current_price(self, ticker: str) -> dict[str, Any]:
        """현재가·당일 고저. 웹소켓이 끊겼을 때의 폴백이자 데몬 재시작 직후의 복구 판정에 쓴다."""
        payload = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", PRICE_TR,
                             params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker})
        if str(payload.get("rt_cd")) != "0":
            raise KISError(f"현재가 조회 실패 [{ticker}]: {payload.get('msg1')}")
        output = payload.get("output") or {}
        price = _number(output.get("stck_prpr"))
        if price <= 0:
            raise KISError(f"현재가가 비어 있습니다 [{ticker}]")
        return {
            "ticker": ticker, "price": price, "open": _number(output.get("stck_oprc")) or price,
            "high": _number(output.get("stck_hgpr")) or price, "low": _number(output.get("stck_lwpr")) or price,
            "volume": _number(output.get("acml_vol")), "halted": str(output.get("temp_stop_yn", "")).upper() == "Y",
        }

    def open_orders(self) -> list[dict[str, Any]]:
        """정정·취소가 가능한(= 미체결 잔량이 있는) 주문. 모의투자는 이 API를 제공하지 않아 빈 목록을 돌려준다."""
        if self.env != "real":
            return []
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "CTX_AREA_FK100": "", "CTX_AREA_NK100": "", "INQR_DVSN_1": "0", "INQR_DVSN_2": "0"}
        payload = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl", PSBL_RVSECNCL_TR, params=params)
        if str(payload.get("rt_cd")) != "0":
            raise KISError(f"정정취소가능주문 조회 실패: {payload.get('msg1')}")
        return [{"broker_order_id": str(row.get("odno") or "").lstrip("0"), "org_no": str(row.get("ord_gno_brno") or ""),
                 "ticker": str(row.get("pdno") or ""), "quantity": _number(row.get("ord_qty")),
                 "remaining": _number(row.get("psbl_qty")), "price": _number(row.get("ord_unpr"))}
                for row in (payload.get("output") or [])]

    def approval_key(self) -> str:
        """실시간 시세 웹소켓 접속키. 액세스 토큰과 발급 경로가 다르고(secretkey 필드) 하루 단위로 재발급한다."""
        cached = self._cached_approval()
        if cached:
            return cached
        response = self.session.post(f"{self.base_url}/oauth2/Approval", json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret}, timeout=20)
        if response.status_code != 200:
            raise KISError(f"KIS 접속키 발급 실패: {response.status_code} {response.text[:200]}")
        key = (response.json() or {}).get("approval_key")
        if not key:
            raise KISError("KIS 접속키 응답에 approval_key가 없습니다")
        MSCR_HOME.mkdir(parents=True, exist_ok=True)
        self.approval_path.write_text(json.dumps({"key": key, "issued": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
        os.chmod(self.approval_path, 0o600)
        return str(key)

    def _cached_approval(self) -> str | None:
        try:
            cached = json.loads(self.approval_path.read_text())
        except (OSError, ValueError):
            return None
        try:
            issued = datetime.fromisoformat(str(cached.get("issued", "")))
        except ValueError:
            return None
        return str(cached["key"]) if cached.get("key") and datetime.now() - issued < timedelta(hours=20) else None

    @property
    def ws_url(self) -> str:
        return f"{os.environ.get('MSCR_KIS_WS_URL') or WS_URL[self.env]}{WS_PATH}"

    def balance(self) -> dict[str, Any]:
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prod, "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        payload = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", BALANCE_TR[self.env], params=params)
        if str(payload.get("rt_cd")) != "0":
            raise KISError(f"잔고 조회 실패: {payload.get('msg1')}")
        positions = [{"ticker": str(row.get("pdno")), "quantity": _number(row.get("hldg_qty")), "avg_cost": _number(row.get("pchs_avg_pric"))} for row in (payload.get("output1") or []) if _number(row.get("hldg_qty")) > 0]
        summary = (payload.get("output2") or [{}])[0]
        return {"positions": positions, "cash_krw": _number(summary.get("dnca_tot_amt")) or _number(summary.get("prvs_rcdl_excc_amt"))}


def _mask_account(account: str) -> str | None:
    matched = ACCOUNT_PATTERN.match(str(account).strip())
    if not matched:
        return None
    cano, prod = matched.group(1), matched.group(2) or "01"
    return f"{cano[:4]}****-{prod}"


def _slot_configured(stored: dict[str, Any], env: str) -> bool:
    slot = stored.get(env) if isinstance(stored.get(env), dict) else {}
    return bool(slot.get("app_key") and slot.get("app_secret") and slot.get("account"))


def load_credentials(env: str | None = None) -> dict[str, str]:
    stored = _load_store()
    target = (env or stored.get("active_env") or "paper").strip().lower()
    slot = stored.get(target) if isinstance(stored.get(target), dict) else {}
    if not _slot_configured(stored, target):
        return {}
    return {"app_key": str(slot["app_key"]), "app_secret": str(slot["app_secret"]), "account": str(slot["account"]), "env": target}


def save_credentials(app_key: str, app_secret: str, account: str, env: str = "paper") -> dict[str, Any]:
    env = (env or "paper").strip().lower()
    if env not in BASE_URL:
        raise KISError("KIS 환경은 paper 또는 real 이어야 합니다")
    app_key, app_secret, account = app_key.strip(), app_secret.strip(), account.strip()
    if not (app_key and app_secret and account):
        raise KISError("앱키, 앱시크릿, 계좌번호를 모두 입력하세요")
    if len(app_key) < 10 or len(app_secret) < 10:
        raise KISError("앱키와 앱시크릿을 다시 확인하세요 (10자 이상)")
    if not ACCOUNT_PATTERN.match(account):
        raise KISError("계좌번호는 8자리 숫자(모의계좌) 또는 12345678-01 형식(실전계좌)이어야 합니다")
    stored = _load_store()
    stored[env] = {"app_key": app_key, "app_secret": app_secret, "account": account}
    stored["active_env"] = env
    store_save(CREDENTIAL_NAME, stored)
    _clear_tokens(env)
    return broker_status()


def set_active_env(env: str) -> dict[str, Any]:
    env = (env or "paper").strip().lower()
    if env not in BASE_URL:
        raise KISError("KIS 환경은 paper 또는 real 이어야 합니다")
    stored = _load_store()
    if not _slot_configured(stored, env):
        raise KISError(f"{'실전' if env == 'real' else '모의'} 계좌 자격증명이 저장되어 있지 않습니다")
    stored["active_env"] = env
    store_save(CREDENTIAL_NAME, stored)
    return broker_status()


def clear_credentials(env: str = "paper") -> dict[str, Any]:
    env = (env or "paper").strip().lower()
    if env not in BASE_URL:
        raise KISError("KIS 환경은 paper 또는 real 이어야 합니다")
    stored = _load_store()
    stored.pop(env, None)
    remaining = [other for other in BASE_URL if _slot_configured(stored, other)]
    if stored.get("active_env") == env:
        stored["active_env"] = remaining[0] if remaining else "paper"
    if remaining:
        store_save(CREDENTIAL_NAME, stored)
    else:
        store_clear(CREDENTIAL_NAME)
    _clear_tokens(env)
    return broker_status()


def _clear_tokens(env: str | None = None) -> None:
    for target in (env,) if env else BASE_URL:
        (MSCR_HOME / f"kis_token_{target}.json").unlink(missing_ok=True)


def broker_from_config() -> KISBroker | None:
    try:
        return KISBroker()
    except KISError:
        return None


def broker_status() -> dict[str, Any]:
    stored = _load_store()
    accounts: dict[str, str | None] = {env: (_mask_account(stored[env]["account"]) if _slot_configured(stored, env) else None) for env in BASE_URL}
    env_override = all(os.environ.get(name) for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"))
    active_env = ((os.environ.get("KIS_ENV") if env_override else None) or stored.get("active_env") or "paper").strip().lower()
    if active_env not in BASE_URL:
        active_env = "paper"
    source = "env" if env_override else ("file" if accounts.get(active_env) else None)
    if source is None:
        return {"enabled": False, "env": None, "account_masked": None, "source": None, "reason": "브로커 자격증명이 없습니다. 화면에서 저장하거나 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT 환경변수를 설정하세요.", "active_env": active_env, "accounts": accounts}
    try:
        broker = KISBroker(env=active_env)
    except KISError as exc:
        return {"enabled": False, "env": None, "account_masked": None, "source": source, "reason": str(exc), "active_env": active_env, "accounts": accounts}
    return {"enabled": True, "env": broker.env, "account_masked": broker.account_masked, "source": source, "reason": None, "active_env": active_env, "accounts": accounts}
