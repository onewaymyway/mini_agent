"""
tests/test_task_manifest_and_plan_snapshot.py — Stage 0.2 验证

对应 self_evolution_implementation_plan.md Stage 0.2：
  - AgentPaths 新增 session_plan_snapshot(sid) / task_manifest(sid, tid)
  - TaskRecord 写入 manifest.json（创建时写初始版本，结束时补写 outcome）
  - ExecutionPlan 在每次状态变更时同步写 plan_snapshot.json，并支持恢复
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.orchestrator.task import Task, TaskRecord, TaskResult, TaskStatus
from mini_agent.orchestrator.plan import (
    ExecutionPlan, PlanTask, PlanTaskStatus,
    get_plan, set_plan, clear_plan, bind_plan_session, try_restore_plan,
)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_plan_state():
    """每个测试前后重置模块级 plan 单例，避免测试间状态泄漏。"""
    clear_plan()
    bind_plan_session(None)
    yield
    clear_plan()
    bind_plan_session(None)


# ── AgentPaths 新增路径方法 ──────────────────────────────────────────────────

def test_agent_paths_new_methods(project_root):
    paths = AgentPaths(project_root=project_root)
    sid, tid = "sess_abc", "task_xyz"

    snap = paths.session_plan_snapshot(sid)
    assert snap == paths.session_dir(sid) / "plan_snapshot.json"

    manifest = paths.task_manifest(sid, tid)
    assert manifest == paths.task_dir(sid, tid) / "manifest.json"


# ── task_manifest.json ───────────────────────────────────────────────────────

def test_manifest_initial_write(project_root):
    paths = AgentPaths(project_root=project_root)
    sid = "sess1"
    task = Task(prompt="Fix token budget bug", goal="修复 token 预算计算溢出问题",
                acceptance_criteria=["所有现有单测通过"])
    rec = TaskRecord(task=task)
    manifest_path = paths.task_manifest(sid, rec.task_id)
    rec.bind_manifest_path(manifest_path)
    rec.write_manifest()

    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["id"] == task.id
    assert data["goal"] == "修复 token 预算计算溢出问题"
    assert data["acceptance_criteria"] == ["所有现有单测通过"]
    assert data["initiator"] == "agent"
    assert data["outcome"] is None  # 未结束，无 outcome


def test_manifest_progress_update_appends_decision_log(project_root):
    paths = AgentPaths(project_root=project_root)
    sid = "sess1"
    task = Task(prompt="Fix bug")
    rec = TaskRecord(task=task)
    rec.bind_manifest_path(paths.task_manifest(sid, rec.task_id))
    rec.write_manifest()

    rec.update_progress(
        current_step="写新测试",
        steps_done=["定位根因", "修改计算逻辑"],
        steps_remaining=["写新测试", "跑测试套件"],
        note="选择修改 _calc_budget() 而非 _trim_history()",
    )

    data = json.loads(rec._manifest_path.read_text(encoding="utf-8"))
    assert data["progress"]["current_step"] == "写新测试"
    assert data["progress"]["steps_done"] == ["定位根因", "修改计算逻辑"]
    assert data["progress"]["steps_remaining"] == ["写新测试", "跑测试套件"]
    assert len(data["decision_log"]) == 1
    assert data["decision_log"][0]["decision"] == "选择修改 _calc_budget() 而非 _trim_history()"


def test_manifest_outcome_written_on_done(project_root):
    paths = AgentPaths(project_root=project_root)
    sid = "sess1"
    task = Task(prompt="Fix bug")
    rec = TaskRecord(task=task)
    rec.bind_manifest_path(paths.task_manifest(sid, rec.task_id))
    rec.write_manifest()

    rec.status = TaskStatus.DONE
    rec.result = TaskResult(output="修复完成", input_tokens=100, output_tokens=50)
    rec.unresolved = ["还有一个 edge case 未覆盖"]
    rec.write_manifest()

    data = json.loads(rec._manifest_path.read_text(encoding="utf-8"))
    assert data["outcome"]["status"] == "done"
    assert data["outcome"]["unresolved"] == ["还有一个 edge case 未覆盖"]
    assert data["outcome"]["token_cost"] == {"input": 100, "output": 50}


def test_manifest_unbound_path_is_noop(project_root):
    """未绑定路径时 write_manifest 静默跳过，不抛异常。"""
    task = Task(prompt="Fix bug")
    rec = TaskRecord(task=task)
    result = rec.write_manifest()
    assert result is None


def test_sub_agent_binds_and_writes_initial_manifest(project_root, monkeypatch):
    """验证 SubAgent.__init__ 在有 session_id 时会立即写一份初始 manifest.json。"""
    from mini_agent.orchestrator.sub_agent import SubAgent
    from mini_agent.config import AppConfig

    cfg = AppConfig(project_root=project_root)
    task = Task(prompt="Some task")
    rec = TaskRecord(task=task)
    sid = "sess_sub1"

    sub = SubAgent(record=rec, base_cfg=cfg, session_id=sid)
    manifest_path = AgentPaths(project_root).task_manifest(sid, rec.task_id)
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["id"] == task.id


# ── plan_snapshot.json ───────────────────────────────────────────────────────

def test_plan_snapshot_written_on_state_change(project_root):
    paths = AgentPaths(project_root=project_root)
    sid = "sess_plan1"
    snap_path = paths.session_plan_snapshot(sid)
    bind_plan_session(snap_path)

    plan = ExecutionPlan(goal="完成 Phase A 基础设施清债")
    plan.add(PlanTask(id="t1", title="history 条目加 _type 字段"))
    plan.add(PlanTask(id="t2", title="SubAgent 输出去截断"))
    set_plan(plan)

    assert snap_path.exists()
    plan.start("t1")
    plan.complete("t1", result="已完成，见 commit abc123")

    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data["goal"] == "完成 Phase A 基础设施清债"
    t1 = next(t for t in data["tasks"] if t["id"] == "t1")
    assert t1["status"] == "done"
    assert t1["result"] == "已完成，见 commit abc123"


def test_plan_snapshot_restore_recovers_done_and_pending(project_root):
    paths = AgentPaths(project_root=project_root)
    sid = "sess_plan2"
    snap_path = paths.session_plan_snapshot(sid)
    bind_plan_session(snap_path)

    plan = ExecutionPlan(goal="目标 X")
    plan.add(PlanTask(id="t1", title="步骤一"))
    plan.add(PlanTask(id="t2", title="步骤二"))
    plan.add(PlanTask(id="t3", title="步骤三"))
    set_plan(plan)
    plan.start("t1")
    plan.complete("t1", result="完成")
    plan.start("t2")  # 模拟 session 在 t2 执行中崩溃

    # 模拟新 session：先清空内存态
    clear_plan()
    bind_plan_session(None)
    assert get_plan() is None

    # 重启后尝试恢复
    ok = try_restore_plan(snap_path)
    assert ok is True

    restored = get_plan()
    assert restored.goal == "目标 X"
    t1 = restored.get("t1")
    t2 = restored.get("t2")
    t3 = restored.get("t3")
    assert t1.status == PlanTaskStatus.DONE
    assert t1.result == "完成"
    assert t2.status == PlanTaskStatus.RUNNING  # 中断时的状态被忠实保留
    assert t3.status == PlanTaskStatus.PENDING


def test_plan_snapshot_restore_missing_file_returns_false(project_root):
    paths = AgentPaths(project_root=project_root)
    snap_path = paths.session_plan_snapshot("sess_nonexistent")
    ok = try_restore_plan(snap_path)
    assert ok is False
    assert get_plan() is None


def test_set_plan_auto_binds_to_current_session(project_root):
    """create_plan 工具内部调用 set_plan 时，应自动绑定到当前 session 的快照路径
    （无需 tools/plan.py 显式知道 session_id）。"""
    paths = AgentPaths(project_root=project_root)
    sid = "sess_auto"
    snap_path = paths.session_plan_snapshot(sid)
    bind_plan_session(snap_path)

    plan = ExecutionPlan(goal="自动绑定测试")
    set_plan(plan)  # 模拟 tools/plan.py 的 create_plan() 内部行为

    assert snap_path.exists()
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data["goal"] == "自动绑定测试"
