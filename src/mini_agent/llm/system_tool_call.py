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
    """
    calls: list[ToolCall] = []

    # 主格式：<tool_use>
    for m in _TOOL_USE_RE.finditer(text):
        tc = _parse_single_call(m.group(1).strip())
        if tc:
            calls.append(tc)

    # 兼容旧格式（```tool_call）
    if not calls:
        for m in _TOOL_CALL_LEGACY_RE.finditer(text):
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

def postprocess_response(response: LLMResponse) -> LLMResponse:
    """
    对任意 LLMResponse 执行后处理：
    1. 提取 <tool_use> 块 → tool_calls
    2. 提取 <think> 等标签 → reasoning（与已有 reasoning 合并）
    3. 清理 text
    4. 有 tool_call → stop_reason 改为 "tool_use"
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

    # 步骤 2：提取 tool_use 块
    tool_calls_from_text = parse_tool_calls(text)
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
