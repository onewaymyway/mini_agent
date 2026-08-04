"""
hybrid_exec/_agent.py — 构造临时最小 Agent 的共享辅助函数

复用 workflow/agent_spawn.py::build_minimal_agent（与 python_step 的
ctx.run_agent_turn()、skill_agent 类型共用同一段"临时起一个最小 Agent"
逻辑，不重新实现一套），仅供 hybrid_exec 包内部的 AgentExplorer /
AgentRepairer / FallbackExecutor.agent_direct 使用。

sandbox 语义（对应 next_doc/hybrid_exec_design_plan.md §9 确认项 3）：
  TaskSpec.agent_fs_write_enabled=False（默认）→ sandbox=True
      → PermissionGuard 拦截 _RISKY_TOOLS（写文件等），Agent 只能只读探查。
  TaskSpec.agent_fs_write_enabled=True → sandbox=False → 允许写文件系统。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .runner import RunnerAppConfig
from .spec import TaskSpec


def run_agent_prompt(
    app_cfg: RunnerAppConfig,
    task: TaskSpec,
    prompt: str,
    *,
    max_turns: int = 8,
    session_label: str = "agent",
) -> str:
    """拉起一个临时最小 Agent 跑一次 prompt，返回最终文本回复。"""
    from mini_agent.workflow.agent_spawn import build_minimal_agent

    project_root = Path(app_cfg.project_root)
    session_dir = (
        project_root
        / ".agent"
        / "hybrid_exec"
        / "agent_sessions"
        / task.task_id
        / f"{session_label}_{int(time.time() * 1000)}"
    )

    agent = build_minimal_agent(
        project_root=project_root,
        verbose=False,
        sandbox=not task.agent_fs_write_enabled,
        model=app_cfg.model,
        llm_provider=app_cfg.llm_provider,
        llm_base_url=app_cfg.llm_base_url,
        api_key=app_cfg.api_key,
        debug_llm=app_cfg.debug_llm,
        debug_llm_console=app_cfg.debug_llm_console,
        max_turns=max_turns,
        session_dir=session_dir,
    )
    return agent.run_turn(prompt)
