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
) -> None:
    """Ingest KRX daily snapshots; indicators are calculated on demand."""
    from .ingest import run_ingest
    try:
        run_ingest(days=days, force=force, source=source)
    except (RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


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


if __name__ == "__main__":
    app()
