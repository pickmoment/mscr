from __future__ import annotations

import json
import keyword
from datetime import datetime
from typing import Any

import pandas as pd
import os
from fastapi import APIRouter, HTTPException, Query, Response

from ..broker.kis import CREDENTIAL_PATH as kis_credential_path
from ..broker.kis import KISError, broker_from_config, broker_status, clear_credentials, save_credentials, set_active_env
from ..db import db_session
from ..dynamic import BUILTIN_CATALOG, BUILTIN_FUNCTIONS, SCREEN_NAMES, SERIES_NAMES, custom_definitions, formula_calls, ticker_snapshot, truncate_price_jump, validate_formula
from ..indicators import bollinger_bands, macd, rsi, sma
from ..portfolio import replay_trades, snapshot, validate_trade
from ..config import DB_PATH, MSCR_HOME, SCHEMA_VERSION, request_delay, request_delay_source
from ..credentials import load as load_settings
from ..credentials import path_for
from ..credentials import save as save_settings
from ..providers.krx import CREDENTIAL_NAME as krx_credential_name
from ..providers.krx import KRXProvider, _stock, clear_krx_credentials, krx_status, save_krx_credentials
from ..screener import FIELDS, run
from ..trading import delete_plan, evaluate_plans, list_orders, list_plans, run_plans, save_plan, sync_orders
from ..autoplan import propose as propose_plan
from .models import ActiveEnvRequest, BrokerCredentialRequest, CashRequest, IndicatorDefinitionRequest, KRXCredentialRequest, PlanProposalRequest, PreferenceRequest, ScreenRequest, ScreenSaveRequest, TradePlanRequest, TradeRequest, TradeRunRequest

router = APIRouter(prefix="/api")

@router.get("/meta")
def meta():
    with db_session() as db:
        counts = {r["kind"]: r["n"] for r in db.execute("SELECT kind,COUNT(*) n FROM instruments GROUP BY kind")}
        bars = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        latest = db.execute("SELECT MAX(ran_at) FROM ingest_runs WHERE status='ok'").fetchone()[0]
        as_of = db.execute("SELECT MAX(date) FROM daily_bars WHERE source='krx_snapshot'").fetchone()[0]
    return {"as_of": as_of, "instrument_count": {"stock": counts.get("stock", 0), "etf": counts.get("etf", 0)}, "bars_rows": bars, "last_ingest_at": latest, "data_ready": bool(bars)}


def _settings() -> dict[str, Any]:
    with db_session() as db:
        counts = {row["kind"]: row["n"] for row in db.execute("SELECT kind,COUNT(*) n FROM instruments GROUP BY kind")}
        bars = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        as_of = db.execute("SELECT MAX(date) FROM daily_bars WHERE source='krx_snapshot'").fetchone()[0]
        last_ingest = db.execute("SELECT MAX(ran_at) FROM ingest_runs WHERE status='ok'").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
    krx = krx_status()
    return {
        "mscr_home": str(MSCR_HOME), "db_path": str(DB_PATH), "schema_version": version, "expected_schema_version": SCHEMA_VERSION,
        "request_delay_sec": request_delay(), "request_delay_source": request_delay_source(),
        "krx": {key: krx[key] for key in ("mode", "source", "openapi_key_masked", "krx_id_masked", "stored")},
        "credential_paths": {"krx": str(path_for(krx_credential_name)), "kis": str(kis_credential_path)},
        "kis": broker_status(),
        "data": {"as_of": as_of, "bars_rows": bars, "instrument_count": {"stock": counts.get("stock", 0), "etf": counts.get("etf", 0)}, "last_ingest_at": last_ingest},
    }


@router.get("/settings")
def settings():
    return _settings()


@router.put("/settings/krx")
def put_krx_credentials(request: KRXCredentialRequest):
    payload = {key: value for key, value in request.model_dump().items() if value is not None}
    if not payload:
        raise HTTPException(422, "저장할 값이 없습니다")
    save_krx_credentials(**payload)
    return _settings()


@router.delete("/settings/krx")
def delete_krx_credentials():
    clear_krx_credentials()
    return _settings()


