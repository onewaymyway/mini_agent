"""
workflow/executors.py — Step 类型化执行器（workflow机制改进计划.md P5）

背景：改造前 runner._execute_step 用 `if step.role: ... else: ...` 隐式二选一
（主 Agent / 角色 Agent）。新增一种 step 类型（子工作流、外部脚本、人工输入、
直接调用工具）不应该要求改动 runner 核心循环——本模块把"怎么执行某种
类型的 step"抽成独立的 StepExecutor 子类，runner._dispatch_step() 只做
"按 step.effective_type 查表分发"，新增类型只需在这里加一个类 + 在
_EXECUTORS 里注册一行。

每个 Executor.execute(runner, step, prompt) 返回该 step 的输出文本（str），
调用方（runner._execute_step）在拿到文本后统一处理 evaluator 评分提取/
质检门判断——这部分逻辑与"用什么方式产生了这段输出"无关，继续留在
runner 里，不下沉到每个 Executor，避免重复。

各类型的语义与保护措施：
  agent        — 独立主 Agent 实例执行（原有行为，见 runner._execute_with_main_agent）
  role_agent   — 指定角色 Agent 执行（原有行为，见 runner._execute_with_role_agent）
  sub_workflow — 把另一个已保存的工作流当作一个 step 执行，递归深度受
                 cfg.workflow.max_sub_workflow_depth 保护，避免 A→B→A 循环
  tool_call    — 直接调用一个已注册工具（不启动整个 Agent 会话），属于
                 "高风险"类型：默认沿用 step.require_approval 语义，
                 cfg.workflow.tool_call_step_auto_approve=False（默认）时，
                 runner 会在 step 未显式声明 require_approval 时依然建议
                 走审批门（见 runner._effective_require_approval）
  human_input  — 阻塞等待外部调用 provide_workflow_step_input 工具送入文本，
                 复用 registry.ControlState 的等待模式（与审批门是两套独立信号）
  script       — 执行 step.script 指定的 shell 命令，默认关闭
                 （cfg.workflow.script_step_enabled=False），避免任意
                 workflow YAML 变成命令执行入口
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .schema import WorkflowStep

if TYPE_CHECKING:
    from .runner import WorkflowRunner


class StepExecutor:
    """
    单个 Step 类型的执行器基类，是 workflow 系统的公开扩展点
    （[P7-④1 workflow_mechanism_improvement_plan.md]）。

    外部插件（如 myplugins/ 下的模块）新增一种 step 类型时，继承本类实现
    execute()（必选）和 validate_step()（可选），再调用模块级
    register_step_executor(type_name, executor实例) 完成注册，不需要改动
    本包源码，也不需要改动 runner.py 的核心循环——runner._execute_step()
    只按 step.effective_type 查表分发。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        """执行该 step，返回输出文本。子类必须实现。"""
        raise NotImplementedError

    def validate_step(self, step: WorkflowStep) -> list[str]:
        """
        校验该 step 定义是否合法，返回错误信息列表（空列表=合法）。

        仅对通过 register_step_executor() 注册的自定义类型生效——内置的
        7 种类型的必填字段校验写死在 schema.py::WorkflowDef.validate() 里，
        不经过这个钩子。自定义类型若有专属必填字段（例如"调用外部 HTTP
        API 的 step 必须填 url"），在这里检查并返回错误文案。
        """
        return []


class AgentStepExecutor(StepExecutor):
    """type=agent（或未设置 type 且 role 为空）：独立主 Agent 实例执行。"""

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        return runner._execute_with_main_agent(step, prompt)


class RoleAgentStepExecutor(StepExecutor):
    """type=role_agent（或未设置 type 且 role 非空）：指定角色 Agent 执行。"""

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        return runner._execute_with_role_agent(step, prompt)


