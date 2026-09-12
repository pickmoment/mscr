from __future__ import annotations

import pytest

from mscr import config
from mscr.providers.krx import KRXOpenAPIProvider, KRXProvider, clear_krx_credentials, krx_credentials, krx_status, save_krx_credentials


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", tmp_path)
    for name in ("KRX_OPENAPI_KEY", "KRX_ID", "KRX_PW", "MSCR_REQUEST_DELAY_SEC"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_krx_status_defaults_to_anonymous():
    assert krx_status() == {"mode": "anonymous", "source": None, "openapi_key_masked": None, "krx_id_masked": None, "stored": []}


def test_saved_openapi_key_selects_official_provider(isolated_store):
    status = save_krx_credentials(openapi_key="74D1B99DFBF345BBA3FB")
    assert (status["mode"], status["source"], status["openapi_key_masked"]) == ("openapi", "file", "74D1****A3FB")
    provider = KRXProvider()
    assert isinstance(provider, KRXOpenAPIProvider) and provider.api_key == "74D1B99DFBF345BBA3FB"


def test_saved_id_pw_reports_idpw_mode():
    status = save_krx_credentials(krx_id="tester", krx_pw="secret")
    assert (status["mode"], status["source"], status["krx_id_masked"]) == ("idpw", "file", "te****er")
    assert krx_credentials()["krx_pw"] == "secret"


def test_environment_overrides_stored_key(monkeypatch):
    save_krx_credentials(openapi_key="STOREDKEY0000000")
    monkeypatch.setenv("KRX_OPENAPI_KEY", "ENVKEY9999999999")
    assert krx_credentials()["openapi_key"] == "ENVKEY9999999999"
    assert krx_status()["source"] == "env"


def test_clearing_single_field_and_all_credentials(isolated_store):
    save_krx_credentials(openapi_key="KEY0123456789abcd", krx_id="tester", krx_pw="secret")
    assert save_krx_credentials(openapi_key="")["mode"] == "idpw"
    assert clear_krx_credentials() == {"mode": "anonymous", "source": None, "openapi_key_masked": None, "krx_id_masked": None, "stored": []}
    assert not (isolated_store / "krx_credentials.json").exists()


def test_request_delay_precedence(monkeypatch):
    from mscr.credentials import save

    assert (config.request_delay(), config.request_delay_source()) == (0.3, "default")
    save("settings", {"request_delay_sec": 1.5})
    assert (config.request_delay(), config.request_delay_source()) == (1.5, "file")
    monkeypatch.setenv("MSCR_REQUEST_DELAY_SEC", "2.5")
    assert (config.request_delay(), config.request_delay_source()) == (2.5, "env")
    monkeypatch.setenv("MSCR_REQUEST_DELAY_SEC", "99")
    assert config.request_delay() == 10.0
    monkeypatch.setenv("MSCR_REQUEST_DELAY_SEC", "oops")
    assert config.request_delay() == 0.3


def test_provider_uses_configured_delay():
    from mscr.credentials import save

    save("settings", {"request_delay_sec": 0.75})
    assert KRXProvider().delay == 0.75
    assert KRXProvider(delay=0).delay == 0


def test_settings_response_contract(monkeypatch, tmp_path):
    monkeypatch.setattr("mscr.config.MSCR_HOME", tmp_path)
    monkeypatch.setattr("mscr.config.DB_PATH", tmp_path / "mscr.db")
    from mscr.api import routes

    monkeypatch.setattr(routes, "MSCR_HOME", tmp_path)
    monkeypatch.setattr(routes, "DB_PATH", tmp_path / "mscr.db")
    monkeypatch.setattr(routes, "db_session", lambda *args, **kwargs: __import__("mscr.db", fromlist=["db_session"]).db_session(tmp_path / "mscr.db"))
    payload = routes._settings()
    assert set(payload) == {"mscr_home", "db_path", "schema_version", "expected_schema_version", "market", "default_market", "request_delay_sec", "request_delay_source", "krx", "massive", "credential_paths", "kis", "ingest_defaults", "data"}
    assert set(payload["credential_paths"]) == {"krx", "kis", "massive"}
    assert set(payload["krx"]) == {"mode", "source", "openapi_key_masked", "krx_id_masked", "stored"}
    assert set(payload["kis"]) == {"enabled", "env", "account_masked", "source", "reason", "active_env", "accounts"}
    assert set(payload["ingest_defaults"]) == {"days", "force", "source", "sources"}
    assert set(payload["data"]) == {"as_of", "bars_rows", "instrument_count", "last_ingest_at"}
    assert payload["kis"]["enabled"] is False and payload["krx"]["mode"] in {"openapi", "idpw", "anonymous"}
    assert payload["ingest_defaults"] == {"days": 400, "force": False, "source": "krx", "sources": ["krx", "fdr", "alphasquare"]}
    assert payload["market"]["key"] == "kr" and payload["market"]["currency"] == "KRW"


def test_ingest_defaults_persist_across_settings_reads(isolated_store):
    from mscr.credentials import save

    save("settings", {"ingest_days": 7, "ingest_force": True, "ingest_source": "alphasquare"})
    from mscr.api import routes

    assert routes._ingest_defaults() == {"days": 7, "force": True, "source": "alphasquare", "sources": ["krx", "fdr", "alphasquare"]}


def test_ingest_defaults_reject_unknown_stored_source(isolated_store):
    from mscr.credentials import save

    save("settings", {"ingest_source": "bogus"})
    from mscr.api import routes

    assert routes._ingest_defaults()["source"] == "krx"