@router.put("/settings/preferences")
def put_preferences(request: PreferenceRequest):
    save_settings("settings", load_settings("settings") | {"request_delay_sec": request.request_delay_sec})
    return _settings()


@router.post("/screen")
def screen(request: ScreenRequest):
    try:
        rows = run(request.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"as_of": rows[0].get("as_of") if rows else None, "count": len(rows), "rows": rows}

@router.get("/screens")
def screens():
    with db_session() as db:
        return [dict(row) | {"spec": json.loads(row["spec"])} for row in db.execute("SELECT id,name,spec,updated_at FROM screens ORDER BY name")]

def _validated_spec(spec: ScreenRequest) -> dict[str, Any]:
    payload = spec.model_dump()
    functions = BUILTIN_FUNCTIONS | {item["key"] for item in custom_definitions(enabled_only=False)}
    sort_formula = str((payload.get("sort") or {}).get("formula", "")).strip()
    if not sort_formula:
        raise HTTPException(422, "정렬 수식이 필요합니다")
    for formula in (payload["formula"], sort_formula):
        try:
            validate_formula(formula, SCREEN_NAMES, functions)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return payload


@router.post("/screens", status_code=201)
def save_screen(request: ScreenSaveRequest):
    payload = _validated_spec(request.spec)
    now = datetime.now().isoformat(timespec="seconds")
    with db_session() as db:
        db.execute("INSERT INTO screens(name,spec,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET spec=excluded.spec,updated_at=excluded.updated_at", (request.name, json.dumps(payload, ensure_ascii=False), now, now))
        row = db.execute("SELECT id FROM screens WHERE name=?", (request.name,)).fetchone()
    return {"id": row[0]}


@router.put("/screens/{screen_id}")
def update_screen(screen_id: int, request: ScreenSaveRequest):
    payload = _validated_spec(request.spec)
    now = datetime.now().isoformat(timespec="seconds")
    with db_session() as db:
        if not db.execute("SELECT 1 FROM screens WHERE id=?", (screen_id,)).fetchone():
            raise HTTPException(404, "screen not found")
        if db.execute("SELECT 1 FROM screens WHERE name=? AND id<>?", (request.name, screen_id)).fetchone():
            raise HTTPException(409, "같은 이름의 프리셋이 있습니다")
        db.execute("UPDATE screens SET name=?, spec=?, updated_at=? WHERE id=?", (request.name, json.dumps(payload, ensure_ascii=False), now, screen_id))
    return {"id": screen_id, "name": request.name, "updated_at": now}

@router.delete("/screens/{screen_id}", status_code=204)
def delete_screen(screen_id: int):
    with db_session() as db:
        db.execute("DELETE FROM screens WHERE id=?", (screen_id,))
    return Response(status_code=204)

@router.get("/indicators")
def indicators():
    fields_by_key = {key: spec for key, spec in FIELDS.items()}
    inputs = [{"id": None, "key": key, "label": fields_by_key[key].label_ko, "unit": fields_by_key[key].unit, "formula": None, "parameters": [], "enabled": True, "builtin": True, "series": key in SERIES_NAMES, "kind": fields_by_key[key].kind, "created_at": None, "updated_at": None} for key in fields_by_key if key in SCREEN_NAMES]
    functions = [{"id": None, "key": item["key"], "label": item["label"], "unit": "number", "formula": item["signature"], "parameters": [{"name": "period", "default": 20, "min": 1, "max": 10000, "integer": True}] if item["key"] in {"sma", "ema", "rsi", "returns", "prior_avg_ratio", "historical_volatility", "atr", "slope", "rolling_max", "rolling_min"} else [], "enabled": True, "builtin": True, "series": False, "kind": "function", "created_at": None, "updated_at": None} for item in BUILTIN_CATALOG]
    custom = [item | {"builtin": False, "series": False, "kind": "function"} for item in custom_definitions(enabled_only=False)]
    return inputs + functions + custom


def _dependency_cycle(key: str, formula: str, others: dict[str, Any]) -> list[str] | None:
    """저장하려는 정의에서 출발해 자기 자신으로 돌아오는 호출 경로를 찾는다."""
    def walk(current: str, path: list[str], body: str) -> list[str] | None:
        for called in sorted(formula_calls(body) & (set(others) | {key})):
            if called == key:
                return path + [called]
            if called in path:
                continue
            found = walk(called, path + [called], others[called]["formula"])
            if found:
                return found
        return None

    return walk(key, [key], formula)


