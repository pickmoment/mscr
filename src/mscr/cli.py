from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

import typer

from .config import DB_PATH, MSCR_HOME, SCHEMA_VERSION, request_delay, request_delay_source
from .credentials import load as load_settings
from .credentials import save as save_settings
from .db import db_session, init_db
from .providers.krx import clear_krx_credentials, krx_status, save_krx_credentials

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def doctor() -> None:
    """Show local database and ingestion health."""
    init_db()
    with db_session() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        as_of = db.execute("SELECT MAX(date) FROM daily_bars WHERE source='krx_snapshot'").fetchone()[0]
        counts = db.execute("SELECT kind, COUNT(*) AS n FROM instruments GROUP BY kind").fetchall()
        bars = db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        indicators = db.execute("SELECT COUNT(*) FROM indicator_definitions WHERE enabled=1").fetchone()[0]
        failures = db.execute("SELECT date, kind, error FROM ingest_runs WHERE status='failed' ORDER BY date DESC LIMIT 10").fetchall()
    count_map = {row["kind"]: row["n"] for row in counts}
    typer.echo(f"db: {DB_PATH}")
    typer.echo(f"schema_version: {version} (expected {SCHEMA_VERSION})")
    typer.echo(f"as_of: {as_of or '-'}")
    typer.echo(f"instruments: stock={count_map.get('stock', 0)} etf={count_map.get('etf', 0)}")
    typer.echo(f"daily_bars: {bars}")
    typer.echo(f"indicators: dynamic ({indicators} custom enabled)")
    try:
        from .broker.kis import broker_status
        from .trading import list_orders, list_plans
        broker = broker_status()
        typer.echo(f"trading: broker={'enabled' if broker.get('enabled') else 'disabled'} env={broker.get('env') or '-'} plans={len(list_plans())} orders={len(list_orders(limit=1000))}")
    except Exception as exc:
        typer.echo(f"trading: unavailable ({exc})")
    krx = krx_status()
    typer.echo(f"KRX credentials: {krx['mode']} (source={krx['source'] or '-'}) delay={request_delay():g}s")
    typer.echo("failed_ingest_runs:")
    if failures:
        for row in failures:
            typer.echo(f"  {row['date']} {row['kind']}: {row['error'] or 'unknown error'}")
    else:
        typer.echo("  none")


