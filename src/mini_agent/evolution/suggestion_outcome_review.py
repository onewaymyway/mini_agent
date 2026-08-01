"""evolution/suggestion_outcome_review.py — 建议采纳率回看
（自诊断闭环深化 P2）。

设计背景见
next_doc/self_diagnosis_feedback_loop_deepening_plan.md §2 P2：
`self_maintenance.py` 只负责"发现问题、生成建议"，从不回头检查"这条建议
后来有没有被采纳、采纳后指标是否真的改善"。这导致巡检本身的建议质量无法
被验证——如果同一个工具连续几周都被标记为"失败率过高"，说明建议要么没人
看、要么看了但没解决问题，这本身就是有价值的信号，但现在完全没人追踪。

本阶段范围（刻意收窄，见下方"范围与取舍"）：
  - 只做 stale_tools 的回看：工具健康数据完全来自持久化的 traces.jsonl，
    可以在没有任何运行时对象（skill_loader/memory_backend）的情况下，从
    一个独立的 cron job 里重新计算"当时的指标"和"现在的指标"做对比。
  - stale_skills/conflicting_lessons 的回看本阶段不做，见下方说明。

范围与取舍（为什么不做 skill/lesson 回看）：
  `skills/tracker.py::SkillUsageTracker` 是纯内存对象，生命周期绑定单个
  `SkillLoader` 实例，没有跨 session 持久化文件可供独立 cron job 读取
  （只有"当前活跃 session 里此刻的调用记录"，不是"历史上某个时间点的调用
  记录"）。要做 skill 侧回看，需要先给 tracker 补一个持久化层，这是比
  "回看"本身更大的改动，超出本阶段范围，记录在计划文档"待讨论问题"里，
  留待有实际需要时再评估是否值得为此新增持久化基础设施。conflicting_lessons
  同理——lesson 是否被"解决"没有客观可复算的指标（不像失败率是纯数字），
  需要人工判断，机器回看意义有限。

判定方式：对比"建议提出时"（health_report 落盘时随附的 failure_rate/
call_count）和"回看时"（重新扫描 traces.jsonl 得到的当前 failure_rate/
call_count），标记：
  - improved         —— 当前失败率明显低于建议提出时
  - worse            —— 当前失败率明显高于建议提出时
  - unchanged        —— 两者接近，视为没有实质变化
  - no_action_taken  —— 该工具在回看窗口内完全没有被调用（call_count=0），
                         无法判断是否被"修好了"还是"干脆没人用了"，这一区分
                         本身也作为输出的一部分，不强行归类为 improved

不做：不据此自动调整任何阈值、不自动禁用/移除工具，纯报告。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:suggestion_outcome_review"

# 回看窗口：只回看"提出时间"落在 [now - REVIEW_MAX_AGE_DAYS, now - REVIEW_MIN_AGE_DAYS]
# 区间内的建议——太新的建议还没给用户足够时间处理，太旧的建议大概率已经被
# 更新的 health_report 覆盖（同一个 subject 后续又被重新标记），没有回看
# 价值。计划文档里定的"2-4 周"节奏。
REVIEW_MIN_AGE_DAYS = 14.0
REVIEW_MAX_AGE_DAYS = 42.0

# 失败率变化超过此绝对值才判定为 improved/worse，避免统计噪声导致的小幅
# 波动被误判为"实质变化"。
_FAILURE_RATE_CHANGE_THRESHOLD = 0.15

_ACTIVITY_DIGEST_SCAN_LIMIT = 500
_TOOL_RECENT_SESSIONS = 20  # 与 self_maintenance.py 的 _TOOL_RECENT_SESSIONS 保持一致


@dataclass
class ToolOutcomeFinding:
    tool_name: str
    baseline_failure_rate: float
    baseline_detected_at: float
    current_failure_rate: Optional[float]
    current_call_count: int
    verdict: str  # "improved" | "worse" | "unchanged" | "no_action_taken"

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "baseline_failure_rate": round(self.baseline_failure_rate, 3),
            "baseline_detected_at": self.baseline_detected_at,
            "current_failure_rate": (
                round(self.current_failure_rate, 3)
                if self.current_failure_rate is not None else None
            ),
            "current_call_count": self.current_call_count,
            "verdict": self.verdict,
        }


@dataclass
class OutcomeReviewSummary:
    findings: list[ToolOutcomeFinding] = field(default_factory=list)
    reviewed_subjects: int = 0
    errors: list[str] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at,
            "reviewed_subjects": self.reviewed_subjects,
            "findings": [f.to_dict() for f in self.findings],
        }


# ── 读取历史建议基线 ──────────────────────────────────────────────────────────

def _collect_tool_baselines(paths: "AgentPaths", now: float) -> dict[str, dict]:
    """扫描 activity_digest.jsonl，收集落在回看窗口内、且尚未被回看过的
    stale_tools 建议基线。同一 tool_name 若在窗口内出现多次，取最早一次
    （最接近"建议首次提出"的时间点），跟"是否已被回看过"的去重状态一起
    由调用方维护（本函数只负责收集候选，不做去重状态判断）。"""
    p = paths.workdir_dir / "activity_digest.jsonl"
    if not p.exists():
        return {}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-_ACTIVITY_DIGEST_SCAN_LIMIT:]
    except Exception:
        return {}

    window_start = now - REVIEW_MAX_AGE_DAYS * 86400.0
    window_end = now - REVIEW_MIN_AGE_DAYS * 86400.0

    baselines: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "health_report":
            continue
        at = float(d.get("at", 0.0) or 0.0)
        if not (window_start <= at <= window_end):
            continue
        for f in d.get("stale_tools", []) or []:
            name = f.get("tool_name", "")
            if not name:
                continue
            existing = baselines.get(name)
            if existing is None or at < existing["detected_at"]:
                baselines[name] = {
                    "detected_at": at,
                    "failure_rate": float(f.get("failure_rate", 0.0) or 0.0),
                }
    return baselines


def _current_tool_stats(paths: "AgentPaths", tool_name: str) -> tuple[int, int]:
    """重新扫描最近若干 session 的 traces.jsonl，返回 (call_count, error_count)。

    与 self_maintenance.py::_check_tool_health() 的扫描逻辑同构，但不做
    阈值/样本量过滤——回看需要即使调用次数很少也能看到"确实变好了"，
    而不是像发现新问题时那样只关心"样本量够大、失败率够高"的情况。
    刻意保留独立实现而不是复用 self_maintenance 的私有方法，避免跨模块
    依赖内部实现细节；两处逻辑简单且稳定，重复维护成本可接受。
    """
    try:
        sessions_root = paths.sessions_dir
    except Exception:
        return (0, 0)
    if not sessions_root.exists():
        return (0, 0)

    session_dirs = sorted(
        [d for d in sessions_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[:_TOOL_RECENT_SESSIONS]

    calls = 0
    errors = 0
    for sd in session_dirs:
        traces_path = sd / "traces.jsonl"
        if not traces_path.exists():
            continue
        try:
            with open(traces_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("phase") != "tool_call":
                        continue
                    if entry.get("tool_name", "") != tool_name:
                        continue
                    calls += 1
                    if entry.get("is_error"):
                        errors += 1
        except Exception:
            continue
    return (calls, errors)


def _judge(baseline_rate: float, current_rate: Optional[float], current_calls: int) -> str:
    if current_calls == 0 or current_rate is None:
        return "no_action_taken"
    delta = current_rate - baseline_rate
    if delta <= -_FAILURE_RATE_CHANGE_THRESHOLD:
        return "improved"
    if delta >= _FAILURE_RATE_CHANGE_THRESHOLD:
        return "worse"
    return "unchanged"


# ── 去重状态（避免同一条基线被反复回看产出重复报告）────────────────────────────

def _state_path(paths: "AgentPaths"):
    return paths.workdir_dir / "suggestion_outcome_review_state.json"


def _load_reviewed_subjects(paths: "AgentPaths") -> dict[str, float]:
    """subject -> baseline_detected_at，记录"这个基线已经被回看过"，避免
    同一条建议在接下来几周的每次 job 运行里都重复出报告。"""
    p = _state_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_reviewed_subjects(paths: "AgentPaths", reviewed: dict[str, float]) -> None:
    p = _state_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(reviewed, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run_suggestion_outcome_review_once(paths: "AgentPaths") -> OutcomeReviewSummary:
    summary = OutcomeReviewSummary()
    now = time.time()

    try:
        baselines = _collect_tool_baselines(paths, now)
    except Exception as exc:
        summary.errors.append(f"collect_baselines_failed: {exc}")
        return summary

    reviewed = _load_reviewed_subjects(paths)

    for tool_name, baseline in baselines.items():
        key = f"tool:{tool_name}"
        prior_reviewed_at = reviewed.get(key)
        # 同一 baseline_detected_at 已经回看过，跳过；detected_at 变化说明
        # 出现了更早的一条基线（理论上不太会发生，稳妥起见按新基线重新判定）。
        if prior_reviewed_at == baseline["detected_at"]:
            continue

        try:
            call_count, error_count = _current_tool_stats(paths, tool_name)
        except Exception as exc:
            summary.errors.append(f"current_stats_failed[{tool_name}]: {exc}")
            continue

        current_rate = (error_count / call_count) if call_count else None
        verdict = _judge(baseline["failure_rate"], current_rate, call_count)

        summary.findings.append(ToolOutcomeFinding(
            tool_name=tool_name,
            baseline_failure_rate=baseline["failure_rate"],
            baseline_detected_at=baseline["detected_at"],
            current_failure_rate=current_rate,
            current_call_count=call_count,
            verdict=verdict,
        ))
        reviewed[key] = baseline["detected_at"]
        summary.reviewed_subjects += 1

    if summary.findings:
        try:
            from mini_agent.evolution.self_maintenance import append_digest_record
            append_digest_record(paths, {
                "type": "suggestion_outcome_review",
                "summary": (
                    f"建议采纳率回看：{summary.reviewed_subjects} 项，"
                    + "、".join(f"{f.tool_name}={f.verdict}" for f in summary.findings)
                ),
                **summary.to_dict(),
            }, initiator="suggestion_outcome_review")
        except Exception as exc:
            summary.errors.append(f"digest_write_failed: {exc}")

    try:
        _save_reviewed_subjects(paths, reviewed)
    except Exception as exc:
        summary.errors.append(f"save_state_failed: {exc}")

    return summary


def ensure_suggestion_outcome_review_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:suggestion_outcome_review`
    （零 LLM 成本，本地回调 handler）。按计划文档"2 周一次"的节奏，
    interval 设为 14 天。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="建议采纳率回看",
        schedule=f"interval:{int(14 * 86400)}",
        description=(
            "回看 2-4 周前 self_maintenance 提出的工具健康建议，对比当前失败率"
            "判断是否改善，零 LLM 成本。"
        ),
        tags=["maintenance", "self_awareness"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_suggestion_outcome_review_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "REVIEW_MIN_AGE_DAYS",
    "REVIEW_MAX_AGE_DAYS",
    "ToolOutcomeFinding",
    "OutcomeReviewSummary",
    "run_suggestion_outcome_review_once",
    "ensure_suggestion_outcome_review_job",
]
