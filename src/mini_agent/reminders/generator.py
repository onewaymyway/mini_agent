"""
reminders/generator.py
~~~~~~~~~~~~~~~~~~~~~~
reminder 生成工具：从对话历史中提取可复用的 reminder，写入文件。

供 reminder-generator skill 调用，也可由 agent 在任务结束后自动触发。
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import List, Optional, Tuple

from .loader import (
    TRIGGER_TOOL_ERROR,
    TRIGGER_POST_TOOL,
    TRIGGER_USER_INTENT,
    TRIGGER_PATTERN,
)


# ── 生成的 reminder 草稿 ──────────────────────────────────────────────────────

def build_reminder_draft(
    name: str,
    trigger_event: str,
    condition_dict: dict,
    content: str,
    inject_as: str = "user",
    priority: int = 60,
) -> str:
    """
    根据参数生成 reminder .md 文件内容（YAML frontmatter + 正文）。

    condition_dict 示例：
        {"tool_name": "bash", "error_pattern": "Permission denied"}
    """
    # 构造 condition YAML 块
    condition_lines = ""
    if condition_dict:
        cond_items = "\n".join(
            f"  {k}: \"{v}\"" for k, v in condition_dict.items() if v
        )
        if cond_items:
            condition_lines = f"condition:\n{cond_items}"

    frontmatter_parts = [
        f"name: {_safe_name(name)}",
        f"trigger_event: {trigger_event}",
    ]
    if condition_lines:
        frontmatter_parts.append(condition_lines)
    frontmatter_parts += [
        f"inject_as: {inject_as}",
        f"priority: {priority}",
        "enabled: true",
    ]

    frontmatter = "\n".join(frontmatter_parts)
    body = textwrap.dedent(content).strip()

    return f"---\n{frontmatter}\n---\n\n{body}\n"


def _safe_name(name: str) -> str:
    """将名称转成安全的文件名格式（小写下划线）。"""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s-]+", "_", name)
    return name or "unnamed_reminder"


# ── 写入文件 ──────────────────────────────────────────────────────────────────

def save_reminder(
    draft: str,
    name: str,
    target_dir: Path,
    overwrite: bool = False,
) -> Tuple[bool, Path]:
    """
    将 reminder 草稿写入 target_dir/<name>.md。

    返回 (成功, 文件路径)。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    fp = target_dir / f"{_safe_name(name)}.md"

    if fp.exists() and not overwrite:
        return False, fp

    fp.write_text(draft, encoding="utf-8")
    return True, fp


# ── LLM 提取提示词 ────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
你是一个 reminder 提取助手。

你的任务是分析给定的对话历史，提取出可以复用的经验，生成结构化的 reminder。

reminder 的作用是：在未来遇到类似情境时，在上下文中追加提示，帮助 AI 更好地解决问题。

每条 reminder 必须满足：
1. 有明确的触发时机（trigger_event）
2. 有具体的触发条件（condition）
3. 内容简洁、可操作、对未来有指导价值

请严格按如下 JSON 格式输出（不要输出任何其他内容）：
{
  "reminders": [
    {
      "name": "snake_case_name",
      "trigger_event": "tool_error|post_tool|user_intent|pattern",
      "condition": {
        "tool_name": "可选，工具名正则",
        "error_pattern": "可选，错误内容正则",
        "output_pattern": "可选，工具输出正则",
        "keyword": "可选，用户消息关键词正则",
        "intent_pattern": "可选，用户消息模式正则",
        "text_pattern": "可选，assistant输出模式正则"
      },
      "inject_as": "user",
      "priority": 70,
      "content": "reminder 的 Markdown 正文，简洁可操作"
    }
  ],
  "reason": "说明为什么提取这些 reminder"
}

如果对话历史中没有值得提取的经验，则返回：{"reminders": [], "reason": "无可提取内容"}
"""


def build_extraction_prompt(history_text: str) -> str:
    """构造发给 LLM 的提取请求。"""
    return (
        "请从以下对话历史中提取可复用的 reminder：\n\n"
        f"```\n{history_text}\n```"
    )


def parse_extraction_response(response_text: str) -> List[dict]:
    """
    解析 LLM 返回的 JSON，提取 reminders 列表。
    返回 list[dict]，每项对应一条 reminder 的元数据。
    """
    import json

    # 去掉可能的 markdown 代码块
    cleaned = re.sub(r"```(?:json)?|```", "", response_text).strip()
    try:
        data = json.loads(cleaned)
        return data.get("reminders", [])
    except json.JSONDecodeError:
        return []


def drafts_from_extraction(extracted: List[dict]) -> List[Tuple[str, str]]:
    """
    将 LLM 提取结果转成 (name, draft_content) 列表。
    """
    result = []
    for item in extracted:
        name = item.get("name", "unnamed")
        trigger_event = item.get("trigger_event", TRIGGER_TOOL_ERROR)
        condition_dict = {
            k: v for k, v in (item.get("condition") or {}).items() if v
        }
        content = item.get("content", "")
        inject_as = item.get("inject_as", "user")
        priority = int(item.get("priority", 60))

        if not content:
            continue

        draft = build_reminder_draft(
            name=name,
            trigger_event=trigger_event,
            condition_dict=condition_dict,
            content=content,
            inject_as=inject_as,
            priority=priority,
        )
        result.append((name, draft))

    return result
