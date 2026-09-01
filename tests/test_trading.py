from __future__ import annotations

import json

import pytest

from mscr.broker.kis import Fill, KISBroker, KISError, OrderResult, broker_from_config, broker_status, clear_credentials, save_credentials, set_active_env
from mscr.db import db_session, init_db
from mscr.portfolio import snapshot
from mscr.trading import _leg_quantities, delete_plan, evaluate_plans, list_orders, list_plans, run_plans, save_plan, sync_orders


class StubBroker:
    env = "paper"
    account_masked = "1234****-01"

    def __init__(self, status: str = "submitted"):
        self.status = status
        self.submitted: list[tuple] = []
        self.fill = Fill(10, 1300.0, 190.0, 0.0, "filled", {"rt_cd": "0"})

    def submit_order(self, ticker, side, quantity, order_type, limit_price):
        self.submitted.append((ticker, side, quantity, order_type, limit_price))
        if self.status == "raise": raise RuntimeError("네트워크 오류")
        return OrderResult("KRX00001" if self.status == "submitted" else None, self.status, "정상 접수" if self.status == "submitted" else "잔고 부족", {"rt_cd": "0" if self.status == "submitted" else "1"})

    def order_fill(self, broker_order_id, order_date=None):
        return self.fill


class SequentialFillBroker:
    """Each submitted order gets a unique id and syncs back the exact quantity/price submitted for it."""

    env = "paper"
    account_masked = "1234****-01"

    def __init__(self):
        self.submitted: list[tuple] = []
        self._next_id = 1
        self._orders: dict[str, tuple[float, float]] = {}

    def submit_order(self, ticker, side, quantity, order_type, limit_price):
        self.submitted.append((ticker, side, quantity, order_type, limit_price))
        order_id = f"SEQ{self._next_id:05d}"
        self._next_id += 1
        self._orders[order_id] = (quantity, limit_price or 1000.0)
        return OrderResult(order_id, "submitted", "정상 접수", {"rt_cd": "0"})

    def order_fill(self, broker_order_id, order_date=None):
        quantity, price = self._orders[broker_order_id]
        return Fill(quantity, price, 0.0, 0.0, "filled", {"rt_cd": "0"})


def insert_bar(db, ticker, day, o, h, l, c, volume=1000, value=1_000_000):
    db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (ticker, day, "krx_snapshot", o, h, l, c, volume, value, None, 0))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "trade.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('005930','삼성전자','stock','KOSPI',0,0,'2026-01-01','2026-08-31',0)")
        insert_bar(db, "005930", "2026-06-01", 1000, 1010, 990, 1000)
    return path


def plan_payload(**overrides):
    payload = {
        "name": "돌파매수", "ticker": "005930", "side": "buy", "quantity": 10,
        "order_type": "limit", "limit_price": 1300,
        "entry_price": 1005, "stop_price": 950,
        "tp1_price": 1100, "tp1_ratio": 0.4,
        "tp2_price": 1200, "tp2_ratio": 0.3,
        "tp3_trailing_pct": 5, "enabled": True,
    }
    return payload | overrides


def test_save_plan_validates_breakout_fields_and_duplicate_name(store):
    save_plan(plan_payload(), store)
    with pytest.raises(ValueError, match="같은 이름의 계획이 있습니다"):
        save_plan(plan_payload(), store)
    with pytest.raises(ValueError, match="등록되지 않은 종목코드"):
        save_plan(plan_payload(name="미등록", ticker="000001"), store)
    with pytest.raises(ValueError, match="주문 가격"):
        save_plan(plan_payload(name="가격없음", limit_price=None), store)
    with pytest.raises(ValueError, match="손절가가 진입가보다 낮아야"):
        save_plan(plan_payload(name="손절오류", stop_price=1100), store)
    with pytest.raises(ValueError, match="진입가 < 1차 익절가 < 2차 익절가"):
        save_plan(plan_payload(name="목표가오류", tp1_price=900), store)
    with pytest.raises(ValueError, match="비율의 합은 1보다 작아야"):
        save_plan(plan_payload(name="비율오류", tp1_ratio=0.6, tp2_ratio=0.5), store)
    assert [plan["name"] for plan in list_plans(store)] == ["돌파매수"]


