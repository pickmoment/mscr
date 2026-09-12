from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import market
from .db import db_session
from .market import bar_source

@dataclass
class Position:
    ticker: str
    quantity: float = 0
    cost: float = 0
    avg_cost: float = 0
    realized: float = 0


def replay_trades(trades: list[dict[str, Any]], reject_oversell: bool = True) -> tuple[dict[str, Position], float]:
    positions: dict[str, Position] = {}
    realized_total = 0.0
    for trade in sorted(trades, key=lambda row: (row["trade_date"], row["id"])):
        ticker = trade["ticker"]; side = trade["side"]; quantity = float(trade["quantity"]); price = float(trade["price"]); fee = float(trade.get("fee", 0)); tax = float(trade.get("tax", 0))
        position = positions.setdefault(ticker, Position(ticker))
        if side == "buy":
            position.cost += quantity * price + fee + tax
            position.quantity += quantity
            position.avg_cost = position.cost / position.quantity
        elif side == "sell":
            if quantity > position.quantity + 1e-9:
                if reject_oversell: raise ValueError(f"매도 수량이 보유 수량을 초과합니다: {ticker}")
                continue
            gain = quantity * price - fee - tax - quantity * position.avg_cost
            position.realized += gain; realized_total += gain
            position.cost -= quantity * position.avg_cost; position.quantity -= quantity
            if position.quantity <= 1e-9: position.quantity = 0; position.cost = 0; position.avg_cost = 0
        else:
            raise ValueError("side must be buy or sell")
    return {ticker: p for ticker, p in positions.items() if p.quantity > 0}, realized_total


def validate_trade(trade: dict[str, Any], path=None) -> None:
    with db_session(path) as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM trades ORDER BY trade_date,id").fetchall()]
    replay_trades(rows + [{**trade, "id": 10**18}], reject_oversell=True)


def snapshot(path=None) -> dict[str, Any]:
    """현재 시장 모드의 보유 종목만 집계한다.

    `trades`에는 시장 구분 컬럼이 없어 종목코드 모양으로 나눈다(KRX는 6자리 숫자). 한 화면에
    원화·달러 평가금액이 섞이지 않게 하는 것이 목적이다."""
    mkt = market.active()
    with db_session(path) as db:
        trades = [dict(row) for row in db.execute("SELECT * FROM trades ORDER BY trade_date,id").fetchall()
                  if market.region_of(row["ticker"]) == mkt.region]
        positions, realized = replay_trades(trades)
        names = {row["ticker"]: row["name"] for row in db.execute("SELECT ticker,name FROM instruments WHERE region=?", (mkt.region,)).fetchall()}
        latest_rows = db.execute(f"""SELECT b.ticker,b.close,b.date,(SELECT p.close FROM daily_bars p WHERE p.ticker=b.ticker AND p.source='{bar_source()}' AND p.date<b.date ORDER BY p.date DESC LIMIT 1) previous_close FROM daily_bars b JOIN (SELECT ticker,MAX(date) date FROM daily_bars WHERE source='{bar_source()}' GROUP BY ticker) x ON x.ticker=b.ticker AND x.date=b.date WHERE b.source='{bar_source()}'""").fetchall()
        latest = {row["ticker"]: dict(row) for row in latest_rows}
        cash_row = db.execute("SELECT value FROM settings WHERE key=?", (mkt.cash_key,)).fetchone()
    output = []; total_market_value = total_cost = total_day_change = 0.0; stale_any = False
    for ticker, position in positions.items():
        bar = latest.get(ticker, {})
        last_close = bar.get("close")
        stale = not bool(last_close); stale_any |= stale
        market_value = position.quantity * last_close if last_close else position.cost
        unrealized = market_value - position.cost
        previous_close = bar.get("previous_close")
        change_pct = (last_close / previous_close - 1) * 100 if last_close and previous_close else 0
        day_change = position.quantity * last_close * (change_pct / 100) if last_close else 0
        total_market_value += market_value; total_cost += position.cost; total_day_change += day_change
        output.append({"ticker": ticker, "name": names.get(ticker, ticker), "quantity": position.quantity, "cost": position.cost, "avg_cost": position.avg_cost, "last_close": last_close, "market_value": market_value, "unrealized": unrealized, "unrealized_pct": unrealized / position.cost if position.cost else None, "day_change": day_change, "weight": 0, "stale": stale})
    for row in output: row["weight"] = row["market_value"] / total_market_value if total_market_value else 0
    total_unrealized = total_market_value - total_cost
    cash = float(cash_row[0]) if cash_row else 0.0


    return {"positions": output, "total_market_value": total_market_value, "total_cost": total_cost, "total_unrealized": total_unrealized, "total_unrealized_pct": total_unrealized / total_cost if total_cost else None, "total_realized": realized, "total_day_change": total_day_change, "cash": cash, "currency": mkt.currency, "total_assets": total_market_value + cash, "stale": stale_any}


RECONCILE_TOLERANCE = 1e-6


def reconcile(broker, path=None) -> dict[str, Any]:
    """로컬 trades 리플레이 포지션을 브로커의 실제 잔고 조회 결과와 대조한다.

    앱을 거치지 않은 수동 주문, `mscr trade sync`를 깜빡한 체결, DB 유실 등으로 로컬 상태가
    실제 계좌와 어긋나도 지금까지는 감지할 방법이 없었다 — `KISBroker.balance()`는 구현돼
    있었지만 어디서도 호출되지 않았다. 여기서 그 값을 실제로 대조에 쓴다.

    현금은 대조하지 않는다: 현금 잔고는 사용자가 화면에서 직접 입력하는 값이라(증거금·예수금
    정산 시점이 다를 수 있음) 브로커 현금과 다른 게 정상일 수 있다 — 오류로 취급하지 않고
    양쪽 값을 그대로 보여주기만 한다.
    """
    local = snapshot(path)
    remote = broker.balance()
    remote_by_ticker = {row["ticker"]: row for row in remote["positions"]}
    local_by_ticker = {row["ticker"]: row for row in local["positions"]}
    tickers = sorted(set(local_by_ticker) | set(remote_by_ticker))
    with db_session(path) as db:
        names = {row["ticker"]: row["name"] for row in db.execute(
            f"SELECT ticker,name FROM instruments WHERE ticker IN ({','.join('?' * len(tickers))})", tickers).fetchall()} if tickers else {}
    rows = []
    for ticker in tickers:
        local_row, remote_row = local_by_ticker.get(ticker), remote_by_ticker.get(ticker)
        local_qty = local_row["quantity"] if local_row else 0.0
        broker_qty = remote_row["quantity"] if remote_row else 0.0
        rows.append({
            "ticker": ticker, "name": names.get(ticker, ticker),
            "local_quantity": local_qty, "broker_quantity": broker_qty, "quantity_diff": local_qty - broker_qty,
            "local_avg_cost": local_row["avg_cost"] if local_row else None,
            "broker_avg_cost": remote_row["avg_cost"] if remote_row else None,
            "matched": abs(local_qty - broker_qty) <= RECONCILE_TOLERANCE,
        })
    mismatched = [row for row in rows if not row["matched"]]
    return {
        "env": broker.env, "account_masked": broker.account_masked,
        "positions": rows, "mismatched": len(mismatched),
        "local_cash_krw": local["cash"], "broker_cash_krw": remote["cash_krw"],
    }
