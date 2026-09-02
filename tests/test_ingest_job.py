import threading
import time

from mscr import ingest_job


def _wait_until_idle(timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = ingest_job.status()
        if not state["running"]:
            return state
        time.sleep(0.01)
    raise AssertionError("ingest job did not finish in time")


def test_start_reports_progress_and_completion(monkeypatch):
    def fake_run_ingest(days, force, source, on_progress=None, provider=None, path=None):
        on_progress(1, 2, "2024-01-02", 10, 5)
        on_progress(2, 2, "2024-01-03", 12, 6)

    monkeypatch.setattr("mscr.ingest.run_ingest", fake_run_ingest)
    started = ingest_job.start(days=10, force=False, source="krx")
    assert started["running"] is True
    assert started["days"] == 10 and started["source"] == "krx"

    final = _wait_until_idle()
    assert final["ok"] is True and final["error"] is None
    assert final["processed"] == 2 and final["total"] == 2
    assert final["current_day"] == "2024-01-03"
    assert final["finished_at"] is not None


def test_start_records_failure(monkeypatch):
    def fake_run_ingest(days, force, source, on_progress=None, provider=None, path=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("mscr.ingest.run_ingest", fake_run_ingest)
    ingest_job.start(days=5, force=False, source="krx")
    final = _wait_until_idle()
    assert final["ok"] is False and final["error"] == "boom"


def test_start_is_noop_while_already_running(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def fake_run_ingest(days, force, source, on_progress=None, provider=None, path=None):
        entered.set()
        release.wait(2)

    monkeypatch.setattr("mscr.ingest.run_ingest", fake_run_ingest)
    first = ingest_job.start(days=5, force=False, source="krx")
    assert entered.wait(2)
    again = ingest_job.start(days=999, force=True, source="fdr")
    assert again["started_at"] == first["started_at"]
    assert again["days"] == 5 and again["source"] == "krx"
    release.set()
    _wait_until_idle()