def test_evaluate_waiting_entry_then_triggers_on_breakout(store):
    save_plan(plan_payload(entry_price=1050, stop_price=1000, tp1_price=1150, tp2_price=1250), store)
    waiting = evaluate_plans(path=store)[0]
    assert (waiting["phase"], waiting["next_leg"], waiting["triggered"]) == ("waiting_entry", "entry", False)

    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-02", 1040, 1060, 1030, 1055)
    triggered = evaluate_plans(path=store)[0]
    assert (triggered["phase"], triggered["next_leg"], triggered["triggered"]) == ("waiting_entry", "entry", True)
    assert (triggered["order_side"], triggered["order_quantity"]) == ("buy", 10.0)


def test_dry_run_preview_does_not_persist_or_block_live_entry(store):
    save_plan(plan_payload(), store)
    broker = StubBroker()
    preview = run_plans(broker=broker, dry_run=True, path=store)
    assert [(order["status"], order["leg"], order["id"]) for order in preview] == [("dry_run", "entry", None)]
    assert broker.submitted == []
    assert list_orders(path=store) == []
    assert evaluate_plans(path=store)[0]["next_leg"] == "entry"

    live = run_plans(broker=broker, dry_run=False, path=store)
    assert (live[0]["status"], live[0]["leg"]) == ("submitted", "entry")
    assert broker.submitted == [("005930", "buy", 10.0, "limit", 1300.0)]
    assert len(list_orders(path=store)) == 1


def test_full_breakout_lifecycle_via_live_orders(store):
    save_plan(plan_payload(entry_price=1050, stop_price=980, tp1_price=1150, tp1_ratio=0.4, tp2_price=1250, tp2_ratio=0.3, tp3_trailing_pct=5), store)
    broker = SequentialFillBroker()

    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-02", 1040, 1060, 1030, 1055)  # breaks out above 1050
    entry_orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (entry_orders[0]["leg"], entry_orders[0]["quantity"], entry_orders[0]["side"]) == ("entry", 10.0, "buy")
    sync_orders(broker=broker, path=store)
    holding = evaluate_plans(path=store)[0]
    assert (holding["phase"], holding["next_leg"], holding["triggered"]) == ("holding", "tp1", False)

    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-03", 1100, 1160, 1090, 1150)  # breaks tp1 @1150
    tp1_orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (tp1_orders[0]["leg"], tp1_orders[0]["quantity"], tp1_orders[0]["side"]) == ("tp1", 4.0, "sell")
    sync_orders(broker=broker, path=store)
    tp1_done = evaluate_plans(path=store)[0]
    assert (tp1_done["phase"], tp1_done["next_leg"], tp1_done["triggered"]) == ("tp1_done", "tp2", False)

    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-04", 1200, 1260, 1240, 1250)  # breaks tp2 @1250, stays above trailing level
    tp2_orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (tp2_orders[0]["leg"], tp2_orders[0]["quantity"], tp2_orders[0]["side"]) == ("tp2", 3.0, "sell")
    sync_orders(broker=broker, path=store)
    trailing_watch = evaluate_plans(path=store)[0]
    assert (trailing_watch["phase"], trailing_watch["next_leg"], trailing_watch["triggered"]) == ("trailing", "trailing", False)

    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-05", 1230, 1245, 1150, 1160)  # pulls back through the trailing stop (peak 1260 * 0.95 = 1197)
    trailing_orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (trailing_orders[0]["leg"], trailing_orders[0]["quantity"], trailing_orders[0]["side"]) == ("trailing", 3.0, "sell")
    sync_orders(broker=broker, path=store)
    closed = evaluate_plans(path=store)[0]
    assert closed["phase"] == "closed"

    orders = list_orders(path=store)
    assert sorted(order["leg"] for order in orders) == ["entry", "tp1", "tp2", "trailing"]
    assert sum(order["quantity"] for order in orders if order["leg"] != "entry") == pytest.approx(10.0)
    assert snapshot(store)["positions"] == []


