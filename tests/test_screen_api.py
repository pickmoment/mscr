from __future__ import annotations

import pytest

from mscr.api import routes
from mscr.api.models import ScreenRequest, ScreenSaveRequest
from mscr.db import db_session, init_db

MULTILINE_FORMULA = "sma(close, 50) > sma(close, 150)\nand sma(close, 150) > sma(close, 200)\nand close >= 1000"


def _spec(formula: str = MULTILINE_FORMULA, sort_formula: str = "close") -> ScreenRequest:
    return ScreenRequest(universe={"kinds": ["stock"], "markets": ["KOSPI"]}, formula=formula, sort={"formula": sort_formula, "dir": "desc"}, limit=10)


@pytest.fixture()
def store(monkeypatch, tmp_path):
    path = tmp_path / "screens.db"
    init_db(path)
    monkeypatch.setattr(routes, "db_session", lambda *args, **kwargs: db_session(path))
    return path


def test_saving_a_preset_accepts_a_multiline_screen_formula(store):
    result = routes.save_screen(ScreenSaveRequest(name="정배열", spec=_spec()))

    saved = routes.screens()
    assert len(saved) == 1
    assert saved[0]["id"] == result["id"]
    assert saved[0]["spec"]["formula"] == MULTILINE_FORMULA


def test_updating_a_preset_accepts_a_multiline_screen_formula(store):
    created = routes.save_screen(ScreenSaveRequest(name="정배열", spec=_spec(formula="close > 0")))

    routes.update_screen(created["id"], ScreenSaveRequest(name="정배열", spec=_spec()))

    saved = routes.screens()
    assert saved[0]["spec"]["formula"] == MULTILINE_FORMULA


def test_saving_a_preset_rejects_a_genuinely_invalid_multiline_formula(store):
    with pytest.raises(Exception) as error:
        routes.save_screen(ScreenSaveRequest(name="깨진수식", spec=_spec(formula="close >\nand 10")))
    assert getattr(error.value, "status_code", None) == 422
