"""
perception/lesson_rules.py — Lesson Memory 规则触发引擎（Stage 1.2）

对应 self_evolution_implementation_plan.md Stage 1.2 / 设计文档第 3 节"触发机制（两条线）"
中的"规则触发（不等会话结束）"一线：用模板直接生成轻量 lesson，不调用 LLM，成本低、响应快。

两条规则：
  1. 同一工具连续失败 ≥ N 次（默认 N=3，可配置 `cfg.memory.lesson_fail_threshold`）
  2. 权限拒绝后紧接着重试成功（识别"纠错过程"）

设计取舍：
  - 不依赖任何 LLM 调用，纯规则判断，保证低延迟、零额外 token 成本
  - 状态（连续失败计数、待观察的拒绝记录）保存在本模块的 LessonRuleEngine 实例里，
    随 session 生命周期存在（由调用方持有，通常挂在 ToolExecutor 上）
  - 命中规则后生成的 MemoryEntry 直接是 entry_type="lesson"、source="self_reflection"，
    confidence 固定为中等水平（0.6），因为规则触发的可信度高于"自由发挥的 LLM 反思猜测"，
    但低于"人类明确纠正"（见 correction_detector.py，confidence=0.7）
"""

from __future__ import annotations

import re as _re
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry


# 权限拒绝后，多久之内的"同工具成功调用"才算作"纠错重试成功"（秒）。
# 超过这个窗口的成功调用更可能是无关的正常使用，不应误判为纠错。
_DENIAL_RETRY_WINDOW_SECONDS = 600.0

# 规则触发 lesson 的固定 confidence：高于纯 LLM 自由反思的猜测，低于人类明确纠正。
_RULE_TRIGGERED_CONFIDENCE = 0.6


# ── 工具错误识别（从 agent.py 迁移至此，供 tool_executor.py 和 agent.py 共享，─────
#    避免 tool_executor.py 反向 import agent.py 造成循环依赖）─────────────────────
# 不同工具的错误输出格式各不相同，单纯依赖 startswith 会漏掉
# Traceback、非零 exit code 等常见格式。
_ERROR_STARTSWITH = (
    "[error",
    "[tool error",
    "Error:",
    "ERROR:",
    "Traceback (most recent call last)",  # Python 异常堆栈
    "error:",                              # bash / 编译器小写 error:
    "fatal:",                              # git fatal
)
_ERROR_PATTERNS = _re.compile(
    r"\[exit code:\s*[1-9]\d*\]"          # exit code 非零
    r"|^\s*(SyntaxError|TypeError|ValueError|KeyError|AttributeError"
    r"|RuntimeError|OSError|IOError|FileNotFoundError|PermissionError"
    r"|ModuleNotFoundError|ImportError|NameError|IndexError"
    r"|JSONDecodeError|UnicodeDecodeError|ConnectionError|TimeoutError"
    r"|CalledProcessError)\b",
    _re.MULTILINE,
)


def is_tool_error(result_str: str) -> bool:
    """
    判断工具调用结果是否为错误输出。

    综合以下特征：
    1. 特定前缀（[error、Traceback、Error: 等）
    2. 非零 exit code（[exit code: N]，N > 0）
    3. 常见 Python / 系统异常类名出现在输出中
    """
    if not result_str:
        return False
    stripped = result_str.lstrip()
    for prefix in _ERROR_STARTSWITH:
        if stripped.startswith(prefix):
            return True
    if _ERROR_PATTERNS.search(result_str):
        return True
    return False


@dataclass
class _PendingDenial:
    """记录一次"工具调用被权限拒绝"事件，等待后续判断是否紧接着重试成功。"""
    tool_name: str
    tool_input_repr: str   # 用于生成 trigger 描述，不用于精确匹配
    denied_at: float = field(default_factory=time.time)


