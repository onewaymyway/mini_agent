"""
skills/tracker.py — Skill 调用追踪与压缩上下文重建

实现类似 Claude Code 的 skill compact 机制：
  - 追踪每个 skill 的调用历史（时间戳 + 调用次数），按最近调用排序（LRU）
  - 压缩时按 LRU 顺序依次填充 skill 内容，受两层 budget 约束：
      · per_skill_tokens  — 单个 skill 最多贡献的 token 数（默认 5000）
      · total_budget      — 所有 skill 共享的总 token 预算（默认 25000）
  - 较早调用的 skill 若 budget 耗尽则被完全丢弃（与 Claude Code 行为一致）

保护机制（protected set）：
  - 受保护的 skill（当前正在使用的 skill）不受 per_skill_tokens 截断
  - 受保护的 skill 不受 total_budget 限制，即使预算耗尽也强制写入
  - 若 skill_contents 只有 1 个条目，无论是否受保护，均不截断（单 skill 豁免）

SkillUsageTracker 可独立使用，也可通过 SkillLoader 集成。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

# Python 3.9 兼容：set[str] 用 Set[str] 或直接用 set
from typing import Set


@dataclass
class SkillCallRecord:
    """单个 skill 的调用记录。"""
    name:         str
    last_called:  float = field(default_factory=time.time)   # Unix timestamp
    call_count:   int   = 0

    def touch(self) -> None:
        """更新最近调用时间并累计次数。"""
        self.last_called = time.time()
        self.call_count += 1


class SkillUsageTracker:
    """
    追踪 skill 调用历史，并在压缩时按 LRU 顺序重建 skill 上下文。

    Attributes:
        per_skill_tokens:  单个普通 skill 最多贡献的 token 数
        total_budget:      所有普通 skill 共享的总 token 预算

    Token 估算采用「字符数 // 4」的粗估方式，与 claude code 做法一致；
    如需精确计数可替换 _estimate_tokens()。
    """

    CHARS_PER_TOKEN = 4   # 粗估：1 token ≈ 4 字符

    def __init__(
        self,
        per_skill_tokens: int = 5_000,
        total_budget:     int = 25_000,
    ) -> None:
        self.per_skill_tokens = per_skill_tokens
        self.total_budget     = total_budget
        self._records: dict[str, SkillCallRecord] = {}

    # ── 记录调用 ───────────────────────────────────────────────────────────────

    def record(self, name: str) -> None:
        """记录一次 skill 调用（激活或 build_context 时调用）。"""
        if name in self._records:
            self._records[name].touch()
        else:
            rec = SkillCallRecord(name=name)
            rec.touch()
            self._records[name] = rec

    def forget(self, name: str) -> None:
        """从追踪记录中移除一个 skill（卸载时可选择调用）。"""
        self._records.pop(name, None)

    def clear(self) -> None:
        """清空所有追踪记录。"""
        self._records.clear()

    # ── 查询 ───────────────────────────────────────────────────────────────────

    @property
    def records(self) -> list[SkillCallRecord]:
        """按最近调用时间降序返回所有记录（最新在前）。"""
        return sorted(
            self._records.values(),
            key=lambda r: r.last_called,
            reverse=True,
        )

    def recent_names(self) -> list[str]:
        """按最近调用顺序返回 skill 名称列表。"""
        return [r.name for r in self.records]

    def get_record(self, name: str) -> Optional[SkillCallRecord]:
        return self._records.get(name)

    def summary_lines(self) -> list[str]:
        """返回人类可读的调用摘要，用于终端展示。"""
        if not self._records:
            return ["  (no skills called yet)"]
        lines = []
        for rec in self.records:
            ts = time.strftime("%H:%M:%S", time.localtime(rec.last_called))
            lines.append(
                f"  {rec.name:<22} calls={rec.call_count}  last={ts}"
            )
        return lines

    # ── 压缩上下文构建 ─────────────────────────────────────────────────────────

    def build_compact_context(
        self,
        skill_contents: dict[str, str],
        protected:      Optional[Set[str]] = None,
    ) -> tuple[str, list[str], list[str]]:
        """
        按 LRU 顺序填充 skill 内容，受双层 budget 约束，支持 skill 保护。

        保护规则：
          1. protected 集合中的 skill 不受 per_skill_tokens 截断——全文写入。
          2. protected 集合中的 skill 不受 total_budget 限制——强制写入。
          3. skill_contents 只有 1 个条目时，无论是否在 protected 中，均不截断。
             （单 skill 豁免：截断单个 skill 毫无意义）

        Args:
            skill_contents: name → full content 映射
            protected:      需要保护的 skill 名称集合（通常是当前 active skill）；
                            None / 空集合 = 不保护任何 skill

        Returns:
            (compact_text, included_names, dropped_names)
            - compact_text:   拼接好的 skill 重附上下文字符串
            - included_names: 本次实际写入的 skill 名称列表（LRU 排序，保护 skill 在前）
            - dropped_names:  因 budget 耗尽被丢弃的 skill 名称列表
        """
        if protected is None:
            protected = set()

        # 规则 3：只有一个 skill，直接保护
        only_one = (len(skill_contents) == 1)

        # 排列顺序：LRU 已追踪 + 未追踪的追加到末尾
        ordered = [n for n in self.recent_names() if n in skill_contents]
        untracked = [n for n in skill_contents if n not in self._records]
        ordered += untracked

        # 受保护的优先处理，确保它们一定在最前面写入
        protected_ordered   = [n for n in ordered if (n in protected or only_one)]
        unprotected_ordered = [n for n in ordered if n not in protected_ordered]

        parts:      list[str] = []
        included:   list[str] = []
        dropped:    list[str] = []
        total_used: int       = 0

        # ── 第一轮：写入受保护 skill（不截断、不检查预算）────────────────────
        for name in protected_ordered:
            content  = skill_contents[name]
            tok_cost = self._estimate_tokens(content)
            parts.append(f"### Skill: {name}\n\n{content}")
            included.append(name)
            total_used += tok_cost

        # ── 第二轮：写入普通 skill（截断 + 预算检查）────────────────────────
        for name in unprotected_ordered:
            content  = skill_contents[name]
            clipped  = self._clip(content, self.per_skill_tokens)
            tok_cost = self._estimate_tokens(clipped)

            if total_used + tok_cost > self.total_budget:
                dropped.append(name)
                continue

            parts.append(f"### Skill: {name}\n\n{clipped}")
            included.append(name)
            total_used += tok_cost

        if not parts:
            return "", [], list(ordered)

        # 构建 header，标注保护信息
        protected_in_output = [n for n in included if (n in protected or only_one)]
        note = ""
        if protected_in_output:
            reason = "single-skill exemption" if only_one else "active skill protection"
            note = f", protected ({reason}): {protected_in_output}"

        header = (
            f"<!-- skill-compact: {len(included)} skill(s) re-attached, "
            f"~{total_used} tokens used of {self.total_budget} budget"
            f"{note} -->\n\n"
        )
        return header + "\n\n---\n\n".join(parts), included, dropped

    # ── 内部工具 ───────────────────────────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    def _clip(self, content: str, max_tokens: int) -> str:
        """将内容截断到 max_tokens 以内（字符粒度，保留行完整性）。"""
        max_chars = max_tokens * self.CHARS_PER_TOKEN
        if len(content) <= max_chars:
            return content
        clipped = content[:max_chars]
        # 尽量在换行处截断，避免破坏行内容
        last_nl = clipped.rfind("\n")
        if last_nl > max_chars * 0.8:
            clipped = clipped[:last_nl]
        omitted_chars  = len(content) - len(clipped)
        omitted_tokens = omitted_chars // self.CHARS_PER_TOKEN
        return clipped + f"\n\n... [{omitted_tokens} tokens omitted due to per-skill budget] ..."
