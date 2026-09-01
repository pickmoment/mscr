from __future__ import annotations

import pytest
from fastapi import HTTPException

from mscr.api import routes
from mscr.api.models import IndicatorDefinitionRequest
from mscr.db import db_session, init_db


def _request(key: str, formula: str, parameters: list[dict] | None = None) -> IndicatorDefinitionRequest:
    return IndicatorDefinitionRequest(
        key=key, label=key, unit="ratio", formula=formula,
        parameters=parameters if parameters is not None else [{"name": "period", "default": 20, "min": 1, "max": 500, "integer": True}],
    )


@pytest.fixture()
def store(monkeypatch, tmp_path):
    path = tmp_path / "indicators.db"
    init_db(path)
    monkeypatch.setattr(routes, "db_session", lambda *args, **kwargs: db_session(path))
    monkeypatch.setattr(routes, "custom_definitions", lambda enabled_only=True: [
        item for item in __import__("mscr.dynamic", fromlist=["custom_definitions"]).custom_definitions(path, enabled_only)
    ])
    return path


def _keys(path) -> set[str]:
    with db_session(path) as db:
        return {row[0] for row in db.execute("SELECT key FROM indicator_definitions")}


def test_definition_may_reference_snapshot_scalars(store):
    routes.save_indicator(_request("custom_cheap_mover", "change_pct / per", []))
    assert "custom_cheap_mover" in _keys(store)


def test_definition_may_call_another_custom_indicator(store):
    routes.save_indicator(_request("custom_gap", "close / sma(close, period) - 1"))
    routes.save_indicator(_request("custom_gap_x2", "custom_gap(period) * 2"))
    assert _keys(store) == {"custom_gap", "custom_gap_x2"}


def test_self_reference_is_rejected(store):
    with pytest.raises(HTTPException) as error:
        routes.save_indicator(_request("custom_loop", "custom_loop(period) + 1"))
    assert error.value.status_code == 422
    assert "순환" in error.value.detail
    assert not _keys(store)


def test_indirect_cycle_is_rejected(store):
    routes.save_indicator(_request("custom_a", "close / sma(close, period) - 1"))
    routes.save_indicator(_request("custom_b", "custom_a(period) * 2"))
    # custom_a 를 custom_b 를 부르도록 고치면 a → b → a 순환이 된다.
    with pytest.raises(HTTPException) as error:
        routes.save_indicator(_request("custom_a", "custom_b(period) + 1"))
    assert "순환" in error.value.detail
    with db_session(store) as db:
        assert db.execute("SELECT formula FROM indicator_definitions WHERE key='custom_a'").fetchone()[0] == "close / sma(close, period) - 1"


def test_unknown_name_is_still_rejected(store):
    with pytest.raises(HTTPException) as error:
        routes.save_indicator(_request("custom_bad", "close / nonexistent_thing(period)"))
    assert error.value.status_code == 422


def test_catalog_exposes_every_name_the_screen_formula_accepts(store):
    from mscr.dynamic import BUILTIN_CATALOG, BUILTIN_FUNCTIONS, SCREEN_NAMES

    routes.save_indicator(_request("custom_gap", "close / sma(close, period) - 1"))
    catalog = routes.indicators()
    keys = {item["key"] for item in catalog}
    missing = (SCREEN_NAMES | BUILTIN_FUNCTIONS) - keys
    assert not missing, f"자동완성 카탈로그에서 빠진 이름: {sorted(missing)}"
    assert {item["key"] for item in BUILTIN_CATALOG} <= keys
    assert "custom_gap" in keys
    assert len(keys) == len(catalog), "중복 키가 있으면 자동완성 목록이 어긋난다"


def test_key_needs_no_prefix(store):
    routes.save_indicator(_request("ma_gap", "close / sma(close, period) - 1"))
    routes.save_indicator(_request("gap_vs_change", "ma_gap(period) - change_pct / 100"))
    assert _keys(store) == {"ma_gap", "gap_vs_change"}


def test_builtin_names_are_rejected_now_that_the_prefix_is_gone(store):
    for key in ("close", "change_pct", "sma", "abs", "halted"):
        with pytest.raises(HTTPException) as error:
            routes.save_indicator(_request(key, "close"))
        assert error.value.status_code == 409, key
        assert key in error.value.detail
    assert not _keys(store)


def test_formula_syntax_keywords_are_rejected_as_keys(store):
    for key in ("and", "or", "not", "if", "else", "lambda", "in", "is", "match", "case"):
        with pytest.raises(HTTPException) as error:
            routes.save_indicator(_request(key, "close"))
        assert error.value.status_code == 422, key
    assert not _keys(store)


def test_keyword_parameter_names_are_rejected(store):
    with pytest.raises(HTTPException) as error:
        routes.save_indicator(_request("ma_gap", "close * period", [{"name": "and", "default": 2, "min": 1, "max": 9, "integer": True}]))
    assert error.value.status_code == 422
    assert not _keys(store)


def test_saved_indicator_keys_can_never_shadow_a_builtin(store):
    """저장 API가 유일한 방어선이므로, 통과한 키로 만든 카탈로그에는 중복이 없어야 한다."""
    from mscr.dynamic import BUILTIN_FUNCTIONS, SCREEN_NAMES

    routes.save_indicator(_request("ma_gap", "close / sma(close, period) - 1"))
    keys = [item["key"] for item in routes.indicators()]
    assert len(keys) == len(set(keys))
    assert not (set(keys) - (SCREEN_NAMES | BUILTIN_FUNCTIONS)) - {"ma_gap"}
