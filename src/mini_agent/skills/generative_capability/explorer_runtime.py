"""
explorer_runtime.py
====================
Generative-Capability 引擎的探索子agent运行时（阶段三）。

对应文档: next_doc/generative-capability-skill-plan.md 第 6 节 explore() /
          第 8 节安全边界 / 实施记录阶段三。

设计要点:
  - 探索子agent运行在与主对话隔离的独立 context 中：这里只接受
    request/intent_schema/explorer_config 三样输入，不携带主对话历史。
  - 工具准入机制（[阶段二十五修订] 此前这里写的是"白名单强制"，与实际接线
    不符，据实更正）：
      * `build_llm_explorer()`（手写决策循环的历史实现，当前未被
        `tools/capability_call.py` 实际接线使用）才是真正的白名单强制——
        模型只能调用 `explorer.tool_allowlist` 中列出的工具名，白名单之外
        一律拒绝执行（见该函数体内 `if name not in allowlist` 分支）。
      * `build_subagent_explorer()`（阶段二十起真正接线、当前唯一在用的
        探索器实现）走的是**黑名单**机制：探索子agent默认拥有 SubAgent
        的完整通用工具集（`bash`/`python`/`write_file` 等），只排除
        `_EXPLORER_EXCLUDED_TOOLS` 里列出的"元编排/递归调用/用户交互/
        workflow"类工具（防止嵌套探索、误用攻击面），`explorer/
        tool_allowlist.json` 声明的领域原语（如 `browser_navigate`）是在
        这份默认工具集之上**追加**的桥接工具，不是收窄。这是刻意设计：
        探索过程经常需要用 `bash`/`write_file` 现写现跑一段脚本来验证
        思路（比如本例的知乎抓取，就需要先用 `bash` 试探 API 是否可行），
        禁掉这些通用工具会让探索能力大幅退化。`tool_allowlist.json` 里
        "调度引擎强制校验..." 的 note 字段描述的是 `build_llm_explorer()`
        路径的行为，同样需要读者留意两条路径不是同一套准入逻辑。
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
    # [阶段二十五] script_source 为空时，区分"探索子agent主动确认无法复用"
    # 与"预算耗尽被强制放行"两种情形，供 distiller.py 决定兜底策略：
    # 前者仍可尝试 LLM 事后总结（子agent当时的判断未必可靠），后者优先级更高。
    script_source_skipped: bool = False
    script_source_forced_empty: bool = False


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

        # [阶段二十三] 探索子agent自己的控制台输出，跟顶层主agent是否开了
        # `--verbose` 完全无关——`SubAgent._build_agent()` 里构造 cfg 时硬编码
        # 了 `verbose=False`（这是通用的 SubAgent 机制，任何子任务默认都不
        # 打印详细参数，避免刷屏），导致探索子agent调用 browser_navigate/
        # browser_get_debug_snapshot 这类工具时，控制台只看得到结果、看不到
        # 调用参数，排查"这次到底传了什么参数导致失败"时很不方便。
        # 这里只覆盖探索子agent自己这一份 cfg（`agent.cfg` 与
        # `agent._tool_executor.cfg` 是同一个对象，`ToolExecutor.execute_all()`
        # 里读的是 `self.cfg.verbose`，改这一处即可生效），不影响顶层主agent
        # 或其它 SubAgent 任务的控制台详略程度。
        agent.cfg.verbose = True

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
        # [阶段二十五] script_source 从"prompt 里请求一下"改为结构性强制：
        # finish() 的 input_schema 把 script_source 列为 required（模型的
        # tool-calling 至少会被迫产出这个字段），且当它是空字符串时
        # _make_finish_fn 不会把 outcome 标记为 finished，而是返回一条
        # 明确的拒绝反馈让模型在预算内重试；只有显式提交哨兵值 "SKIP"
        # （代表"我确认过，这次解法真的不具备参数化复用形状"）才允许放弃。
        # 超过 _FINISH_SCRIPT_SOURCE_MAX_NUDGES 次仍未给出有效值才放行，
        # 避免在预算耗尽边缘死循环——此时 outcome 里会记一个
        # script_source_forced_empty 标记，供 distiller.py 决定走哪条兜底。
        agent.registry.register_fn(
            fn=_make_finish_fn(outcome),
            name=FINISH_TOOL,
            description=(
                "探索成功，提交最终结构化数据（必须符合 intent_schema）。"
                "同时必须通过 script_source 提交一个不依赖具体探索过程、"
                "符合 `run(input: dict) -> dict` 接口约定的可复用脚本源码——"
                "这是本次解法能否沉淀进 skill 目录、下次被直接复用的关键，"
                "不是可有可无的附加信息。如果确认这次操作序列纯属碰运气、"
                "不存在可参数化复用的形状（极少数情况），显式传字符串 "
                "\"SKIP\" 而不是留空。"
            ),
            input_schema={
                "type": "object",
                "required": ["data", "script_source"],
                "properties": {
                    "data": intent_schema or {"type": "object"},
                    "script_source": {
                        "type": "string",
                        "description": (
                            "一个完整的 Python 模块源码，必须定义 "
                            "`def run(input: dict) -> dict`，返回 "
                            "{\"status\": \"success\"|\"fail\", \"data\": ..., "
                            "\"error\": ...}。把 target.url / query 等本次 "
                            "request 里的具体值替换成对 input 参数的引用，"
                            "而不是硬编码这次探索用到的具体值。真的没有可"
                            "复用形状时传 \"SKIP\"，不要留空。"
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
            script_source = outcome.get("script_source")
            inferred_script_source = False
            if not script_source:
                script_source = _infer_script_source_from_steps(steps)
                inferred_script_source = script_source is not None
            capability_debug_log(
                "explorer_finished",
                {
                    "data": outcome.get("data"),
                    "script_source_submitted": bool(outcome.get("script_source")),
                    # [阶段二十四] 直接从调试日志确认这次是不是靠启发式兜底
                    # 才补上的 script_source——如果这个字段频繁是 True，
                    # 说明 system_extra 的提示力度还不够，值得再加强。
                    "script_source_inferred": inferred_script_source,
                },
                where="explorer_runtime.build_subagent_explorer",
            )
            return ExploreTrace(
                success=True, data=outcome.get("data"), steps=steps, stop_reason="finished",
                script_source=script_source,
                script_source_skipped=bool(outcome.get("script_source_skipped")),
                script_source_forced_empty=bool(outcome.get("script_source_forced_empty")),
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


_FINISH_SCRIPT_SOURCE_MAX_NUDGES = 2
_SCRIPT_SOURCE_SKIP_SENTINEL = "SKIP"


def _make_finish_fn(outcome: dict) -> Callable[..., str]:
    nudges = {"count": 0}

    def finish(data: dict, script_source: str = "") -> str:
        script_source = (script_source or "").strip()
        if script_source and script_source != _SCRIPT_SOURCE_SKIP_SENTINEL:
            outcome["finished"] = True
            outcome["data"] = data
            outcome["script_source"] = script_source
            return "已收到最终数据和可复用脚本，探索到此结束。不要再调用任何工具，直接用一句话简短总结并结束本轮回复。"

        if script_source == _SCRIPT_SOURCE_SKIP_SENTINEL:
            outcome["finished"] = True
            outcome["data"] = data
            outcome["script_source"] = None
            outcome["script_source_skipped"] = True
            return "已收到最终数据（明确放弃提交可复用脚本），探索到此结束。不要再调用任何工具，直接用一句话简短总结并结束本轮回复。"

        nudges["count"] += 1
        if nudges["count"] > _FINISH_SCRIPT_SOURCE_MAX_NUDGES:
            # 预算即将耗尽，不再死磕：接受这次 finish，但如实标记
            # script_source 是被强制放行的空值，供 distiller.py 走
            # LLM 事后总结/trace-replay 兜底，而不是假装这是一次正常提交。
            outcome["finished"] = True
            outcome["data"] = data
            outcome["script_source"] = None
            outcome["script_source_forced_empty"] = True
            return (
                "已收到最终数据。script_source 多次留空，引擎将尝试事后从本次"
                "探索过程自动总结出可复用脚本。探索到此结束，不要再调用任何工具。"
            )

        return (
            "拒绝：script_source 不能留空。请基于刚才成功的操作路径，整理出一个 "
            "`def run(input: dict) -> dict` 的可复用脚本源码，再次调用 finish 并"
            "带上 script_source；如果确认这次真的不具备可复用形状，传字符串 "
            "\"SKIP\"。"
        )
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


def _auto_derive_domain_tool_names(explorer_config: dict) -> list[str]:
    """
    [本次新增] 按 `explorer.depends_skills`（或兼容别名 `base_tools`）声明的
    静态 skill 名，自动去 `<skills_root>/<name>/impl/tools_impl.py::
    TOOL_IMPLEMENTATIONS` 读取真正有实现的原语名单——这是"这个原语是否真的
    能跑"的唯一权威数据源（`real_tools.py::build_default_tool_executor()`
    最终派发时用的也是同一份字典），不再需要 `tool_allowlist.json` 手工抄一
    份、两边容易漂移。

    `_resolved_skill_dir` 由 `capability_engine.py::explore()` 注入，
    skills_root 约定为该目录的父目录（即 `.claude/skills/`）。任何一环
    缺失（未声明 depends_skills、找不到 skills_root、目标 skill 没有
    `impl/tools_impl.py`、依赖库未安装等）都安静返回空列表，不抛异常——
    调用方据此退回 `tool_allowlist.json`/`base_tools` 兜底，行为上等价于
    "没有可自动派生的领域原语"，不是新的失败模式。
    """
    depends_skills = explorer_config.get("depends_skills") or explorer_config.get("base_tools")
    skill_dir_str = explorer_config.get("_resolved_skill_dir")
    if not depends_skills or not skill_dir_str:
        return []
    skills_root = Path(skill_dir_str).parent
    try:
        from .real_tools import load_skill_local_tool_implementations
        impls = load_skill_local_tool_implementations(list(depends_skills), skills_root)
    except Exception:  # noqa: BLE001
        return []
    return sorted(impls.keys())


def _resolve_domain_tool_names(explorer_config: dict) -> list[str]:
    """
    解析该领域声明的底层原语工具名。

    优先级（[本次调整] 新增第 0/2 步，其余历史写法保持兼容）:
      0. capability.yaml -> explorer.allowed_tools（内联列表，显式手写，
         优先级最高，视为"就是要精确控制这个集合"）。
      1. 自动派生：按 `explorer.depends_skills`（或兼容别名 base_tools）从
         依赖的静态 skill 的 `impl/tools_impl.py::TOOL_IMPLEMENTATIONS`
         读到的原语名单（见 `_auto_derive_domain_tool_names`）——默认全量
         可用，这是本次改动新增的主路径。
      2. 若同时存在 `tool_allowlist.json`/`capability.yaml ->
         explorer.tool_allowlist`（历史写法），语义从"唯一原语来源"降级为
         "在自动派生集合基础上做交集收窄"——自动派生集合非空时按交集收窄；
         若自动派生集合为空（如目标 skill 尚无 impl/tools_impl.py，或本次
         运行环境未装依赖库），则退回旧行为直接使用该文件声明的列表，不因
         为自动派生失败而丢失存量 skill 的可用原语。
      3. 都没有时退回 capability.yaml -> explorer.base_tools（占位/未接线
         领域的静态 skill 名，不是真实工具名，仅作最后兜底，与阶段十四之前
         行为一致）。
    """
    inline = explorer_config.get("allowed_tools")
    if inline:
        return list(inline)

    auto_derived = _auto_derive_domain_tool_names(explorer_config)

    allowlist_from_file: Optional[list[str]] = None
    path = explorer_config.get("_resolved_tool_allowlist_path")
    if path and Path(path).exists():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        if data.get("allowed_tools"):
            allowlist_from_file = list(data["allowed_tools"])
        elif data.get("tools"):
            allowlist_from_file = [
                t["name"] for t in data["tools"] if isinstance(t, dict) and t.get("name")
            ]

    if allowlist_from_file is not None:
        if auto_derived:
            narrowed = [name for name in allowlist_from_file if name in set(auto_derived)]
            return narrowed
        return allowlist_from_file

    if auto_derived:
        return auto_derived

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
        "`run(input: dict) -> dict` 脚本，**请务必**一并通过 `finish` 的 "
        "`script_source` 字段提交源码——尤其是如果你用 `write_file`/"
        "`create_file` 写了一个 `.py` 脚本来完成任务（比如自己解析已抓到的"
        "页面内容），这份脚本本身就应该原样作为 `script_source` 提交。省略"
        "`script_source` 不是安全的默认选择：引擎的自动兜底是"
        "\"原样重放你调用过的工具序列\"，而你写到临时文件里的脚本路径是"
        "绑定在这次探索所在 session 下的，重放时大概率不存在，会导致这次"
        "探索的成果无法沉淀进 skill 目录——下次同样的请求还要从头重新探索"
        "一遍。"
    )
    parts.append(
        "写 `script_source` 时不要求全程只调用领域原语：过滤空结果、判断"
        "验证码/登录墙关键词、正则清洗文本、重试这类纯逻辑处理，直接写"
        "标准 Python 就好；只有真正需要驱动浏览器等外部系统的步骤才需要"
        "调用领域原语（脚本里通过 `from tool_runtime import get_tool_"
        "executor` 取执行器）。"
    )
    parts.append(
        "如果确认这条路径走不通（如验证码/登录墙/明显反爬拦截/该需求本来就"
        "无法达成），调用 `report_failure` 如实说明原因，不要编造数据。"
    )
    parts.append(f"intent_schema: {json.dumps(intent_schema, ensure_ascii=False)}")
    return "\n".join(p for p in parts if p)


def _infer_script_source_from_steps(steps: list["ExploreStep"]) -> Optional[str]:
    """
    [阶段二十四] `finish` 调用时没带 `script_source` 的技术兜底。

    真实复现过的模式：探索子agent用通用工具 `write_file` 把一段可复用的
    解析/操作逻辑写成一个 `.py` 脚本（写到 session 临时目录下），再用
    `bash` 执行它验证/拿结果，最后 `finish(data=...)` 却忘了把这份脚本
    源码通过 `script_source` 一并提交。此时蒸馏器只能走 trace-replay 兜底
    路径——而 trace 里记录的 `write_file`/`bash` 步骤，`path`/`command`
    参数都绑定着这次探索所在 session 的临时目录，天然不可复用（下次探索
    session id 变了，这个目录大概率不存在），trace-replay 大概率会在蒸馏
    的沙箱自测阶段失败，导致这次探索成果完全无法沉淀进 skill 目录。

    这里在放弃之前，先尝试识别这个具体模式并自动补上 `script_source`：
    从后往前找最后一个 `write_file`（或 `create_file`）步骤，若其
    `input.path` 以 `.py` 结尾、`input.content` 非空，且其后紧跟着至少一个
    `bash` 步骤、其 `input.command` 里引用了同一个路径且该步骤没有报错
    （即"写完就跑了，还跑成功了"），就认为这个文件的内容就是这次探索真正
    要复用的逻辑，把它作为 `script_source` 候选返回。

    找不到匹配模式时返回 None，调用方据此原样落回原有的 trace-replay 兜底，
    行为完全不变——这只是新增一条更可能成功的路径，不影响任何既有分支。

    这是纯粹的启发式识别，不代表这份脚本一定能通过后续的沙箱自测/
    intent_schema 校验——那两道既有关卡不受这里的识别结果影响，识别错了
    最多是"多尝试了一次注定会被自测拒绝的 script_source"，不会污染
    members/。
    """
    write_file_tools = {"write_file", "create_file"}
    last_write_idx: Optional[int] = None
    for i in range(len(steps) - 1, -1, -1):
        step = steps[i]
        if step.tool not in write_file_tools:
            continue
        path = str((step.input or {}).get("path", ""))
        content = (step.input or {}).get("content")
        if not path.endswith(".py") or not content:
            continue
        # 这个 write_file 之后（不要求紧邻，允许中间夹杂其它探测性步骤）
        # 是否有一个成功执行、且引用了同一路径的 bash 步骤。
        for later in steps[i + 1:]:
            if later.tool != "bash":
                continue
            command = str((later.input or {}).get("command", ""))
            if path not in command:
                continue
            if later.error:
                continue
            last_write_idx = i
            break
        if last_write_idx is not None:
            return str(content)
    return None


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