class SubWorkflowStepExecutor(StepExecutor):
    """type=sub_workflow：把 step.workflow_name 引用的另一个已保存工作流当作一个 step 执行。

    子工作流拿到的 inputs 是 `{"input": <本 step 已解析好占位符的 prompt>}`，
    子工作流内部的 step prompt 若要引用它，写 `{input}` 即可；子工作流的
    `final_output`（最后一个成功步骤的输出）作为本 step 的输出返回。

    深度保护：通过 runner._sub_workflow_depth 记录当前递归深度，超过
    `cfg.workflow.max_sub_workflow_depth` 时直接拒绝执行，防止
    A → B → A 这类循环引用导致无限递归/线程耗尽。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        from pathlib import Path
        from .store import WorkflowStore

        depth = getattr(runner, "_sub_workflow_depth", 0)
        wf_cfg = getattr(runner._cfg, "workflow", None)
        max_depth = int(getattr(wf_cfg, "max_sub_workflow_depth", 3))
        if depth >= max_depth:
            raise RuntimeError(
                f"sub_workflow 嵌套深度已达上限（max_sub_workflow_depth={max_depth}），"
                f"拒绝执行 step {step.id!r} 引用的工作流 {step.workflow_name!r}，"
                "请检查是否存在循环引用（A 引用 B，B 又引用 A 之类）"
            )

        store = WorkflowStore(Path(runner._cfg.project_root))
        sub_wf = store.load(step.workflow_name or "")
        if sub_wf is None:
            raise ValueError(f"sub_workflow 引用的工作流不存在：{step.workflow_name!r}")

        parent_wf_session = getattr(runner, "_current_wf_session", None)
        sub_session_id = None
        if parent_wf_session is not None:
            sub_session_id = f"{parent_wf_session.workflow_session_id}_sub_{step.id}"

        # 延迟导入避免模块级循环依赖（runner.py 也会导入本模块）
        from .runner import WorkflowRunner as _Runner
        sub_runner = _Runner(runner._cfg)
        sub_runner._sub_workflow_depth = depth + 1
        result = sub_runner.run(sub_wf, inputs={"input": prompt}, workflow_session_id=sub_session_id)
        return result.final_output or result.to_summary()


class ToolCallStepExecutor(StepExecutor):
    """type=tool_call：直接调用一个已注册工具，而不是启动一整个 Agent 会话。

    step.tool_args 为空时，把已解析占位符的 prompt 作为唯一实参，按工具
    函数签名的第一个参数名传入（适合"单参数字符串工具"场景，如
    web_search(query) 这类；参数更复杂时应显式填写 tool_args）。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        from mini_agent.tools import get_default_registry

        registry = get_default_registry()
        tool_def = registry.get(step.tool_name or "")
        if tool_def is None:
            raise ValueError(f"tool_call 引用的工具不存在：{step.tool_name!r}")

        tool_input = dict(step.tool_args or {})
        if not tool_input:
            try:
                import inspect
                sig = inspect.signature(tool_def.fn)
                params = [p for p in sig.parameters if p != "self"]
                if params:
                    tool_input = {params[0]: prompt}
            except (TypeError, ValueError):
                tool_input = {}

        result = registry.call(step.tool_name, tool_input)
        return result if isinstance(result, str) else str(result)


