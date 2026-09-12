"""
llm/system_tool_call.py — System-prompt 模式工具调用 + 通用文本后处理

工具调用格式（与 prompts/system/tool_call_protocol.md 一致）：

  模型输出（tool call）：
    <tool_use>
    {"name": "bash", "input": {"command": "ls"}}
    </tool_use>

  系统回注（tool result）：
    <tool_result>
    {"name": "bash", "output": "file1.py\nfile2.py"}
    </tool_result>

同时兼容旧的 ```tool_call 格式（向后兼容）。

postprocess_response() 对所有 provider 的响应统一执行：
  - 提取 <tool_use> 块 → tool_calls
  - 提取 <think>/<thinking>/<reasoning> 标签 → reasoning
  - 清理 text（移除工具块和 thinking 标签）
"""

from __future__ import annotations

import json
import re
import uuid
import warnings
from typing import Optional

from .base import ToolCall, ToolSchema, LLMResponse, LLMUsage


# ── 正则表达式 ────────────────────────────────────────────────────────────────

# 主格式：<tool_use>\n{...}\n</tool_use>
# 注意：开/闭标签前后的空白（含换行）都是可选的——模型偶尔会把闭合标签
# 紧贴在 JSON 末尾输出（如 "}</tool_use>"，中间没有换行），如果这里强制
# 要求 \n 就会导致整个块解析失败、工具调用被当成纯文本吞掉。所以两侧统一
# 用 \s* 兜底，不再要求字面 \n。
_TOOL_USE_RE = re.compile(
    r"<tool_use>\s*(.*?)\s*</tool_use>",
    re.DOTALL,
)

# 兼容旧格式：```tool_call\n{...}\n```（同样放宽收尾空白要求）
_TOOL_CALL_LEGACY_RE = re.compile(
    r"```tool_call\s*(.*?)\s*```",
    re.DOTALL,
)

# tool_result 回注格式（同上，放宽收尾空白要求）
_TOOL_RESULT_RE = re.compile(
    r"<tool_result>\s*(.*?)\s*</tool_result>",
    re.DOTALL,
)

# thinking/reasoning 标签
_THINK_RE = re.compile(
    r"<(?P<tag>think|thinking|reasoning)>(.*?)</(?P=tag)>",
    re.DOTALL | re.IGNORECASE,
)


# ── tool_use 示例/引用标记 ────────────────────────────────────────────────────
#
# [turn_judge_self_loop_fix_plan.md §2 修复 2 补充] 修复 2（tools 为空时跳过
# <tool_use> 提取）只解决了"零工具判官（TurnJudge 等）自己的输出被误判"这一
# 类场景；但只要某个 Agent/判官本次请求 tools 非空（比如 judge_tools_enabled
# 开启后的 GoalJudge、或任何正常挂了工具的主 Agent），它在输出里"引用/复述"
# 别处文本（如另一段没闭合的 <tool_use>、或格式纠错模板里的示例）时，同样会
# 被这里的正则误判成"自己发起的一次工具调用"。
#
# 这里不采用"按工具名白名单过滤"的方案：引用的示例经常就是复述一段引用了
# *真实、已注册*工具名的问题文本（比如原样复述主 Agent 那段没闭合的、引用了
# bash/write_file 的半成品），白名单挡不住这种情况。
#
# 改用一个专门的固定中文标记句子：约定所有"举例/引用性质"的 <tool_use> 文本，
# 前面必须紧跟这一行标记（见 prompts/reminders/format_issue_*.md 以及
# prompts/system/turn_judge.md 等对判官类角色的提示词）。解析时只要在某个
# <tool_use> 匹配前的一小段窗口内看到这个标记，就整体跳过该匹配——不计入
# tool_calls，也不再对它跑 JSON 解析、不产生任何警告。
#
# 之所以选一句专门的中文标记而不是用 ``` 代码块围栏：
#   1）reminder 模板里的示例本来就已经用 ``` 包裹了，但现有正则从不区分是否
#      在代码块内，围栏挡不住已经观察到的误判案例；
#   2）围栏是模型日常组织回答就会用的通用格式，容易被模型无意间也套在真实
#      调用外面，导致真实调用被连带跳过；这句专门的标记语义单一，只在"举例
#      /引用"场景下才会出现，信号更干净。
#
# 注意：这个常量是本机制的唯一权威定义。prompts/reminders/*.md 与
# prompts/system/{turn_judge,goal_judge,evaluator}.md 等提示词文件里出现的
# 同一句中文文案，必须与这里逐字保持一致——两边目前是分别硬编码（reminder
# 加载器不走 {{var}} 模板渲染），改动其中一处务必同步改另一处，否则标记会
# 失效（详见 tests/test_system_tool_call_and_debug.py 里对该常量取值的校验）。
TOOL_USE_EXAMPLE_MARKER = "【以下为工具格式示例并非实际工具调用】"

