from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False}


def status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def start(days: int, force: bool, source: str) -> dict[str, Any]:
    """이미 실행 중이면 새 작업을 시작하지 않고 현재 상태를 그대로 반환한다."""
    with _lock:
        if _state.get("running"):
            return dict(_state)
        _state.clear()
        _state.update({
            "running": True, "source": source, "days": days, "force": force,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "processed": 0, "total": None, "current_day": None,
            "ok": None, "error": None,
        })
        snapshot = dict(_state)

    def _progress(idx: int, total: int, day: str, stock_rows: int, etf_rows: int) -> None:
        with _lock:
            _state.update({"processed": idx, "total": total, "current_day": day})

    def _run() -> None:
        from .ingest import run_ingest
        try:
            run_ingest(days=days, force=force, source=source, on_progress=_progress)
            with _lock:
                _state.update({"ok": True})
        except Exception as exc:
            with _lock:
                _state.update({"ok": False, "error": str(exc)})
        finally:
            with _lock:
                _state.update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})

    threading.Thread(target=_run, daemon=True, name="mscr-ingest").start()
    return snapshot
