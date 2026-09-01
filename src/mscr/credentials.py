from __future__ import annotations

import json
import os
from typing import Any

from .config import MSCR_HOME


def path_for(name: str):
    return MSCR_HOME / f"{name}.json"


def load(name: str) -> dict[str, Any]:
    try:
        stored = json.loads(path_for(name).read_text())
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def save(name: str, values: dict[str, Any]) -> dict[str, Any]:
    target = path_for(name)
    MSCR_HOME.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(values, ensure_ascii=False))
    os.chmod(target, 0o600)
    return values


def update(name: str, values: dict[str, Any]) -> dict[str, Any]:
    merged = load(name) | {key: value for key, value in values.items() if value not in (None, "")}
    for key, value in values.items():
        if value == "": merged.pop(key, None)
    if not merged:
        clear(name)
        return {}
    return save(name, merged)


def clear(name: str) -> None:
    path_for(name).unlink(missing_ok=True)


def mask(value: str | None, keep: int = 4) -> str | None:
    if not value: return None
    text = str(value)
    return f"{text[:keep]}****{text[-keep:]}" if len(text) > keep * 2 else f"{text[:1]}****"