@app.command()
def ingest(
    days: int = typer.Option(400),
    force: bool = typer.Option(False),
    source: str = typer.Option("krx", help="krx(기본), fdr(전종목 스냅샷 대체, 주식만 지원) 또는 alphasquare(로컬 유니버스 종목별 개별 조회, 최후 폴백, --days 그대로 적용)"),
    capture: bool = typer.Option(True, help="수집 후 저장된 프리셋의 최신 거래일 신호를 로그에 남깁니다."),
) -> None:
    """Ingest KRX daily snapshots; indicators are calculated on demand."""
    from .ingest import run_ingest
    from .signals import capture as capture_signals
    try:
        run_ingest(days=days, force=force, source=source)
    except (RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if capture:
        result = capture_signals(offsets=[0])
        typer.echo(f"signals: {result['dates']}일 수집, 신호 {result['rows']}건 (건너뜀 {result['skipped']})")


@app.command("krx-latest")
def krx_latest_day() -> None:
    """Query KRX's most recently published trading day without running a full ingest."""
    from .ingest import latest_trading_day
    try:
        typer.echo(latest_trading_day())
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


config_app = typer.Typer(add_completion=False, no_args_is_help=True, help="KRX and shared settings stored in ~/.mscr.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show resolved KRX credentials and request delay."""
    krx = krx_status()
    typer.echo(f"home: {MSCR_HOME}")
    typer.echo(f"krx: mode={krx['mode']} source={krx['source'] or '-'} key={krx['openapi_key_masked'] or '-'} id={krx['krx_id_masked'] or '-'} stored={','.join(krx['stored']) or '-'}")
    typer.echo(f"delay: {request_delay():g}s (source={request_delay_source()})")
    from .broker.kis import broker_status
    broker = broker_status()
    typer.echo(f"kis: {'enabled' if broker['enabled'] else 'disabled'} env={broker['env'] or '-'} account={broker['account_masked'] or '-'} source={broker['source'] or '-'}")


@config_app.command("krx")
def config_krx(
    openapi_key: str = typer.Option(None, "--openapi-key", help="KRX Open API AUTH_KEY; pass '' to remove."),
    krx_id: str = typer.Option(None, "--id", help="Legacy KRX web id; pass '' to remove."),
    krx_pw: str = typer.Option(None, "--pw", help="Legacy KRX web password; pass '' to remove."),
) -> None:
    """Store KRX ingest credentials in ~/.mscr (0600)."""
    values = {key: value for key, value in {"openapi_key": openapi_key, "krx_id": krx_id, "krx_pw": krx_pw}.items() if value is not None}
    if not values:
        typer.echo("저장할 값이 없습니다. --openapi-key 또는 --id/--pw 를 지정하세요.", err=True)
        raise typer.Exit(code=1)
    status = save_krx_credentials(**values)
    typer.echo(f"saved: mode={status['mode']} source={status['source'] or '-'} stored={','.join(status['stored']) or '-'}")


@config_app.command("krx-clear")
def config_krx_clear() -> None:
    """Delete stored KRX credentials."""
    status = clear_krx_credentials()
    typer.echo(f"cleared: mode={status['mode']}")


@config_app.command("delay")
def config_delay(seconds: float = typer.Argument(..., min=0, max=10)) -> None:
    """Store the request delay used by ingest."""
    save_settings("settings", load_settings("settings") | {"request_delay_sec": seconds})
    typer.echo(f"delay: {request_delay():g}s (source={request_delay_source()})")

@app.command()
def serve(
    port: int = typer.Option(8765),
    no_open: bool = typer.Option(False, "--no-open"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the local web application."""
    import uvicorn

    if not no_open:
        webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run("mscr.api.app:app", host="127.0.0.1", port=port, reload=reload)

trade_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Trading plans, KIS orders and fill sync.")
app.add_typer(trade_app, name="trade")


def _live_broker():
    from .broker.kis import broker_from_config
    broker = broker_from_config()
    if broker is None:
        typer.echo("브로커가 설정되지 않았습니다. 'mscr trade login' 또는 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT 환경변수를 사용하세요.", err=True)
        raise typer.Exit(code=1)
    return broker


@trade_app.command("status")
def trade_status() -> None:
    """Show KIS broker credential status for both paper and real accounts."""
    from .broker.kis import broker_status
    broker = broker_status()
    typer.echo(f"broker: {'enabled' if broker.get('enabled') else 'disabled'} active_env={broker.get('active_env') or '-'} env={broker.get('env') or '-'} account={broker.get('account_masked') or '-'} source={broker.get('source') or '-'}")
    accounts = broker.get("accounts") or {}
    typer.echo(f"paper: {accounts.get('paper') or '미설정'}")
    typer.echo(f"real: {accounts.get('real') or '미설정'}")
    if broker.get("reason"):
        typer.echo(f"reason: {broker['reason']}")


@trade_app.command("login")
def trade_login(
    app_key: str = typer.Option(None, "--app-key", help="KIS APP KEY."),
    app_secret: str = typer.Option(None, "--app-secret", help="KIS APP SECRET."),
    account: str = typer.Option(None, "--account", help="Account number as 12345678 (paper) or 12345678-01 (real)."),
    env: str = typer.Option("paper", "--env", help="paper or real."),
) -> None:
    """Store KIS credentials in ~/.mscr (0600) instead of environment variables."""
    from .broker.kis import KISError, save_credentials
    app_key = app_key or typer.prompt("APP KEY", hide_input=True)
    app_secret = app_secret or typer.prompt("APP SECRET", hide_input=True)
    account = account or typer.prompt("계좌번호 (12345678 또는 12345678-01)")
    try:
        status = save_credentials(app_key, app_secret, account, env)
    except KISError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"saved: env={status.get('env')} account={status.get('account_masked')} source={status.get('source')}")


@trade_app.command("logout")
def trade_logout(env: str = typer.Option("paper", "--env", help="paper or real.")) -> None:
    """Delete stored KIS credentials and cached tokens for one environment."""
    from .broker.kis import KISError, clear_credentials
    try:
        status = clear_credentials(env)
    except KISError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"cleared: env={env} broker={'enabled' if status.get('enabled') else 'disabled'} source={status.get('source') or '-'}")


@trade_app.command("plans")
def trade_plans() -> None:
    """List trading plans with their current trigger state."""
    init_db()
    from .trading import evaluate_plans, list_plans
    plans = list_plans()
    if not plans:
        typer.echo("plans: none")
        return
    states = {item["plan_id"]: item for item in evaluate_plans()}
    typer.echo(f"plans: {len(plans)}")
    for plan in plans:
        state = states.get(plan["id"], {})
        price = f"@{plan['limit_price']:g}" if plan.get("limit_price") is not None else "@market"
        close = state.get("close")
        mark = "off" if not plan.get("enabled") else ("trigger" if state.get("triggered") else "wait")
        typer.echo(f"  [{plan['id']}] {plan['name']} {plan['ticker']} {plan['side']} {plan['quantity']:g}{price} {mark} phase={state.get('phase') or '-'} next={state.get('next_leg') or '-'} as_of={state.get('as_of') or '-'} close={f'{close:g}' if close else '-'} {state.get('reason') or ''}".rstrip())
        typer.echo(f"      entry={plan['entry_price']:g} stop={plan['stop_price']:g} tp1={plan['tp1_price']:g}({plan['tp1_ratio']:.0%}) tp2={plan['tp2_price']:g}({plan['tp2_ratio']:.0%}) trailing={plan['tp3_trailing_pct']:g}%")


@trade_app.command("run")
def trade_run(live: bool = typer.Option(False, "--live", help="Send real orders instead of a dry run."), plan: list[int] = typer.Option(None, "--plan", help="Limit to plan ids.")) -> None:
    """Evaluate plans and record orders (dry run unless --live)."""
    init_db()
    from .trading import run_plans
    broker = None
    if live:
        broker = _live_broker()
        if not typer.confirm(f"실주문을 전송합니다 (env={broker.env}, account={broker.account_masked}). 계속할까요?"):
            raise typer.Exit(code=1)
    try:
        orders = run_plans(broker=broker, dry_run=not live, plan_ids=list(plan) if plan else None)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"orders: {len(orders)} ({'live' if live else 'dry-run'})")
    for order in orders:
        typer.echo(f"  #{order.get('id')} {order['ticker']} {order['side']} {order['quantity']:g} {order['order_type']} status={order['status']} broker_order_id={order.get('broker_order_id') or '-'} {order.get('message') or ''}".rstrip())


@trade_app.command("simulate")
def trade_simulate(
    plan_id: int = typer.Argument(..., help="Plan id (see `mscr trade plans`)."),
    start: str = typer.Option(..., "--from", help="Start date (YYYY-MM-DD)."),
    end: str = typer.Option(..., "--to", help="End date (YYYY-MM-DD)."),
) -> None:
    """Replay a plan's entry/stop/tp1/tp2/trailing rules over historical daily bars. Nothing is recorded."""
    init_db()
    from .trading import simulate_plan
    try:
        result = simulate_plan(plan_id, start, end)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{result['name']} {result['ticker']} {result['side']} · {result['start']}~{result['end']} ({result['bars']}일) · phase={result['phase']}")
    for leg in result["legs"]:
        typer.echo(f"  {leg['leg']:<8} {leg['date']} {leg['price']:g} x{leg['quantity']:g}")
    typer.echo(f"realized={_r(result['realized_r'])} open={_r(result['open_r'])} total={_r(result['total_r'])}")
    for warning in result["warnings"]:
        typer.echo(f"  ! {warning}")


@trade_app.command("sync")
def trade_sync() -> None:
    """Poll broker fills and record filled orders as trades."""
    init_db()
    from .trading import sync_orders
    broker = _live_broker()
    try:
        orders = sync_orders(broker=broker)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"synced: {len(orders)}")
    for order in orders:
        typer.echo(f"  #{order.get('id')} {order['ticker']} {order['side']} status={order['status']} filled={order.get('filled_quantity') or 0:g}@{order.get('filled_price') or 0:g} fee={order.get('fee') or 0:g} tax={order.get('tax') or 0:g} trade_id={order.get('trade_id') or '-'}")


@trade_app.command("reconcile")
def trade_reconcile() -> None:
    """Compare local replayed positions against the broker's real account balance."""
    init_db()
    from .portfolio import reconcile
    broker = _live_broker()
    try:
        result = reconcile(broker)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"env={result['env']} account={result['account_masked']} mismatched={result['mismatched']}/{len(result['positions'])}")
    for row in result["positions"]:
        mark = "OK" if row["matched"] else "!!"
        typer.echo(f"  {mark} {row['ticker']} {row['name']} local={row['local_quantity']:g} broker={row['broker_quantity']:g} diff={row['quantity_diff']:+g}")
    typer.echo(f"cash: local={result['local_cash_krw']:,.0f}원 broker={result['broker_cash_krw']:,.0f}원 (사용자 입력값이라 다를 수 있습니다)")


@app.command()
def brief(date: str = typer.Option(None, "--date", help="기준 거래일 (기본: 최신 수집일)")) -> None:
    """Print the end-of-day briefing: preset signal changes, plan triggers, watchlist targets, position risk."""
    from .brief import build, render
    typer.echo(render(build(date)))


signals_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Saved-preset signal log used by the briefing, preset diff and backtest.")
app.add_typer(signals_app, name="signals")


@signals_app.command("capture")
def signals_capture(
    days: int = typer.Option(1, "--days", min=1, max=1000, help="최신 거래일부터 거슬러 올라갈 거래일 수."),
    force: bool = typer.Option(False, "--force", help="이미 수집한 날짜도 다시 계산합니다."),
    screen: list[int] = typer.Option(None, "--screen", help="프리셋 id로 제한합니다."),
) -> None:
    """Run saved presets over past trading days and store which tickers matched."""
    from .signals import capture
    result = capture(screen_ids=list(screen) or None, offsets=range(days), force=force,
                     on_progress=lambda done, total, label: typer.echo(f"  [{done}/{total}] {label}"))
    typer.echo(f"captured: 프리셋 {result['screens']}개 · {result['dates']}일 · 신호 {result['rows']}건 (건너뜀 {result['skipped']})")


@signals_app.command("coverage")
def signals_coverage() -> None:
    """Show how many trading days of signal log each preset has."""
    from .signals import coverage, trading_days
    days = len(trading_days())
    typer.echo(f"trading days in db: {days}")
    for row in coverage():
        typer.echo(f"  #{row['id']} {row['name']}: {row['days']}일 ({row['first_date'] or '-'} ~ {row['last_date'] or '-'}) 신호 {row['signals']}건")


@app.command("risk")
def risk_command(
    per_trade: float = typer.Option(None, "--per-trade", help="1건 리스크 한도(총자산 대비 %)."),
    max_heat: float = typer.Option(None, "--max-heat", help="포트폴리오 히트 한도(총자산 대비 %)."),
) -> None:
    """Show portfolio heat: how much of total assets every open and pending plan can lose."""
    from .risk import heat, save_limits
    if per_trade is not None or max_heat is not None:
        current = heat()["limits"]
        try:
            save_limits(per_trade if per_trade is not None else current["risk_per_trade_pct"],
                        max_heat if max_heat is not None else current["max_portfolio_heat_pct"])
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    state = heat()
    limits = state["limits"]
    typer.echo(f"equity: {state['equity']:,.0f}원 (현금 {state['cash_krw']:,.0f} + 평가액 {state['market_value']:,.0f})")
    heat_pct = "-" if state["heat_pct"] is None else f"{state['heat_pct']:.2f}%"
    typer.echo(f"heat: {heat_pct} / 한도 {limits['max_portfolio_heat_pct']:g}% · 위험 {state['total_risk_krw']:,.0f}원 (진행 {state['open_risk_krw']:,.0f} + 대기 {state['pending_risk_krw']:,.0f})")
    typer.echo(f"budget: 1건 한도 {state['budget']['per_trade_krw']:,.0f}원 · 남은 여유 {state['budget']['remaining_krw']:,.0f}원")
    for row in state["plans"]:
        if row["state"] in ("closed", "disabled"): continue
        typer.echo(f"  {row['name']} {row['ticker']} {row['state']} {row['quantity']:g}주 위험 {row['risk_krw']:,.0f}원")
    for row in state["unprotected"]:
        typer.echo(f"  [손절 없음] {row['ticker']} {row['name']} 평가액 {row['market_value']:,.0f}원")
    for message in state["warnings"]:
        typer.echo(f"  ! {message}")


def _r(value) -> str:
    return "—" if value is None else f"{value:+.2f}R"


def _percent(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


@app.command("review")
def review_command(limit: int = typer.Option(20, "--limit", min=1, max=200)) -> None:
    """Settle finished plans in R and show setup-level statistics."""
    from .review import plan_results, stats
    summary = stats()
    typer.echo(f"closed: {summary['trades']}건 · 승률 {_percent(summary['win_rate'])} · 기대 {_r(summary['expectancy_r'])} · 합계 {_r(summary['total_r'])} · 진행 {summary['open']['count']}건")
    for row in summary["by_setup"]:
        typer.echo(f"  [{row['setup']}] {row['trades']}건 승률 {_percent(row['win_rate'])} 평균 {_r(row['avg_r'])} 합계 {_r(row['total_r'])}")
    for row in plan_results()[:limit]:
        typer.echo(f"  {row['entry_date'] or '-'} {row['ticker']} {row['ticker_name']} {row['status']} 실현 {_r(row['realized_r'])} 미실현 {_r(row['open_r'])} MAE {_r(row['mae_r'])} MFE {_r(row['mfe_r'])}")
    for message in summary["warnings"]:
        typer.echo(f"  ! {message}")


@app.command("backtest")
def backtest_command(
    screen_id: int = typer.Argument(..., help="프리셋 id (mscr signals coverage로 확인)."),
    entry: str = typer.Option("next_open", "--entry", help="next_open 또는 breakout."),
    stop_mode: str = typer.Option("atr", "--stop-mode", help="atr 또는 box."),
    atr_multiple: float = typer.Option(2.0, "--atr-multiple"),
    target_r: float = typer.Option(3.0, "--target-r"),
    horizon: int = typer.Option(60, "--horizon"),
    top_n: int = typer.Option(5, "--top-n"),
) -> None:
    """Replay a preset's signal log under an explicit exit protocol."""
    from .backtest import run
    try:
        result = run(screen_id, {"entry": entry, "stop_mode": stop_mode, "atr_multiple": atr_multiple, "target_r": target_r, "horizon_days": horizon, "top_n": top_n})
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{result['name']}: 신호 {result['signals']}건 → 거래 {result['trades']}건 ({result['period']['start']} ~ {result['period']['end']})")
    typer.echo(f"기대값 {result['expectancy_r']}R ±{result['stderr_r']} · 목표도달 {result['target_rate']}% · 손절 {result['stop_rate']}% · 흑자월 {result['profitable_months']}/{result['total_months']}")
    for row in result["by_half"]:
        typer.echo(f"  {row['label']}: {row['trades']}건 {row['expectancy_r']}R")
    for message in result["warnings"]:
        typer.echo(f"  ! {message}")


if __name__ == "__main__":
    app()
