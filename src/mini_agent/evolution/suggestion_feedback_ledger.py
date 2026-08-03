"""evolution/suggestion_feedback_ledger.py — 建议采纳/拒绝累积权重账本（F3）

背景见 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
断点 C3、C4：`soft_goal_deriver.py` 对"用户 reject 过的 Goal"只做 30 天
TTL 去重，`improvement_backlog_merge.py` 每次重新计算分数、不参考"这类
建议历史上是否反复被拒绝"。两条链路各自独立开发，缺一个共享的、不过期
的"建议反馈账本"。

本模块只提供一个轻量级、零 LLM 的账本读写接口：
  - `record_outcome(paths, category, outcome)` — 累加 accepted/rejected 计数
  - `get_weight(paths, category)` — 返回 [0, 1] 区间的衰减系数，供调用方
    乘到自己的分数/urgency 上

不做：不决定"多少次拒绝就该彻底屏蔽"（调用方自行决定阈值），不跨类别
共享权重（category 由调用方定义，可以是 dedupe_key、也可以是更粗的
"来源:kind"），不做时间衰减（保持最简单的累积计数，衰减策略留待后续
观察真实数据再决定是否需要引入半衰期）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

Outcome = Literal["accepted", "rejected"]

# 衰减配置：rejected 计数达到阈值且 accepted 为 0 时，权重打折。
# 刻意选择"打折"而非"归零"——避免系统单方面永久否决某个方向，人工在看板/
# CLI 里仍能看到被降权的候选（见方案文档"风险与克制原则"）。
_REJECTED_THRESHOLD = 3
_DECAY_WEIGHT = 0.7  # 命中阈值时的权重（乘法因子）
_ACCEPTED_BONUS_THRESHOLD = 2
_BONUS_WEIGHT = 1.15  # 历史采纳较多时的小幅加成


@dataclass
class SuggestionFeedbackEntry:
    accepted: int = 0
    rejected: int = 0
    last_outcome_ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "last_outcome_ts": self.last_outcome_ts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SuggestionFeedbackEntry":
        return cls(
            accepted=int(d.get("accepted", 0) or 0),
            rejected=int(d.get("rejected", 0) or 0),
            last_outcome_ts=float(d.get("last_outcome_ts", 0.0) or 0.0),
        )


def _ledger_path(paths: "AgentPaths"):
    return getattr(paths, "suggestion_feedback_ledger_path", None) or (
        paths.workdir_dir / "suggestion_feedback_ledger.json"
    )


def _load_ledger(paths: "AgentPaths") -> dict[str, dict]:
    p = _ledger_path(paths)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # [P0 补测试发现的边界情况] 账本文件内容是合法 JSON 但顶层不是
        # dict（如被误写成 list，或磁盘损坏后残留半截结构）时，不能直接
        # 把非 dict 值返回给调用方——.get(category, {}) 之类的下游调用
        # 会因此抛 AttributeError。这里统一退化为空账本，与"文件不存在"
        # 同等对待，保持本模块一贯的"失败不阻断主流程"风格。
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.suggestion_feedback_ledger._load_ledger")
        return {}


def _save_ledger(paths: "AgentPaths", data: dict[str, dict]) -> None:
    p = _ledger_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.suggestion_feedback_ledger._save_ledger")


def record_outcome(paths: "AgentPaths", category: str, outcome: Outcome) -> None:
    """记录一次建议的采纳/拒绝结果。category 由调用方定义（如 dedupe_key
    或粗粒度的 "source:kind"），累积计数不过期。"""
    if not category:
        return
    data = _load_ledger(paths)
    entry = SuggestionFeedbackEntry.from_dict(data.get(category, {}))
    if outcome == "accepted":
        entry.accepted += 1
    elif outcome == "rejected":
        entry.rejected += 1
    entry.last_outcome_ts = time.time()
    data[category] = entry.to_dict()
    _save_ledger(paths, data)


def get_weight(paths: "AgentPaths", category: str) -> float:
    """返回该类别建议的分数衰减/加成系数（乘法因子，默认 1.0 = 无影响）。

    规则（纯规则，无 LLM）：
      - rejected >= _REJECTED_THRESHOLD 且 accepted == 0 → 打折（_DECAY_WEIGHT）
      - accepted >= _ACCEPTED_BONUS_THRESHOLD → 小幅加成（_BONUS_WEIGHT）
      - 其余情况 → 1.0（无历史或历史不够明确）
    """
    if not category:
        return 1.0
    data = _load_ledger(paths)
    entry = SuggestionFeedbackEntry.from_dict(data.get(category, {}))
    if entry.rejected >= _REJECTED_THRESHOLD and entry.accepted == 0:
        return _DECAY_WEIGHT
    if entry.accepted >= _ACCEPTED_BONUS_THRESHOLD:
        return _BONUS_WEIGHT
    return 1.0


def get_entry(paths: "AgentPaths", category: str) -> SuggestionFeedbackEntry:
    """供月度回顾一类只读汇报消费，返回原始计数（不做衰减计算）。"""
    data = _load_ledger(paths)
    return SuggestionFeedbackEntry.from_dict(data.get(category, {}))


def all_categories(paths: "AgentPaths") -> dict[str, SuggestionFeedbackEntry]:
    """返回账本里全部类别的原始记录，供统计/汇报使用。"""
    data = _load_ledger(paths)
    return {k: SuggestionFeedbackEntry.from_dict(v) for k, v in data.items()}