class LessonRuleEngine:
    """
    规则触发引擎。调用方在每次工具调用结果产生后调用 `observe()`，
    若命中规则，返回一个待写入记忆的 MemoryEntry；否则返回 None。

    用法（典型挂载点：ToolExecutor.execute_all 内，每次工具调用后）：
        engine = LessonRuleEngine(session_id=..., model=..., fail_threshold=3)
        entry = engine.observe(tool_name, tool_input, allowed, result_str, is_error)
        if entry is not None:
            memory_backend.add(entry)
    """

    def __init__(
        self,
        session_id: str,
        model: str = "",
        fail_threshold: int = 3,
    ) -> None:
        self.session_id = session_id
        self.model = model
        self.fail_threshold = max(1, fail_threshold)
        # 工具名 -> 连续失败次数
        self._fail_counts: dict[str, int] = {}
        # 工具名 -> 最近一次被拒绝事件（用于"拒绝后重试成功"检测）
        self._pending_denials: dict[str, _PendingDenial] = {}
        # 已经为某个 (tool_name, fail_count桶) 生成过 lesson，避免同一连续失败区间重复生成
        self._fail_lesson_emitted: set[str] = set()

    # ── 核心入口 ──────────────────────────────────────────────────────────────

    def observe(
        self,
        tool_name: str,
        tool_input: dict,
        allowed: bool,
        result_str: str,
        is_error: bool,
    ) -> Optional["MemoryEntry"]:
        """
        观察一次工具调用的结果，返回命中规则时生成的 MemoryEntry，否则 None。

        参数：
            tool_name:   工具名
            tool_input:  工具调用参数（仅用于生成可读的 trigger 描述，不做精确匹配）
            allowed:     本次调用是否通过权限检查（False = 被用户拒绝）
            result_str:  工具调用结果字符串（被拒绝时通常是 "[Tool call denied by user]"）
            is_error:    本次调用是否判定为错误（由调用方用 _is_tool_error() 等判断后传入）
        """
        if not allowed:
            # 权限拒绝：记录待观察事件，本身不触发 lesson（拒绝不算"失败"，
            # 失败计数只统计真正执行后出错的情况）
            self._pending_denials[tool_name] = _PendingDenial(
                tool_name=tool_name,
                tool_input_repr=self._safe_repr(tool_input),
            )
            return None

        # 走到这里说明本次调用被允许执行。先检查"拒绝后重试成功"规则。
        retry_entry = self._check_denial_retry_success(tool_name, is_error)
        if retry_entry is not None:
            return retry_entry

        # 再检查"连续失败"规则。
        return self._check_consecutive_failure(tool_name, tool_input, is_error)

    # ── 规则一：连续失败 ──────────────────────────────────────────────────────

    def _check_consecutive_failure(
        self, tool_name: str, tool_input: dict, is_error: bool
    ) -> Optional["MemoryEntry"]:
        if not is_error:
            # 成功调用清空该工具的连续失败计数，并解除"重复生成"的限制
            self._fail_counts.pop(tool_name, None)
            self._fail_lesson_emitted.discard(tool_name)
            return None

        count = self._fail_counts.get(tool_name, 0) + 1
        self._fail_counts[tool_name] = count

        if count < self.fail_threshold:
            return None

        # 达到阈值：每个连续失败区间只生成一次（直到下次成功调用重置）
        if tool_name in self._fail_lesson_emitted:
            return None
        self._fail_lesson_emitted.add(tool_name)

        return self._make_entry(
            trigger=f"工具 `{tool_name}` 连续失败 {count} 次，参数示例：{self._safe_repr(tool_input)}",
            outcome=f"连续 {count} 次调用均失败，可能存在系统性问题（参数错误、环境缺失、权限不足等）",
            root_cause="",  # 规则触发无法推断根因，留空待后续反思补充
            suggested_action=f"下次调用 `{tool_name}` 前，先检查上一次失败的具体原因，避免重复同样的错误",
            occurrence_count=count,
        )

    # ── 规则二：权限拒绝后重试成功 ────────────────────────────────────────────

    def _check_denial_retry_success(
        self, tool_name: str, is_error: bool
    ) -> Optional["MemoryEntry"]:
        pending = self._pending_denials.get(tool_name)
        if pending is None:
            return None

        # 无论本次成功还是失败，这个待观察事件只消费一次
        del self._pending_denials[tool_name]

        if is_error:
            # 拒绝后的重试仍然失败，不构成"纠错成功"模式
            return None
        if time.time() - pending.denied_at > _DENIAL_RETRY_WINDOW_SECONDS:
            # 间隔太久，更可能是无关的独立调用
            return None

        return self._make_entry(
            trigger=f"工具 `{tool_name}` 调用一度被用户拒绝（参数示例：{pending.tool_input_repr}）",
            outcome="拒绝后，agent 调整方式重新调用，本次执行成功",
            root_cause="",
            suggested_action=f"调用 `{tool_name}` 前，优先确认是否符合用户预期的方式，减少不必要的拒绝-重试循环",
            occurrence_count=1,
        )

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    def _make_entry(
        self,
        trigger: str,
        outcome: str,
        root_cause: str,
        suggested_action: str,
        occurrence_count: int,
    ) -> "MemoryEntry":
        from mini_agent.perception.memory_store import MemoryEntry
        return MemoryEntry(
            session_id=self.session_id,
            summary="",
            key_outcomes=[],
            tags=["lesson", "rule_triggered"],
            model=self.model,
            entry_type="lesson",
            trigger=trigger,
            outcome=outcome,
            root_cause=root_cause,
            suggested_action=suggested_action,
            confidence=_RULE_TRIGGERED_CONFIDENCE,
            occurrence_count=occurrence_count,
            source="self_reflection",
        )

    @staticmethod
    def _safe_repr(tool_input: dict, max_len: int = 120) -> str:
        try:
            import json as _json
            s = _json.dumps(tool_input, ensure_ascii=False)
        except Exception:
            s = str(tool_input)
        return s if len(s) <= max_len else s[:max_len] + "…"
