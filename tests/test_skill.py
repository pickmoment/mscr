"""에이전트 스킬 문서 설치 — 타겟(claude/agents) × 범위(project/global)."""

from __future__ import annotations

import pytest

from mscr import skill


@pytest.fixture()
def home(monkeypatch, tmp_path):
    """글로벌 설치가 실제 홈 디렉터리에 쓰지 않게 막는다."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake))
    return fake


def test_rendered_document_has_no_placeholders_left(home):
    project = skill.render("project")
    globally = skill.render("global")
    for text in (project, globally):
        assert "{{" not in text
        assert text.startswith("---\nname: mscr\n")
    # 프로젝트는 저장소 안에서, 글로벌은 어디서든 도는 명령이어야 한다.
    assert "uv run mscr query" in project
    assert "uv run mscr" not in globally and "mscr query status" in globally


def test_install_writes_both_targets_and_is_idempotent(tmp_path, home):
    rows = skill.install(["claude", "agents"], scope="project", root=tmp_path)
    assert [row["status"] for row in rows] == ["installed", "installed"]
    assert (tmp_path / ".claude/skills/mscr/SKILL.md").exists()
    assert (tmp_path / ".agents/skills/mscr/SKILL.md").exists()

    again = skill.install(["claude", "agents"], scope="project", root=tmp_path)
    assert [row["status"] for row in again] == ["unchanged", "unchanged"]


def test_global_scope_installs_under_the_home_directory(tmp_path, home):
    rows = skill.install(["agents"], scope="global", root=tmp_path)
    assert rows[0]["status"] == "installed"
    assert (home / ".agents/skills/mscr/SKILL.md").exists()
    # 프로젝트 디렉터리는 건드리지 않는다.
    assert not (tmp_path / ".agents").exists()


def test_edited_document_is_not_overwritten_without_force(tmp_path, home):
    skill.install(["claude"], scope="project", root=tmp_path)
    path = tmp_path / ".claude/skills/mscr/SKILL.md"
    path.write_text("사용자가 손댄 문서")

    assert skill.install(["claude"], scope="project", root=tmp_path)[0]["status"] == "conflict"
    assert path.read_text() == "사용자가 손댄 문서"
    assert skill.install(["claude"], scope="project", root=tmp_path, force=True)[0]["status"] == "updated"
    assert path.read_text() == skill.render("project")


def test_dry_run_reports_without_touching_the_disk(tmp_path, home):
    rows = skill.install(["all"], scope="project", root=tmp_path, dry_run=True)
    assert [row["status"] for row in rows] == ["dry_run", "dry_run"]
    assert not (tmp_path / ".claude").exists() and not (tmp_path / ".agents").exists()


def test_remove_deletes_the_file_and_its_empty_directory(tmp_path, home):
    skill.install(["claude"], scope="project", root=tmp_path)
    rows = skill.remove(["claude"], scope="project", root=tmp_path)
    assert rows[0]["status"] == "removed"
    assert not (tmp_path / ".claude/skills/mscr").exists()
    assert skill.remove(["claude"], scope="project", root=tmp_path)[0]["status"] == "missing"


def test_status_covers_every_target_and_scope(tmp_path, home):
    skill.install(["claude"], scope="project", root=tmp_path)
    (tmp_path / ".agents/skills/mscr").mkdir(parents=True)
    (tmp_path / ".agents/skills/mscr/SKILL.md").write_text("손으로 바꾼 문서")

    rows = {(row["target"], row["scope"]): row["status"] for row in skill.status(root=tmp_path)}
    assert rows[("claude", "project")] == "installed"
    assert rows[("agents", "project")] == "modified"
    assert rows[("claude", "global")] == "absent" and rows[("agents", "global")] == "absent"


def test_unknown_target_is_rejected(tmp_path, home):
    with pytest.raises(ValueError, match="지원하지 않는 타겟"):
        skill.install(["cursor"], scope="project", root=tmp_path)
    with pytest.raises(ValueError, match="지원하지 않는 범위"):
        skill.install(["claude"], scope="user", root=tmp_path)
