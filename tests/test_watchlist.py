from __future__ import annotations

import pytest

from mscr.db import db_session, init_db
from mscr.watchlist import DEFAULT_NAME, create_from_tickers, create_watchlist, delete_item, delete_items, delete_watchlist, list_watchlists, rename_watchlist, save_item, snapshot, transfer_items


def insert_bar(db, ticker, day, close):
    db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (ticker, day, "krx_snapshot", close, close, close, close, 1000, 1_000_000, None, 0))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "watchlist.db"
    init_db(path)
    with db_session(path) as db:
        for ticker, name in (("005930", "삼성전자"), ("000660", "SK하이닉스"), ("035720", "카카오")):
            db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock','KOSPI',0,0,'2026-01-01','2026-08-31',0)", (ticker, name))
        insert_bar(db, "005930", "2026-06-01", 1000)
        insert_bar(db, "005930", "2026-06-02", 1100)
        insert_bar(db, "000660", "2026-06-01", 500)
        insert_bar(db, "000660", "2026-06-02", 450)
        db.execute("INSERT INTO snapshots_fundamental(ticker,date,bps,per,pbr,eps,div,dps,market_cap,shares) VALUES('005930','2026-06-02',0,12.5,1.4,0,0,0,7000000,100)")
    return path


def test_adding_a_ticker_without_a_list_creates_the_default_list(store):
    save_item(None, "005930", path=store)

    lists = list_watchlists(path=store)
    assert [entry["name"] for entry in lists] == [DEFAULT_NAME]
    assert lists[0]["item_count"] == 1


def test_membership_flag_is_reported_per_list_for_a_ticker(store):
    held = create_watchlist("반도체", path=store)["id"]
    create_watchlist("플랫폼", path=store)
    save_item(held, "005930", path=store)

    lists = list_watchlists(ticker="005930", path=store)
    assert {entry["name"]: entry["contains"] for entry in lists} == {"반도체": True, "플랫폼": False}
    assert all(entry["contains"] is False for entry in list_watchlists(ticker="035720", path=store))


def test_re_adding_a_ticker_updates_notes_but_keeps_the_entry_price(store):
    watchlist_id = create_watchlist("반도체", path=store)["id"]
    save_item(watchlist_id, "005930", memo="첫 메모", target_price=2000, path=store)
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-03", 1300)

    save_item(watchlist_id, "005930", memo="수정 메모", target_price=1500, path=store)

    row = snapshot(watchlist_id, path=store)["rows"][0]
    assert row["memo"] == "수정 메모"
    assert row["target_price"] == 1500
    assert row["added_price"] == 1100


def test_snapshot_reports_change_target_gap_and_return_since_added(store):
    watchlist_id = create_watchlist("반도체", path=store)["id"]
    save_item(watchlist_id, "005930", target_price=1210, path=store)
    save_item(watchlist_id, "000660", path=store)
    save_item(watchlist_id, "035720", memo="시세 없음", path=store)
    with db_session(store) as db:
        insert_bar(db, "005930", "2026-06-03", 1320)

    result = snapshot(watchlist_id, path=store)
    rows = {row["ticker"]: row for row in result["rows"]}
    assert rows["005930"]["change_pct"] == pytest.approx(20.0)
    assert rows["005930"]["since_added_pct"] == pytest.approx(20.0)
    assert rows["005930"]["target_gap_pct"] == pytest.approx(-8.333333, rel=1e-4)
    assert rows["005930"]["per"] == 12.5
    assert rows["000660"]["change_pct"] == pytest.approx(-10.0)
    assert rows["035720"]["stale"] is True and rows["035720"]["close"] is None
    assert result["as_of"] == "2026-06-03"
    assert result["summary"]["up"] == 1 and result["summary"]["down"] == 1
    assert result["summary"]["reached_target"] == 1
    assert result["summary"]["avg_change_pct"] == pytest.approx(5.0)


def test_deleting_a_list_removes_its_items(store):
    watchlist_id = create_watchlist("반도체", path=store)["id"]
    save_item(watchlist_id, "005930", path=store)

    delete_watchlist(watchlist_id, path=store)

    with db_session(store) as db:
        assert db.execute("SELECT COUNT(*) FROM watchlist_items").fetchone()[0] == 0


def test_the_same_ticker_can_sit_in_two_lists_independently(store):
    first = create_watchlist("반도체", path=store)["id"]
    second = create_watchlist("장기", path=store)["id"]
    save_item(first, "005930", memo="단기", path=store)
    save_item(second, "005930", memo="장기", path=store)

    delete_item(first, "005930", path=store)

    assert snapshot(first, path=store)["rows"] == []
    assert snapshot(second, path=store)["rows"][0]["memo"] == "장기"