# 标记只在匹配起点之前的这段窗口内查找；同时不跨越"上一个 <tool_use>/
# ```tool_call 匹配的结束位置"，避免窗口过大，把更早、不相关的一次真实调用
# 后面的说明文字误当成当前这次匹配的标记。
_EXAMPLE_MARKER_WINDOW = 300


def _is_marked_as_example(text: str, match_start: int, window_start: int) -> bool:
    """
    判断 text 中位于 [window_start, match_start) 的这段文本里是否出现了
    TOOL_USE_EXAMPLE_MARKER——出现即视为"举例/引用"，调用方应跳过这次匹配。
    """
    if match_start <= window_start:
        return False
    return TOOL_USE_EXAMPLE_MARKER in text[window_start:match_start]


# ── 工具列表渲染 ──────────────────────────────────────────────────────────────

def render_tool_list(tools: list[ToolSchema]) -> str:
    """将 ToolSchema 列表渲染为注入 system prompt 的描述文本。"""
    if not tools:
        return "(no tools available)"
    parts: list[str] = []
    for t in tools:
        entry = {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        }
        parts.append(f"```json\n{json.dumps(entry, indent=2, ensure_ascii=False)}\n```")
    return "\n\n".join(parts)


# ── tool_use 块解析 ───────────────────────────────────────────────────────────

def parse_tool_calls(text: str) -> list[ToolCall]:
    """
    从模型输出文本中提取所有 <tool_use> 块（及兼容旧格式的 ```tool_call 块）。
    解析为 ToolCall 列表。容错：JSON 无效时跳过，缺 id 时自动生成。

    [tool_use 示例/引用标记] 匹配前被 TOOL_USE_EXAMPLE_MARKER 标记的
    <tool_use> 块视为"举例/引用"而非真实调用，整体跳过（不解析、不计入
    结果、不产生任何警告）。见 TOOL_USE_EXAMPLE_MARKER 定义处的说明。
    """
    calls: list[ToolCall] = []

    # 主格式：<tool_use>
    prev_end = 0
    for m in _TOOL_USE_RE.finditer(text):
        window_start = max(0, m.start() - _EXAMPLE_MARKER_WINDOW, prev_end)
        is_example = _is_marked_as_example(text, m.start(), window_start)
        prev_end = m.end()
        if is_example:
            continue
        tc = _parse_single_call(m.group(1).strip())
        if tc:
            calls.append(tc)

    # 兼容旧格式（```tool_call）
    if not calls:
        prev_end = 0
        for m in _TOOL_CALL_LEGACY_RE.finditer(text):
            window_start = max(0, m.start() - _EXAMPLE_MARKER_WINDOW, prev_end)
            is_example = _is_marked_as_example(text, m.start(), window_start)
            prev_end = m.end()
            if is_example:
                continue
            tc = _parse_single_call(m.group(1).strip())
            if tc:
                calls.append(tc)

    return calls


