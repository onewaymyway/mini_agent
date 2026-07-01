"""
perception/intent_action_mapper.py — 工具透明性：意图-动作映射层（具身改进 v3，§ 2.3）

具身来源：工具透明性（tool transparency）——盲人手杖用熟了之后，使用者感知到的
是"路面在这里有个坑"，而不是"手杖碰到了什么"；手杖本身从意识中"消失"，被
感知到的对象前移到了手杖末端接触的世界。当前 ToolExecutor.execute_all() 对
每次工具调用都是独立的请求-响应事件，turn 结束后 history/traces 里留下的是
"调用了 read_file ×3、str_replace_editor ×2"这类原始流水账，而不是"做了一次
代码重构"这种意图层面的总结——这正是"还在感知手杖本身"的状态。

归属：任务执行层。工具调用在 run_turn 的主循环中发生，聚合逻辑也应该在这一层
（而不是对话交互层或 daemon 层）——与 v3 §九设计原则 1"接入点精确化"一致。

实现取舍：
  - 纯规则匹配，不调用 LLM。意图分组是给"事后总结/可观测性"用的辅助信息，
    不是决策依据，不值得为此付出一次额外的 LLM 调用成本。
  - 输入是同一个 execute_all() batch 内的 (ToolCall, result_str) 序列——
    即同一轮 LLM 响应触发的工具调用集合。跨多轮 turn 的聚合不在本模块职责
    范围内（那需要跨越 history 边界，复杂度和收益不成比例）。
  - 分组算法：按"工具名所属的意图类别"做连续游程（run-length）分组——
    连续的同类别调用归为一个 ActionEvent，类别切换则开启新 ActionEvent。
    不做更复杂的语义聚类（比如读了 3 个无关文件不应该被认为是"一次探索"），
    保持可解释、零额外开销。
  - bash 工具的类别判断依赖命令内容关键词（测试命令 vs 普通 shell 命令），
    因为同一个工具名在不同场景下意图完全不同。

消费方：
  - agent.py 主循环：execute_tools span 内调用，结果写入 traces.jsonl
    （phase="execute_tools" 记录的 extra 字段），供 /diagnostics 和 Phase G
    扫描使用，而不污染 history 本身（history 的原始工具调用记录保持不变，
    ActionEvent 只是一层可选的语义标注）。
  - evolution/autonomous_loop.py 的 digest 记录：cron/autonomous 触发的工具
    调用批次，写入 activity_digest.jsonl 时用 ActionEvent 描述而不是原始
    工具调用列表（晨报里"做了一次代码重构"比"调用了 5 次工具"更有意义）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.llm.base import ToolCall


# ── 意图类别定义 ──────────────────────────────────────────────────────────────

# 工具名 → 意图类别（不依赖 bash 内容判断的部分）
_TOOL_INTENT: dict[str, str] = {
    "read_file":          "exploration",
    "list_dir":           "exploration",
    "glob":                "exploration",
    "grep":                "exploration",
    "tree_summary":       "exploration",
    "diff_files":          "exploration",
    "write_file":          "code_edit",
    "create_file":         "code_edit",
    "patch_file":          "code_edit",
    "patch_file_simple":   "code_edit",
    "delete_file":         "code_edit",
    "web_search":          "research",
}

# bash 命令内容关键词 → 细分类别（按优先级顺序匹配，命中第一个即返回）
_BASH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(pytest|unittest|go\s+test|cargo\s+test|npm\s+(run\s+)?test|jest|mocha)\b", re.I), "test_run"),
    (re.compile(r"\b(pip|npm|yarn|cargo|apt|apt-get|brew)\s+install\b", re.I), "env_setup"),
    (re.compile(r"\b(git\s+(commit|push|pull|merge|rebase|checkout|branch))\b", re.I), "vcs_op"),
    (re.compile(r"\b(mkdir|venv|virtualenv|python\s+-m\s+venv)\b", re.I), "env_setup"),
]

# 意图类别 → 人类可读描述（用于生成摘要文本）
_INTENT_LABELS: dict[str, str] = {
    "exploration": "探索/检索代码",
    "code_edit":   "代码编辑",
    "test_run":    "运行测试",
    "env_setup":   "环境配置",
    "vcs_op":      "版本控制操作",
    "research":    "网络检索",
    "other":       "其他操作",
}


def _classify(tool_name: str, tool_input: dict) -> str:
    """单次工具调用的意图类别判断。"""
    if tool_name == "bash":
        command = str(tool_input.get("command", "") or tool_input.get("cmd", ""))
        for pattern, category in _BASH_PATTERNS:
            if pattern.search(command):
                return category
        return "other"
    return _TOOL_INTENT.get(tool_name, "other")


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class ActionEvent:
    """一组被归为同一意图的连续工具调用。"""

    intent: str                         # 意图类别（见 _INTENT_LABELS）
    tool_names: list[str] = field(default_factory=list)   # 该事件内涉及的工具名（去重保序）
    call_count: int = 0                 # 涉及的工具调用总数
    error_count: int = 0                # 其中出错的调用数
    start_index: int = 0                # 在原 batch 中的起始下标（0-based）

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "label": _INTENT_LABELS.get(self.intent, self.intent),
            "tool_names": list(self.tool_names),
            "call_count": self.call_count,
            "error_count": self.error_count,
            "start_index": self.start_index,
        }

    def to_summary_text(self) -> str:
        """单行人类可读摘要，例如"代码编辑 ×3（read_file, patch_file）"。"""
        label = _INTENT_LABELS.get(self.intent, self.intent)
        names = ", ".join(self.tool_names)
        text = f"{label} ×{self.call_count}（{names}）"
        if self.error_count:
            text += f"，{self.error_count} 次出错"
        return text


class IntentActionMapper:
    """
    将一个 execute_all() batch 内的工具调用序列按意图分组，生成更高层次的
    行动事件列表（ActionEvent）。纯规则匹配，O(n)，不调用 LLM。
    """

    @staticmethod
    def group_calls(
        tool_calls: list["ToolCall"],
        result_strs: Optional[list[str]] = None,
    ) -> list[ActionEvent]:
        """
        按意图类别做连续游程分组。

        Args:
            tool_calls:  本次 batch 的工具调用列表（顺序即调用顺序）。
            result_strs: 对应的结果字符串列表（用于统计 error_count），
                         长度应与 tool_calls 一致；缺失时 error_count 恒为 0。
        """
        if not tool_calls:
            return []

        from mini_agent.perception.lesson_rules import is_tool_error

        events: list[ActionEvent] = []
        current: Optional[ActionEvent] = None

        for idx, tc in enumerate(tool_calls):
            intent = _classify(tc.name, tc.input or {})
            is_err = False
            if result_strs is not None and idx < len(result_strs):
                try:
                    is_err = is_tool_error(result_strs[idx])
                except Exception:
                    is_err = False

            if current is None or current.intent != intent:
                current = ActionEvent(intent=intent, start_index=idx)
                events.append(current)

            if tc.name not in current.tool_names:
                current.tool_names.append(tc.name)
            current.call_count += 1
            if is_err:
                current.error_count += 1

        return events

    @staticmethod
    def summarize(events: list[ActionEvent]) -> str:
        """把一组 ActionEvent 格式化为一段摘要文本（供 digest / 日志使用）。"""
        if not events:
            return ""
        return "；".join(e.to_summary_text() for e in events)


__all__ = ["ActionEvent", "IntentActionMapper"]