@router.post("/indicators", status_code=201)
def save_indicator(request: IndicatorDefinitionRequest):
    # 접두어 규칙이 없으므로 충돌 검사가 유일한 방어선이다. 사용자 지표는 평가 시 내장 함수보다 먼저
    # 조회되므로 이름이 겹치면 내장을 조용히 가로챈다.
    if request.key in SCREEN_NAMES or request.key in BUILTIN_FUNCTIONS:
        raise HTTPException(409, f"내장 이름은 덮어쓸 수 없습니다: {request.key}")
    # 키워드는 수식으로 파싱되지 않아 저장하면 두 번 다시 쓸 수 없는 지표가 된다.
    if keyword.iskeyword(request.key) or keyword.issoftkeyword(request.key):
        raise HTTPException(422, f"수식 문법에 쓰이는 이름은 지표 키로 쓸 수 없습니다: {request.key}")
    names = [parameter.name for parameter in request.parameters]
    if len(names) != len(set(names)) or any(name in SCREEN_NAMES or name in BUILTIN_FUNCTIONS or keyword.iskeyword(name) or keyword.issoftkeyword(name) for name in names):
        raise HTTPException(422, "파라미터 이름은 서로 달라야 하고 내장 이름이나 수식 문법 이름을 쓸 수 없습니다")
    for parameter in request.parameters:
        if parameter.min is not None and parameter.default < parameter.min or parameter.max is not None and parameter.default > parameter.max or parameter.min is not None and parameter.max is not None and parameter.min > parameter.max:
            raise HTTPException(422, f"invalid range for parameter: {parameter.name}")
    others = {item["key"]: item for item in custom_definitions(enabled_only=False) if item["key"] != request.key}
    try:
        # 정의 수식은 스크린 수식과 같은 이름을 볼 수 있다. 런타임 env가 스칼라까지 담고 있으므로 SERIES_NAMES로 좁히면 안 된다.
        # 자기 이름도 일단 통과시켜 아래 순환 검사가 "허용되지 않은 함수" 대신 순환이라고 정확히 알려주게 한다.
        validate_formula(request.formula, SCREEN_NAMES | set(names), BUILTIN_FUNCTIONS | set(others) | {request.key})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    cycle = _dependency_cycle(request.key, request.formula, others)
    if cycle:
        raise HTTPException(422, f"사용자 지표가 순환 참조합니다: {' → '.join(cycle)}")
    now = datetime.now().isoformat(timespec="seconds")
    parameters = json.dumps([parameter.model_dump() for parameter in request.parameters], ensure_ascii=False)
    with db_session() as db:
        db.execute("INSERT INTO indicator_definitions(key,label,unit,formula,parameters,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET label=excluded.label,unit=excluded.unit,formula=excluded.formula,parameters=excluded.parameters,enabled=excluded.enabled,updated_at=excluded.updated_at", (request.key, request.label, request.unit, request.formula, parameters, int(request.enabled), now, now))
        row = db.execute("SELECT id FROM indicator_definitions WHERE key=?", (request.key,)).fetchone()
    return {"id": row[0]}


@router.delete("/indicators/{indicator_id}", status_code=204)
def delete_indicator(indicator_id: int):
    with db_session() as db:
        db.execute("DELETE FROM indicator_definitions WHERE id=?", (indicator_id,))
    return Response(status_code=204)

