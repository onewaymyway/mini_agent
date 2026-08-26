"""
tests/test_workflow_cli_headless.py — 阶段 2（headless 单次执行入口）验收测试

对应 next_doc/external_projects_workspace_plan.md 阶段 2 验收标准：
"该命令可以被 OS 原生 cron / Windows 计划任务直接调用，在 daemon 完全
没有启动的情况下产出正确结果"。

核实结论（见该文档阶段 2 变更记录）：`mini-agent workflow run <name>
--project/--workspace <path>`（`cli/commands/workflow_cmd.py::
run_workflow_cli`）已经是这个入口——它只调用 `load_config(project_root=
...)`，不初始化 HTTP API / SSE / kanban，前台执行分支是纯同步调用，跑完
即返回。这里用一个不依赖 LLM/网络的 `script` 类型 step 验证这条路径端到
端可用，并显式确认过程中不会触碰任何 daemon/HTTP 相关模块。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _save_echo_workflow(project_root: Path, name: str = "hello_headless") -> None:
    from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
    from mini_agent.workflow.store import WorkflowStore

    wf = WorkflowDef(
        name=name,
        steps=[
            WorkflowStep(
                id="say_hello",
                name="say_hello",
                prompt="say hello",
                type="script",
                script="echo hello-from-headless-workflow",
            ),
        ],
    )
    WorkflowStore(project_root).save(wf)


def test_extract_project_root_accepts_workspace_alias():
    from mini_agent.cli.app import _extract_project_root

    root1, rest1 = _extract_project_root(["run", "foo", "--project", "/tmp/a"])
    root2, rest2 = _extract_project_root(["run", "foo", "--workspace", "/tmp/a"])
    root3, rest3 = _extract_project_root(["run", "foo", "-w", "/tmp/a"])

    assert root1 == root2 == root3 == Path("/tmp/a")
    assert rest1 == rest2 == rest3 == ["run", "foo"]


def test_headless_workflow_run_executes_without_daemon(tmp_path):
    """
    `run_workflow_cli` 跑完一个纯 script step 的工作流，产出可读取的成功
    结果——全程只经过 load_config + WorkflowRunner.run，不需要网络、LLM
    api_key，也不需要任何 daemon 进程已经在运行。
    """
    project_root = tmp_path / "headless_project"
    project_root.mkdir()
    _save_echo_workflow(project_root)

    # 显式打开 script_step_enabled（默认关闭，避免任意 YAML 变成命令执行
    # 入口），通过 agent_config.json 写死，走与真实 cron 场景一致的
    # "配置文件驱动、无需交互式传参"路径。
    (project_root / ".agent").mkdir(exist_ok=True)
    import json
    (project_root / "agent_config.json").write_text(
        json.dumps({"workflow": {"script_step_enabled": True}}),
        encoding="utf-8",
    )

    daemon_modules_before = {
        m for m in sys.modules if m.startswith("mini_agent.api")
    }

    from mini_agent.cli.commands.workflow_cmd import run_workflow_cli

    exit_code = run_workflow_cli(["run", "hello_headless"], project_root)

    daemon_modules_after = {
        m for m in sys.modules if m.startswith("mini_agent.api")
    }

    assert exit_code == 0
    # headless 执行路径不应该拉起 daemon 的 HTTP/SSE/kanban 相关模块
    assert daemon_modules_after == daemon_modules_before

    # 产出可通过 WorkflowSession 读取，结果确实是这次同步执行留下的
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.workflow.session import WorkflowSession

    paths = AgentPaths(project_root=project_root)
    ids = paths.list_workflow_session_ids()
    assert len(ids) == 1
    session = WorkflowSession.load(paths, ids[0])
    assert session is not None
    assert session.workflow_name == "hello_headless"
    step_result = session.step_results.get("say_hello")
    assert step_result is not None
    assert step_result.status.value in ("done", "DONE") or str(step_result.status) in ("StepStatus.DONE", "done")


def test_headless_run_isolated_across_two_workspaces(tmp_path):
    """两个不同 --project/--workspace 根互不影响彼此的工作流定义与执行记录。"""
    import json
    from mini_agent.cli.commands.workflow_cmd import run_workflow_cli
    from mini_agent.storage.paths import AgentPaths

    root_a = tmp_path / "proj_a"
    root_b = tmp_path / "proj_b"
    root_a.mkdir()
    root_b.mkdir()

    for root in (root_a, root_b):
        _save_echo_workflow(root)
        (root / "agent_config.json").write_text(
            json.dumps({"workflow": {"script_step_enabled": True}}),
            encoding="utf-8",
        )

    assert run_workflow_cli(["run", "hello_headless"], root_a) == 0
    # root_b 从未执行过，验证它的执行记录目录确实是空的，不受 root_a 影响
    ids_b_before = AgentPaths(project_root=root_b).list_workflow_session_ids()
    assert ids_b_before == []

    assert run_workflow_cli(["run", "hello_headless"], root_b) == 0
    ids_a = AgentPaths(project_root=root_a).list_workflow_session_ids()
    ids_b = AgentPaths(project_root=root_b).list_workflow_session_ids()
    assert len(ids_a) == 1
    assert len(ids_b) == 1
    assert ids_a[0] != ids_b[0]
