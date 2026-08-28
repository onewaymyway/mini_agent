"""
测试 next_doc/hybrid_exec_improvement_directions.md A1/A2/B1 三条改进：
  - A1: Workspace 补齐 hybrid_exec 相关路径属性 + default_executor(workspace=...)
  - A2: try_hybrid_exec() 检测不到环境时降级为 None
  - B1: mini-agent hybrid-exec scaffold 子命令 / scaffold.render_scaffold()
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mini_agent.hybrid_exec import TaskSpec, default_executor
from mini_agent.hybrid_exec.optional import try_hybrid_exec
from mini_agent.hybrid_exec.scaffold import CARRIER_CHOICES, default_output_filename, render_scaffold
from mini_agent.workspace import Workspace


# ── A1 ──────────────────────────────────────────────────────────────────


def test_workspace_hybrid_exec_paths(tmp_path: Path):
    ws = Workspace(root=tmp_path)
    assert ws.hybrid_exec_scripts_dir == tmp_path / ".agent" / "hybrid_exec" / "scripts"
    assert ws.hybrid_exec_runs_dir == tmp_path / ".agent" / "hybrid_exec" / "runs"
    assert ws.hybrid_exec_playbooks_dir == tmp_path / ".agent" / "hybrid_exec" / "playbooks"


def test_default_executor_workspace_and_project_root_equivalent(tmp_path: Path):
    """传 workspace= 和传等价的 project_root= 应该派生出完全相同的仓库/运行
    记录目录——两条路径在数值上完全相同，只是显式接口不同（A1 doc 的表述）。"""
    ws = Workspace(root=tmp_path)
    ex_from_ws = default_executor(workspace=ws)
    ex_from_root = default_executor(project_root=tmp_path)
    assert ex_from_ws.repo.base_dir == ex_from_root.repo.base_dir == ws.hybrid_exec_scripts_dir
    assert ex_from_ws.run_recorder.base_dir == ex_from_root.run_recorder.base_dir == ws.hybrid_exec_runs_dir


def test_default_executor_requires_project_root_or_workspace():
    with pytest.raises(ValueError):
        default_executor()


def test_default_executor_workspace_takes_priority(tmp_path: Path):
    """workspace 和 project_root 都传时，workspace 优先。"""
    ws_dir = tmp_path / "ws_root"
    other_dir = tmp_path / "other_root"
    ws = Workspace(root=ws_dir)
    executor = default_executor(project_root=other_dir, workspace=ws)
    assert executor.repo.base_dir == ws.hybrid_exec_scripts_dir
    assert other_dir not in executor.repo.base_dir.parents


# ── A2 ──────────────────────────────────────────────────────────────────


def test_try_hybrid_exec_requires_some_locator():
    with pytest.raises(ValueError):
        try_hybrid_exec(TaskSpec(task_id="x", description="x"))


def test_try_hybrid_exec_degrades_to_none_on_bad_environment(tmp_path: Path, monkeypatch):
    """default_executor() 构造阶段抛异常时，try_hybrid_exec() 应该吞掉异常
    返回 None，而不是让异常继续往外传播（模拟"环境不可用"场景）。"""
    import mini_agent.hybrid_exec.optional as optional_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟环境不可用：provider 配置缺失")

    monkeypatch.setattr("mini_agent.hybrid_exec.executor.default_executor", _boom)
    result = try_hybrid_exec(
        TaskSpec(task_id="x", description="x"),
        project_root=tmp_path,
    )
    assert result is None


def test_try_hybrid_exec_returns_result_when_executor_provided(tmp_path: Path):
    """传入现成的 executor 时应该直接跑并原样返回 ExecutionResult（成功/
    失败都不应该被吞成 None——只有"构造/运行阶段本身出问题"才吞）。"""
    executor = default_executor(project_root=tmp_path)
    task = TaskSpec(task_id="noop_task", description="什么都不做的任务", allow_tiers=())
    result = try_hybrid_exec(task, executor=executor)
    assert result is not None
    assert result.ok is False  # allow_tiers 为空，脚本/LLM/Agent 都没跑，fallback 也拿不到结果


# ── B1 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("carrier", CARRIER_CHOICES)
def test_render_scaffold_produces_nonempty_text_with_task_id(carrier):
    code = render_scaffold(carrier, "my_task_v1", "一句话描述")
    assert "my_task_v1" in code
    assert len(code) > 0


def test_render_scaffold_unknown_carrier_raises():
    with pytest.raises(ValueError):
        render_scaffold("not-a-real-carrier", "t", "d")


def test_render_scaffold_script_compiles():
    code = render_scaffold("script", "extract_entities_v1", "抽取实体")
    compile(code, "<scaffold-script>", "exec")


def test_render_scaffold_entrypoint_compiles():
    code = render_scaffold("entrypoint", "fix_scraper_v1", "修复抓取脚本")
    compile(code, "<scaffold-entrypoint>", "exec")


def test_default_output_filename_per_carrier():
    assert default_output_filename("script", "t1") == "t1_hybrid_exec.py"
    assert default_output_filename("workflow-step", "t1") == "t1_hybrid_step.yaml"
    assert default_output_filename("entrypoint", "t1") == "t1.py"


def test_cli_scaffold_writes_file(tmp_path: Path):
    out = tmp_path / "generated.py"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from mini_agent.cli.app import main; sys.exit(main())",
            "hybrid-exec",
            "scaffold",
            "demo_task_v1",
            "--carrier",
            "script",
            "--desc",
            "演示任务",
            "--output",
            str(out),
            "--project",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "demo_task_v1" in content
    assert "自检通过" in proc.stdout


def test_cli_scaffold_refuses_overwrite_without_force(tmp_path: Path):
    out = tmp_path / "generated.py"
    out.write_text("existing content", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from mini_agent.cli.app import main; sys.exit(main())",
            "hybrid-exec",
            "scaffold",
            "demo_task_v1",
            "--carrier",
            "script",
            "--output",
            str(out),
            "--project",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert out.read_text(encoding="utf-8") == "existing content"
    assert "未覆盖" in proc.stdout