@router.get("/instruments/{ticker}")
def instrument(ticker: str):
    with db_session() as db:
        item = db.execute("SELECT * FROM instruments WHERE ticker=?", (ticker,)).fetchone()
        if not item:
            raise HTTPException(404, "instrument not found")
        item = dict(item)
        quote = db.execute("SELECT * FROM daily_bars WHERE ticker=? AND source='krx_snapshot' ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
        fund = db.execute("SELECT * FROM snapshots_fundamental WHERE ticker=? ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    metric = ticker_snapshot(ticker)
    quote = dict(quote) if quote else {}
    fund = dict(fund) if fund else {}
    result = {
        "ticker": ticker, "name": item["name"], "kind": item["kind"], "market": item["market"], "category": item["category"], "base_index": item["base_index"], "is_preferred": bool(item["is_preferred"]), "delisted": bool(item["delisted"]), "as_of": metric.get("as_of") or quote.get("date"),
        "quote": {key: quote.get(key) for key in ("close", "open", "high", "low", "volume", "value", "nav")},
        "fundamental": {key: fund.get(key) for key in ("market_cap", "shares", "per", "pbr", "eps", "bps", "div", "dps")},
        "bars_available": metric.get("bars_available", 0),
    }
    result["quote"]["change_pct"] = metric.get("change_pct")
    result["quote"]["weighted_return"] = metric.get("weighted_return")
    result["quote"]["halted"] = bool(quote.get("halted"))
    result["position"] = next((p for p in snapshot()["positions"] if p["ticker"] == ticker), None)
    result["etf"] = None
    if item["kind"] == "etf":
        nav = quote.get("nav")
        result["etf"] = {"nav": nav, "premium_pct": quote.get("close") / nav - 1 if quote.get("close") and nav else None, "tracking_error": None, "top_holdings": None}
        if krx_status()["mode"] != "openapi":
            try:
                provider = KRXProvider(); as_of = result["as_of"].replace("-", "")
                stock = _stock()
                tracking = provider._call(stock.get_etf_tracking_error, (pd.Timestamp(result["as_of"]) - pd.Timedelta(days=90)).strftime("%Y%m%d"), as_of, ticker)
                result["etf"]["tracking_error"] = float(tracking.iloc[-1].iloc[0]) if hasattr(tracking, "iloc") and not tracking.empty else None
                holdings = provider._call(stock.get_etf_portfolio_deposit_file, ticker, as_of)
                result["etf"]["top_holdings"] = [{"name": str(index), "weight": float(row.iloc[-1])} for index, row in holdings.head(10).iterrows()]
            except Exception:
                pass
    return result

@router.get("/instruments/{ticker}/bars")
def bars(ticker: str, range: str = Query("1y"), indicators: str = Query("ma,rsi,macd,bb,volume_ma"), ma_periods: str = Query("5,20,60"), rsi_period: int = Query(14, ge=1, le=10000), macd_fast: int = Query(12, ge=1, le=10000), macd_slow: int = Query(26, ge=1, le=10000), macd_signal: int = Query(9, ge=1, le=10000), bb_period: int = Query(20, ge=1, le=10000), bb_k: float = Query(2.0, gt=0, le=20), volume_ma_period: int = Query(50, ge=1, le=10000)):
    if range not in {"3m", "6m", "1y", "3y", "max"}:
        raise HTTPException(422, "invalid range")
    try:
        ma_values = list(dict.fromkeys(int(value.strip()) for value in ma_periods.split(",") if value.strip()))
    except ValueError as exc:
        raise HTTPException(422, "invalid MA periods") from exc
    if not ma_values or len(ma_values) > 5 or any(value < 1 or value > 10000 for value in ma_values):
        raise HTTPException(422, "MA periods must contain 1-5 positive integers")
    with db_session() as db:
        item = db.execute("SELECT kind FROM instruments WHERE ticker=?", (ticker,)).fetchone()
        if not item:
            raise HTTPException(404, "instrument not found")
        as_of = db.execute("SELECT MAX(date) FROM daily_bars WHERE ticker=?", (ticker,)).fetchone()[0]
        if not as_of:
            return {"ticker": ticker, "adjusted": False, "price_jump_flag": False, "bars": [], "overlays": {}}
        start = (pd.Timestamp(as_of) - pd.Timedelta(days={"3m": 92, "6m": 184, "1y": 366, "3y": 1096, "max": 100000}[range])).strftime("%Y-%m-%d")
        rows = db.execute("SELECT * FROM daily_bars WHERE ticker=? AND source='adjusted' AND date>=? AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL ORDER BY date", (ticker, start)).fetchall()
        adjusted = bool(rows)
        stale = bool(rows) and rows[-1]["date"] < as_of
        # 캐시된 adjusted 데이터가 이 범위의 시작일까지 닿는지 별도로 확인한다. rows는 date>=start로만 걸러서
        # tail(최신 봉)이 최신이어도 head(과거 봉)가 이전에 더 좁은 range로 캐시된 채 남아있을 수 있다.
        floor = db.execute("SELECT earliest_attempted FROM bars_coverage WHERE ticker=? AND source='adjusted'", (ticker,)).fetchone()
        incomplete = floor is None or floor[0] > start
        if not rows or stale or incomplete:
            try:
                history = KRXProvider().history(ticker, start, as_of, item["kind"], adjusted=True)
                if not history.empty:
                    db.executemany("INSERT OR REPLACE INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(ticker, r.get("date"), "adjusted", r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("volume"), r.get("value"), r.get("nav"), 0) for r in history.to_dict("records")])
                    rows = db.execute("SELECT * FROM daily_bars WHERE ticker=? AND source='adjusted' AND date>=? AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL AND volume IS NOT NULL ORDER BY date", (ticker, start)).fetchall()
                    adjusted = True
                # 결과가 비어도(더 이상 과거 데이터가 없다는 뜻) 다음 요청에서 같은 구간을 또 조회하지 않도록 기록한다.
                db.execute("INSERT INTO bars_coverage(ticker,source,earliest_attempted) VALUES(?,'adjusted',?) ON CONFLICT(ticker,source) DO UPDATE SET earliest_attempted=MIN(earliest_attempted,excluded.earliest_attempted)", (ticker, start))
            except Exception:
                pass
        if not rows:
            rows = db.execute("SELECT * FROM daily_bars WHERE ticker=? AND source='krx_snapshot' AND date>=? ORDER BY date", (ticker, start)).fetchall()
            adjusted = False
    frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        return {"ticker": ticker, "adjusted": adjusted, "price_jump_flag": False, "bars": [], "overlays": {}}
    halted = frame["halted"].astype(bool)
    for col in ("open", "high", "low"):
        frame.loc[halted, col] = frame.loc[halted, "close"]
    valid, price_jump_flag = truncate_price_jump(frame[~halted].reset_index(drop=True))
    series = [pd.Series(valid[col].to_numpy(dtype=float), index=valid["date"].tolist()) for col in ("open", "high", "low", "close", "volume")]
    requested = {part.strip() for part in indicators.split(",")}
    output = {"ticker": ticker, "adjusted": adjusted, "price_jump_flag": price_jump_flag, "bars": [{"time": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"], "halted": bool(r["halted"])} for r in frame.to_dict("records")], "overlays": {}}
    if "ma" in requested:
        for period in ma_values:
            values = sma(series[3], period)
            output["overlays"][f"ma{period}"] = [{"time": idx, "value": value} for idx, value in values.items() if pd.notna(value)]
    if "rsi" in requested:
        values = rsi(series[3], rsi_period)
        output["rsi"] = [{"time": idx, "value": value} for idx, value in values.items() if pd.notna(value)]
    if "macd" in requested:
        values = macd(series[3], macd_fast, macd_slow, macd_signal)
        output["macd"] = {key: [{"time": idx, "value": value} for idx, value in series_value.items() if pd.notna(value)] for key, series_value in values.items()}
    if "bb" in requested:
        values = bollinger_bands(series[3], bb_period, bb_k)
        output["bb"] = {key: [{"time": idx, "value": value} for idx, value in values[key].items() if pd.notna(value)] for key in ("upper", "lower")}
    if "volume_ma" in requested:
        values = sma(series[4], volume_ma_period)
        output["volume_ma"] = [{"time": idx, "value": value} for idx, value in values.items() if pd.notna(value)]
    return output

@router.get("/portfolio")
def portfolio():
    return snapshot()

@router.get("/trades")
def trades():
    with db_session() as db:
        return [dict(row) for row in db.execute("SELECT t.*,i.name FROM trades t LEFT JOIN instruments i ON i.ticker=t.ticker ORDER BY trade_date,id").fetchall()]

@router.post("/trades", status_code=201)
def add_trade(request: TradeRequest):
    payload = request.model_dump(); payload["trade_date"] = payload["trade_date"].isoformat()
    try:
        validate_trade(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with db_session() as db:
        cursor = db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,memo,created_at) VALUES(?,?,?,?,?,?,?,?,datetime('now'))", tuple(payload[key] for key in ("ticker", "side", "trade_date", "quantity", "price", "fee", "tax", "memo")))
    return {"id": cursor.lastrowid}

@router.delete("/trades/{trade_id}", status_code=204)
def delete_trade(trade_id: int):
    with db_session() as db:
        if not db.execute("SELECT 1 FROM trades WHERE id=?", (trade_id,)).fetchone(): raise HTTPException(404, "trade not found")
        remaining = [dict(row) for row in db.execute("SELECT * FROM trades WHERE id != ? ORDER BY trade_date,id", (trade_id,)).fetchall()]
        try: replay_trades(remaining)
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        db.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    return Response(status_code=204)

@router.put("/settings/cash")
def cash(request: CashRequest):
    with db_session() as db: db.execute("INSERT INTO settings(key,value) VALUES('cash_krw',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(request.cash_krw),))
    return {"cash_krw": request.cash_krw}

def _trading_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    return HTTPException(409 if "같은 이름의 계획이 있습니다" in message else 422, message)

def _require_broker():
    broker = broker_from_config()
    if broker is None:
        raise HTTPException(422, "브로커가 설정되지 않았습니다")
    return broker

@router.get("/trading/status")
def trading_status():
    return broker_status()

@router.put("/trading/credentials")
def trading_credentials(request: BrokerCredentialRequest):
    try:
        status = save_credentials(request.app_key, request.app_secret, request.account, request.env)
    except KISError as exc:
        print(f"[mscr] KIS 자격증명 저장 실패: {exc}", flush=True)
        raise HTTPException(422, str(exc)) from exc
    print(f"[mscr] KIS 자격증명 저장: env={request.env} account={status['accounts'].get(request.env)} → {kis_credential_path}", flush=True)
    return status

@router.delete("/trading/credentials")
def delete_trading_credentials(env: str = Query("paper")):
    try:
        status = clear_credentials(env)
    except KISError as exc:
        raise HTTPException(422, str(exc)) from exc
    print(f"[mscr] KIS 자격증명 삭제(env={env}) → {kis_credential_path}", flush=True)
    return status

@router.put("/trading/active-env")
def trading_active_env(request: ActiveEnvRequest):
    try:
        return set_active_env(request.env)
    except KISError as exc:
        raise HTTPException(422, str(exc)) from exc



@router.get("/trading/plans")
def trading_plans():
    return list_plans()

@router.post("/trading/plans")
def save_trading_plan(request: TradePlanRequest):
    payload = request.model_dump()
    if payload.get("id") is None: payload.pop("id", None)
    try:
        return {"id": save_plan(payload)}
    except ValueError as exc:
        raise _trading_error(exc) from exc

@router.post("/trading/plans/propose")
def propose_trading_plan(request: PlanProposalRequest):
    try:
        return propose_plan(request.ticker, request.side, request.entry_price, request.max_investment, request.max_loss)
    except ValueError as exc:
        raise _trading_error(exc) from exc

@router.delete("/trading/plans/{plan_id}", status_code=204)
def delete_trading_plan(plan_id: int):
    try:
        delete_plan(plan_id)
    except ValueError as exc:
        raise _trading_error(exc) from exc
    return Response(status_code=204)

@router.get("/trading/evaluate")
def trading_evaluate():
    try:
        return evaluate_plans()
    except ValueError as exc:
        raise _trading_error(exc) from exc

@router.post("/trading/run")
def trading_run(request: TradeRunRequest):
    broker = None if request.dry_run else _require_broker()
    try:
        return run_plans(broker=broker, dry_run=request.dry_run, plan_ids=request.plan_ids)
    except ValueError as exc:
        raise _trading_error(exc) from exc
    except KISError as exc:
        raise HTTPException(502, str(exc)) from exc

@router.post("/trading/sync")
def trading_sync():
    broker = _require_broker()
    try:
        return sync_orders(broker=broker)
    except ValueError as exc:
        raise _trading_error(exc) from exc
    except KISError as exc:
        raise HTTPException(502, str(exc)) from exc

@router.get("/trading/orders")
def trading_orders(limit: int = Query(200, ge=1, le=2000)):
    return list_orders(limit=limit)