class HumanInputStepExecutor(StepExecutor):
    """type=human_input：阻塞等待外部调用 provide_workflow_step_input 工具送入文本。

    与人工审批门（require_approval）是两套独立机制：审批门是"允许/拒绝
    该 step 是否执行"，human_input 是"该 step 本身就是在向人类要一段
    文本输入"，返回值直接作为该 step 的 output，可被后续 step 用
    `{step_id.output}` 引用。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        import mini_agent.ui.renderer as R
        from . import registry as wf_registry

        control = getattr(runner, "_current_control", None)
        wf_session = getattr(runner, "_current_wf_session", None)
        paths = getattr(runner, "_current_paths", None)
        wf_cfg = getattr(runner._cfg, "workflow", None)
        poll_interval = float(getattr(wf_cfg, "approval_poll_interval_seconds", 3.0))
        wait_timeout = getattr(wf_cfg, "human_input_wait_timeout_seconds", 1800.0)

        prompt_to_show = step.input_prompt or prompt

        if control is None:
            # 没有 registry 上下文（如单测直接调用 _execute_step），无法真的
            # 等待外部输入，直接把展示文本原样返回，避免破坏现有测试假设。
            return prompt_to_show

        control.pending_input_step = step.id
        control.input_provided.clear()
        control.provided_input_text = ""
        if wf_session is not None and paths is not None:
            wf_session.append_event(paths, "human_input_requested", {
                "step_id": step.id, "prompt": prompt_to_show,
            })

        R.print_warning(
            f"[Workflow] ⌨️ 步骤 {step.id} 需要人工输入，等待 provide_workflow_step_input"
            f"（workflow_session_id={getattr(wf_session, 'workflow_session_id', '?')}）\n"
            f"提示：{prompt_to_show}"
        )

        waited = 0.0
        while True:
            if control.cancel_requested.is_set():
                raise RuntimeError("等待人工输入期间收到 cancel 信号")
            if control.input_provided.is_set():
                break
            if wait_timeout and waited >= wait_timeout:
                raise TimeoutError(f"步骤 {step.id} 等待人工输入超时（{wait_timeout}s）")
            time.sleep(poll_interval)
            waited += poll_interval

        control.pending_input_step = None
        text = control.provided_input_text
        if wf_session is not None and paths is not None:
            wf_session.append_event(paths, "human_input_provided", {"step_id": step.id})
        return text


class ScriptStepExecutor(StepExecutor):
    """type=script：执行 step.script 指定的 shell 命令。

    默认被 cfg.workflow.script_step_enabled=False 关闭——工作流 YAML 可能
    来自 LLM 生成或他人分享，把"执行任意 shell 命令"设为默认开启会引入
    明显的供应链风险；需要显式在 agent_config.json 里打开开关才能使用。
    超时优先取 step.timeout，否则取 cfg.workflow.script_step_timeout_seconds。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        wf_cfg = getattr(runner._cfg, "workflow", None)
        if not bool(getattr(wf_cfg, "script_step_enabled", False)):
            raise PermissionError(
                "script 类型 step 已被禁用（cfg.workflow.script_step_enabled=False）。"
                "如需启用，请在 agent_config.json 的 workflow 节里设置 "
                "\"script_step_enabled\": true 后重试。"
            )
        if not step.script:
            raise ValueError(f"步骤 {step.id!r} 是 script 类型但未指定 script 命令")

        timeout = step.timeout or float(getattr(wf_cfg, "script_step_timeout_seconds", 60.0))
        _is_windows = sys.platform == "win32"
        _popen_kwargs = {
            "shell": True,
            "cwd": str(runner._cfg.project_root),
            "capture_output": True,
            "text": True,
            "timeout": timeout,
        }
        if _is_windows:
            # Windows: use CREATE_NEW_PROCESS_GROUP for proper process tree termination
            _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Unix: use start_new_session for proper process group handling
            _popen_kwargs["start_new_session"] = True
        proc = subprocess.run(step.script, **_popen_kwargs)
        if proc.returncode != 0:
            raise RuntimeError(
                f"脚本执行失败（returncode={proc.returncode}）：\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        return proc.stdout


class SkillAgentStepExecutor(StepExecutor):
    """type=skill_agent（workflow_directory_mode_design.md 阶段3）：
    临时启动一个只强制挂载 step.skill_name 指定的 skill 的最小 Agent 执行
    prompt，不做关键词触发判断——用于"这一步明确要用某个 skill 的能力"，
    比依赖关键词命中更直接。查找顺序：先 workflow 本地资源包
    （文件夹模式 workflow 的 skills/ 目录），再全局 skills_dir。
    """

    def execute(self, runner: "WorkflowRunner", step: WorkflowStep, prompt: str) -> str:
        from mini_agent.config import load_config
        from mini_agent.agent import Agent
        from mini_agent.permissions import PermissionGuard
        from mini_agent.tools import get_default_registry
        from mini_agent.skills import SkillLoader

        if not step.skill_name:
            raise ValueError(f"步骤 {step.id!r} 是 skill_agent 类型但未指定 skill_name")

        bundle = getattr(runner, "_current_resource_bundle", None)
        skill = bundle.get_skill(step.skill_name) if bundle else None
        skill_loader = bundle.skill_loader if (bundle and skill is not None) else None

        if skill_loader is None:
            # 本地资源包里没有，退回全局 skills_dir 重新构造一个 loader。
            global_skills_dir = getattr(runner._cfg, "skills_dir", None)
            if not global_skills_dir:
                raise ValueError(
                    f"步骤 {step.id!r} 引用的 skill 不存在：{step.skill_name!r}"
                    "（未配置 skills_dir，也没有 workflow 本地 skills/ 目录）"
                )
            skill_loader = SkillLoader([Path(global_skills_dir)])
            if skill_loader._all.get(step.skill_name) is None:
                raise ValueError(f"步骤 {step.id!r} 引用的 skill 不存在：{step.skill_name!r}")

        step_cfg = load_config(
            project_root=runner._cfg.project_root,
            verbose=runner._cfg.verbose,
            sandbox=runner._cfg.sandbox,
            auto_approve=True,
            model=runner._effective_step_field(step, "model", None) or runner._cfg.model,
            llm_provider=runner._cfg.llm_provider,
            llm_base_url=runner._cfg.llm_base_url,
            debug_llm=getattr(runner._cfg, "debug_llm", False),
            debug_llm_console=getattr(runner._cfg, "debug_llm_console", False),
        )
        step_cfg.api_key = runner._cfg.api_key
        step_cfg.max_turns = runner._effective_step_field(step, "max_turns", 10)
        step_cfg.stream = False
        eff_timeout = runner._effective_step_field(step, "timeout", None)
        if eff_timeout:
            step_cfg.request_timeout = eff_timeout

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=runner._cfg.sandbox,
            project_root=runner._cfg.project_root,
        )
        agent = Agent(cfg=step_cfg, guard=guard, registry=get_default_registry(), skill_loader=skill_loader)
        # 强制激活指定 skill，不依赖关键词触发。
        try:
            skill_loader.activate(step.skill_name)
        except Exception:
            pass
        return agent.run_turn(prompt)


