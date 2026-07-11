"""
evolution/outcome_tracker.py — 自我进化"用户真实反馈"闭环指标

对应 next_doc/priority_improvements_implementation_plan.md 方案三。

现有验证链路（evolution/validators.py T0~T3、evolution/eval_runner.py）比较的
都是过程指标：schema 校验、lint/类型检查、单测、eval 场景对比（tool 失败率/
turns/token）。这些指标衡量的是"这次自我修改有没有引入明显的技术性回归"，
本模块补的是另一条正交的信号："这次修改是否真的解决了它声称要解决的问题"——
即触发这次 skill_propose/self-evolution 的那个 lesson group，在修改落地之后，
是否真的不再高频出现。

设计原则：
  - 不改变 T0~T3 验证流水线（那是 merge 前的门槛，保持不变）；本模块是
    commit 落地之后的、异步的"效果回填"，两者互补不冲突。
  - 只产生建议，不自动执行 revert——与 SoftGoalDeriver 推导出的 Goal 需要
    人工 accept/reject 是同一套设计哲学：自动化到"提出建议"为止。
  - 失败静默降级：tick() 内部任何异常都不应阻断调用方（Phase G）主流程。
  - 复用现有统计能力：触发次数统计直接调用 perception/lesson_review.py
    的 LessonGroup 聚合逻辑，不重新实现一套计数器。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_TRACKING_FILENAME = "outcome_tracking.json"

# 观察期默认长度（天）：与 baseline 统计窗口对齐
DEFAULT_OBSERVATION_WINDOW_DAYS = 14
# 判定为 "improved" 的下降比例阈值：触发次数下降 ≥ 50% 才算显著改善
IMPROVED_DROP_RATIO = 0.5
# 判定为 "worsened" 的上升比例阈值：触发次数上升 ≥ 20% 才算变差（避免噪声）
WORSENED_RISE_RATIO = 0.2
# 基线样本量过小时不参与判定（避免小样本噪声误导用户）
MIN_BASELINE_COUNT = 3

_VALID_STATUS = ("observing", "resolved")
_VALID_VERDICT = ("improved", "no_change", "worsened", "insufficient_data", "reverted_by_user")


@dataclass
class TrackedCommit:
    """一条 self-evolution commit 的效果回填追踪记录。"""

    commit_id: str
    trigger_lesson_group_id: str
    committed_at: float
    baseline_trigger_count: int
    baseline_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS
    observation_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS
    observation_deadline: float = 0.0
    status: str = "observing"          # observing → resolved
    post_trigger_count: Optional[int] = None
    verdict: Optional[str] = None      # improved / no_change / worsened / insufficient_data / reverted_by_user
    resolved_at: Optional[float] = None
    commit_summary: str = ""           # 记录时的人类可读摘要（如 skill 名/reason），供 /digest 展示

    def __post_init__(self) -> None:
        if not self.observation_deadline:
            self.observation_deadline = self.committed_at + self.observation_window_days * 86400

    def to_dict(self) -> dict:
        return {
            "commit_id": self.commit_id,
            "trigger_lesson_group_id": self.trigger_lesson_group_id,
            "committed_at": self.committed_at,
            "baseline_trigger_count": self.baseline_trigger_count,
            "baseline_window_days": self.baseline_window_days,
            "observation_window_days": self.observation_window_days,
            "observation_deadline": self.observation_deadline,
            "status": self.status,
            "post_trigger_count": self.post_trigger_count,
            "verdict": self.verdict,
            "resolved_at": self.resolved_at,
            "commit_summary": self.commit_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrackedCommit":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ── 持久化 ─────────────────────────────────────────────────────────────────

def _tracking_path(paths) -> Path:
    return paths.workdir_dir / _TRACKING_FILENAME


def _load_all(paths) -> list[TrackedCommit]:
    p = _tracking_path(paths)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    records = data.get("tracked_commits", [])
    result = []
    for r in records:
        try:
            result.append(TrackedCommit.from_dict(r))
        except Exception:
            continue
    return result


def _save_all(paths, records: list[TrackedCommit]) -> None:
    p = _tracking_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"tracked_commits": [r.to_dict() for r in records]}
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        tmp.unlink(missing_ok=True)


# ── 触发次数统计（复用 perception/lesson_review.py 的分组逻辑）───────────────

def _current_trigger_count(paths, memory_backend, lesson_group_id: str) -> Optional[int]:
    """
    重新对当前所有 lesson 条目分组，找到 key == lesson_group_id 的组，
    返回其 total_occurrence。找不到该组（可能是问题已经完全不再触发，
    lesson_review 里没有任何条目可分组）时返回 0——这是最强的正面信号，
    不是"查询失败"，调用方需要区分这两种情况（返回 None 才是真正的失败）。
    """
    if memory_backend is None or not hasattr(memory_backend, "all_entries"):
        return None
    try:
        from mini_agent.perception.lesson_review import group_lessons

        all_entries = memory_backend.all_entries()
        lesson_entries = [e for e in all_entries if getattr(e, "entry_type", "") == "lesson"]
        groups = group_lessons(lesson_entries)
        for g in groups:
            if g.key == lesson_group_id:
                return g.total_occurrence
        return 0
    except Exception:
        return None


def _lesson_group_baseline(memory_backend, lesson_group_id: str) -> int:
    """record_commit_baseline() 内部调用：commit 落地前一刻的 baseline 计数，
    实现与 _current_trigger_count 相同，只是调用时机不同（不需要 paths 参数
    因为不需要访问追踪文件）。"""
    if memory_backend is None or not hasattr(memory_backend, "all_entries"):
        return 0
    try:
        from mini_agent.perception.lesson_review import group_lessons

        all_entries = memory_backend.all_entries()
        lesson_entries = [e for e in all_entries if getattr(e, "entry_type", "") == "lesson"]
        groups = group_lessons(lesson_entries)
        for g in groups:
            if g.key == lesson_group_id:
                return g.total_occurrence
        return 0
    except Exception:
        return 0


# ── 公开 API ──────────────────────────────────────────────────────────────

def record_commit_baseline(
    paths,
    memory_backend,
    *,
    commit_id: str,
    lesson_group_id: str,
    commit_summary: str = "",
    observation_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS,
) -> None:
    """
    在一次 self-evolution commit 完成后调用（当前接入点：
    tools/evolution.py::skill_propose 成功时，对 source_lessons 中的每个
    lesson group id 各记一条）。记录基线数据，之后交给 tick() 定期检查。

    失败静默：记录失败不应影响 skill_propose 本身的返回结果。
    """
    try:
        baseline_count = _lesson_group_baseline(memory_backend, lesson_group_id)
        records = _load_all(paths)
        # 避免同一个 commit_id 重复记录（skill_propose 理论上不会对同一
        # commit 调用两次，这里只是防御性去重）。
        records = [r for r in records if r.commit_id != commit_id]
        records.append(TrackedCommit(
            commit_id=commit_id,
            trigger_lesson_group_id=lesson_group_id,
            committed_at=time.time(),
            baseline_trigger_count=baseline_count,
            observation_window_days=observation_window_days,
            commit_summary=commit_summary,
        ))
        _save_all(paths, records)
    except Exception:
        import logging
        logging.getLogger(__name__).debug(
            "[outcome_tracker] record_commit_baseline failed", exc_info=True
        )


def mark_reverted(paths, commit_id: str) -> None:
    """
    commit 在观察期内被用户 `/evolution revert` 撤销时调用：提前结束观察，
    因为继续观察一个已经被撤销的 commit 没有意义。
    """
    try:
        records = _load_all(paths)
        changed = False
        for r in records:
            if r.commit_id == commit_id and r.status == "observing":
                r.status = "resolved"
                r.verdict = "reverted_by_user"
                r.resolved_at = time.time()
                changed = True
        if changed:
            _save_all(paths, records)
    except Exception:
        pass


def tick(paths, memory_backend) -> list[TrackedCommit]:
    """
    由 Phase G 周期性维护调用（evolution/phase_g.py::run_phase_g()）。
    检查所有 status=="observing" 且已到 observation_deadline 的记录，
    重新查询该 lesson_group 当前触发计数，计算 verdict。

    返回本次 tick 新解决（resolved）的记录列表，供调用方写进 PhaseGReport /
    /digest 展示。失败静默降级：异常不阻断 Phase G 主流程，返回空列表。
    """
    resolved: list[TrackedCommit] = []
    try:
        records = _load_all(paths)
        now = time.time()
        changed = False
        for r in records:
            if r.status != "observing" or now < r.observation_deadline:
                continue
            post_count = _current_trigger_count(paths, memory_backend, r.trigger_lesson_group_id)
            r.status = "resolved"
            r.resolved_at = now
            if post_count is None:
                # 查询本身失败（memory_backend 不可用等）——不判定，留待下次 tick 重试。
                r.status = "observing"
                continue
            r.post_trigger_count = post_count
            r.verdict = _judge(r.baseline_trigger_count, post_count)
            changed = True
            resolved.append(r)
        if changed:
            _save_all(paths, records)
    except Exception:
        import logging
        logging.getLogger(__name__).debug("[outcome_tracker] tick failed", exc_info=True)
    return resolved


def _judge(baseline: int, post: int) -> str:
    if baseline < MIN_BASELINE_COUNT:
        return "insufficient_data"
    if post == 0:
        return "improved"
    ratio = (baseline - post) / baseline
    if ratio >= IMPROVED_DROP_RATIO:
        return "improved"
    if -ratio >= WORSENED_RISE_RATIO:
        return "worsened"
    return "no_change"


def get_all(paths) -> list[TrackedCommit]:
    """列出所有追踪记录（observing + resolved），供 `/evolution outcomes` 使用。"""
    return _load_all(paths)


def get_revert_candidates(paths) -> list[TrackedCommit]:
    """verdict == "worsened" 的记录——建议用户复核是否要 revert。
    只提供建议，不自动执行 revert（最终决策权留给用户）。"""
    return [r for r in _load_all(paths) if r.verdict == "worsened"]


__all__ = [
    "TrackedCommit",
    "record_commit_baseline",
    "mark_reverted",
    "tick",
    "get_all",
    "get_revert_candidates",
]
