from __future__ import annotations

import ast
import json
import math
import operator
from typing import Any

import numpy as np
import pandas as pd

from .db import db_session
from .market import active as active_market
from .market import bar_source
from .indicators import atr, crosses, ema, historical_volatility, obv, obv_ratio, prior_avg_ratio, returns, rsi, slope, sma

SERIES_NAMES = {"open", "high", "low", "close", "volume", "value"}
SCALAR_NAMES = {"market_cap", "shares", "per", "pbr", "eps", "bps", "div", "change_pct", "bars_available", "halted", "price_jump_flag", "weighted_return"}
SCREEN_NAMES = SERIES_NAMES | SCALAR_NAMES
BUILTIN_FUNCTIONS = {"sma", "ema", "rsi", "returns", "prior_avg_ratio", "obv", "obv_ratio", "historical_volatility", "atr", "slope", "rolling_max", "rolling_min", "crosses_above", "crosses_below", "abs"}
BUILTIN_CATALOG = [
    {"key": "sma", "label": "단순 이동평균", "signature": "sma(series, period)"},
    {"key": "ema", "label": "지수 이동평균", "signature": "ema(series, period)"},
    {"key": "rsi", "label": "RSI", "signature": "rsi(close, period)"},
    {"key": "returns", "label": "기간 수익률", "signature": "returns(close, period)"},
    {"key": "prior_avg_ratio", "label": "직전 평균 대비 비율", "signature": "prior_avg_ratio(series, period)"},
    {"key": "obv", "label": "롤링 OBV", "signature": "obv(close, volume, period)"},
    {"key": "obv_ratio", "label": "롤링 OBV 비율", "signature": "obv_ratio(close, volume, period)"},
    {"key": "historical_volatility", "label": "실현 변동성", "signature": "historical_volatility(close, period)"},
    {"key": "atr", "label": "ATR", "signature": "atr(high, low, close, period)"},
    {"key": "slope", "label": "정규화 회귀 기울기", "signature": "slope(series, period)"},
    {"key": "rolling_max", "label": "기간 최고값", "signature": "rolling_max(series, period)"},
    {"key": "crosses_above", "label": "상향 교차", "signature": "crosses_above(fast, slow)"},
    {"key": "crosses_below", "label": "하향 교차", "signature": "crosses_below(fast, slow)"},
    {"key": "rolling_min", "label": "기간 최저값", "signature": "rolling_min(series, period)"},
    {"key": "abs", "label": "절댓값", "signature": "abs(value)"},
]
_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_COMPARE = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}


def _parse(formula: str) -> ast.Expression:
    """수식을 괄호로 한 번 더 감싸 파싱한다. 괄호 안에서는 개행이 단순 공백으로 취급되므로,
    여러 줄로 나눠 쓴(예: 조건마다 줄바꿈) 수식도 그대로 파싱된다."""
    return ast.parse(f"({formula})", mode="eval")


def validate_formula(formula: str, allowed_names: set[str] | None = None, allowed_functions: set[str] | None = None) -> None:
    try:
        tree = _parse(formula)
    except SyntaxError as exc:
        raise ValueError(f"수식 문법 오류: {exc.msg}") from exc
    functions = allowed_functions or BUILTIN_FUNCTIONS
    allowed_nodes = (ast.Expression, ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or, ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq, ast.Name, ast.Load, ast.Call, ast.Constant)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in functions or node.keywords:
                raise ValueError("허용되지 않은 함수 호출입니다")
        elif isinstance(node, ast.Name):
            is_function = node.id in functions
            if node.id.startswith("__") or (allowed_names is not None and node.id not in allowed_names and not is_function):
                raise ValueError(f"알 수 없거나 허용되지 않은 이름입니다: {node.id}")
        elif not isinstance(node, allowed_nodes):
            raise ValueError(f"허용되지 않은 수식 요소입니다: {type(node).__name__}")


def formula_names(formula: str, function_names: set[str] | None = None) -> set[str]:
    functions = function_names or BUILTIN_FUNCTIONS
    return {node.id for node in ast.walk(_parse(formula)) if isinstance(node, ast.Name) and node.id not in functions}


