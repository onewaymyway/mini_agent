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

from llm.base import ToolCall, ToolSchema, LLMResponse, LLMUsage


# ── 正则表达式 ────────────────────────────────────────────────────────────────

# 主格式：<tool_use>\n{...}\n</tool_use>
_TOOL_USE_RE = re.compile(
    r"<tool_use>\s*\n(.*?)\n\s*</tool_use>",
    re.DOTALL,
)

# 兼容旧格式：```tool_call\n{...}\n```
_TOOL_CALL_LEGACY_RE = re.compile(
    r"```tool_call\s*\n(.*?)\n```",
    re.DOTALL,
)

# tool_result 回注格式
_TOOL_RESULT_RE = re.compile(
    r"<tool_result>\s*\n(.*?)\n\s*</tool_result>",
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


# ── thinking/reasoning 提取 ───────────────────────────────────────────────────

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