def test_stop_loss_liquidates_remaining_quantity_after_partial_take_profit(store):
    save_plan(plan_payload(entry_price=1050, stop_price=980, tp1_price=1150, tp1_ratio=0.4, tp2_price=1250, tp2_ratio=0.3), store)
    broker = SequentialFillBroker()
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-02", 1040, 1060, 1030, 1055)
    run_plans(broker=broker, dry_run=False, path=store)  # entry
    sync_orders(broker=broker, path=store)
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-03", 1100, 1160, 1090, 1150)
    run_plans(broker=broker, dry_run=False, path=store)  # tp1 fills 4 shares
    sync_orders(broker=broker, path=store)
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-04", 1000, 1010, 960, 970)  # crashes through stop @980
    evaluation = evaluate_plans(path=store)[0]
    assert (evaluation["next_leg"], evaluation["triggered"]) == ("stop", True)
    assert evaluation["order_quantity"] == pytest.approx(6.0)  # 10 - 4 already sold at tp1
    orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (orders[0]["leg"], orders[0]["quantity"], orders[0]["side"]) == ("stop", 6.0, "sell")
    sync_orders(broker=broker, path=store)
    assert evaluate_plans(path=store)[0]["phase"] == "closed"
    assert snapshot(store)["positions"] == []


def test_live_entry_fill_creates_trade_and_updates_portfolio(store):
    save_plan(plan_payload(), store)
    broker = StubBroker()
    orders = run_plans(broker=broker, dry_run=False, path=store)
    assert broker.submitted == [("005930", "buy", 10.0, "limit", 1300.0)]
    assert (orders[0]["status"], orders[0]["broker_order_id"], orders[0]["leg"]) == ("submitted", "KRX00001", "entry")

    synced = sync_orders(broker=broker, path=store)
    assert [(order["status"], order["filled_quantity"], order["filled_price"]) for order in synced] == [("filled", 10.0, 1300.0)]
    assert synced[0]["trade_id"]

    positions = snapshot(store)["positions"]
    assert [(row["ticker"], row["quantity"]) for row in positions] == [("005930", 10.0)]
    assert positions[0]["cost"] == pytest.approx(10 * 1300 + 190)

    assert sync_orders(broker=broker, path=store) == []
    with db_session(store) as db:
        assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    holding = evaluate_plans(path=store)[0]
    assert (holding["phase"], holding["next_leg"]) == ("holding", "tp1")


def test_exit_leg_skipped_when_live_holdings_not_yet_confirmed(store):
    save_plan(plan_payload(), store)
    broker = StubBroker()  # default status stays "submitted"; never synced/filled
    run_plans(broker=broker, dry_run=False, path=store)
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-02", 1090, 1110, 1080, 1105)  # breaks tp1 @1100
    orders = run_plans(broker=broker, dry_run=False, path=store)
    assert orders[0]["status"] == "skipped"
    assert "보유 수량 부족" in orders[0]["message"]


def test_stale_bars_block_orders(store):
    save_plan(plan_payload(name="지연종목"), store)
    with db_session(store) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000660','SK하이닉스','stock','KOSPI',0,0,'2026-01-01','2026-08-31',0)")
        insert_bar(db, "000660", "2026-09-30", 1, 1, 1, 1)
    orders = run_plans(broker=StubBroker(), dry_run=False, path=store)
    assert orders[0]["status"] == "skipped"
    assert "일봉이 오래되었습니다" in orders[0]["message"]