def formula_calls(formula: str) -> set[str]:
    return {node.func.id for node in ast.walk(_parse(formula)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def _period(value: Any, name: str = "period") -> int:
    if isinstance(value, bool) or int(value) != value or int(value) <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _bind_parameters(definition: dict[str, Any], args: list[Any]) -> dict[str, float]:
    parameters = definition.get("parameters", [])
    if len(args) > len(parameters):
        raise ValueError(f"{definition['key']} expects {len(parameters)} parameters")
    bound: dict[str, float] = {}
    for index, parameter in enumerate(parameters):
        value = args[index] if index < len(args) else parameter["default"]
        if parameter.get("integer", True):
            value = _period(value, parameter["name"])
        else:
            value = float(value)
        if parameter.get("min") is not None and value < parameter["min"]:
            raise ValueError(f"{parameter['name']} is below minimum")
        if parameter.get("max") is not None and value > parameter["max"]:
            raise ValueError(f"{parameter['name']} is above maximum")
        bound[parameter["name"]] = value
    return bound


def _evaluate(node: ast.AST, env: dict[str, Any], custom: dict[str, dict[str, Any]], base: dict[str, Any] | None = None, stack: frozenset[str] = frozenset()) -> Any:
    """`env`는 현재 수식이 볼 수 있는 이름, `base`는 사용자 지표 본문이 시작할 바탕 이름이다.
    호출한 지표의 파라미터가 호출된 지표로 새지 않게 둘을 분리한다. `stack`은 순환 참조를 막는다."""
    if base is None: base = env
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, env, custom, base, stack)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"알 수 없는 입력값입니다: {node.id}")
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left, env, custom, base, stack), _evaluate(node.right, env, custom, base, stack)
        return np.nan if left is None or right is None else _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _evaluate(node.operand, env, custom, base, stack)
        return operator.invert(value) if isinstance(value, pd.Series) else not value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand, env, custom, base, stack))
    if isinstance(node, ast.BoolOp):
        values = [_evaluate(value, env, custom, base, stack) for value in node.values]
        result = values[0]
        for value in values[1:]:
            result = operator.and_(result, value) if isinstance(node.op, ast.And) else operator.or_(result, value)
        return result
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, env, custom, base, stack)
        result: Any = True
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _evaluate(comparator, env, custom, base, stack)
            comparison = False if left is None or right is None else _COMPARE[type(op_node)](left, right)
            result = operator.and_(result, comparison)
            left = right
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        args = [_evaluate(arg, env, custom, base, stack) for arg in node.args]
        if name in custom:
            if name in stack:
                raise ValueError(f"사용자 지표가 순환 참조합니다: {' → '.join(sorted(stack))} → {name}")
            definition = custom[name]
            local_env = base | _bind_parameters(definition, args)
            validate_formula(definition["formula"], set(local_env), BUILTIN_FUNCTIONS | set(custom))
            return _evaluate(_parse(definition["formula"]), local_env, custom, base, stack | {name})
        functions = {
            "sma": lambda series, period: sma(series, _period(period)),
            "ema": lambda series, period: ema(series, _period(period)),
            "rsi": lambda series, period: rsi(series, _period(period)),
            "returns": lambda series, period: returns(series, _period(period)),
            "prior_avg_ratio": lambda series, period: prior_avg_ratio(series, _period(period)),
            "obv": lambda close, volume, period: obv(close, volume, _period(period)),
            "obv_ratio": lambda close, volume, period: obv_ratio(close, volume, _period(period)),
            "historical_volatility": lambda series, period: historical_volatility(series, _period(period)),
            "atr": lambda high, low, close, period: atr(high, low, close, _period(period)),
            "slope": lambda series, period: slope(series, _period(period)),
            "rolling_max": lambda series, period: series.rolling(_period(period), min_periods=_period(period)).max(),
            "rolling_min": lambda series, period: series.rolling(_period(period), min_periods=_period(period)).min(),
            "crosses_above": lambda fast, slow: crosses(fast, slow)[0],
            "crosses_below": lambda fast, slow: crosses(fast, slow)[1],
            "abs": abs,
        }
        return functions[name](*args)
    raise ValueError("지원하지 않는 수식입니다")