def _parse_single_call(raw_json: str) -> Optional[ToolCall]:
    """解析单个 JSON 片段为 ToolCall。"""
    data=None
    try:
        
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        warnings.warn(f"[system_tool_call] Invalid tool call JSON: {e}\n{raw_json[:200]}")

        try:
            # 尝试使用json_repair修复，提升成功率
            import json_repair
            obj = json_repair.repair_json(raw_json, return_objects=True)
            if isinstance(obj, dict):
                warnings.warn(f"[tool_parser] JSON 已修复 : {raw_json[:80]!r}")
                data=obj
        except Exception as e2:
            from mini_agent.errors import log_exception
            log_exception(e2, where='mini_agent.llm.system_tool_call._parse_single_call')
            warnings.warn(f"[tool_parser] json_repair 失败 {raw_json}: {e2}")
        if not data:
            return None

    # 支持两种字段命名：
    #   新格式: {"name": "...", "input": {...}}
    #   旧格式: {"tool": "...", "parameters": {...}} / {"arguments": {...}}
    name = data.get("name") or data.get("tool", "")
    tid  = data.get("id") or f"tc_{uuid.uuid4().hex[:8]}"
    params = (
        data.get("input")
        or data.get("parameters")
        or data.get("arguments")
        or {}
    )
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            params = {}

    # 兜底：模型偶尔会把 input/arguments 写成 list（或其他非 dict 类型），
    # 若不在此处拦截，脏数据会一路流到 renderer._tool_summary() 等下游
    # 假定 dict 的地方，触发 'list' object has no attribute 'get' 之类的崩溃。
    if not isinstance(params, dict):
        warnings.warn(
            f"[system_tool_call] Tool call params 不是 dict（实际是 {type(params).__name__}），"
            f"已降级为空 dict: {raw_json[:150]}"
        )
        params = {}

    if not name:
        warnings.warn(f"[system_tool_call] Tool call missing 'name' field: {raw_json[:100]}")
        return None

    return ToolCall(id=tid, name=name, input=params)


def strip_tool_use_blocks(text: str) -> str:
    """移除文本中所有 <tool_use> 和旧格式 ```tool_call 块。"""
    text = _TOOL_USE_RE.sub("", text)
    text = _TOOL_CALL_LEGACY_RE.sub("", text)
    return text.strip()


# ── tool_result 回注 ──────────────────────────────────────────────────────────

def render_tool_results(tool_calls: list[ToolCall], results: list[str]) -> str:
    """
    将工具执行结果渲染为回注给模型的 user 消息内容。
    使用 <tool_result> 格式（与 tool_call_protocol.md 对应）。
    """
    parts: list[str] = []
    for tc, result in zip(tool_calls, results):
        entry = {"name": tc.name, "output": result}
        parts.append(
            f"<tool_result>\n{json.dumps(entry, indent=2, ensure_ascii=False)}\n</tool_result>"
        )
    return "\n\n".join(parts)