# ── 分发表 ────────────────────────────────────────────────────────────────────
# runner._dispatch_step() 按 step.effective_type 查表；新增类型只需在这里
# 加一行注册，不需要改动 runner 的核心循环。

_EXECUTORS: dict[str, StepExecutor] = {
    "agent": AgentStepExecutor(),
    "role_agent": RoleAgentStepExecutor(),
    "sub_workflow": SubWorkflowStepExecutor(),
    "tool_call": ToolCallStepExecutor(),
    "human_input": HumanInputStepExecutor(),
    "script": ScriptStepExecutor(),
    "skill_agent": SkillAgentStepExecutor(),
}


def get_executor(step_type: str) -> StepExecutor:
    executor = _EXECUTORS.get(step_type)
    if executor is None:
        raise ValueError(f"未知的 step 类型：{step_type!r}（可选：{list(_EXECUTORS)}）")
    return executor


def get_registered_types() -> tuple[str, ...]:
    """返回当前所有已注册的 step 类型（内置 7 种 + 插件注册的自定义类型）。"""
    return tuple(_EXECUTORS.keys())


def register_step_executor(type_name: str, executor: StepExecutor) -> None:
    """
    [P7-④1 workflow_mechanism_improvement_plan.md] 注册一个自定义 step
    Executor，供外部插件调用（地位类似 tools.py 的 @tool 装饰器）。

    典型用法（myplugins/my_http_step.py）：

        from mini_agent.workflow.executors import StepExecutor, register_step_executor

        class HttpStepExecutor(StepExecutor):
            def execute(self, runner, step, prompt):
                ...
                return response_text

            def validate_step(self, step):
                errs = []
                if not step.tool_args.get("url"):
                    errs.append(f"步骤 {step.id!r} 是 http 类型但未指定 url")
                return errs

        def register(cfg):
            register_step_executor("http", HttpStepExecutor())

    `register(cfg)` 由 mini_agent.plugins.discover_and_register_plugins()
    在启动阶段扫描 myplugins/*.py 时自动调用（见 plugins.py）。

    覆盖内置类型（"agent"/"role_agent"/... 这 7 个）会打印警告但仍然允许
    （便于测试环境替换实现），生产场景不建议覆盖内置类型。
    """
    if type_name in _EXECUTORS and type_name in (
        "agent", "role_agent", "sub_workflow", "tool_call", "human_input", "script", "skill_agent",
    ):
        import logging
        logging.getLogger(__name__).warning(
            "[workflow] register_step_executor 覆盖了内置 step 类型 %r，"
            "这通常只应在测试中发生", type_name,
        )
    _EXECUTORS[type_name] = executor