def test_broker_failure_and_rejection_stay_retryable(store):
    save_plan(plan_payload(), store)
    rejected = run_plans(broker=StubBroker("rejected"), dry_run=False, path=store)
    assert (rejected[0]["status"], rejected[0]["message"]) == ("rejected", "잔고 부족")
    failed = run_plans(broker=StubBroker("raise"), dry_run=False, path=store)
    assert failed[0]["status"] == "failed" and "RuntimeError" in failed[0]["message"]
    still_waiting = evaluate_plans(path=store)[0]
    assert (still_waiting["phase"], still_waiting["next_leg"]) == ("waiting_entry", "entry")
    assert [order["status"] for order in run_plans(dry_run=True, path=store)] == ["dry_run"]


def test_deleting_plan_keeps_order_history(store):
    plan_id = save_plan(plan_payload(), store)
    run_plans(broker=StubBroker(), dry_run=False, path=store)
    delete_plan(plan_id, store)
    orders = list_orders(path=store)
    assert len(orders) == 1 and orders[0]["plan_id"] is None


def test_run_without_broker_requires_dry_run(store):
    save_plan(plan_payload(), store)
    with pytest.raises(ValueError, match="브로커가 설정되지 않았습니다"):
        run_plans(dry_run=False, path=store)


def test_broker_status_and_stored_credentials(monkeypatch, tmp_path):
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT", "KIS_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    assert broker_status() == {"enabled": False, "env": None, "account_masked": None, "source": None, "reason": "브로커 자격증명이 없습니다. 화면에서 저장하거나 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT 환경변수를 설정하세요.", "active_env": "paper", "accounts": {"paper": None, "real": None}}
    assert broker_from_config() is None

    (tmp_path / "kis_token_paper.json").write_text("{}")
    saved = save_credentials("PSappkey0000000000", "SECappsecret0000000000", "12345678-01", "paper")
    assert saved == {"enabled": True, "env": "paper", "account_masked": "1234****-01", "source": "file", "reason": None, "active_env": "paper", "accounts": {"paper": "1234****-01", "real": None}}
    assert (tmp_path / "kis_credentials.json").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "kis_token_paper.json").exists()
    broker = broker_from_config()
    assert (broker.env, broker.app_key, broker.cano, broker.prod) == ("paper", "PSappkey0000000000", "12345678", "01")

    # Saving a real-account slot must not disturb the already-stored paper slot.
    real_saved = save_credentials("REALappkey0000000000", "REALappsecret0000000000", "99998888-02", "real")
    assert real_saved["accounts"] == {"paper": "1234****-01", "real": "9999****-02"}
    assert real_saved["active_env"] == "real"
    assert real_saved["env"] == "real"

    # Switching the active env back to paper reuses the still-stored paper credentials.
    switched = set_active_env("paper")
    assert switched["active_env"] == "paper"
    assert switched["env"] == "paper"
    assert switched["accounts"] == {"paper": "1234****-01", "real": "9999****-02"}

    monkeypatch.setenv("KIS_APP_KEY", "ENVKEY")
    monkeypatch.setenv("KIS_APP_SECRET", "ENVSECRET")
    monkeypatch.setenv("KIS_ACCOUNT", "77776666-02")
    monkeypatch.setenv("KIS_ENV", "real")
    env_status = broker_status()
    assert (env_status["source"], env_status["env"], env_status["account_masked"]) == ("env", "real", "7777****-02")
    assert env_status["accounts"] == {"paper": "1234****-01", "real": "9999****-02"}

    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT", "KIS_ENV"):
        monkeypatch.delenv(name, raising=False)

    # Clearing the active env falls back to the other stored slot instead of disabling the broker.
    cleared_paper = clear_credentials("paper")
    assert cleared_paper["enabled"] is True
    assert cleared_paper["active_env"] == "real"
    assert cleared_paper["accounts"] == {"paper": None, "real": "9999****-02"}

    assert clear_credentials("real")["enabled"] is False
    assert not (tmp_path / "kis_credentials.json").exists()