# ── content-block 扁平化（OpenAI 兼容 provider 专用） ──────────────────────────
#
# history_manager.append_assistant() 把 assistant 回复存成 Anthropic 风格的
# content-block 列表：[{"type":"text","text":...}, {"type":"tool_use","id":...,
# "name":...,"input":...}]（见 history/entry.py）。Anthropic 原生 API 的
# messages.create() 接受这种 list content，原样传下去没问题。
#
# 但所有走 system-prompt 工具协议的 OpenAI 兼容 provider（NVIDIA NIM / OpenAI /
# Ollama / OpenRouter / Agnes）的 chat/completions 端点，message.content 字段
# 只接受字符串或 null——传入 list 会被网关的严格 schema 直接拒绝（例如 NVIDIA
# NIM 返回 400: "data did not match any variant of untagged enum
# ChatCompletionRequestAssistantMessageContent"）。这些历史消息一旦包含之前的
# 工具调用（第二轮及以后的对话必然会有），content 就是 list，直接把裸
# messages 转发给这些 provider 必然触发此错误。
#
# 这里补的是转换：把 content-block 列表重新序列化为该协议本身使用的纯文本
# 格式（<tool_use>{"name":...,"input":...}</tool_use>），与 parse_tool_calls()
# 能解析的格式完全一致——下一轮模型看到的历史里，自己之前的工具调用仍然是
# 用它认识的 <tool_use> 标签表示的，语义不丢失，只是从"结构化 block"变回了
# "协议约定的文本"。
def flatten_message_content(messages: list[dict]) -> list[dict]:
    """
    将 messages 中 content 为 block 列表的条目转换为纯字符串（服务于 OpenAI
    兼容协议）。content 已经是字符串（或其他非 list 类型）的消息原样返回，
    不做任何改动——只处理需要转换的那部分，避免影响已经符合协议的消息。
    """
    result: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
            elif btype == "tool_use":
                payload = {"name": block.get("name", ""), "input": block.get("input", {})}
                parts.append(
                    f"<tool_use>\n{json.dumps(payload, ensure_ascii=False)}\n</tool_use>"
                )
            else:
                # 未知 block 类型：兜底保留可读文本，避免信息静默丢失，
                # 优先于"直接跳过"（跳过会让模型看不到这段历史内容）。
                parts.append(str(block))
        new_msg = dict(msg)
        new_msg["content"] = "\n\n".join(parts)
        result.append(new_msg)
    return result




def extract_thinking_blocks(text: str) -> tuple[str, str]:
    """
    从文本中提取所有 <think>/<thinking>/<reasoning> 标签内容。
    返回 (clean_text, thinking_content)。
    """
    thinking_parts: list[str] = []

    def _collect(m: re.Match) -> str:
        content = m.group(2).strip()
        if content:
            thinking_parts.append(content)
        return ""

    clean = _THINK_RE.sub(_collect, text).strip()
    return clean, "\n\n".join(thinking_parts)


# ── 通用后处理 ────────────────────────────────────────────────────────────────

def postprocess_response(response: LLMResponse, parse_tool_use: bool = True) -> LLMResponse:
    """
    对任意 LLMResponse 执行后处理：
    1. 提取 <tool_use> 块 → tool_calls（parse_tool_use=False 时跳过本步）
    2. 提取 <think> 等标签 → reasoning（与已有 reasoning 合并）
    3. 清理 text
    4. 有 tool_call → stop_reason 改为 "tool_use"

    parse_tool_use：
        [BUGFIX / 零工具 Agent 被 <tool_use> 正则误伤] 此前这里对所有
        provider 响应无差别执行 <tool_use> 提取，不管这个 Agent 本次请求
        是否真的挂了工具。TurnJudge / GoalJudge 等纯文本判官类 Agent
        （tools_enabled=False，本次请求 tools 为空列表）经常需要在判定
        文本里"引用/复述"主 Agent 输出的问题片段（比如举例说明一段没
        闭合的 <tool_use> 该怎么修），这些引用文本本身并不是它们发起的
        工具调用，却会被同一个正则误判成"格式错误的工具调用"，导致这一轮
        被当成无效输出，被迫重试甚至陷入死循环。调用方在本次请求的
        tools 列表为空时应传入 parse_tool_use=False，跳过提取，只做
        <think> 等标签清理；只要挂了任意工具，行为与此前完全一致。
    """
    text=response.text
    if not text:
        if response.reasoning:
            text=response.reasoning

    if not text:
        return response
    
    # if not response.text:
    #     return response

    # text = response.text

    # 步骤 1：提取 thinking 标签
    text, tag_thinking = extract_thinking_blocks(text)

    # 合并 reasoning
    existing = response.reasoning or ""
    if tag_thinking and not _already_in(existing, tag_thinking):
        combined_reasoning = "\n\n".join(filter(None, [existing, tag_thinking]))
    else:
        combined_reasoning = existing

    # 步骤 2：提取 tool_use 块（parse_tool_use=False 时跳过，见函数 docstring）
    tool_calls_from_text = parse_tool_calls(text) if parse_tool_use else []
    final_tool_calls = response.tool_calls if response.tool_calls else tool_calls_from_text

    if tool_calls_from_text:
        text = strip_tool_use_blocks(text)

    text = text.strip()

    # 步骤 3：确定 stop_reason
    stop_reason = response.stop_reason
    if final_tool_calls and stop_reason != "tool_use":
        stop_reason = "tool_use"

    # 无变化则返回原对象
    if (text == response.text
            and combined_reasoning == (response.reasoning or "")
            and final_tool_calls == response.tool_calls
            and stop_reason == response.stop_reason):
        return response

    return LLMResponse(
        text=text,
        reasoning=combined_reasoning,
        tool_calls=final_tool_calls,
        usage=response.usage,
        stop_reason=stop_reason,
        raw=response.raw,
    )


