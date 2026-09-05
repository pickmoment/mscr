from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

Progress = Callable[[int, int | None, str | None], None]

_lock = threading.Lock()
_states: dict[str, dict[str, Any]] = {}


def status(name: str) -> dict[str, Any]:
    with _lock:
        return dict(_states.get(name, {"running": False}))


def start(name: str, target: Callable[[Progress], Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """이름별로 한 번에 하나만 도는 백그라운드 작업. 이미 실행 중이면 새로 시작하지 않고 현재 상태를
    그대로 돌려준다(수집·신호 로그 적재 모두 DB를 길게 쓰므로 동시 실행을 막는다)."""
    with _lock:
        if _states.get(name, {}).get("running"):
            return dict(_states[name])
        _states[name] = {
            "running": True, "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
            "processed": 0, "total": None, "current": None, "ok": None, "error": None, "result": None,
            **(meta or {}),
        }
        snapshot = dict(_states[name])

    def _progress(processed: int, total: int | None = None, current: str | None = None) -> None:
        with _lock:
            _states[name].update({"processed": processed, "total": total, "current": current})

    def _run() -> None:
        try:
            result = target(_progress)
            with _lock:
                _states[name].update({"ok": True, "result": result})
        except Exception as exc:
            with _lock:
                _states[name].update({"ok": False, "error": str(exc)})
        finally:
            with _lock:
                _states[name].update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})

    threading.Thread(target=_run, daemon=True, name=f"mscr-{name}").start()
    return snapshot
