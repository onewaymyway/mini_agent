"""evolution/relevance_threshold_calibration.py — 阈值自校准（P3）。

设计背景见
next_doc/external_knowledge_feedback_loop_improvement_plan.md §3 P3：
`goal_relevance.py::DEFAULT_PREFILTER_THRESHOLD = 0.12` 是一个"先给宽松默认值，
跑一段时间观察"的硬编码值，注释里写明意图但从未有过回头校准的机制。

本模块做的事：周期性回看 `goal_relevance_candidates.jsonl` 中 Stage②
（`goal_relevance.py::run_goal_relevance_judge_once()`）已经判定过的候选，
按"最终被判定为 relevant 的比例"这一个信号，对 Stage①阈值做小步长、有
上下限的自动微调，并把每次调整的前后值和依据记录下来：

  - `relevant_rate` 明显偏低（大量通过 Stage①的候选最终被 LLM 判定为不相关）
    → 说明 Stage①筛得太松，调高阈值收紧，减少 Stage② LLM 空转成本。
  - `relevant_rate` 明显偏高（几乎所有通过 Stage①的候选都被判定为相关）
    → 说明 Stage①可能筛得偏紧、存在漏判风险，小步调低阈值。
  - 落在健康区间内 → 不调整。

关键风险与本模块的应对（§3 P3 明确要求）：
  1. 需要先有足够样本量才允许调整：首次调整前要求校准状态"存在"已超过
     `MIN_WARMUP_SECONDS`（默认 28 天，对齐"至少积累 4 周判定数据"），且
     每次参与计算的样本数不低于 `MIN_SAMPLE_SIZE`；样本不足直接跳过，
     不强行调整。
  2. 需要一个人工一键回滚到默认阈值的逃生通道：`reset_relevance_threshold()`，
     供人工在校准逻辑跑偏时调用，把当前阈值重置回
     `DEFAULT_PREFILTER_THRESHOLD` 并清空调整历史（保留一条"人工重置"的
     审计记录）。

不引入 LLM 调用（只读 Stage②已经产出的判定结果做统计），符合 §2 设计目标 2。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:relevance_threshold_calibration"

# 首次调整前要求校准状态已经"存在"超过这个时长——对齐设计里"建议至少积累
# 4 周判定数据后才允许首次调整"的风险控制。
MIN_WARMUP_SECONDS = 28 * 24 * 3600

# 单次调整参与统计的最小样本量（judged 且带 relevant 字段的候选数），
# 不足则跳过本次调整，避免小样本噪声驱动阈值漂移。
MIN_SAMPLE_SIZE = 20

# relevant_rate 健康区间：低于下界视为"筛得太松"，高于上界视为"筛得偏紧"，
# 区间内不调整。
LOW_HEALTHY_RATE = 0.15
HIGH_HEALTHY_RATE = 0.5

# 每次调整的步长与上下限——"小步长、有上下限"（§3 P3）。
ADJUSTMENT_STEP = 0.01
THRESHOLD_MIN = 0.05
THRESHOLD_MAX = 0.4

# 调整历史最多保留多少条，超过按"先进先出"裁剪，避免文件无限增长
# （风格对齐 wiki_utility_audit.py 的滚动裁剪思路）。
MAX_HISTORY_ENTRIES = 100


def _default_threshold() -> float:
    from mini_agent.external_input.goal_relevance import DEFAULT_PREFILTER_THRESHOLD
    return DEFAULT_PREFILTER_THRESHOLD


@dataclass
class CalibrationState:
    current_threshold: float
    created_at: float
    last_calibrated_at: Optional[float] = None
    last_reviewed_created_at: float = 0.0
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "current_threshold": self.current_threshold,
            "created_at": self.created_at,
            "last_calibrated_at": self.last_calibrated_at,
            "last_reviewed_created_at": self.last_reviewed_created_at,
            "history": self.history,
        }


@dataclass
class CalibrationSummary:
    """一次校准巡检的执行摘要，供本地回调 handler / 日志使用。"""

    sample_size: int = 0
    relevant_rate: Optional[float] = None
    adjusted: bool = False
    old_threshold: Optional[float] = None
    new_threshold: Optional[float] = None
    reason: str = ""
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _state_path(paths: "AgentPaths"):
    return paths.external_input_relevance_threshold_state


def load_calibration_state(paths: "AgentPaths") -> CalibrationState:
    """读取当前校准状态，文件不存在/损坏则返回一份新初始化的状态
    （current_threshold = DEFAULT_PREFILTER_THRESHOLD，created_at = now）。
    不落盘——由调用方决定是否需要持久化这份新初始状态。
    """
    p = _state_path(paths)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return CalibrationState(
                current_threshold=float(data.get("current_threshold", _default_threshold())),
                created_at=float(data.get("created_at", time.time())),
                last_calibrated_at=data.get("last_calibrated_at"),
                last_reviewed_created_at=float(data.get("last_reviewed_created_at", 0.0)),
                history=list(data.get("history", [])),
            )
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.evolution.relevance_threshold_calibration.load_calibration_state")
    return CalibrationState(current_threshold=_default_threshold(), created_at=time.time())


def load_calibrated_threshold(paths: "AgentPaths") -> float:
    """Stage①调用方（`autonomous_loop.py::_tick_maintenance()`）用来获取
    "当前生效阈值"的轻量入口——文件不存在时直接返回默认值，不触发落盘
    （避免每个 tick 都写一次状态文件）。"""
    try:
        return load_calibration_state(paths).current_threshold
    except Exception:
        return _default_threshold()


def _save_state(paths: "AgentPaths", state: CalibrationState) -> None:
    p = _state_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def reset_relevance_threshold(paths: "AgentPaths") -> CalibrationState:
    """人工一键回滚逃生通道（§3 P3 风险应对 2）：把当前阈值重置回
    `DEFAULT_PREFILTER_THRESHOLD`，清空调整历史，但保留一条"人工重置"的
    审计记录（而不是连痕迹都不留），并把 `created_at` 重置为 now——
    等价于重新开始一轮 warmup 计时，避免重置后立刻又基于重置前的旧样本
    触发一次新的自动调整。
    """
    old = load_calibration_state(paths)
    now = time.time()
    new_state = CalibrationState(
        current_threshold=_default_threshold(),
        created_at=now,
        last_calibrated_at=None,
        last_reviewed_created_at=old.last_reviewed_created_at,
        history=[{
            "at": now,
            "old_threshold": old.current_threshold,
            "new_threshold": _default_threshold(),
            "reason": "manual_reset",
            "sample_size": 0,
        }],
    )
    _save_state(paths, new_state)
    return new_state


def _load_judged_candidates(paths: "AgentPaths") -> list[dict]:
    p = paths.external_input_goal_relevance_candidates
    if not p.exists():
        return []
    records: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        # 只看已经判定过、且成功解析出 relevant 字段的记录（§9.1 风格：
        # 解析失败的候选虽然 judged=True，但不写 relevant 字段，此处
        # 按"跳过"处理，不当成 False 参与统计，避免误判"解析失败"为
        # "判定不相关"进而拉低 relevant_rate）。
        if rec.get("judged") and "relevant" in rec:
            records.append(rec)
    return records


def run_relevance_threshold_calibration_once(paths: "AgentPaths") -> CalibrationSummary:
    """扫描一次已判定候选，按 relevant_rate 决定是否微调阈值。

    只统计 `created_at` 晚于上次校准 `last_reviewed_created_at` 游标的
    候选（避免每次都把历史上所有候选重新算一遍、旧样本反复影响新调整），
    无论本次是否触发调整，游标都会前移到"本次已看过的最大 created_at"，
    避免同一批候选被反复计入下一轮统计。
    """
    summary = CalibrationSummary()
    state = load_calibration_state(paths)

    try:
        judged = _load_judged_candidates(paths)
    except Exception as exc:
        summary.errors.append(f"load_failed: {exc}")
        return summary

    new_batch = [r for r in judged if float(r.get("created_at", 0.0) or 0.0) > state.last_reviewed_created_at]
    summary.sample_size = len(new_batch)

    if not new_batch:
        return summary

    max_created_at = max(float(r.get("created_at", 0.0) or 0.0) for r in new_batch)

    now = time.time()
    is_warmed_up = (now - state.created_at) >= MIN_WARMUP_SECONDS
    enough_samples = summary.sample_size >= MIN_SAMPLE_SIZE

    if not (is_warmed_up and enough_samples):
        # 样本不足/仍在 warmup 期：游标依然前移（避免这批候选被下一次
        # 运行重复计入、导致样本量虚高），但不调整阈值。
        state.last_reviewed_created_at = max_created_at
        try:
            _save_state(paths, state)
        except Exception as exc:
            summary.errors.append(f"save_failed: {exc}")
        summary.reason = "warmup" if not is_warmed_up else "insufficient_samples"
        return summary

    relevant_count = sum(1 for r in new_batch if r.get("relevant"))
    relevant_rate = relevant_count / summary.sample_size
    summary.relevant_rate = round(relevant_rate, 4)

    old_threshold = state.current_threshold
    new_threshold = old_threshold
    reason = "within_healthy_range"
    if relevant_rate < LOW_HEALTHY_RATE:
        new_threshold = min(THRESHOLD_MAX, old_threshold + ADJUSTMENT_STEP)
        reason = "relevant_rate_too_low_tighten"
    elif relevant_rate > HIGH_HEALTHY_RATE:
        new_threshold = max(THRESHOLD_MIN, old_threshold - ADJUSTMENT_STEP)
        reason = "relevant_rate_too_high_loosen"

    summary.reason = reason
    state.last_reviewed_created_at = max_created_at

    if new_threshold != old_threshold:
        summary.adjusted = True
        summary.old_threshold = old_threshold
        summary.new_threshold = new_threshold
        state.current_threshold = new_threshold
        state.last_calibrated_at = now
        state.history.append({
            "at": now,
            "old_threshold": old_threshold,
            "new_threshold": new_threshold,
            "reason": reason,
            "relevant_rate": summary.relevant_rate,
            "sample_size": summary.sample_size,
        })
        if len(state.history) > MAX_HISTORY_ENTRIES:
            state.history = state.history[-MAX_HISTORY_ENTRIES:]

    try:
        _save_state(paths, state)
    except Exception as exc:
        summary.errors.append(f"save_failed: {exc}")

    return summary


def ensure_relevance_threshold_calibration_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
    *, schedule: str = "interval:604800",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:relevance_threshold_calibration`
    （零 LLM 成本，本地回调 handler，跟 `candidate_queue_triage.py`/
    `wiki_utility_audit.py` 同构）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="GoalRelevance 阈值自校准",
        schedule=schedule,
        description=(
            "回看 goal_relevance_candidates.jsonl 中 Stage②已判定候选的 "
            "relevant 比例，对 Stage①阈值做小步长、有上下限的自动微调，"
            "零 LLM 成本。"
        ),
        tags=["maintenance", "goal_relevance"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_relevance_threshold_calibration_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "MIN_WARMUP_SECONDS",
    "MIN_SAMPLE_SIZE",
    "LOW_HEALTHY_RATE",
    "HIGH_HEALTHY_RATE",
    "ADJUSTMENT_STEP",
    "THRESHOLD_MIN",
    "THRESHOLD_MAX",
    "CalibrationState",
    "CalibrationSummary",
    "load_calibration_state",
    "load_calibrated_threshold",
    "reset_relevance_threshold",
    "run_relevance_threshold_calibration_once",
    "ensure_relevance_threshold_calibration_job",
]
