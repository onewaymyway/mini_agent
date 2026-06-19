"""
tests/test_task_status_truncation.py — Stage 0.3 验证

对应 self_evolution_implementation_plan.md Stage 0.3：
  get_task_status 在真实截断发生时，返回 JSON 中应包含
  truncated=true 和 full_length=N，提示主 agent 可用 full=True 重新取。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.orchestration as ot
from mini_agent.orchestrator.task import Task, TaskRecord, TaskResult, TaskStatus
from mini_agent.orchestrator.task_manager import TaskManager


def make_cfg():
    from mini_agent.config import load_config
    cfg = load_config()
    cfg.api_key = "test"
    cfg.stream = False
    return cfg


def _make_manager_with_record(output: str) -> tuple[TaskManager, str]:
    """构造一个 TaskManager，里面塞入一个已完成、带指定长度输出的 TaskRecord。"""
    cfg = make_cfg()
    mgr = TaskManager(cfg, max_workers=1)
    task = Task(prompt="long output task")
    rec = TaskRecord(task=task)
    rec.status = TaskStatus.DONE
    rec.result = TaskResult(output=output, input_tokens=10, output_tokens=20)
    mgr._records[task.id] = rec
    return mgr, task.id


def test_truncated_flag_present_when_output_exceeds_limit(monkeypatch):
    long_output = "x" * 4000  # > 3000 字符
    mgr, tid = _make_manager_with_record(long_output)
    monkeypatch.setattr(ot, "_task_manager", mgr)

    result = ot.get_task_status(tid, full=False)
    data = json.loads(result)

    assert data["truncated"] is True
    assert data["full_length"] == 4000
    assert len(data["output"]) == 3000
    assert "hint" in data
    assert "full=True" in data["hint"]


def test_no_truncated_flag_when_output_within_limit(monkeypatch):
    short_output = "y" * 100  # 远小于 3000
    mgr, tid = _make_manager_with_record(short_output)
    monkeypatch.setattr(ot, "_task_manager", mgr)

    result = ot.get_task_status(tid, full=False)
    data = json.loads(result)

    assert "truncated" not in data
    assert "full_length" not in data
    assert data["output"] == short_output


def test_full_true_returns_complete_output_without_truncated_flag(monkeypatch):
    long_output = "z" * 4000
    mgr, tid = _make_manager_with_record(long_output)
    monkeypatch.setattr(ot, "_task_manager", mgr)

    result = ot.get_task_status(tid, full=True)
    data = json.loads(result)

    assert data["output"] == long_output
    assert len(data["output"]) == 4000
    # full=True 显式取了完整输出，不应再标记 truncated
    assert "truncated" not in data


def test_exact_boundary_length_not_flagged_truncated(monkeypatch):
    """正好 3000 字符（未超过）不应被标记为截断。"""
    boundary_output = "a" * 3000
    mgr, tid = _make_manager_with_record(boundary_output)
    monkeypatch.setattr(ot, "_task_manager", mgr)

    result = ot.get_task_status(tid, full=False)
    data = json.loads(result)

    assert "truncated" not in data
    assert len(data["output"]) == 3000
