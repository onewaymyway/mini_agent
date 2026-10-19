"""S5 测试：第二层目录差异检测、执行器钩子、巡检超时提示、配置默认值。

对应 next_doc/goal_output_project_auto_link_plan.md §5、§9、§12 与第 10 章 S5 退出条件：
  1. 漏调工具的执行也能产生待确认项（origin=auto_detect）；
  2. 只处理新增目录（§12-3）；已有目录被修改不产生提示；
  3. 与第一层工具并存时不重复提示（幂等 / 忽略 / 已确认语义复用 add_pending）；
  4. 检测异常不影响执行收尾（钩子全部 try/except）；
  5. 开关关闭时完全不做检测、不写账本；
  6. 待确认超时只报告，不清理，且去重键稳定（§12-1）；
  7. 检测只写 _registry/ 与账本，产出目录内容逐文件哈希不变。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_agent.config.models import OutputRegistryConfig
from mini_agent.output_registry import (
    ROLE_BUILD,
    ROLE_ITERATE,
    add_pending,
    link_management_output,
    list_pending,
    mark_management_root,
    reject_pending,
)
from mini_agent.output_registry.auto_detect import detect_new_project_links, snapshot_project_names
from mini_agent.output_registry.patrol import pending_link_findings, run_report
from mini_agent.output_registry.pending_links import ledger_path
from mini_agent.output_registry.registry import load_record
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _tree_hash(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "_registry"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


@pytest.fixture
def env(tmp_path: Path):
    proj = tmp_path / "proj"
    out_root = proj / "output_projects"
    (out_root / "legacy" / "output").mkdir(parents=True)
    (out_root / "legacy" / "output" / "a.md").write_text("old", encoding="utf-8")
    (out_root / "_registry_internal_dummy").mkdir()  # 下划线开头：应被忽略
    backlog = GoalBacklog(AgentPaths(project_root=proj))
    top = backlog.get_root_node()
    domain = backlog.add_node("domain", "工作", parent_id=top.id)
    stage = backlog.add_node("stage", "阶段", parent_id=domain.id)
    goal = backlog.add_node("goal", "做出产品", parent_id=stage.id)
    goal2 = backlog.add_node("goal", "另一个目标", parent_id=stage.id)
    obj = backlog.add_node("objective", "第1轮", parent_id=goal.id)
    return {"proj": proj, "out": out_root, "domain": domain, "goal": goal, "goal2": goal2, "obj": obj}


def _new_dir(env, name: str) -> None:
    (env["out"] / name / "output").mkdir(parents=True)
    (env["out"] / name / "output" / "x.md").write_text(name, encoding="utf-8")


# ── 1. 快照 ───────────────────────────────────────────────────────────────────

def test_snapshot_lists_top_level_dirs_only_and_skips_internal(env):
    (env["out"] / "file_not_dir.txt").write_text("x", encoding="utf-8")
    snap = snapshot_project_names(env["proj"])
    assert "legacy" in snap
    assert "_registry_internal_dummy" not in snap
    assert "file_not_dir.txt" not in snap


def test_snapshot_empty_when_root_missing(tmp_path):
    assert snapshot_project_names(tmp_path / "nowhere") == set()


# ── 2. 差异检测 ───────────────────────────────────────────────────────────────

def test_new_dir_becomes_auto_detect_pending(env):
    before = snapshot_project_names(env["proj"])
    _new_dir(env, "fresh")
    res = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    assert res == [{"slug": "fresh", "outcome": "created"}]
    item = list_pending(env["proj"], state="pending")[0]
    assert item["slug"] == "fresh" and item["origin"] == "auto_detect"
    assert item["management_id"] == env["goal"].id and item["goal_id"] == env["obj"].id
    assert item["role"] == ROLE_BUILD


def test_new_dir_is_registered_but_not_linked(env):
    before = snapshot_project_names(env["proj"])
    _new_dir(env, "fresh")
    detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    rec = load_record(env["proj"], "fresh")
    assert rec is not None and rec["links"] == [], "检测只登记，不建立正式关联"


def test_existing_dir_modification_is_not_reported(env):
    """§12-3：首版只处理新增目录，已有目录被本轮修改不产生提示。"""
    before = snapshot_project_names(env["proj"])
    (env["out"] / "legacy" / "output" / "a.md").write_text("changed", encoding="utf-8")
    res = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    assert res == [] and list_pending(env["proj"]) == []


def test_no_resolvable_management_root_skips(env):
    before = snapshot_project_names(env["proj"])
    _new_dir(env, "floaty")
    res = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["domain"].id)
    assert res == [] and list_pending(env["proj"]) == []


def test_invalid_directory_name_is_skipped(env):
    before = snapshot_project_names(env["proj"])
    (env["out"] / "bad name").mkdir()
    res = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    assert res == [] and list_pending(env["proj"]) == []


def test_repeat_detection_is_idempotent(env):
    before = snapshot_project_names(env["proj"])
    _new_dir(env, "fresh")
    detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    again = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    assert again == [{"slug": "fresh", "outcome": "existing"}]
    assert len(list_pending(env["proj"], state="pending")) == 1


def test_rejected_by_user_is_not_regenerated(env):
    before = snapshot_project_names(env["proj"])
    _new_dir(env, "fresh")
    detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    reject_pending(env["proj"], list_pending(env["proj"], state="pending")[0]["id"])
    again = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    assert again == [{"slug": "fresh", "outcome": "rejected"}]
    assert list_pending(env["proj"], state="pending") == []


def test_agent_tool_already_linked_same_goal_is_not_duplicated(env):
    """第一层（工具）已正式关联时，第二层不再产生待确认。"""
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    _new_dir(env, "fresh")
    link_management_output(env["proj"], env["goal"].id, "fresh", ROLE_BUILD)
    before = {"legacy"}  # 快照时 legacy 已存在；fresh 是本轮新增
    res = detect_new_project_links(env["proj"], before=before, trigger_node_id=env["obj"].id)
    assert res == [{"slug": "fresh", "outcome": "confirmed"}]
    assert list_pending(env["proj"], state="pending") == []


def test_role_becomes_iterate_when_other_management_builds(env):
    """legacy 已被 goal2 以 build 关联；本轮 goal 检测到 legacy 时角色应为 iterate。"""
    mark_management_root(env["proj"], env["goal2"].id, "另一个目标")
    link_management_output(env["proj"], env["goal2"].id, "legacy", ROLE_BUILD)
    res = detect_new_project_links(env["proj"], before=set(), trigger_node_id=env["obj"].id)
    assert {"slug": "legacy", "outcome": "created"} in res
    item = next(i for i in list_pending(env["proj"], state="pending") if i["slug"] == "legacy")
    assert item["role"] == ROLE_ITERATE


def test_detection_does_not_modify_project_contents(env):
    before_snap = snapshot_project_names(env["proj"])
    _new_dir(env, "fresh")
    tree_before = _tree_hash(env["out"])
    detect_new_project_links(env["proj"], before=before_snap, trigger_node_id=env["obj"].id)
    tree_after = _tree_hash(env["out"])
    # 只允许新增 fresh 目录下 PROJECT.json/PROJECT_INFO 等 _registry 之外的镜像文件之外的变化：
    # register_project 不写目录内容，因此 fresh 的已有文件哈希必须不变
    for rel, digest in tree_before.items():
        assert tree_after.get(rel) == digest


# ── 3. 执行器钩子 ─────────────────────────────────────────────────────────────

def _executor_stub(env, *, detect: bool):
    from mini_agent.evolution.objective_executor import ObjectiveExecutor
    ex = ObjectiveExecutor.__new__(ObjectiveExecutor)
    ex._cfg = SimpleNamespace(output_registry=OutputRegistryConfig(auto_link_detect_enabled=detect))
    ex._paths = AgentPaths(project_root=env["proj"])
    ex._goal_backlog = GoalBacklog(AgentPaths(project_root=env["proj"]))
    ex._goal_backlog.load()
    return ex


def _exec_stub(env, eid="exec_1"):
    return SimpleNamespace(execution_id=eid, objective_id=env["obj"].id)


def test_hook_disabled_records_nothing_and_detects_nothing(env):
    ex = _executor_stub(env, detect=False)
    ex._auto_link_snapshot(_exec_stub(env))
    assert "_auto_link_snapshots" not in ex.__dict__ or ex.__dict__["_auto_link_snapshots"] == {}
    _new_dir(env, "fresh")
    ex._auto_link_detect_finish(_exec_stub(env))
    assert list_pending(env["proj"]) == []


def test_hook_enabled_detects_on_finish(env):
    ex = _executor_stub(env, detect=True)
    ex._auto_link_snapshot(_exec_stub(env))
    _new_dir(env, "fresh")
    ex._auto_link_detect_finish(_exec_stub(env))
    assert [i["slug"] for i in list_pending(env["proj"], state="pending")] == ["fresh"]
    assert "exec_1" not in ex.__dict__["_auto_link_snapshots"], "收尾后快照必须清理"


def test_snapshot_taken_once_per_execution(env):
    ex = _executor_stub(env, detect=True)
    ex._auto_link_snapshot(_exec_stub(env))
    _new_dir(env, "after_first")
    ex._auto_link_snapshot(_exec_stub(env))  # 重试时不得覆盖首次快照
    ex._auto_link_detect_finish(_exec_stub(env))
    assert "after_first" in [i["slug"] for i in list_pending(env["proj"], state="pending")]


def test_cancel_discards_snapshot_without_detecting(env):
    ex = _executor_stub(env, detect=True)
    ex._auto_link_snapshot(_exec_stub(env))
    _new_dir(env, "fresh")
    ex._auto_link_detect_finish(_exec_stub(env), detect=False)
    assert list_pending(env["proj"]) == []
    assert ex.__dict__["_auto_link_snapshots"] == {}


def test_detection_exception_is_swallowed(env, monkeypatch):
    import mini_agent.output_registry.auto_detect as ad
    monkeypatch.setattr(ad, "detect_new_project_links", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ex = _executor_stub(env, detect=True)
    ex._auto_link_snapshot(_exec_stub(env))
    ex._auto_link_detect_finish(_exec_stub(env))  # 不得抛出


def test_snapshot_exception_is_swallowed(env, monkeypatch):
    import mini_agent.output_registry.auto_detect as ad
    monkeypatch.setattr(ad, "snapshot_project_names", lambda *a, **k: (_ for _ in ()).throw(OSError("io")))
    ex = _executor_stub(env, detect=True)
    ex._auto_link_snapshot(_exec_stub(env))  # 不得抛出


# ── 4. 巡检：待确认超时提示 ───────────────────────────────────────────────────

def _age_pending(root: Path, days: float) -> None:
    path = ledger_path(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["items"]:
        item["created_at"] = time.time() - days * 86400
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_stale_pending_produces_finding_fresh_does_not(env):
    add_pending(env["proj"], slug="legacy", goal_id=env["obj"].id, management_id=env["goal"].id,
                role=ROLE_BUILD, origin="agent_tool", valid_goal_ids={env["goal"].id, env["obj"].id})
    assert pending_link_findings(env["proj"], 7.0) == []
    _age_pending(env["proj"], 10)
    found = pending_link_findings(env["proj"], 7.0)
    assert len(found) == 1 and found[0]["kind"] == "pending_link_stale"


def test_stale_finding_detail_is_stable_across_runs(env):
    add_pending(env["proj"], slug="legacy", goal_id=env["obj"].id, management_id=env["goal"].id,
                role=ROLE_BUILD, origin="agent_tool", valid_goal_ids={env["goal"].id, env["obj"].id})
    _age_pending(env["proj"], 10)
    a = pending_link_findings(env["proj"], 7.0)[0]["detail"]
    _age_pending(env["proj"], 14)
    b = pending_link_findings(env["proj"], 7.0)[0]["detail"]
    assert a == b, "去重键必须稳定，否则每次巡检都会重复推送"


def test_patrol_report_includes_stale_finding_and_never_cleans(env):
    add_pending(env["proj"], slug="legacy", goal_id=env["obj"].id, management_id=env["goal"].id,
                role=ROLE_BUILD, origin="agent_tool", valid_goal_ids={env["goal"].id, env["obj"].id})
    _age_pending(env["proj"], 10)
    report = run_report(env["proj"], notify=False)
    assert any(f["kind"] == "pending_link_stale" for f in report["findings"])
    assert len(list_pending(env["proj"], state="pending")) == 1, "巡检只报告，不清理待确认"


# ── 5. 配置默认值 ─────────────────────────────────────────────────────────────

def test_config_defaults_and_shipped_values():
    defaults = OutputRegistryConfig()
    assert defaults.auto_link_detect_enabled is False
    assert defaults.pending_link_stale_days == 7.0
    shipped = json.loads((Path(__file__).resolve().parents[1] / "agent_config.json").read_text(encoding="utf-8"))
    assert shipped["output_registry"]["auto_link_detect_enabled"] is False
    assert shipped["output_registry"]["pending_link_stale_days"] == 7