def test_duplicate_names_and_unknown_tickers_are_rejected(store):
    watchlist_id = create_watchlist("반도체", path=store)["id"]
    other = create_watchlist("플랫폼", path=store)["id"]

    with pytest.raises(ValueError, match="같은 이름"):
        create_watchlist("반도체", path=store)
    with pytest.raises(ValueError, match="같은 이름"):
        rename_watchlist(other, "반도체", path=store)
    with pytest.raises(ValueError, match="등록되지 않은"):
        save_item(watchlist_id, "999999", path=store)
    with pytest.raises(ValueError, match="종목코드"):
        save_item(watchlist_id, "12345", path=store)
    with pytest.raises(ValueError, match="목표가"):
        save_item(watchlist_id, "005930", target_price=0, path=store)
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        snapshot(watchlist_id + 1000, path=store)
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        delete_item(watchlist_id, "005930", path=store)


def test_bulk_registration_creates_a_list_with_entry_prices_and_skips_unknown_tickers(store):
    result = create_from_tickers("스크린 결과", ["005930", "999999", "000660", "005930"], path=store)

    assert result["added"] == 2 and result["skipped"] == ["999999"]
    rows = {row["ticker"]: row for row in snapshot(result["id"], path=store)["rows"]}
    assert set(rows) == {"005930", "000660"}
    assert rows["005930"]["added_price"] == 1100
    assert rows["000660"]["memo"] is None and rows["000660"]["target_price"] is None


def test_bulk_registration_leaves_no_list_behind_when_it_fails(store):
    create_watchlist("이미 있음", path=store)

    with pytest.raises(ValueError, match="같은 이름"):
        create_from_tickers("이미 있음", ["005930"], path=store)
    with pytest.raises(ValueError, match="등록된 종목이 하나도 없어"):
        create_from_tickers("전부 미등록", ["999999", "888888"], path=store)
    with pytest.raises(ValueError, match="종목코드"):
        create_from_tickers("잘못된 코드", ["12345"], path=store)

    assert [entry["name"] for entry in list_watchlists(path=store)] == ["이미 있음"]


def test_selected_items_are_removed_in_one_call(store):
    watchlist_id = create_from_tickers("관찰", ["005930", "000660", "035720"], path=store)["id"]

    result = delete_items(watchlist_id, ["005930", "035720", "005930"], path=store)

    assert result["affected"] == 2
    assert [row["ticker"] for row in snapshot(watchlist_id, path=store)["rows"]] == ["000660"]


def test_moving_selected_items_carries_notes_and_entry_price_and_empties_the_source(store):
    source = create_watchlist("후보", path=store)["id"]
    target = create_watchlist("본진", path=store)["id"]
    save_item(source, "005930", memo="관찰 중", target_price=1500, path=store)
    save_item(source, "000660", path=store)

    result = transfer_items(source, target, ["005930"], path=store)

    assert result == {"affected": 1, "skipped": []}
    assert [row["ticker"] for row in snapshot(source, path=store)["rows"]] == ["000660"]
    moved = snapshot(target, path=store)["rows"][0]
    assert moved["ticker"] == "005930" and moved["memo"] == "관찰 중" and moved["target_price"] == 1500
    assert moved["added_price"] == 1100


def test_copying_selected_items_keeps_the_source_entry(store):
    source = create_watchlist("후보", path=store)["id"]
    target = create_watchlist("본진", path=store)["id"]
    save_item(source, "005930", memo="관찰 중", path=store)

    transfer_items(source, target, ["005930"], keep_source=True, path=store)

    assert [row["ticker"] for row in snapshot(source, path=store)["rows"]] == ["005930"]
    assert snapshot(target, path=store)["rows"][0]["memo"] == "관찰 중"


def test_transfer_skips_tickers_already_in_the_target_without_touching_them(store):
    source = create_watchlist("후보", path=store)["id"]
    target = create_watchlist("본진", path=store)["id"]
    save_item(source, "005930", memo="새 메모", path=store)
    save_item(source, "000660", path=store)
    save_item(target, "005930", memo="원래 메모", path=store)

    result = transfer_items(source, target, ["005930", "000660"], path=store)

    assert result["affected"] == 1 and result["skipped"] == ["005930"]
    assert [row["ticker"] for row in snapshot(source, path=store)["rows"]] == ["005930"]
    assert {row["ticker"]: row["memo"] for row in snapshot(target, path=store)["rows"]} == {"005930": "원래 메모", "000660": None}


def test_bulk_actions_reject_bad_arguments(store):
    watchlist_id = create_from_tickers("관찰", ["005930"], path=store)["id"]

    with pytest.raises(ValueError, match="선택된 종목이 없습니다"):
        delete_items(watchlist_id, [], path=store)
    with pytest.raises(ValueError, match="같은 목록으로는"):
        transfer_items(watchlist_id, watchlist_id, ["005930"], path=store)
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        transfer_items(watchlist_id, watchlist_id + 1000, ["005930"], path=store)
    assert [row["ticker"] for row in snapshot(watchlist_id, path=store)["rows"]] == ["005930"]