def _already_in(existing: str, new_thinking: str) -> bool:
    if not existing or not new_thinking:
        return False
    return new_thinking[:50] in existing


# ── tool_use 消息转换（用于不支持 tool_use 类型的模型）───────────────────────

# ── system 消息格式转换 ───────────────────────────────────────────────────────

def convert_system_to_message(
    system: str,
    messages: list[dict],
) -> tuple[str, list[dict]]:
    """
    将独立的 system 字段合并为 messages 列表里第一条 role="system" 消息。

    用于不支持顶层 system 参数的模型（如部分本地/兼容模型）。

    转换前：
        system  = "You are a helpful assistant."
        messages = [{"role": "user", "content": "Hello"}]

    转换后：
        system  = ""   ← 清空，避免重复
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user",   "content": "Hello"},
        ]

    若 messages 首条已是 role="system"，则合并内容（新内容在前）；
    若 system 为空，直接原样返回。
    """
    if not system:
        return system, messages

    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        merged = (system.rstrip() + "\n\n" + existing).strip() if existing else system
        new_messages = [{"role": "system", "content": merged}] + messages[1:]
    else:
        new_messages = [{"role": "system", "content": system}] + list(messages)

    return "", new_messages


def convert_tool_use_to_text(messages: list[dict]) -> list[dict]:
    """
    将 messages 中的 assistant 消息里的 tool_use 类型转换为 text 类型。
    把 tool_use 信息序列化为 JSON 字符串，放在 text 类型的 content 中。

    适用场景：某些模型不支持 tool_use 类型的 content，只能接受 text 类型。

    转换前：
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "我将创建一个文件"},
                {
                    "type": "tool_use",
                    "id": "tc_abc123",
                    "name": "create_file",
                    "input": {"path": "./test.py", "content": "print(1)"}
                }
            ]
        }

    转换后：
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "我将创建一个文件"},
                {
                    "type": "text",
                    "text": "<tool_use>\n{\"id\": \"tc_abc123\", \"name\": \"create_file\", \"input\": {...}}\n</tool_use>"
                }
            ]
        }
    """
    if not messages:
        return messages

    converted = []
    for msg in messages:
        # 只处理 assistant 角色的消息
        if msg.get("role") != "assistant":
            converted.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            converted.append(msg)
            continue

        new_content = []
        for item in content:
            if not isinstance(item, dict):
                new_content.append(item)
                continue

            # 如果是 tool_use 类型，转换为 text 类型
            if item.get("type") == "tool_use":
                tool_entry = {
                    "name": item.get("name", ""),
                    "input": item.get("input", {}),
                }
                tool_text = f"<tool_use>\n{json.dumps(tool_entry, ensure_ascii=False, indent=2)}\n</tool_use>"
                new_content.append({"type": "text", "text": tool_text})
            else:
                new_content.append(item)

        converted.append({"role": "assistant", "content": new_content})

    return converted
