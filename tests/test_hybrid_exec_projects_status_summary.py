"""
测试 next_doc/hybrid_exec_improvement_directions.md A3（最小改动路径）：
`mini-agent projects status <name>` 附带打印 hybrid_exec 用量摘要。
"""

from __future__ import annotations

from pathlib import Path

from mini_agent.cli.commands.projects_cmd import _hybrid_exec_summary_line
from mini_agent.hybrid_exec import TaskSpec, default_executor


def test_summary_line_when_never_used(tmp_path: Path):
    line = _hybrid_exec_summary_line(tmp_path)
    assert "未使用" in line


def test_summary_line_when_dir_exists_but_empty(tmp_path: Path):
    (tmp_path / ".agent" / "hybrid_exec").mkdir(parents=True)
    line = _hybrid_exec_summary_line(tmp_path)
    assert "暂无已归档任务" in line


def test_summary_line_after_running_a_task(tmp_path: Path):
    """先真跑一个（无脚本、无 LLM/Agent 可用、必然走到 fallback 失败）
    hybrid_exec 任务把目录结构造出来，再校验摘要行的形状——不追求这里
    的任务本身跑成功，只验证"目录存在 + 有 run 记录后"这条摘要路径能
    正常工作、不抛异常。"""
    executor = default_executor(project_root=tmp_path)
    executor.run(TaskSpec(task_id="demo_v1", description="demo", allow_tiers=()))

    line = _hybrid_exec_summary_line(tmp_path)
    assert line.startswith("hybrid_exec:")
    assert "个任务" in line


def test_summary_line_survives_broken_meta(tmp_path: Path, monkeypatch):
    """build_kanban_summary 内部探测失败时，摘要行应该降级为一句警告，
    不应该让异常从 _hybrid_exec_summary_line 里往外抛。"""
    (tmp_path / ".agent" / "hybrid_exec").mkdir(parents=True)

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟摘要读取失败")

    monkeypatch.setattr("mini_agent.hybrid_exec.build_kanban_summary", _boom)
    line = _hybrid_exec_summary_line(tmp_path)
    assert "摘要读取失败" in line
