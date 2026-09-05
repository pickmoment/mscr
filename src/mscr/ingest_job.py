from __future__ import annotations

from typing import Any, Callable

from . import jobs

NAME = "ingest"


def status() -> dict[str, Any]:
    state = jobs.status(NAME)
    return state | {"current_day": state.get("current")}


def start(days: int, force: bool, source: str, then: Callable[[], Any] | None = None) -> dict[str, Any]:
    """수집을 백그라운드로 돌린다. `then`은 수집이 끝난 뒤 실행할 후속 작업(예: 신호 로그 적재)이며,
    호출자가 명시적으로 넘긴 경우에만 실행된다."""
    def _target(progress):
        from .ingest import run_ingest

        run_ingest(days=days, force=force, source=source, on_progress=lambda idx, total, day, stock_rows, etf_rows: progress(idx, total, day))
        if then is not None:
            then()

    state = jobs.start(NAME, _target, {"source": source, "days": days, "force": force})
    return state | {"current_day": state.get("current")}
