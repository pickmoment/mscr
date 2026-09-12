from __future__ import annotations

import os
from pathlib import Path

MSCR_HOME = Path(os.environ.get("MSCR_HOME", "~/.mscr")).expanduser()
DB_PATH = MSCR_HOME / "mscr.db"
REQUEST_RETRIES = 3
SCHEMA_VERSION = 7
DEFAULT_REQUEST_DELAY_SEC = 0.3


def request_delay() -> float:
    from .credentials import load
    raw = os.environ.get("MSCR_REQUEST_DELAY_SEC") or load("settings").get("request_delay_sec")
    try:
        return min(10.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_DELAY_SEC


def request_delay_source() -> str:
    from .credentials import load
    if os.environ.get("MSCR_REQUEST_DELAY_SEC"): return "env"
    return "file" if load("settings").get("request_delay_sec") is not None else "default"
