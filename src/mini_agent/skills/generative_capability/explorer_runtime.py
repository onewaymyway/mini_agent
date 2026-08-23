"""
explorer_runtime.py
====================
Generative-Capability 引擎的探索子agent运行时（阶段三）。

对应文档: next_doc/generative-capability-skill-plan.md 第 6 节 explore() /
          第 8 节安全边界 / 实施记录阶段三。

设计要点:
  - 探索子agent运行在与主对话隔离的独立 context 中：这里只接受
    request/intent_schema/explorer_config 三样输入，不携带主对话历史。
  - 工具白名单强制：模型只能调用 capability.yaml -> explorer.tool_allowlist
    中列出的工具名；引擎侧对模型试图调用的工具名做二次校验，白名单之外
    一律拒绝执行并作为一次失败的工具调用反馈给模型（不静默忽略、不越权执行）。
  - 步数/时间预算硬上限：由调用方通过 explorer_config 传入
    max_steps / max_seconds，超出后直接终止并判定失败，不允许无限重试。
  - 真正执行工具的是调用方注入的 tool_executor（因为具体的浏览器操作原语
    属于运行时环境能力，不属于本引擎职责范围），本文件只负责"决策循环"：
    调用 LLM 决定下一步调什么工具 / 何时结束，并记录 trace 供 distill 使用。
  - 不自我认定成功：探索循环只有当模型显式调用 `finish` 工具并且其提交的
    数据通过 intent_schema 校验后，才视为成功；模型可随时调用
    `report_failure` 如实报告失败原因（如遇到验证码/登录墙）。

阶段九改造说明（对应 next_doc/generative-capability-skill-plan.md 实施记录阶段九）:
  - 此前本文件自行用 urllib 拼 Anthropic Messages API 请求驱动决策循环，是
    整个引擎里唯一一处没有走框架统一 LLM 调用基础设施
    （`llm/service.py::LLMHelper`）的地方，导致固定写死 provider=anthropic、
    不跟随 /model 切换、不复用 LLMClientPool 的多 key/fallback 与
    RetryPolicy。现在改为调用方传入的 `llm_helper`（通常是
    `Agent.llm_helper`）驱动，通过 `LLMHelper.chat()` 发起带工具的多轮调用。
  - 消息历史沿用与 `history_manager.py::HistoryManager` 完全相同的内部消息
    约定（assistant 消息的 content 是 `[{"type":"text",...},
    {"type":"tool_use","id","name","input"}]` 列表；工具结果消息是
    `[{"type":"tool_result","tool_use_id","content"}]` 列表），这套约定是
    provider 无关的——各 provider 的 client 内部各自负责转换成自己的 wire
    格式，因此这里天然适配任何已接入的 provider，不只是 Anthropic。

阶段二十改造说明（对应 next_doc/generative_capability_explorer_rearch_plan.md，
本文件"阶段一 —— explore() 切换到 SubAgent 驱动"的落地）:
  - `build_llm_explorer()`（手写决策循环，只认白名单原语）标记为**遗留实现**，
    继续保留供已有调用方/测试兼容，但不再是推荐路径。
  - 新增 `build_subagent_explorer()`：不再手写 messages 历史/工具 schema/步数
    计时，而是构造一个真实 `orchestrator.sub_agent.SubAgent`（完整
    `Agent` + 系统里全部已注册工具：bash/python/文件读写等），驱动它跑完
    一次真实 `agent.run_turn()`。隔离性（独立 context/session）、安全边界
    （`PermissionGuard`+`sandbox`）、预算（`task.max_turns`）全部复用
    `SubAgent`/`Task` 已有基础设施，不再自造 `max_seconds`/`stop_reason`
    计时循环。
  - `finish`/`report_failure` 契约保留，但改为在探索用的 `SubAgent` 的
    `Agent.registry` 上以真实工具的形式动态注册（而不是手写循环里的两个
    特判分支）；`finish` 新增可选 `script_source` 字段（阶段二铺垫，见
    `distiller.py` 对应改造）。
  - **领域声明的底层原语桥接**：`browser-site-scraper` 等 skill 在
    `capability.yaml -> explorer.base_tools` / `explorer/tool_allowlist.json`
    里声明的 `browser_navigate` 等工具名，历史上是通过调用方注入的
    `tool_executor(name, input) -> dict` 分发（`real_tools.py` +
    各静态 skill 自带的 `impl/tools_impl.py`），并不是 `Agent` 自身注册表里
    的工具（`Agent` 内置的是 bash/python/文件读写等通用工具，没有
    `browser_navigate` 这个名字）。为了不丢失这批已接入的真实底层能力，
    `build_subagent_explorer()` 会把这些领域声明的工具名，各自包一层桥接
    `ToolDef`（`fn` 内部转发给 `tool_executor`），动态注册到探索用
    `SubAgent` 的 `Agent.registry` 上——探索子agent因此同时拥有"系统通用
    工具"和"领域底层原语"两类工具，不再被后者反向限制上限（对应方案文档
    第 1 节问题 1）。
  - `tool_allowlist.json`/`capability.yaml -> explorer.tool_allowlist` 两种
    历史写法（`{"allowed_tools":[...]}` 或 `{"tools":[{"name":...}]}`）都继续
    兼容读取；新增 `explorer.allowed_tools`（内联列表，直接写在
    capability.yaml 里，不必再单开一个 json 文件）与 `explorer.
    preferred_primitives`（蒸馏提示，非限制，见方案文档 3.2 节）两个可选
    字段，均为非破坏性新增。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from mini_agent.llm.service import LLMHelper

FINISH_TOOL = "finish"
REPORT_FAILURE_TOOL = "report_failure"


@dataclass
class ExploreStep:
    tool: str
    input: dict
    output: Any = None
    error: Optional[str] = None


@dataclass
class ExploreTrace:
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    steps: list[ExploreStep] = field(default_factory=list)
    stop_reason: str = ""  # "finished" | "reported_failure" | "step_budget" | "time_budget" | "llm_error"
    # [阶段二十/阶段二铺垫] 探索子agent自己在 `finish` 时一并提交的可复用
    # `run(input) -> dict` 脚本源码（可选）。非空时 distiller.py 优先走
    # "校验并落盘" 路径，而不是靠 trace→重放脚本猜测动作形状；为空则沿用
    # 现有 trace-replay 兜底策略（见 distiller.py 文件头说明）。
    script_source: Optional[str] = None


# --------------------------------------------------------------------------- #
# 真实 LLM 驱动的探索循环
# --------------------------------------------------------------------------- #

def build_llm_explorer(
    tool_executor: Callable[[str, dict], dict],
    llm_helper: Optional["LLMHelper"] = None,
    *,
    cfg: Any = None,
    override_model: Optional[str] = None,
    override_provider: Optional[str] = None,
    max_retries: int = 2,
) -> Callable[[dict, dict, dict], ExploreTrace]:
    """
    返回一个符合 CapabilityEngine.explore() 所需签名的 explorer:
        (request, intent_schema, explorer_config) -> ExploreTrace

    tool_executor(tool_name, tool_input) -> dict 由调用方注入，真正执行
    白名单内的底层浏览器操作原语（本引擎不实现具体的浏览器控制逻辑）。

    llm_helper / cfg / override_model / override_provider / max_retries 的
    语义与 `llm_resolver.build_llm_resolver()` 完全一致（见其 docstring）：
    优先用传入的 `llm_helper`（跟随 /model 切换），否则退化为
    `LLMHelper.from_config(cfg)`；两者都未传时在调用时返回明确的失败
    ExploreTrace，而不是抛出让调用方措手不及的异常——探索循环的失败本来就是
    一等公民（`stop_reason="llm_error"`），环境配置问题也按这个既有约定处理。
    """

    def _explorer(request: dict, intent_schema: dict, explorer_config: dict) -> ExploreTrace:
        from mini_agent.llm.base import ToolSchema
        from mini_agent.llm.service import LLMHelper

        helper = llm_helper
        if helper is None:
            if cfg is None:
                return ExploreTrace(
                    success=False,
                    error=(
                        "build_llm_explorer 既未传入 llm_helper 也未传入 cfg，"
                        "无法构造 LLMHelper，无法启动探索子agent。这是环境配置问题。"
                    ),
                    stop_reason="llm_error",
                )
            helper = LLMHelper.from_config(cfg)

        allowlist = _load_tool_allowlist(explorer_config)
        prompt_text = _load_prompt(explorer_config)
        max_steps = int(explorer_config.get("max_steps", 40))
        max_seconds = int(explorer_config.get("max_seconds", 180))

        raw_tools = _build_tool_schemas(allowlist, intent_schema)
        tool_schemas = [
            ToolSchema(name=t["name"], description=t["description"], input_schema=t["input_schema"])
            for t in raw_tools
        ]
        system_prompt = (
            prompt_text
            + "\n\n严格规则：\n"
            f"1. 你只能调用以下工具名之一：{', '.join(allowlist + [FINISH_TOOL, REPORT_FAILURE_TOOL])}。\n"
            f"2. 最多执行 {max_steps} 步，超出会被强制终止并判定失败，请高效行动。\n"
            "3. 确认拿到符合要求结构的数据后，调用 `finish` 工具提交最终数据。\n"
            "4. 如果确认这条路径走不通（如验证码/登录墙/明显反爬拦截），"
            "调用 `report_failure` 如实说明原因，不要编造数据。"
        )
        user_content = json.dumps(
            {"request": request, "intent_schema": intent_schema}, ensure_ascii=False
        )

        messages: list[dict] = [{"role": "user", "content": user_content}]
        steps: list[ExploreStep] = []
        start = time.time()

        for _step_index in range(max_steps):
            elapsed = time.time() - start
            if elapsed > max_seconds:
                return ExploreTrace(success=False, error="探索超出时间预算(max_seconds)",
                                     steps=steps, stop_reason="time_budget")

            step_messages = messages
            remaining = max_seconds - elapsed
            # [阶段十九] 之前模型完全不知道自己还剩多少时间，容易在预算快耗尽
            # 时还去发起一次耗时的工具调用（比如又一次 navigate/wait_for_
            # selector），下一轮循环开头才会因为超时被强制判失败——白白浪费了
            # 一次本可以用来诚实调用 report_failure/finish 收尾的机会。剩余
            # 时间紧张时，在当轮 LLM 调用前追加一条提醒，不修改
            # system_prompt 本身（避免每轮都变化增加 prompt 缓存失效成本）。
            if remaining < 30:
                step_messages = messages + [{
                    "role": "user",
                    "content": (
                        f"[系统提醒] 时间预算只剩 {remaining:.0f} 秒，接下来可能"
                        "只够再发起 1 次工具调用。如果已经拿到足够数据，请立刻"
                        "调用 finish；如果已经能判断走不通（如登录墙/验证码/"
                        "选择器一直找不到），请立刻调用 report_failure 如实说明"
                        "原因，不要再尝试新的操作。"
                    ),
                }]

            try:
                response = helper.chat(
                    messages=step_messages,
                    system=system_prompt,
                    tools=tool_schemas,
                    max_retries=max_retries,
                    override_model=override_model,
                    override_provider=override_provider,
                )
            except Exception as e:  # noqa: BLE001
                return ExploreTrace(success=False, error=f"探索子agent LLM调用失败: {e}",
                                     steps=steps, stop_reason="llm_error")

            # 消息历史约定与 history_manager.py::HistoryManager.append_assistant()
            # 完全一致（provider 无关，各 client 内部自行转换成自己的 wire 格式）。
            assistant_content: list[dict] = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                )
            messages.append({"role": "assistant", "content": assistant_content})

            if not response.tool_calls:
                # 模型没有调用任何工具也没有结束，视为无法继续推进，如实终止。
                return ExploreTrace(success=False, error="探索子agent未调用任何工具即停止，判定探索失败",
                                     steps=steps, stop_reason="llm_error")

            tool_results = []
            finished_trace: Optional[ExploreTrace] = None
            for tc in response.tool_calls:
                name = tc.name
                tool_input = tc.input or {}
                tool_use_id = tc.id

                if name == FINISH_TOOL:
                    data = tool_input.get("data")
                    steps.append(ExploreStep(tool=name, input=tool_input, output=data))
                    finished_trace = ExploreTrace(success=True, data=data, steps=steps, stop_reason="finished")
                    break
                if name == REPORT_FAILURE_TOOL:
                    reason = tool_input.get("reason", "探索子agent报告失败，未说明原因")
                    steps.append(ExploreStep(tool=name, input=tool_input, error=reason))
                    finished_trace = ExploreTrace(success=False, error=reason, steps=steps,
                                                   stop_reason="reported_failure")
                    break
                if name not in allowlist:
                    # 工具白名单强制：越权工具调用一律拒绝执行，作为失败结果反馈给模型，
                    # 而不是静默放行或直接崩溃退出。
                    error_msg = f"工具 `{name}` 不在白名单 {allowlist} 中，已拒绝执行"
                    steps.append(ExploreStep(tool=name, input=tool_input, error=error_msg))
                    tool_results.append(_tool_result_block(tool_use_id, {"error": error_msg}))
                    continue

                try:
                    output = tool_executor(name, tool_input)
                except Exception as e:  # noqa: BLE001
                    output = {"error": f"工具执行异常: {e}"}
                steps.append(ExploreStep(tool=name, input=tool_input, output=output))
                tool_results.append(_tool_result_block(tool_use_id, output))

            if finished_trace is not None:
                return finished_trace

            messages.append({"role": "user", "content": tool_results})

        return ExploreTrace(success=False, error="探索超出步数预算(max_steps)",
                             steps=steps, stop_reason="step_budget")

    return _explorer


def _tool_result_block(tool_use_id: str, output: Any) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(output, ensure_ascii=False) if not isinstance(output, str) else output,
    }


def _build_tool_schemas(allowlist: list[str], intent_schema: dict) -> list[dict]:
    tools = [
        {
            "name": name,
            "description": f"执行底层浏览器操作原语 `{name}`（由运行时环境实现，具体参数依场景而定）",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
        }
        for name in allowlist
    ]
    tools.append({
        "name": FINISH_TOOL,
        "description": "探索成功，提交最终结构化数据，必须符合 intent_schema",
        "input_schema": {
            "type": "object",
            "required": ["data"],
            "properties": {"data": intent_schema or {"type": "object"}},
        },
    })
    tools.append({
        "name": REPORT_FAILURE_TOOL,
        "description": "如实报告探索失败及原因，不要编造数据",
        "input_schema": {
            "type": "object",
            "required": ["reason"],
            "properties": {"reason": {"type": "string"}},
        },
    })
    return tools


def _load_tool_allowlist(explorer_config: dict) -> list[str]:
    path = explorer_config.get("_resolved_tool_allowlist_path")
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return list(data.get("allowed_tools", []))
    return list(explorer_config.get("base_tools", []))


def _load_prompt(explorer_config: dict) -> str:
    path = explorer_config.get("_resolved_prompt_path")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return "你正在为一个此前没有现成方案的需求探索可复用的操作路径。"


# --------------------------------------------------------------------------- #
# [阶段二十] SubAgent 驱动的探索器 —— 新的推荐实现
# --------------------------------------------------------------------------- #

# [阶段二十二] 探索子agent工具黑名单 —— 通用机制，跟具体 skill 无关，不放
# 在任何 skill 目录下。
#
# 起因：`agent.registry.filtered()` 不传 names/groups 时的既有语义是"给全部
# 工具"（这是 filtered() 从设计上就有意的行为，见其 docstring），探索子agent
# 之前就是靠这条路径拿到全量 registry 的。但探索子agent是一个 auto_approve、
# 有独立 max_turns 预算的后台任务，不应该拿到面向"顶层多轮交互式主agent"设计
# 的全部工具——真实复现过的两个后果：
#   1）探索子agent自己又重新走一遍 skill_list → skill_activate 的发现仪式，
#      纯粹浪费探索预算（它本该通过 system_extra 直接被告知有哪些领域原语）；
#   2）探索子agent的工具集里包含 capability_call 本身，一旦它在探索过程中
#      调用 capability_call，就会递归构造出另一个探索子agent——即"嵌套探索"，
#      理论上没有深度上限。
#
# 修法不是去掉 SubAgent 隔离（隔离本身是对的：探索过程会产生大量像整页 HTML
# 这样的中间产物，不应该污染主agent的上下文；独立 max_turns 也应该跟主对话
# 预算解耦），而是收窄探索子agent能拿到的工具集。
_EXPLORER_EXCLUDED_TOOLS = frozenset({
    # 元编排/自我调度类：探索子agent不该自己发现/激活别的 skill，更不该
    # 递归调用 capability_call（这条是防"嵌套探索"最关键的一条）。
    "capability_call",
    "skill_list", "skill_activate", "skill_deactivate",
    "skill_usage_stats", "skill_resource_list", "skill_resource_load",
    "skill_resource_unload", "skill_propose",
    # 二次 agent 派生类：避免探索子agent自己再派生出新的子agent。
    "spawn_agent", "spawn_named_agent", "spawn_agents",
    "run_ensemble_llm", "run_ensemble_subagents",
    # 面向用户交互类：探索子agent是后台任务，不应该卡在等用户交互上。
    "ask_user", "ask_user_confirm", "ask_user_choice",
    # workflow 生成/执行/自我修改类：跟"探索一条可复用操作路径"这个任务
    # 无关，纯粹是多余的攻击面/误用面。
    "generate_workflow", "save_workflow", "patch_workflow_step",
    "test_workflow_step", "list_recent_sessions",
    "summarize_session_for_workflow", "build_workflow_from_summary",
    "run_workflow", "list_workflows", "show_workflow", "delete_workflow",
    "resume_workflow_run", "list_workflow_runs", "get_workflow_stats",
    "get_workflow_run_status", "pause_workflow_run", "cancel_workflow_run",
    "approve_workflow_step", "reject_workflow_step",
    "provide_workflow_step_input", "list_workflow_templates",
    "create_workflow_from_template", "preview_workflow",
    "run_hybrid_exec_task", "list_hybrid_exec_tasks", "show_hybrid_exec_task",
    "agent_status", "agent_inspect", "agent_patch", "agent_policy",
})


def build_subagent_explorer(
    base_cfg: Any,
    *,
    tool_executor: Optional[Callable[[str, dict], dict]] = None,
    session_id: Optional[str] = None,
    session_dir: Any = None,
    shared_tool_cache: Any = None,
    override_model: Optional[str] = None,
    override_provider: Optional[str] = None,
) -> Callable[[dict, dict, dict], ExploreTrace]:
    """
    返回一个符合 CapabilityEngine.explore() 所需签名的 explorer:
        (request, intent_schema, explorer_config) -> ExploreTrace

    与 `build_llm_explorer()` 的区别（见文件头"阶段二十改造说明"）:
      - 不手写决策循环，而是构造一个真实 `orchestrator.sub_agent.SubAgent`，
        跑一次真实的 `Agent.run_turn()`，隔离性/安全边界/预算全部复用
        SubAgent/Task 既有基础设施。
      - `tool_executor` 仍然可选注入：若给出，且该领域在 capability.yaml /
        tool_allowlist.json 里声明了底层原语工具名，会把这些工具名各自桥接
        成探索用 SubAgent 上的真实工具（转发调用 tool_executor），探索子
        agent因此同时拥有系统通用工具（bash/python/文件读写等）与领域底层
        原语（如 browser_navigate）。不给 tool_executor 时，探索子agent仍
        可以用系统通用工具尝试解决问题，只是碰不到领域声明的这批原语。
      - `base_cfg`/`session_id`/`session_dir`/`shared_tool_cache` 语义与
        `orchestrator.sub_agent.SubAgent.__init__` 完全一致，调用方（通常是
        `tools/capability_call.py`）从当前正在跑的 TaskManager/Agent 里取。
    """

    def _explorer(request: dict, intent_schema: dict, explorer_config: dict) -> ExploreTrace:
        from mini_agent.orchestrator.task import Task, TaskRecord
        from mini_agent.orchestrator.sub_agent import SubAgent
        from mini_agent.tools import get_default_registry

        domain_tool_names = _resolve_domain_tool_names(explorer_config)
        preferred_primitives = list(explorer_config.get("preferred_primitives", []))
        max_turns = int(explorer_config.get("max_turns") or explorer_config.get("max_steps", 40))
        prompt_text = _load_prompt(explorer_config)

        system_extra = _build_explore_system_extra(
            prompt_text, intent_schema, domain_tool_names, preferred_primitives, max_turns,
        )
        user_prompt = json.dumps(
            {"request": request, "intent_schema": intent_schema}, ensure_ascii=False
        )

        task = Task(
            prompt=user_prompt,
            name="generative_capability_explore",
            system_extra=system_extra,
            model=override_model,
            provider=override_provider,
            auto_approve=True,
            max_turns=max_turns,
            tags=["generative_capability_explore"],
        )
        record = TaskRecord(task=task)
        sub = SubAgent(
            record, base_cfg,
            session_id=session_id, session_dir=session_dir, shared_tool_cache=shared_tool_cache,
        )

        from .capability_debug import capability_debug_log

        try:
            agent = sub._build_agent(task)
        except Exception as e:  # noqa: BLE001
            capability_debug_log(
                "explorer_build_agent_failed", {"error": str(e)},
                where="explorer_runtime.build_subagent_explorer",
            )
            return ExploreTrace(success=False, error=f"构造探索子agent失败: {e}", stop_reason="llm_error")

        # 若 _build_agent() 落回了全局默认 registry（task 未声明
        # allowed_tools/allowed_tool_groups 时的既有行为——build_subagent_explorer()
        # 构造的 Task 从不设置 allowed_tools，因此这个分支实际上*总是*会走到），
        # 必须先换成一份私有副本再注册 finish/report_failure/领域桥接工具——
        # 否则会污染全局单例，被其它并发 agent/SubAgent 看到（同一坑见
        # orchestrator/sub_agent.py::_build_agent 里 active_skills 分支的
        # 注释）。
        #
        # [BUGFIX] `Agent.__init__` 在构造期内部把 `self.registry` 的引用传给了
        # `self._tool_executor`（`ToolExecutor(registry=self.registry, ...)`），
        # 这个引用是在 `_build_agent()` 返回*之前*就已经捕获、固化的。这里对
        # `agent.registry` 的重新赋值只更新了 `agent.registry` 这一个属性，
        # `agent._tool_executor.registry` 仍指向重新赋值*之前*的那个（全局默认）
        # registry 对象——两者从此不再是同一个对象，之后在这个新对象上
        # `register_fn()` 注册的 finish/report_failure/领域桥接工具，实际工具
        # 派发时（`agent.run_turn()` -> `ToolExecutor.execute_all()` ->
        # `self.registry.call(name, ...)`）根本看不到，报
        # `Unknown tool: 'browser_navigate'`（或 finish/report_failure 同样会
        # 遇到，只是模型没恰好选中才没暴露）。必须把这份新的私有 registry 同步
        # 写回 `agent._tool_executor.registry`，保持两者引用一致。
        _registry_was_replaced = agent.registry is get_default_registry()
        if _registry_was_replaced:
            _excluded_present = [n for n in agent.registry.names if n in _EXPLORER_EXCLUDED_TOOLS]
            _kept_names = [n for n in agent.registry.names if n not in _EXPLORER_EXCLUDED_TOOLS]
            agent.registry = agent.registry.filtered(names=_kept_names)
            _synced = getattr(agent, "_tool_executor", None) is not None
            if _synced:
                agent._tool_executor.registry = agent.registry
            capability_debug_log(
                "explorer_registry_replaced_with_private_copy",
                {
                    # 这两个正是上次真实复现"Unknown tool: browser_navigate"
                    # 时需要肉眼猜的信息：registry 是否真的被换成了私有副本、
                    # _tool_executor.registry 是否真的同步跟上了。
                    "tool_executor_synced": _synced,
                    "registry_tool_count_before_domain_bridge": len(agent.registry.names),
                    # [阶段二十二] 这条能直接确认"嵌套探索"黑名单是否真的生效
                    # ——excluded_tools_stripped 里如果出现 capability_call，
                    # 说明这次探索子agent确实拿不到它了。
                    "excluded_tools_stripped": _excluded_present,
                },
                where="explorer_runtime.build_subagent_explorer",
            )
        else:
            capability_debug_log(
                "explorer_registry_not_global_default",
                {"registry_names_sample": agent.registry.names[:10]},
                where="explorer_runtime.build_subagent_explorer",
            )

        outcome: dict = {}
        agent.registry.register_fn(
            fn=_make_finish_fn(outcome),
            name=FINISH_TOOL,
            description=(
                "探索成功，提交最终结构化数据（必须符合 intent_schema）。"
                "如果这次解法可以整理成一个不依赖具体探索过程、符合 "
                "`run(input: dict) -> dict` 接口约定的可复用脚本，请一并通过 "
                "script_source 提交源码（可选；省略时引擎会尝试用本次探索的"
                "工具调用序列重放兜底，但不保证同样可靠）。"
            ),
            input_schema={
                "type": "object",
                "required": ["data"],
                "properties": {
                    "data": intent_schema or {"type": "object"},
                    "script_source": {
                        "type": "string",
                        "description": (
                            "可选。一个完整的 Python 模块源码，必须定义 "
                            "`def run(input: dict) -> dict`，返回 "
                            "{\"status\": \"success\"|\"fail\", \"data\": ..., "
                            "\"error\": ...}。仅当这次探索用到的方法可以在 "
                            "target/query 等参数变化时被参数化复用时才提交；"
                            "如果只是碰运气般的一次性操作序列，留空即可。"
                        ),
                    },
                },
            },
            requires_approval=False,
            override=True,
        )
        agent.registry.register_fn(
            fn=_make_report_failure_fn(outcome),
            name=REPORT_FAILURE_TOOL,
            description="如实报告探索失败及原因（如验证码/登录墙/选择器一直找不到），不要编造数据。",
            input_schema={
                "type": "object",
                "required": ["reason"],
                "properties": {"reason": {"type": "string"}},
            },
            requires_approval=False,
            override=True,
        )

        capability_debug_log(
            "explorer_finish_report_failure_registered",
            {"registry_names_after": agent.registry.names},
            where="explorer_runtime.build_subagent_explorer",
        )

        if tool_executor is not None:
            for name in domain_tool_names:
                if name in agent.registry.names:
                    capability_debug_log(
                        "explorer_domain_tool_skipped_name_conflict",
                        {"tool_name": name, "reason": "系统已有同名通用工具，不覆盖"},
                        where="explorer_runtime.build_subagent_explorer",
                    )
                    continue  # 系统已有同名通用工具时不覆盖，避免语义混淆
                agent.registry.register_fn(
                    fn=_make_domain_tool_bridge(name, tool_executor),
                    name=name,
                    description=f"该领域声明的底层操作原语 `{name}`（由运行时环境实现，具体参数依场景而定）。",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": True},
                    requires_approval=False,
                    override=True,
                )
                capability_debug_log(
                    "explorer_domain_tool_registered",
                    {
                        "tool_name": name,
                        # call() 和 schema 生成分别读 _tool_executor.registry /
                        # agent.registry，两边都确认一下，这就是这次复现里
                        # 真正需要肉眼核对、之前没有直接证据的那件事。
                        "in_agent_registry": name in agent.registry.names,
                        "in_tool_executor_registry": (
                            getattr(agent, "_tool_executor", None) is not None
                            and name in agent._tool_executor.registry.names
                        ),
                    },
                    where="explorer_runtime.build_subagent_explorer",
                )

        try:
            output_text = sub._run_with_capture(agent, task.prompt)
        except Exception as e:  # noqa: BLE001
            capability_debug_log(
                "explorer_run_exception", {"error": str(e)},
                where="explorer_runtime.build_subagent_explorer",
            )
            return ExploreTrace(
                success=False, error=f"探索子agent运行异常: {e}",
                steps=_extract_steps_from_agent(agent), stop_reason="llm_error",
            )

        steps = _extract_steps_from_agent(agent)
        capability_debug_log(
            "explorer_steps_extracted",
            {
                "step_count": len(steps),
                "steps": [
                    {"tool": s.tool, "input": s.input, "error": s.error}
                    for s in steps
                ],
            },
            where="explorer_runtime.build_subagent_explorer",
        )

        if outcome.get("finished"):
            capability_debug_log("explorer_finished", {"data": outcome.get("data")},
                                  where="explorer_runtime.build_subagent_explorer")
            return ExploreTrace(
                success=True, data=outcome.get("data"), steps=steps, stop_reason="finished",
                script_source=outcome.get("script_source"),
            )
        if outcome.get("failed"):
            capability_debug_log("explorer_reported_failure", {"reason": outcome.get("reason")},
                                  where="explorer_runtime.build_subagent_explorer")
            return ExploreTrace(
                success=False, error=outcome.get("reason") or "探索子agent报告失败，未说明原因",
                steps=steps, stop_reason="reported_failure",
            )
        # max_turns 耗尽仍未调用 finish/report_failure：如实判失败，
        # 不臆测/伪造成功（对应 SubAgent 预算耗尽即失败的既有约定）。
        tail = (output_text or "").strip()[:200]
        capability_debug_log("explorer_step_budget_exhausted", {"max_turns": max_turns, "tail": tail},
                              where="explorer_runtime.build_subagent_explorer")
        return ExploreTrace(
            success=False,
            error=(
                f"探索子agent在 max_turns={max_turns} 内未调用 `finish`/`report_failure`，"
                f"判定为步数预算耗尽。" + (f" 最后一轮输出: {tail}" if tail else "")
            ),
            steps=steps, stop_reason="step_budget",
        )

    return _explorer


def _make_finish_fn(outcome: dict) -> Callable[..., str]:
    def finish(data: dict, script_source: str = "") -> str:
        outcome["finished"] = True
        outcome["data"] = data
        outcome["script_source"] = script_source or None
        return "已收到最终数据，探索到此结束。不要再调用任何工具，直接用一句话简短总结并结束本轮回复。"
    return finish


def _make_report_failure_fn(outcome: dict) -> Callable[..., str]:
    def report_failure(reason: str) -> str:
        outcome["failed"] = True
        outcome["reason"] = reason
        return "已收到失败报告，探索到此结束。不要再调用任何工具，直接结束本轮回复。"
    return report_failure


def _make_domain_tool_bridge(name: str, tool_executor: Callable[[str, dict], dict]) -> Callable[..., dict]:
    def _bridge(**kwargs) -> dict:
        try:
            return tool_executor(name, kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": f"工具 `{name}` 执行异常: {e}"}
    _bridge.__name__ = name
    return _bridge


def _resolve_domain_tool_names(explorer_config: dict) -> list[str]:
    """
    解析该领域声明的底层原语工具名，兼容三种历史写法:
      1. capability.yaml -> explorer.allowed_tools（新增，内联列表）
      2. explorer/tool_allowlist.json -> {"allowed_tools": [...]}（browser-
         site-scraper / doc-template-generation 现有写法）
      3. explorer/tool_allowlist.json -> {"tools": [{"name": ...}, ...]}
         （text-transform-capability 现有写法）
      4. 都没有时退回 capability.yaml -> explorer.base_tools（占位/未接线
         领域的静态 skill 名，不是真实工具名，仅作最后兜底）。
    """
    inline = explorer_config.get("allowed_tools")
    if inline:
        return list(inline)

    path = explorer_config.get("_resolved_tool_allowlist_path")
    if path and Path(path).exists():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        if data.get("allowed_tools"):
            return list(data["allowed_tools"])
        if data.get("tools"):
            return [t["name"] for t in data["tools"] if isinstance(t, dict) and t.get("name")]

    return list(explorer_config.get("base_tools", []))


def _build_explore_system_extra(
    prompt_text: str,
    intent_schema: dict,
    domain_tool_names: list[str],
    preferred_primitives: list[str],
    max_turns: int,
) -> str:
    parts = [prompt_text.strip(), ""]
    parts.append(
        "你正在为一个此前没有现成方案的需求探索可复用的解决路径。除了 bash/"
        "python/文件读写等通用工具外，"
        + (f"你还可以使用该领域提供的底层操作原语: {', '.join(domain_tool_names)}。"
           if domain_tool_names else "该领域暂未声明可用的底层操作原语，请优先尝试通用工具。")
    )
    if preferred_primitives:
        parts.append(
            "该领域的探索倾向于产出可机械蒸馏的动作序列，建议优先尝试: "
            f"{', '.join(preferred_primitives)}；但这只是提示，不是限制——这些"
            "路径走不通时，你仍可以使用被允许的完整工具集（含 bash/python）。"
        )
    parts.append(f"最多执行 {max_turns} 个回合，超出会被判定为探索失败，请高效行动。")
    parts.append(
        "确认拿到符合 intent_schema 的结构化数据后，调用 `finish` 工具提交；"
        "如果这次解法可以整理成一个不依赖具体探索过程的 "
        "`run(input: dict) -> dict` 脚本，请一并通过 `finish` 的 "
        "`script_source` 字段提交源码（可选，省略时引擎会尝试用你的工具调用"
        "序列重放兜底）。"
    )
    parts.append(
        "如果确认这条路径走不通（如验证码/登录墙/明显反爬拦截/该需求本来就"
        "无法达成），调用 `report_failure` 如实说明原因，不要编造数据。"
    )
    parts.append(f"intent_schema: {json.dumps(intent_schema, ensure_ascii=False)}")
    return "\n".join(p for p in parts if p)


def _extract_steps_from_agent(agent: Any) -> list[ExploreStep]:
    """
    从探索用 SubAgent 内部 Agent 实例的对话历史里，尽力还原出一份
    `(tool, input, output)` 步骤序列，供 distiller.py 现有的 trace-replay
    兜底路径消费（`script_source` 未提交时用）。

    最佳努力（best-effort）实现：直接读取
    `HistoryManager._history`（与 `history_manager.py` 内部消息约定一致，
    provider 无关），按 `tool_use.id` 匹配对应的 `tool_result`。任何解析
    异常都不应该让探索流程失败——拿不到 steps 时 trace-replay 兜底路径
    自然会没有素材可用，但 `script_source` 路径（阶段二）不受影响。
    """
    steps: list[ExploreStep] = []
    try:
        history = list(getattr(agent, "_hist")._history)
    except Exception:  # noqa: BLE001
        return steps

    tool_uses: dict[str, tuple[str, dict]] = {}
    order: list[str] = []
    results: dict[str, Any] = {}

    for msg in history:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                tid = block.get("id")
                if tid:
                    tool_uses[tid] = (block.get("name", ""), block.get("input") or {})
                    order.append(tid)
            elif btype == "tool_result":
                tid = block.get("tool_use_id")
                raw = block.get("content")
                parsed = raw
                if isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        parsed = raw
                if tid:
                    results[tid] = parsed

    for tid in order:
        name, tool_input = tool_uses[tid]
        output = results.get(tid)
        error = output.get("error") if isinstance(output, dict) else None
        steps.append(ExploreStep(tool=name, input=tool_input, output=output, error=error))
    return steps


# --------------------------------------------------------------------------- #
# 桩实现：用于离线自测/CI，不发起任何网络调用
# --------------------------------------------------------------------------- #

def build_stub_explorer(
    steps: Optional[list[ExploreStep]] = None,
    final_data: Optional[dict] = None,
    final_error: Optional[str] = None,
    script_source: Optional[str] = None,
) -> Callable[[dict, dict, dict], ExploreTrace]:
    """
    固定返回给定结果的桩探索器，仅用于验证 CapabilityEngine 与
    explore()/distill() 之间的接线逻辑，不代表真实探索质量。

    script_source: 可选。传入时模拟探索子agent在 finish 时一并提交了可复用
    脚本源码的情形（阶段二十/阶段二），用于测试 distiller.py 的
    script_source 优先路径。
    """

    def _stub(request: dict, intent_schema: dict, explorer_config: dict) -> ExploreTrace:  # noqa: ARG001
        if final_error is not None:
            return ExploreTrace(success=False, error=final_error, steps=steps or [],
                                 stop_reason="reported_failure")
        return ExploreTrace(success=True, data=final_data or {}, steps=steps or [],
                             stop_reason="finished", script_source=script_source)

    return _stub
