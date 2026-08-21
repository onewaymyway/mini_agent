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
            if time.time() - start > max_seconds:
                return ExploreTrace(success=False, error="探索超出时间预算(max_seconds)",
                                     steps=steps, stop_reason="time_budget")

            try:
                response = helper.chat(
                    messages=messages,
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
# 桩实现：用于离线自测/CI，不发起任何网络调用
# --------------------------------------------------------------------------- #

def build_stub_explorer(
    steps: Optional[list[ExploreStep]] = None,
    final_data: Optional[dict] = None,
    final_error: Optional[str] = None,
) -> Callable[[dict, dict, dict], ExploreTrace]:
    """
    固定返回给定结果的桩探索器，仅用于验证 CapabilityEngine 与
    explore()/distill() 之间的接线逻辑，不代表真实探索质量。
    """

    def _stub(request: dict, intent_schema: dict, explorer_config: dict) -> ExploreTrace:  # noqa: ARG001
        if final_error is not None:
            return ExploreTrace(success=False, error=final_error, steps=steps or [],
                                 stop_reason="reported_failure")
        return ExploreTrace(success=True, data=final_data or {}, steps=steps or [],
                             stop_reason="finished")

    return _stub
