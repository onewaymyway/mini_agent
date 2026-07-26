"""
workflow/agent_spawn.py — "临时起一个最小 Agent 执行一次 prompt" 的共享逻辑
（next_doc/workflow_python_step_and_zhihu_publish_plan.md §B3）

背景：这段逻辑原来只在 executors.py::SkillAgentStepExecutor.execute() 里
写了一份（构造 step_cfg → PermissionGuard → Agent → 激活 skill →
run_turn）。新增 python_step 的 ctx.run_agent_turn() 需要同样的能力——
且 python_step 在独立子进程里执行，拿不到 WorkflowRunner 实例，只能拿到
一份基础配置——所以把"构造最小 Agent"这部分抽成不依赖 runner 对象的
纯函数，runner.py::WorkflowRunner._spawn_minimal_agent 和
py_step_runner.py 都调用它，避免两处实现分叉。
"""

from __future__ import annotations

from typing import Any, Optional


def build_minimal_agent(
    *,
    project_root: Any,
    verbose: bool,
    sandbox: bool,
    model: Optional[str],
    llm_provider: Any,
    llm_base_url: Optional[str],
    api_key: Optional[str],
    debug_llm: bool = False,
    debug_llm_console: bool = False,
    max_turns: int = 10,
    timeout: Optional[float] = None,
    skill_name: Optional[str] = None,
    skill_loader: Optional[Any] = None,
    global_skills_dir: Optional[Any] = None,
) -> Any:
    """构造一个自动批准（auto_approve=True）、非流式输出的最小 Agent 实例，
    可选强制激活一个 skill（不走关键词触发判断）。返回值是 Agent 实例，
    调用方自己 agent.run_turn(prompt)。

    skill_loader 优先用调用方传入的（通常来自 workflow 本地资源包）；
    没有则退回 global_skills_dir 现建一个 SkillLoader。两者都没有且
    指定了 skill_name 时抛 ValueError（与原 SkillAgentStepExecutor 行为
    一致）。
    """
    from mini_agent.config import load_config
    from mini_agent.agent import Agent
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry
    from mini_agent.skills import SkillLoader

    if skill_name and skill_loader is None:
        if not global_skills_dir:
            raise ValueError(
                f"引用的 skill 不存在：{skill_name!r}（未配置 skills_dir，"
                "也没有 workflow 本地 skills/ 目录）"
            )
        skill_loader = SkillLoader([global_skills_dir])
        if skill_loader._all.get(skill_name) is None:
            raise ValueError(f"引用的 skill 不存在：{skill_name!r}")

    step_cfg = load_config(
        project_root=project_root,
        verbose=verbose,
        sandbox=sandbox,
        auto_approve=True,
        model=model,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        debug_llm=debug_llm,
        debug_llm_console=debug_llm_console,
    )
    step_cfg.api_key = api_key
    step_cfg.max_turns = max_turns
    step_cfg.stream = False
    if timeout:
        step_cfg.request_timeout = timeout

    guard = PermissionGuard(auto_approve=True, sandbox=sandbox, project_root=project_root)
    agent = Agent(cfg=step_cfg, guard=guard, registry=get_default_registry(), skill_loader=skill_loader)
    if skill_name and skill_loader is not None:
        try:
            skill_loader.activate(skill_name)
        except Exception:
            pass
    return agent
