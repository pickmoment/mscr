"""AI 에이전트용 스킬 문서 설치.

`SKILL.md`는 패키지 안에 원본 하나로 두고(`src/mscr/SKILL.md`), 설치할 때 실행 명령과 실행
위치만 끼워 넣어 각 도구가 읽는 자리에 떨군다. 원본이 하나라 문서가 갈라지지 않는다.

- `claude`: Claude Code — 프로젝트 `./.claude/skills/`, 글로벌 `~/.claude/skills/`
- `agents`: 에이전트 공통 규약 — 프로젝트 `./.agents/skills/`, 글로벌 `~/.agents/skills/`
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SKILL_NAME = "mscr"
TEMPLATE_PATH = Path(__file__).with_name("SKILL.md")
SCOPES = ("project", "global")


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    project_dir: str   # 프로젝트 루트 기준 상대 경로
    global_dir: str    # 홈 디렉터리 기준 상대 경로

    def directory(self, scope: str, root: Path) -> Path:
        base = root if scope == "project" else Path.home()
        return base / (self.project_dir if scope == "project" else self.global_dir) / SKILL_NAME

    def path(self, scope: str, root: Path) -> Path:
        return self.directory(scope, root) / "SKILL.md"


TARGETS: dict[str, Target] = {
    "claude": Target("claude", "Claude Code", ".claude/skills", ".claude/skills"),
    "agents": Target("agents", "에이전트 공통(.agents)", ".agents/skills", ".agents/skills"),
}


def resolve_targets(values: Iterable[str] | None) -> list[Target]:
    keys = [str(value).strip().lower() for value in (values or ["claude"]) if str(value).strip()]
    if "all" in keys:
        return list(TARGETS.values())
    unknown = [key for key in keys if key not in TARGETS]
    if unknown:
        raise ValueError(f"지원하지 않는 타겟입니다: {', '.join(unknown)} (가능: {', '.join(TARGETS)}, all)")
    # 같은 타겟을 두 번 줘도 한 번만 설치한다.
    return [TARGETS[key] for key in dict.fromkeys(keys)]


def resolve_scope(scope: str) -> str:
    value = str(scope or "project").strip().lower()
    if value not in SCOPES:
        raise ValueError(f"지원하지 않는 범위입니다: {scope} (가능: {', '.join(SCOPES)})")
    return value


def command_for(scope: str) -> str:
    """스킬 문서에 박아 넣을 실행 명령.

    프로젝트 설치는 저장소 안에서 도는 `uv run mscr`가 맞고, 글로벌 설치는 어느 디렉터리에서든
    돌아야 하므로 지금 실행 중인 인터프리터 옆의 실행 파일(절대 경로)을 쓴다.
    """
    if scope == "project":
        return "uv run mscr"
    candidate = Path(sys.executable).with_name("mscr")
    if candidate.exists():
        return str(candidate)
    return shutil.which("mscr") or "mscr"


WHERE = {
    "project": "프로젝트 루트(`pyproject.toml`이 있는 디렉터리)에서 실행한다.",
    "global": "어느 디렉터리에서 실행해도 된다 — 데이터베이스는 `~/.mscr`에 있다.",
}


def render(scope: str = "project") -> str:
    scope = resolve_scope(scope)
    return TEMPLATE_PATH.read_text().replace("{{MSCR}}", command_for(scope)).replace("{{WHERE}}", WHERE[scope])


def install(targets: Iterable[str] | None = None, scope: str = "project", root: Path | str | None = None,
            force: bool = False, dry_run: bool = False) -> list[dict[str, Any]]:
    """고른 타겟에 스킬 문서를 설치한다.

    이미 같은 내용이면 건드리지 않고(`unchanged`), 내용이 다르면 `--force` 없이는 덮어쓰지
    않는다(`conflict`) — 사용자가 손댄 문서를 조용히 날리지 않기 위해서다.
    """
    scope = resolve_scope(scope)
    base = Path(root or Path.cwd())
    content = render(scope)
    results = []
    for target in resolve_targets(targets):
        path = target.path(scope, base)
        existing = path.read_text() if path.exists() else None
        if existing == content:
            status = "unchanged"
        elif existing is not None and not force:
            status = "conflict"
        else:
            status = "updated" if existing is not None else "installed"
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        results.append({"target": target.key, "label": target.label, "scope": scope, "path": str(path),
                        "status": "dry_run" if dry_run and status in ("installed", "updated") else status})
    return results


def remove(targets: Iterable[str] | None = None, scope: str = "project", root: Path | str | None = None,
           dry_run: bool = False) -> list[dict[str, Any]]:
    scope = resolve_scope(scope)
    base = Path(root or Path.cwd())
    results = []
    for target in resolve_targets(targets):
        path = target.path(scope, base)
        status = "missing"
        if path.exists():
            status = "dry_run" if dry_run else "removed"
            if not dry_run:
                path.unlink()
                # 우리가 만든 빈 디렉터리만 치운다.
                for folder in (path.parent,):
                    if folder.exists() and not any(folder.iterdir()):
                        folder.rmdir()
        results.append({"target": target.key, "label": target.label, "scope": scope, "path": str(path), "status": status})
    return results


def status(root: Path | str | None = None) -> list[dict[str, Any]]:
    """타겟×범위 네 자리의 설치 상태. 어디에 무엇이 깔려 있는지 한눈에 본다."""
    base = Path(root or Path.cwd())
    rows = []
    for target in TARGETS.values():
        for scope in SCOPES:
            path = target.path(scope, base)
            content = render(scope)
            state = "absent"
            if path.exists():
                state = "installed" if path.read_text() == content else "modified"
            rows.append({"target": target.key, "label": target.label, "scope": scope, "path": str(path), "status": state})
    return rows
