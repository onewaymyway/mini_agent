"""P0 只读盘点测试（next_doc/management_output_project_model_plan.md 第 9 章 P0）。

保证：
  1. 盘点不修改 output_projects_root 下任何文件（逐文件哈希比对）；
  2. 盘点不调用 GoalBacklog.load()，goals.json 原样保留；
  3. 草稿只写入 .agent/output_registry/，且不在 output_projects_root 之下；
  4. 兼容现有目录：无 PROJECT.json、无 PROJECT_INFO.md 的旧项目仍被识别，
     且标记为 legacy / managed=false；
  5. 下划线开头的保留目录不被当作项目。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mini_agent.output_registry import build_inventory, write_inventory_draft
from mini_agent.output_registry.inventory import draft_path_for


def _snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            out[os.path.relpath(os.path.join(dirpath, d), root) + "/"] = "<dir>"
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


@pytest.fixture
def legacy_project(tmp_path: Path) -> Path:
    """构造一个带旧式产出目录的项目：模拟用户已有的 output_projects。"""
    proj = tmp_path / "proj"
    root = proj / "output_projects"

    # 有介绍文件、有产出的旧项目，被一个目标的 user_output_dir 引用
    alpha = root / "alpha_research"
    (alpha / "output").mkdir(parents=True)
    (alpha / "PROJECT_INFO.md").write_text("# alpha\n研究项目", encoding="utf-8")
    (alpha / "output" / "report.md").write_text("报告正文", encoding="utf-8")

    # 没有介绍文件的旧项目，没有任何目标引用（游离）
    beta = root / "beta_scratch"
    beta.mkdir(parents=True)
    (beta / "script.py").write_text("print('x')\n", encoding="utf-8")

    # 保留目录：不应被当作项目
    (root / "_registry").mkdir()
    (root / "_registry" / "index.json").write_text("{}", encoding="utf-8")

    # goals.json：一个指向 alpha，一个指向根之外，一个没有 user_output_dir
    goals = {
        "version": 1,
        "goals": [
            {"id": "g1", "level": "goal", "title": "调研英伟达", "status": "active",
             "user_output_dir": "output_projects/alpha_research"},
            {"id": "g2", "level": "goal", "title": "外部路径", "status": "active",
             "user_output_dir": "research/external"},
            {"id": "g3", "level": "goal", "title": "无产出", "status": "active"},
        ],
        "directions": [],
    }
    agent_dir = proj / ".agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "goals.json").write_text(json.dumps(goals, ensure_ascii=False), encoding="utf-8")

    # 会话产出目录（六处之一）
    (agent_dir / "sessions" / "s1" / "output").mkdir(parents=True)
    (agent_dir / "sessions" / "s1" / "output" / "tmp.md").write_text("x", encoding="utf-8")
    return proj


def test_inventory_does_not_touch_output_projects_root(legacy_project: Path):
    root = legacy_project / "output_projects"
    before = _snapshot(root)
    inv = build_inventory(legacy_project)
    write_inventory_draft(legacy_project, inv)
    after = _snapshot(root)
    assert before == after, "P0 盘点不得修改 output_projects_root 下的任何内容"


def test_goals_json_is_not_modified(legacy_project: Path):
    goals_path = legacy_project / ".agent" / "goals.json"
    raw_before = goals_path.read_bytes()
    build_inventory(legacy_project)
    assert goals_path.read_bytes() == raw_before


def test_draft_written_only_under_agent_and_outside_root(legacy_project: Path):
    draft = write_inventory_draft(legacy_project)
    assert draft == draft_path_for(legacy_project)
    assert draft.is_file()
    assert ".agent" in draft.parts
    root = (legacy_project / "output_projects").resolve()
    assert root not in draft.resolve().parents


def test_legacy_projects_are_identified_without_mutation(legacy_project: Path):
    inv = build_inventory(legacy_project)
    by_slug = {p["slug"]: p for p in inv["projects"]}
    assert set(by_slug) == {"alpha_research", "beta_scratch"}

    alpha = by_slug["alpha_research"]
    assert alpha["legacy"] is True
    assert alpha["managed"] is False
    assert alpha["in_registry"] is False
    assert alpha["has_project_info"] is True
    assert alpha["suggested_owner_goal_ids"] == ["g1"]
    assert alpha["suggested_status"] == "candidate_bound"

    beta = by_slug["beta_scratch"]
    assert beta["has_project_info"] is False
    assert beta["suggested_status"] == "floating"
    assert any("PROJECT_INFO.md" in n for n in beta["notes"])


def test_reserved_underscore_dirs_are_not_projects(legacy_project: Path):
    inv = build_inventory(legacy_project)
    slugs = {p["slug"] for p in inv["projects"]}
    assert "_registry" not in slugs
    assert [r["name"] for r in inv["reserved_dirs"]] == ["_registry"]


def test_goal_bindings_and_anomalies(legacy_project: Path):
    inv = build_inventory(legacy_project)
    bindings = {b["goal_id"]: b for b in inv["goal_bindings"]}
    assert bindings["g1"]["matched_slug"] == "alpha_research"
    assert bindings["g2"]["matched_slug"] is None
    assert bindings["g3"]["user_output_dir"] is None
    assert any("g2" in a and "不在 output_projects_root" in a for a in inv["anomalies"])


def test_six_output_locations_are_reported(legacy_project: Path):
    inv = build_inventory(legacy_project)
    keys = [loc["key"] for loc in inv["output_locations"]]
    assert keys == [
        "session_output",
        "adhoc_output",
        "workflow_output",
        "daemon_outputs",
        "goal_user_output",
        "output_projects_root",
    ]
    session = next(l for l in inv["output_locations"] if l["key"] == "session_output")
    assert session["exists"] is True and session["entry_count"] == 1


def test_missing_goals_and_root_are_not_errors(tmp_path: Path):
    inv = build_inventory(tmp_path)
    assert inv["projects"] == []
    assert inv["goal_bindings"] == []
    assert inv["summary"]["candidate_projects"] == 0


def test_corrupt_goals_json_is_reported_not_raised(tmp_path: Path):
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "goals.json").write_text("{not json", encoding="utf-8")
    inv = build_inventory(tmp_path)
    assert inv["goal_bindings"] == []
    assert any("goals.json 无法解析" in a for a in inv["anomalies"])