def _custom_map(custom: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """사용자 지표는 `_evaluate`에서 내장 함수보다 먼저 조회된다. 따라서 이름이 겹치면 내장을
    조용히 가로챈다. 저장 API가 막고 있지만 DB를 직접 고친 경우까지 포함해 여기서 끊는다."""
    mapping = {item["key"]: item for item in custom or []}
    shadowed = sorted(set(mapping) & (BUILTIN_FUNCTIONS | SCREEN_NAMES))
    if shadowed:
        raise ValueError(f"사용자 지표가 내장 이름을 덮어씁니다: {', '.join(shadowed)}")
    return mapping


def evaluate_formula(formula: str, env: dict[str, Any], custom: list[dict[str, Any]] | None = None) -> pd.Series:
    custom_map = _custom_map(custom)
    validate_formula(formula, set(env), BUILTIN_FUNCTIONS | set(custom_map))
    value = _evaluate(_parse(formula), env, custom_map)
    if isinstance(value, pd.Series):
        return value.replace([np.inf, -np.inf], np.nan)
    index = next((item.index for item in env.values() if isinstance(item, pd.Series)), pd.RangeIndex(1))
    return pd.Series(value, index=index, dtype="float64")


def _last(value: Any) -> Any:
    if isinstance(value, pd.Series):
        if value.empty:
            return None
        value = value.iloc[-1]
    if value is None or (isinstance(value, (float, np.floating)) and not np.isfinite(value)):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def custom_definitions(path=None, enabled_only: bool = True) -> list[dict[str, Any]]:
    where = " WHERE enabled=1" if enabled_only else ""
    with db_session(path) as db:
        rows = [dict(row) for row in db.execute(f"SELECT * FROM indicator_definitions{where} ORDER BY label,key").fetchall()]
    for row in rows:
        row["enabled"] = bool(row["enabled"])
        row["parameters"] = json.loads(row.get("parameters") or "[]")
    return rows


def truncate_price_jump(valid: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """감자·액면병합 등으로 미수정 종가가 하루 만에 KRX 일일 가격제한폭(±30%)을 넘게 뛰면
    그 이전 구간을 잘라낸다. `valid`는 이미 halted 봉이 제외된, date 오름차순 프레임이어야 한다.
    이평선 등 추세 지표는 이 결과만으로 계산해야 한다 — 안 그러면 장기 이평선이 단절 이전의
    가격 수준에 끌려가 정배열/역배열이 실제와 반대로 나올 수 있다."""
    if valid.empty:
        return valid, False
    close_ratio = valid["close"].astype(float) / valid["close"].astype(float).shift(1)
    breaks = close_ratio.index[(close_ratio > 1.3) | (close_ratio < 1 / 1.3)]
    price_jump_flag = len(breaks) > 0
    if price_jump_flag:
        valid = valid.iloc[breaks[-1]:].reset_index(drop=True)
    return valid, price_jump_flag


_WEIGHTED_RETURN_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


def _as_of_date(days: list[str], as_of_offset: int) -> str | None:
    """오프셋(거래일 수)을 실제 날짜로 바꾼다. 종목마다 결측·정지 봉 수가 달라 '행 개수'로 되감으면
    같은 오프셋이 종목마다 다른 날짜를 가리키고, 지표가 조용히 다른 날 값이 된다. 기준일은 항상
    시장 전체의 거래일로 정한다. 데이터 시작보다 더 거슬러 올라가면 빈 문자열을 돌려 결과를 비운다."""
    if as_of_offset <= 0: return None
    return days[-1 - as_of_offset] if as_of_offset < len(days) else ""


def weighted_return_score(frame: pd.DataFrame, as_of_date: str | None = None) -> float | None:
    """최근 3·6·9·12개월(거래일 63/126/189/252) 누적수익률의 가중평균(0.4/0.2/0.2/0.2, 최근
    분기에 가중). 퍼센트 단위로 반환한다(`change_pct`와 동일한 표기). 12개월치 유효 봉이 없으면
    계산할 수 없다."""
    frame = frame.sort_values("date")
    if as_of_date is not None:
        frame = frame[frame["date"] <= as_of_date]
    valid = frame[~frame["halted"].astype(bool)].copy().reset_index(drop=True)
    valid, _ = truncate_price_jump(valid)
    if len(valid) < _WEIGHTED_RETURN_WEIGHTS[-1][0] + 1:
        return None
    close = valid["close"].astype(float)
    score = 0.0
    for period, weight in _WEIGHTED_RETURN_WEIGHTS:
        change = close.iloc[-1] / close.iloc[-1 - period] - 1
        if pd.isna(change):
            return None
        score += weight * change
    return score * 100


def calculate_group(frame: pd.DataFrame, expression: str | None = None, sort_expression: str | None = None, extras: dict[str, Any] | None = None, custom: list[dict[str, Any]] | None = None, as_of_date: str | None = None) -> dict[str, Any]:
    frame = frame.sort_values("date")
    if as_of_date is not None:
        frame = frame[frame["date"] <= as_of_date]
    valid = frame[~frame["halted"].astype(bool)].copy().reset_index(drop=True)
    if valid.empty:
        return {}
    valid, price_jump_flag = truncate_price_jump(valid)
    env = {name: pd.Series(valid[name].to_numpy(dtype=float), index=valid["date"].tolist()) for name in SERIES_NAMES}
    env.update(extras or {})
    env["change_pct"] = returns(env["close"], 1) * 100
    env["bars_available"] = len(valid)
    env["halted"] = bool(frame.iloc[-1]["halted"])
    env["price_jump_flag"] = price_jump_flag
    latest = valid.iloc[-1]
    result = {name: _last(latest.get(name)) for name in SERIES_NAMES} | (extras or {})
    result.update({"bars_available": len(valid), "halted": bool(frame.iloc[-1]["halted"]), "as_of": str(latest["date"]), "change_pct": _last(env["change_pct"]), "price_jump_flag": price_jump_flag})
    if expression:
        result["_match"] = bool(_last(evaluate_formula(expression, env, custom)))
    if sort_expression:
        result["_sort"] = _last(evaluate_formula(sort_expression, env, custom))
    return result


def ticker_snapshot(ticker: str, path=None, as_of_offset: int = 0) -> dict[str, Any]:
    with db_session(path) as db:
        rows = db.execute(f"SELECT date,open,high,low,close,volume,value,halted FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date", (ticker,)).fetchall()
        days = [row[0] for row in db.execute(f"SELECT DISTINCT date FROM daily_bars WHERE source='{bar_source()}' ORDER BY date").fetchall()] if as_of_offset else []
    if not rows:
        return {}
    frame = pd.DataFrame([dict(row) for row in rows])
    as_of_date = _as_of_date(days, as_of_offset)
    return calculate_group(frame, extras={"weighted_return": weighted_return_score(frame, as_of_date)}, as_of_date=as_of_date)


def screen_context(spec: dict[str, Any], path=None) -> dict[str, Any]:
    """수식·유니버스를 검증하고 일봉·재무 스냅샷을 한 번만 읽어 둔다. 같은 스펙을 여러 기준일로
    반복 평가할 때(신호 로그 수집·프리셋 검증) 데이터 적재를 되풀이하지 않기 위한 분리다."""
    universe = spec.get("universe", {})
    kinds = universe.get("kinds") or ["stock", "etf"]
    markets = universe.get("markets") or []
    exchanges = set(active_market().exchanges)
    if any(kind not in {"stock", "etf"} for kind in kinds) or any(exchange not in exchanges for exchange in markets):
        raise ValueError("invalid universe")
    formula = str(spec.get("formula", "")).strip()
    sort_expression = str((spec.get("sort") or {}).get("formula", "close")).strip()
    if not formula or not sort_expression:
        raise ValueError("screen and sort formulas are required")
    if int(spec.get("as_of_offset", 0) or 0) < 0:
        raise ValueError("as_of_offset은 0 이상이어야 합니다")
    custom = custom_definitions(path)
    function_names = BUILTIN_FUNCTIONS | {item["key"] for item in custom}
    validate_formula(formula, SCREEN_NAMES, function_names)
    validate_formula(sort_expression, SCREEN_NAMES, function_names)
    # 유니버스는 현재 시장 모드로 닫아 둔다. 한국·미국 종목을 한 화면에 섞지 않는다.
    clauses = ["region=?", f"kind IN ({','.join('?' for _ in kinds)})"]
    params: list[Any] = [active_market().region, *kinds]
    if markets:
        clauses.append(f"market IN ({','.join('?' for _ in markets)})")
        params.extend(markets)
    if universe.get("exclude_preferred"):
        clauses.append("is_preferred=0")
    if universe.get("exclude_spac"):
        clauses.append("is_spac=0")
    with db_session(path) as db:
        instruments = [dict(row) for row in db.execute(f"SELECT * FROM instruments WHERE {' AND '.join(clauses)}", params).fetchall()]
        tickers = {row["ticker"] for row in instruments}
        bars = [dict(row) for row in db.execute(f"SELECT ticker,date,open,high,low,close,volume,value,halted FROM daily_bars WHERE source='{bar_source()}' ORDER BY ticker,date").fetchall() if row["ticker"] in tickers]
        fundamentals = {row["ticker"]: dict(row) for row in db.execute("SELECT f.* FROM snapshots_fundamental f JOIN (SELECT ticker,MAX(date) date FROM snapshots_fundamental GROUP BY ticker) x ON x.ticker=f.ticker AND x.date=f.date").fetchall()}
        days = [row[0] for row in db.execute(f"SELECT DISTINCT date FROM daily_bars WHERE source='{bar_source()}' ORDER BY date").fetchall()]
    return {
        "instruments": instruments, "days": days,
        "by_ticker": {ticker: group for ticker, group in pd.DataFrame(bars).groupby("ticker")} if bars else {},
        "fundamentals": fundamentals, "custom": custom, "formula": formula, "sort_expression": sort_expression,
        "reverse": str((spec.get("sort") or {}).get("dir", "desc")).lower() != "asc",
        "limit": max(1, min(int(spec.get("limit", 500)), 2000)),
        "exclude_halted": bool(universe.get("exclude_halted")), "min_bars": int(universe.get("min_bars", 0)),
    }


def evaluate_context(context: dict[str, Any], as_of_offset: int = 0) -> list[dict[str, Any]]:
    if as_of_offset < 0:
        raise ValueError("as_of_offset은 0 이상이어야 합니다")
    as_of_date = _as_of_date(context["days"], as_of_offset)
    output: list[dict[str, Any]] = []
    for instrument in context["instruments"]:
        group = context["by_ticker"].get(instrument["ticker"])
        if group is None:
            continue
        fundamental = context["fundamentals"].get(instrument["ticker"], {})
        extras = {key: fundamental.get(key) for key in ("market_cap", "shares", "per", "pbr", "eps", "bps", "div")} | {"weighted_return": weighted_return_score(group, as_of_date)}
        values = calculate_group(group, context["formula"], context["sort_expression"], extras, context["custom"], as_of_date)
        if context["exclude_halted"] and values.get("halted"):
            continue
        if values.get("bars_available", 0) < context["min_bars"] or not values.get("_match"):
            continue
        output.append(values | {key: instrument.get(key) for key in ("ticker", "name", "kind", "market")})
    output.sort(key=lambda row: (row.get("_sort") is not None, row.get("_sort") or -math.inf), reverse=context["reverse"])
    return output[: context["limit"]]


def run_screen(spec: dict[str, Any], path=None) -> list[dict[str, Any]]:
    return evaluate_context(screen_context(spec, path), int(spec.get("as_of_offset", 0) or 0))