def test_legacy_flat_credential_file_migrates_into_paper_slot(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    (tmp_path / "kis_credentials.json").write_text(
        '{"app_key": "LEGACYkey0000000000", "app_secret": "LEGACYsecret0000000000", "account": "50203804", "env": "paper"}'
    )
    status = broker_status()
    assert status == {"enabled": True, "env": "paper", "account_masked": "5020****-01", "source": "file", "reason": None, "active_env": "paper", "accounts": {"paper": "5020****-01", "real": None}}
    on_disk = json.loads((tmp_path / "kis_credentials.json").read_text())
    assert set(on_disk) == {"paper", "active_env"}
    assert on_disk["paper"] == {"app_key": "LEGACYkey0000000000", "app_secret": "LEGACYsecret0000000000", "account": "50203804"}
    broker = broker_from_config()
    assert (broker.env, broker.cano, broker.prod) == ("paper", "50203804", "01")


def test_set_active_env_rejects_unconfigured_slot(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    save_credentials("PSappkey0000000000", "SECappsecret0000000000", "12345678-01", "paper")
    with pytest.raises(KISError, match="실전 계좌 자격증명이 저장되어 있지 않습니다"):
        set_active_env("real")


def test_paper_account_without_product_code_defaults_to_01(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    saved = save_credentials("PSappkey0000000000", "SECappsecret0000000000", "12345678", "paper")
    assert saved["accounts"]["paper"] == "1234****-01"
    broker = broker_from_config()
    assert (broker.cano, broker.prod) == ("12345678", "01")


def test_save_credentials_rejects_bad_account_and_env(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    with pytest.raises(KISError, match="계좌번호는 8자리 숫자"):
        save_credentials("PSappkey0000000000", "SECappsecret0000000000", "1234-01", "paper")
    with pytest.raises(KISError, match="paper 또는 real"):
        save_credentials("PSappkey0000000000", "SECappsecret0000000000", "12345678-01", "live")
    with pytest.raises(KISError, match="모두 입력하세요"):
        save_credentials("", "", "12345678-01", "paper")
    with pytest.raises(KISError, match="10자 이상"):
        save_credentials("short", "alsoshort", "12345678-01", "paper")


def test_kis_submit_order_sends_market_order_body(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    broker = KISBroker(env="paper", app_key="key", app_secret="secret", account="12345678-01")
    calls: list[dict] = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, json=None, timeout=None):
        return Response({"access_token": "token", "access_token_token_expired": "2099-01-01 00:00:00"})

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "params": params, "body": json})
        return Response({"rt_cd": "0", "msg1": "정상 접수", "output": {"ODNO": "0000117057"}})

    monkeypatch.setattr(broker.session, "post", fake_post)
    monkeypatch.setattr(broker.session, "request", fake_request)
    result = broker.submit_order("005930", "sell", 3, "market", None)

    assert (result.status, result.broker_order_id) == ("submitted", "0000117057")
    assert calls[0]["url"].endswith("/uapi/domestic-stock/v1/trading/order-cash")
    assert calls[0]["headers"]["tr_id"] == "VTTC0011U"
    assert calls[0]["headers"]["authorization"] == "Bearer token"
    assert calls[0]["body"] == {"CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "005930", "ORD_DVSN": "01", "ORD_QTY": "3", "ORD_UNPR": "0", "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01", "CNDT_PRIC": ""}


def test_kis_order_fill_parses_partial_and_rejection(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    broker = KISBroker(env="real", app_key="key", app_secret="secret", account="12345678-01")
    payloads = [
        {"rt_cd": "0", "output1": [{"odno": "0000117057", "tot_ccld_qty": "4", "rmn_qty": "6", "tot_ccld_amt": "280,000", "avg_prvs": "70,000"}]},
        {"rt_cd": "0", "output1": [{"odno": "0000117057", "tot_ccld_qty": "10", "rmn_qty": "0", "tot_ccld_amt": "700,000", "avg_prvs": ""}]},
        {"rt_cd": "0", "output1": [{"odno": "0000117057", "tot_ccld_qty": "0", "rmn_qty": "0", "cncl_yn": "Y"}]},
        {"rt_cd": "0", "output1": []},
    ]

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(broker, "_token", lambda: "token")
    monkeypatch.setattr(broker.session, "request", lambda *args, **kwargs: Response(payloads.pop(0)))

    partial = broker.order_fill("0000117057", "2026-08-31")
    assert (partial.status, partial.quantity, partial.price) == ("partial", 4.0, 70000.0)
    filled = broker.order_fill("0000117057")
    assert (filled.status, filled.quantity, filled.price) == ("filled", 10.0, 70000.0)
    assert broker.order_fill("0000117057").status == "rejected"
    assert broker.order_fill("0000117057").status == "submitted"


def test_duplicate_broker_order_id_same_day_is_recorded_not_fatal(store):
    save_plan(plan_payload(name="계획A"), store)
    save_plan(plan_payload(name="계획B", entry_price=1000), store)
    broker = StubBroker()
    orders = run_plans(broker=broker, dry_run=False, path=store)
    assert [order["status"] for order in orders] == ["submitted", "failed"]
    assert "중복 주문번호" in orders[1]["message"]
    assert len(list_orders(path=store)) == 2


def test_leg_quantities_allocate_integers_summing_to_plan_quantity():
    for quantity in range(1, 201):
        for tp1_ratio, tp2_ratio in ((0.4, 0.3), (1 / 3, 1 / 3), (0.5, 0.25), (0.2, 0.7), (0.45, 0.5), (0.1, 0.1), (0.7, 0.29)):
            legs = _leg_quantities(quantity, tp1_ratio, tp2_ratio)
            assert all(float(leg).is_integer() and leg >= 0 for leg in legs), (quantity, tp1_ratio, tp2_ratio, legs)
            assert sum(legs) == float(quantity), (quantity, tp1_ratio, tp2_ratio, legs)
    assert _leg_quantities(5, 0.4, 0.3) == (2.0, 2.0, 1.0)
    assert _leg_quantities(7, 0.4, 0.3) == (3.0, 2.0, 2.0)  # 절삭이면 2+2+2=6 으로 1주가 남았다
    assert _leg_quantities(9, 0.4, 0.3) == (4.0, 3.0, 2.0)
    assert _leg_quantities(2.5, 0.4, 0.2) == pytest.approx((1.0, 0.5, 1.0))  # 소수 수량은 기존 비율 배분 유지


def test_integer_leg_split_liquidates_whole_position(store):
    save_plan(plan_payload(quantity=7, entry_price=1050, stop_price=980, tp1_price=1150, tp1_ratio=0.4, tp2_price=1250, tp2_ratio=0.3, tp3_trailing_pct=5), store)
    broker = SequentialFillBroker()
    stages = [
        ("2026-06-02", (1040, 1060, 1030, 1055), "entry", 7.0),
        ("2026-06-03", (1100, 1160, 1090, 1150), "tp1", 3.0),
        ("2026-06-04", (1200, 1260, 1240, 1250), "tp2", 2.0),
        ("2026-06-05", (1230, 1245, 1150, 1160), "trailing", 2.0),
    ]
    for day, bar, leg, quantity in stages:
        with db_session(store) as db:
            insert_bar(db, "005930", day, *bar)
        evaluation = evaluate_plans(path=store)[0]
        assert float(evaluation["order_quantity"]).is_integer() and evaluation["order_quantity"] > 0
        orders = run_plans(broker=broker, dry_run=False, path=store)
        assert (orders[0]["leg"], orders[0]["quantity"]) == (leg, quantity)
        sync_orders(broker=broker, path=store)

    assert evaluate_plans(path=store)[0]["phase"] == "closed"
    assert sum(order["quantity"] for order in list_orders(path=store) if order["leg"] != "entry") == 7.0
    assert snapshot(store)["positions"] == []


def test_stop_leg_quantity_tracks_integer_remainder_per_stage(store):
    save_plan(plan_payload(quantity=7, entry_price=1050, stop_price=980, tp1_price=1150, tp1_ratio=0.4, tp2_price=1250, tp2_ratio=0.3), store)
    broker = SequentialFillBroker()
    crash = (1000, 1010, 960, 970)  # 손절가 980 이탈

    def stop_quantity(day):
        with db_session(store) as db:
            insert_bar(db, "005930", day, *crash)
        evaluation = evaluate_plans(path=store)[0]
        assert (evaluation["next_leg"], evaluation["triggered"]) == ("stop", True)
        return evaluation["order_quantity"]

    def advance(day, bar):
        with db_session(store) as db:
            insert_bar(db, "005930", day, *bar)
        run_plans(broker=broker, dry_run=False, path=store)
        sync_orders(broker=broker, path=store)

    advance("2026-06-02", (1040, 1060, 1030, 1055))  # entry 7주
    assert stop_quantity("2026-06-03") == 7.0
    advance("2026-06-04", (1100, 1160, 1090, 1150))  # tp1 3주
    assert stop_quantity("2026-06-05") == 4.0
    advance("2026-06-06", (1200, 1260, 1240, 1250))  # tp2 2주
    assert stop_quantity("2026-06-07") == 2.0

    orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (orders[0]["leg"], orders[0]["quantity"], orders[0]["side"]) == ("stop", 2.0, "sell")
    sync_orders(broker=broker, path=store)
    assert evaluate_plans(path=store)[0]["phase"] == "closed"
    assert snapshot(store)["positions"] == []


def test_zero_quantity_take_profit_legs_are_skipped_not_ordered(store):
    save_plan(plan_payload(quantity=1, tp1_ratio=0.4, tp2_ratio=0.3, tp3_trailing_pct=5), store)
    broker = SequentialFillBroker()
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-02", 1000, 1060, 990, 1055)  # 진입가 1005 돌파
    entry = run_plans(broker=broker, dry_run=False, path=store)
    assert (entry[0]["leg"], entry[0]["quantity"]) == ("entry", 1.0)
    sync_orders(broker=broker, path=store)

    # 1주는 3분할이 불가능하므로 tp1/tp2(0주)를 건너뛰고 트레일링 레그가 전량을 청산한다.
    holding = evaluate_plans(path=store)[0]
    assert (holding["phase"], holding["next_leg"], holding["order_quantity"]) == ("trailing", "trailing", 1.0)

    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-03", 1010, 1015, 990, 995)  # 고점 1060 * 0.95 = 1007 이탈
    orders = run_plans(broker=broker, dry_run=False, path=store)
    assert (orders[0]["leg"], orders[0]["quantity"], orders[0]["side"]) == ("trailing", 1.0, "sell")
    sync_orders(broker=broker, path=store)
    assert [order["leg"] for order in list_orders(path=store)] == ["trailing", "entry"]
    assert evaluate_plans(path=store)[0]["phase"] == "closed"
    assert snapshot(store)["positions"] == []


def test_kis_submit_order_rounds_quantity_and_rejects_zero_shares(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.broker.kis.MSCR_HOME", tmp_path)
    broker = KISBroker(env="paper", app_key="key", app_secret="secret", account="12345678-01")
    calls: list[dict] = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_request(method, url, headers=None, params=None, json=None, timeout=None):
        calls.append({"body": json})
        return Response({"rt_cd": "0", "msg1": "정상 접수", "output": {"ODNO": "0000117057"}})

    monkeypatch.setattr(broker, "_token", lambda: "token")
    monkeypatch.setattr(broker.session, "request", fake_request)

    broker.submit_order("005930", "sell", 2.9999999999999996, "market", None)
    assert calls[0]["body"]["ORD_QTY"] == "3"  # 절삭이면 2주만 나가 1주가 미청산으로 남는다

    with pytest.raises(KISError, match="0주 이하"):
        broker.submit_order("005930", "sell", 0.0, "market", None)
    with pytest.raises(KISError, match="0주 이하"):
        broker.submit_order("005930", "sell", 0.4, "market", None)
    assert len(calls) == 1
