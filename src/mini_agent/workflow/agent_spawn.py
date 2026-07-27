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
    session_dir: Optional[Any] = None,
) -> Any:
    """构造一个自动批准（auto_approve=True）、非流式输出的最小 Agent 实例，
    可选强制激活一个 skill（不走关键词触发判断）。返回值是 Agent 实例，
    调用方自己 agent.run_turn(prompt)。

    skill_loader 优先用调用方传入的（通常来自 workflow 本地资源包）；
    没有则退回 global_skills_dir 现建一个 SkillLoader。两者都没有且
    指定了 skill_name 时抛 ValueError（与原 SkillAgentStepExecutor 行为
    一致）。

    session_dir 不传时（默认）沿用 SessionManager 的默认行为，落到全局
    `.agent/sessions/`——这是历史遗留：这个"临时最小 Agent"以前只用来跑
    一次性 prompt，没考虑过数据归档。[数据聚合修复] 现在 skill_agent /
    python_step 的 ctx.run_agent_turn() 都是 workflow 的一个 step，产出的
    session 数据（history/traces/output 等）理应跟这次 workflow 执行绑在
    一起，而不是散落进跟其它任意会话混在一起的全局目录、事后很难对应回
    是哪次 workflow 跑出来的。调用方（runner._spawn_minimal_agent /
    py_step_runner._make_run_agent_turn）传入
    `.agent/workflow_sessions/<wf_session_id>/step_<step_id>/` 之类的路径，
    这里透传给 step_cfg.session.dir 即可，SessionManager 会在其下再建一层
    随机 session_id 子目录（与 workflow_step_agent_dir 的约定一致）。
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
    if session_dir:
        # AppConfig.session_dir 是只读 property（代理 self.session.dir），
        # 没有 setter；真正可写的字段是 step_cfg.session.dir（与
        # runner.py::_execute_with_main_agent 的写法一致）。
        step_cfg.session.dir = session_dir

    guard = PermissionGuard(auto_approve=True, sandbox=sandbox, project_root=project_root)
    agent = Agent(cfg=step_cfg, guard=guard, registry=get_default_registry(), skill_loader=skill_loader)
    if skill_name and skill_loader is not None:
        try:
            skill_loader.activate(skill_name)
        except Exception:
            pass
    return agent
