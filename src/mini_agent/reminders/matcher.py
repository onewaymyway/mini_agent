"""
reminders/matcher.py
~~~~~~~~~~~~~~~~~~~~
ReminderMatcher：根据情境从 reminder 池中挑选匹配的 reminder。

匹配规则：
- 各字段用正则匹配（忽略大小写）；
- 同一事件中多条 reminder 均可匹配，按 priority 降序返回；
- 外部由 ReminderManager 负责截取 max_per_turn 条。
"""

from __future__ import annotations

import re
from typing import List, Optional

from .loader import (
    Reminder,
    TRIGGER_TOOL_ERROR,
    TRIGGER_POST_TOOL,
    TRIGGER_USER_INTENT,
    TRIGGER_PATTERN,
    TRIGGER_PRE_TOOL,
)


class ReminderMatcher:
    """条件匹配引擎，无状态，可复用。"""

    def __init__(self, reminders: List[Reminder], verbose: bool = False) -> None:
        self._reminders = reminders
        self._verbose = verbose

    def update(self, reminders: List[Reminder]) -> None:
        """热更新 reminder 列表（reload 后调用）。"""
        self._reminders = reminders

    # ── 四类触发入口 ──────────────────────────────────────────────────────────

    def match_tool_error(
        self,
        tool_name: str,
        error_str: str,
        error_category: Optional[str] = None,
    ) -> List[Reminder]:
        """
        工具调用出错时触发。
        condition.tool_name     → 匹配工具名（可选）
        condition.error_pattern → 匹配错误内容（可选）
        condition.error_category → 精确匹配 classify_error() 分类（[15.2] 新增）
        """
        matched = []
        for r in self._reminders:
            if r.trigger_event != TRIGGER_TOOL_ERROR:
                continue
            if not self._match_tool_name(r, tool_name):
                continue
            if r.condition.error_pattern:
                if not _re_search(r.condition.error_pattern, error_str):
                    continue
            # [Stage 7 / 15.2] error_category 精确路由
            if r.condition.error_category:
                if error_category != r.condition.error_category:
                    continue
            matched.append(r)

        return _sort(matched)

    def match_post_tool(
        self, tool_name: str, output_str: str
    ) -> List[Reminder]:
        """
        工具调用成功后触发。
        condition.tool_name     → 匹配工具名（可选）
        condition.output_pattern → 匹配输出内容（可选）
        """
        matched = []
        for r in self._reminders:
            if r.trigger_event != TRIGGER_POST_TOOL:
                continue
            if not self._match_tool_name(r, tool_name):
                continue
            if r.condition.output_pattern:
                if not _re_search(r.condition.output_pattern, output_str):
                    continue
            matched.append(r)

        return _sort(matched)

    def match_user_intent(self, user_message: str) -> List[Reminder]:
        """
        用户消息进入时触发。
        condition.keyword       → 关键词（正则）
        condition.intent_pattern → 更复杂的消息模式（正则）
        两个字段均设置时，满足其一即匹配。
        """
        matched = []
        for r in self._reminders:
            if r.trigger_event != TRIGGER_USER_INTENT:
                continue
            kw_ok = (
                _re_search(r.condition.keyword, user_message)
                if r.condition.keyword else None
            )
            ip_ok = (
                _re_search(r.condition.intent_pattern, user_message)
                if r.condition.intent_pattern else None
            )
            # 两个条件都没设 → 不匹配（避免全量注入）
            if kw_ok is None and ip_ok is None:
                continue
            # 至少一个匹配
            if kw_ok or ip_ok:
                matched.append(r)

        return _sort(matched)

    def match_pattern(self, assistant_text: str) -> List[Reminder]:
        """
        assistant 输出文本后触发。
        condition.text_pattern → 匹配 assistant 输出（正则）
        """
        matched = []
        for r in self._reminders:
            if r.trigger_event != TRIGGER_PATTERN:
                continue
            if not r.condition.text_pattern:
                continue
            if _re_search(r.condition.text_pattern, assistant_text):
                matched.append(r)

        return _sort(matched)

    def match_pre_tool(
        self, tool_name: str, tool_input: Optional[dict] = None
    ) -> List[Reminder]:
        """
        [具身改进 A3] 工具调用前触发（前馈控制）。
        condition.tool_name → 匹配工具名（可选，为空表示对任意工具都生效）。

        tool_input 暂不参与匹配（不同工具参数结构差异很大，正则匹配整个
        dict 的字符串表示容易误判）；保留参数位是为了未来扩展按参数内容
        匹配时不需要再改调用方签名。
        """
        matched = []
        for r in self._reminders:
            if r.trigger_event != TRIGGER_PRE_TOOL:
                continue
            if not self._match_tool_name(r, tool_name):
                continue
            matched.append(r)

        return _sort(matched)

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _match_tool_name(self, r: Reminder, tool_name: str) -> bool:
        """condition.tool_name 为空时总是匹配（适用于任何工具）。"""
        if not r.condition.tool_name:
            return True
        return _re_search(r.condition.tool_name, tool_name) is not None


# ── 模块级工具函数 ────────────────────────────────────────────────────────────

def _re_search(pattern: Optional[str], text: str) -> Optional[re.Match]:
    """安全正则搜索，pattern 为 None 或编译失败时返回 None。"""
    if not pattern:
        return None
    try:
        return re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    except re.error:
        # 正则语法错误，视为不匹配
        return None


def _sort(reminders: List[Reminder]) -> List[Reminder]:
    """按 priority 降序排列。"""
    return sorted(reminders, key=lambda r: r.priority, reverse=True)
