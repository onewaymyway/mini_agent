"""P1 属性测试：目标节点的 verifiable / executable / deliverable 属性。

对应 next_doc/management_output_project_model_plan.md 第 5.1 节与第 8.1 节。

覆盖：
  1. level 推导默认值（与 P1 特征测试锁定的行为一致）；
  2. 显式值覆盖推导值；
  3. executable 决定 is_structural（执行语义由属性决定）；
  4. 序列化：新键总是写出，缺失时读为 None（旧数据兼容）；
  5. 旧 goals.json 加载后再保存，旧字段不丢失。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


def _node(level: str, **kwargs) -> GoalNode:
    return GoalNode(id=f"{level}_x", level=level, title="t", source="user", status="active", **kwargs)


# ── 1. level 推导默认值 ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "level,verifiable,executable,deliverable",
    [
        ("ultimate", False, False, False),
        ("domain", False, False, False),
        ("stage", False, False, False),
        ("goal", True, True, False),
        ("objective", True, True, False),
    ],
)
def test_default_attributes_derive_from_level(level, verifiable, executable, deliverable):
    n = _node(level)
    assert n.verifiable is None and n.executable is None and n.deliverable is None
    assert n.effective_verifiable is verifiable
    assert n.effective_executable is executable
    assert n.effective_deliverable is deliverable


def test_unknown_level_falls_back_to_all_false():
    n = _node("weird_level")
    assert (n.effective_verifiable, n.effective_executable, n.effective_deliverable) == (False, False, False)
    assert n.is_structural is True


def test_attributes_dict_shape():
    n = _node("goal", recurring=True)
    assert n.attributes == {
        "verifiable": True,
        "executable": True,
        "recurring": True,
        "deliverable": False,
    }


# ── 2. 显式值覆盖推导值 ───────────────────────────────────────────────────────

def test_explicit_true_overrides_structural_default():
    stage = _node("stage", executable=True)
    assert stage.effective_executable is True
    assert stage.is_structural is False


def test_explicit_false_overrides_goal_default():
    goal = _node("goal", executable=False)
    assert goal.effective_executable is False
    assert goal.is_structural is True


def test_explicit_deliverable_and_verifiable_are_used():
    goal = _node("goal", deliverable=True, verifiable=False)
    assert goal.effective_deliverable is True
    assert goal.effective_verifiable is False


# ── 3. 执行语义由 executable 决定 ─────────────────────────────────────────────

def test_is_structural_is_exactly_not_executable():
    for level in ("ultimate", "domain", "stage", "goal", "objective"):
        for explicit in (None, True, False):
            n = _node(level, executable=explicit)
            assert n.is_structural is (not n.effective_executable)


# ── 4. 序列化 ─────────────────────────────────────────────────────────────────

def test_to_dict_always_emits_new_keys():
    d = _node("goal").to_dict()
    assert {"verifiable", "executable", "deliverable"} <= d.keys()
    assert d["verifiable"] is None and d["executable"] is None and d["deliverable"] is None


def test_from_dict_missing_keys_become_none():
    legacy = {"id": "g1", "level": "goal", "title": "旧", "source": "user", "status": "active"}
    n = GoalNode.from_dict(legacy)
    assert n.verifiable is None and n.executable is None and n.deliverable is None
    assert n.effective_executable is True


def test_explicit_values_round_trip():
    n = _node("goal", verifiable=False, executable=False, deliverable=True)
    again = GoalNode.from_dict(n.to_dict())
    assert (again.verifiable, again.executable, again.deliverable) == (False, False, True)


# ── 5. 旧 goals.json 兼容 ─────────────────────────────────────────────────────

def test_legacy_file_load_and_save_keeps_old_fields(tmp_path: Path):
    legacy = {
        "version": 1,
        "goals": [
            {"id": "g1", "level": "goal", "title": "旧目标", "source": "user",
             "status": "active", "parent_id": None, "children_ids": [], "priority": 5,
             "description": "保留我", "tags": ["a"]},
        ],
        "directions": [],
    }
    agent = tmp_path / ".agent"
    agent.mkdir()
    path = agent / "goals.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    b = GoalBacklog(AgentPaths(project_root=tmp_path))
    b.load()
    b.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    g = next(x for x in saved["goals"] if x["id"] == "g1")
    assert g["title"] == "旧目标"
    assert g["priority"] == 5
    assert g["description"] == "保留我"
    assert g["tags"] == ["a"]
    # 新键以 null 写出：推导行为不变
    assert g["verifiable"] is None and g["executable"] is None and g["deliverable"] is None
