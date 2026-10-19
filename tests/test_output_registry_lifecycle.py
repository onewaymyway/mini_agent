"""P3 生命周期测试：受管新建、版本发布、管理项目重新打开。

对应 next_doc/management_output_project_model_plan.md 第 5.3、5.6、8.4 节与第 9 章 P3。

保证：
  1. 新建只创建、不覆盖；受管项目带 PROJECT.json 镜像；
  2. 版本号严格语义化且递增；重复或回退被拒绝；
  3. 快照复制全部内容（不含 releases/ 自身），且之后的修改不影响快照；
  4. 快照文件为只读；版本记录只追加；
  5. 旧项目发布版本不产生 PROJECT.json（兼容）；
  6. 删除注册表后可以从 PROJECT.json 恢复版本历史；
  7. 重新打开管理项目需要原因，并记录历史。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mini_agent.output_registry import (
    RegistryError,
    link_management_output,
    load_index,
    mark_management_root,
    rebuild_index,
    register_project,
)
from mini_agent.output_registry.lifecycle import (
    create_output_project,
    parse_semver,
    publish_version,
    reopen_management_root,
)
from mini_agent.output_registry.registry import load_record, registry_dir
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "output_projects").mkdir(parents=True)
    return p


@pytest.fixture
def backlog(proj: Path) -> GoalBacklog:
    b = GoalBacklog(AgentPaths(project_root=proj))
    top = b.get_root_node()
    domain = b.add_node("domain", "工作", parent_id=top.id)
    b.add_node("goal", "做出产品", parent_id=domain.id)
    return b


def _goal(backlog: GoalBacklog):
    return next(n for n in backlog.all_nodes() if n.level == "goal")


# ── 1. 受管新建 ───────────────────────────────────────────────────────────────

def test_create_builds_skeleton_and_mirror(proj: Path):
    rec = create_output_project(proj, "novel", title="长篇小说", intro="科幻")
    root = proj / "output_projects" / "novel"
    assert (root / "PROJECT_INFO.md").is_file()
    assert (root / "output").is_dir()
    assert "长篇小说" in (root / "PROJECT_INFO.md").read_text(encoding="utf-8")
    assert rec["managed"] is True and rec["legacy"] is False
    mirror = json.loads((root / "PROJECT.json").read_text(encoding="utf-8"))
    assert mirror["slug"] == "novel" and mirror["managed"] is True


def test_create_refuses_existing_directory(proj: Path):
    existing = proj / "output_projects" / "taken"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(RegistryError):
        create_output_project(proj, "taken")
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_create_rejects_illegal_slug(proj: Path):
    with pytest.raises(RegistryError):
        create_output_project(proj, "../escape")


# ── 2. 语义化版本 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "01.0.0", "1.0.0-beta", "1.2.3.4", ""])
def test_semver_rejects_non_strict_formats(bad):
    with pytest.raises(RegistryError):
        parse_semver(bad)


def test_semver_parses_strict_form():
    assert parse_semver("10.2.3") == (10, 2, 3)


def test_publish_rejects_non_increasing_versions(proj: Path):
    create_output_project(proj, "novel")
    publish_version(proj, "novel", "1.0.0")
    with pytest.raises(RegistryError):
        publish_version(proj, "novel", "1.0.0")
    with pytest.raises(RegistryError):
        publish_version(proj, "novel", "0.9.9")
    publish_version(proj, "novel", "1.0.1")
    assert load_index(proj)["projects"]["novel"]["latest_version"] == "1.0.1"


# ── 3. 快照 ───────────────────────────────────────────────────────────────────

def test_snapshot_copies_full_content_without_releases(proj: Path):
    create_output_project(proj, "novel", title="t")
    root = proj / "output_projects" / "novel"
    (root / "output" / "ch1.md").write_text("第一章", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "设定.md").write_text("设定", encoding="utf-8")

    publish_version(proj, "novel", "1.0.0", changelog="首卷")
    snap = root / "releases" / "1.0.0"
    assert (snap / "output" / "ch1.md").read_text(encoding="utf-8") == "第一章"
    assert (snap / "notes" / "设定.md").is_file()
    assert (snap / PROJECT_INFO_NAME).is_file()
    assert not (snap / "releases").exists(), "快照不得包含 releases/ 自身"


PROJECT_INFO_NAME = "PROJECT_INFO.md"


def test_snapshot_is_unaffected_by_later_edits(proj: Path):
    create_output_project(proj, "novel")
    root = proj / "output_projects" / "novel"
    (root / "output" / "ch1.md").write_text("v1 正文", encoding="utf-8")
    publish_version(proj, "novel", "1.0.0")
    (root / "output" / "ch1.md").write_text("v2 正文", encoding="utf-8")
    snap_file = root / "releases" / "1.0.0" / "output" / "ch1.md"
    assert snap_file.read_text(encoding="utf-8") == "v1 正文"


def test_snapshot_files_are_read_only(proj: Path):
    create_output_project(proj, "novel")
    publish_version(proj, "novel", "1.0.0")
    snap_file = proj / "output_projects" / "novel" / "releases" / "1.0.0" / PROJECT_INFO_NAME
    mode = os.stat(snap_file).st_mode & 0o777
    assert mode & 0o222 == 0, "快照文件不应具有写权限"


def test_version_records_are_append_only(proj: Path):
    create_output_project(proj, "novel")
    publish_version(proj, "novel", "1.0.0", changelog="a")
    publish_version(proj, "novel", "1.1.0", changelog="b")
    versions = load_record(proj, "novel")["versions"]
    assert [v["version"] for v in versions] == ["1.0.0", "1.1.0"]
    assert versions[0]["changelog"] == "a"


def test_publish_records_source_goal(proj: Path, backlog: GoalBacklog):
    create_output_project(proj, "novel")
    goal = _goal(backlog)
    mark_management_root(proj, goal.id, goal.title)
    entry = publish_version(proj, "novel", "1.0.0", source_goal_id=goal.id)
    assert entry["source_goal_id"] == goal.id


# ── 4. 旧项目兼容 ─────────────────────────────────────────────────────────────

def test_legacy_project_publish_does_not_write_project_json(proj: Path):
    legacy = proj / "output_projects" / "old_alpha"
    (legacy / "output").mkdir(parents=True)
    (legacy / "output" / "a.md").write_text("旧内容", encoding="utf-8")
    register_project(proj, "old_alpha")

    publish_version(proj, "old_alpha", "1.0.0")
    assert not (legacy / "PROJECT.json").exists(), "旧项目不得写入 PROJECT.json"
    assert (legacy / "releases" / "1.0.0" / "output" / "a.md").read_text(encoding="utf-8") == "旧内容"
    assert load_record(proj, "old_alpha")["legacy"] is True


def test_publish_unknown_project_rejected(proj: Path):
    with pytest.raises(RegistryError):
        publish_version(proj, "ghost", "1.0.0")


# ── 5. 从镜像恢复 ─────────────────────────────────────────────────────────────

def test_rebuild_restores_versions_from_project_json(proj: Path):
    create_output_project(proj, "novel")
    publish_version(proj, "novel", "1.0.0")
    publish_version(proj, "novel", "1.1.0")

    import shutil
    shutil.rmtree(registry_dir(proj))  # 删除整个注册表（分表与总表）

    rebuild_index(proj)
    rec = load_record(proj, "novel")
    assert rec is not None
    assert [v["version"] for v in rec["versions"]] == ["1.0.0", "1.1.0"]
    assert load_index(proj)["projects"]["novel"]["latest_version"] == "1.1.0"


# ── 6. 管理项目重新打开 ───────────────────────────────────────────────────────

def test_reopen_requires_reason(proj: Path, backlog: GoalBacklog):
    goal = _goal(backlog)
    mark_management_root(proj, goal.id)
    with pytest.raises(RegistryError):
        reopen_management_root(proj, goal.id, "   ", backlog)


def test_reopen_rejects_non_management_node(proj: Path, backlog: GoalBacklog):
    goal = _goal(backlog)
    with pytest.raises(RegistryError):
        reopen_management_root(proj, goal.id, "原因", backlog)


def test_reopen_active_management_rejected(proj: Path, backlog: GoalBacklog):
    goal = _goal(backlog)
    mark_management_root(proj, goal.id)
    with pytest.raises(RegistryError):
        reopen_management_root(proj, goal.id, "原因", backlog)


def test_reopen_completed_management_records_history(proj: Path, backlog: GoalBacklog):
    goal = _goal(backlog)
    mark_management_root(proj, goal.id, goal.title)
    backlog.set_status(goal.id, "completed", actor="test", reason="done")
    assert backlog.get(goal.id).status == "completed"

    result = reopen_management_root(proj, goal.id, "需求变更，继续迭代", backlog)
    assert result["status"] == "active" and result["from_status"] == "completed"
    assert backlog.get(goal.id).status == "active"
    history = load_index(proj)["management_roots"][goal.id]["history"]
    assert history[-1]["event"] == "reopened"
    assert history[-1]["reason"] == "需求变更，继续迭代"


def test_link_still_works_after_reopen(proj: Path, backlog: GoalBacklog):
    create_output_project(proj, "novel")
    goal = _goal(backlog)
    mark_management_root(proj, goal.id)
    backlog.set_status(goal.id, "completed", actor="test", reason="done")
    reopen_management_root(proj, goal.id, "继续", backlog)
    rec = link_management_output(proj, goal.id, "novel", "build")
    assert rec["owner_goal_id"] == goal.id
