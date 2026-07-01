"""
evolution/self_maintenance.py — 自维护模块（具身改进 v3 C4）

具身来源：Varela 的自创生（autopoiesis）——生物体不只是被动响应环境扰动，
还主动维持自身边界和内部一致性（细胞修复膜损伤、免疫系统清除异常细胞）。
当前 Agent 对自身健康状况是纯被动的：工具调用失败了才知道工具可能有问题，
skill 内容过时了要等到产生错误建议才会被发现，记忆库里出现自相矛盾的
lesson 也不会被主动揪出来——这是"失去免疫系统"的状态。

归属：daemon 后台维护层，与 Phase G（能力/技能层面的扫描）并列，但关注点
不同——Phase G 回答"我学到了什么、该不该提升"，SelfMaintenanceModule 回答
"我自己有没有哪里坏了"。

实现取舍（复用已有数据源，不新增追踪基础设施）：
  - stale_tools：原计划文档描述为"最近 N 天未被成功调用的工具"，但核对
    代码库后发现并不存在跨 session 持久化的"每个工具最后一次成功调用时间"
    记录（SessionStats.tool_stats 只在单个 session 内有效）。改用已经持久化
    的信号：扫描最近若干 session 的 traces.jsonl 里 phase="tool_call" 记录，
    统计每个工具最近调用的失败率——失败率异常高（且样本量足够）的工具，
    比"许久没调用"更直接地提示"这个工具可能已经失效，需要排查"。
  - stale_skills：直接复用 phase_g.py::_days_since_last_use() 同款的
    skill_loader.tracker 基础设施（prune_skills 已经在用，这里只是从
    "高成本+未使用→建议剪枝"的角度换成"长期未使用→可能过时，建议复核"
    的角度，阈值和触发条件都不同，因此不与 prune_skills 合并实现）。
  - conflicting_lessons：复用 perception/lesson_review.py::group_lessons()
    的聚类结果（与 B2 LessonToReminderBridge 同款基础设施）——同一聚类内
    若同时出现"正面建议"（成功/应该/建议/可以）和"负面信号"（失败/不行/
    不应该/出错）的 outcome 文本，标记为可能矛盾。这是启发式而非精确判断，
    生成的是"建议人工复核"，不是确定性结论。
  - 触发方式：与 Phase G 同款"时间门控"模式（不需要常驻线程）——
    SessionEnd 检查距上次运行是否超过 interval，超过则跑一次；同时注册为
    cron job（sys:self_maintain），daemon 模式下也能按计划触发。
  - 不自动修复：health_check() 只产出报告和建议文本，写入
    activity_digest.jsonl（type="health_report"），用户下次连接时在晨报
    中看到——与 v3 §九"保留人类控制权"原则一致。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_STATE_FILENAME = "self_maintenance_state.json"

# 工具健康检查参数
_TOOL_FAILURE_RATE_THRESHOLD = 0.6   # 失败率超过此值视为"可能失效"
_TOOL_MIN_SAMPLES = 3                # 样本量过小时不下结论
_TOOL_RECENT_SESSIONS = 20           # 最多扫描最近多少个 session 的 traces.jsonl

# skill 新鲜度检查参数
_SKILL_STALE_DAYS = 30.0             # 超过此天数未使用视为"可能过时"

# lesson 矛盾检测关键词（启发式，非精确判断）
_POSITIVE_KEYWORDS = ("成功", "应该", "建议", "可以", "有效", "推荐")
_NEGATIVE_KEYWORDS = ("失败", "不行", "不应该", "出错", "无效", "不要", "避免")


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class StaleToolFinding:
    tool_name: str
    call_count: int
    error_count: int
    failure_rate: float

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "failure_rate": round(self.failure_rate, 3),
        }


@dataclass
class StaleSkillFinding:
    skill_name: str
    last_used_days_ago: float

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "last_used_days_ago": round(self.last_used_days_ago, 1),
        }


@dataclass
class ConflictingLessonFinding:
    group_key: str
    positive_sample: str
    negative_sample: str

    def to_dict(self) -> dict:
        return {
            "group_key": self.group_key,
            "positive_sample": self.positive_sample,
            "negative_sample": self.negative_sample,
        }


@dataclass
class HealthReport:
    stale_tools: list[StaleToolFinding] = field(default_factory=list)
    stale_skills: list[StaleSkillFinding] = field(default_factory=list)
    conflicting_lessons: list[ConflictingLessonFinding] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    @property
    def has_findings(self) -> bool:
        return bool(self.stale_tools or self.stale_skills or self.conflicting_lessons)

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at,
            "stale_tools": [f.to_dict() for f in self.stale_tools],
            "stale_skills": [f.to_dict() for f in self.stale_skills],
            "conflicting_lessons": [f.to_dict() for f in self.conflicting_lessons],
        }


class SelfMaintenanceModule:
    """
    自维护扫描入口。无状态、纯只读分析，不修改任何工具/skill/memory 内容，
    只产出 HealthReport + 修复建议文本。
    """

    def health_check(
        self,
        paths,
        skill_loader=None,
        memory_backend=None,
    ) -> HealthReport:
        report = HealthReport()
        try:
            report.stale_tools = self._check_tool_health(paths)
        except Exception:
            pass
        try:
            report.stale_skills = self._check_skill_freshness(skill_loader)
        except Exception:
            pass
        try:
            report.conflicting_lessons = self._check_memory_conflicts(memory_backend)
        except Exception:
            pass
        return report

    def generate_repair_suggestions(self, report: HealthReport) -> list[str]:
        """生成修复建议文本（不自动执行，写入晨报待用户确认）。"""
        suggestions: list[str] = []
        for f in report.stale_tools:
            suggestions.append(
                f"工具 `{f.tool_name}` 最近 {f.call_count} 次调用中有 {f.error_count} 次失败"
                f"（失败率 {f.failure_rate:.0%}），建议验证是否仍可用（API/参数是否变更）。"
            )
        for f in report.stale_skills:
            suggestions.append(
                f"Skill `{f.skill_name}` 已 {f.last_used_days_ago:.0f} 天未被使用，"
                "建议审查是否仍然相关，或考虑用 /evolve phase-g 评估剪枝。"
            )
        for f in report.conflicting_lessons:
            suggestions.append(
                f"发现可能矛盾的经验（{f.group_key}）："
                f"「{f.positive_sample}」 vs 「{f.negative_sample}」，建议人工判断保留哪条。"
            )
        return suggestions

    # ── stale_tools ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_tool_health(paths) -> list[StaleToolFinding]:
        """扫描最近若干 session 的 traces.jsonl，统计每个工具的近期失败率。"""
        try:
            sessions_root = paths.sessions_dir
        except Exception:
            return []
        if not sessions_root.exists():
            return []

        session_dirs = sorted(
            [d for d in sessions_root.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[:_TOOL_RECENT_SESSIONS]

        calls: dict[str, int] = {}
        errors: dict[str, int] = {}

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
                        name = entry.get("tool_name", "")
                        if not name:
                            continue
                        calls[name] = calls.get(name, 0) + 1
                        if entry.get("is_error"):
                            errors[name] = errors.get(name, 0) + 1
            except Exception:
                continue

        findings: list[StaleToolFinding] = []
        for name, call_count in calls.items():
            if call_count < _TOOL_MIN_SAMPLES:
                continue
            error_count = errors.get(name, 0)
            failure_rate = error_count / call_count
            if failure_rate >= _TOOL_FAILURE_RATE_THRESHOLD:
                findings.append(StaleToolFinding(
                    tool_name=name,
                    call_count=call_count,
                    error_count=error_count,
                    failure_rate=failure_rate,
                ))
        findings.sort(key=lambda f: -f.failure_rate)
        return findings

    # ── stale_skills ──────────────────────────────────────────────────────────

    @staticmethod
    def _check_skill_freshness(skill_loader) -> list[StaleSkillFinding]:
        """复用 phase_g.py 同款 tracker，找出长期未使用但仍激活的 skill。"""
        if skill_loader is None:
            return []
        tracker = getattr(skill_loader, "tracker", None)
        if tracker is None:
            return []

        now = time.time()
        findings: list[StaleSkillFinding] = []
        for name in list(getattr(skill_loader, "active", [])):
            rec = None
            try:
                rec = tracker.get_record(name)
            except Exception:
                rec = None
            last_ts = getattr(rec, "last_used_at", 0.0) if rec is not None else 0.0
            if not last_ts:
                continue  # 从未记录过使用时间，不下"过时"结论（可能是刚激活）
            days_ago = (now - last_ts) / 86400.0
            if days_ago >= _SKILL_STALE_DAYS:
                findings.append(StaleSkillFinding(skill_name=name, last_used_days_ago=days_ago))

        findings.sort(key=lambda f: -f.last_used_days_ago)
        return findings

    # ── conflicting_lessons ───────────────────────────────────────────────────

    @staticmethod
    def _check_memory_conflicts(memory_backend) -> list[ConflictingLessonFinding]:
        """复用 lesson_review.group_lessons() 的聚类，在同一聚类内找
        "正面建议" vs "负面信号" 同时出现的情况，标记为可能矛盾。"""
        if memory_backend is None or not hasattr(memory_backend, "all_entries"):
            return []
        try:
            from mini_agent.perception.lesson_review import group_lessons
        except ImportError:
            return []

        all_entries = memory_backend.all_entries()
        lesson_entries = [e for e in all_entries if getattr(e, "entry_type", "") == "lesson"]
        if len(lesson_entries) < 2:
            return []

        try:
            groups = group_lessons(lesson_entries, min_group_size=2)
        except Exception:
            return []

        findings: list[ConflictingLessonFinding] = []
        for group in groups:
            entries = getattr(group, "entries", None)
            if not entries:
                continue
            positive: Optional[Any] = None
            negative: Optional[Any] = None
            for entry in entries:
                text = " ".join([
                    getattr(entry, "outcome", "") or "",
                    getattr(entry, "suggested_action", "") or "",
                ])
                has_pos = any(kw in text for kw in _POSITIVE_KEYWORDS)
                has_neg = any(kw in text for kw in _NEGATIVE_KEYWORDS)
                if has_pos and positive is None:
                    positive = entry
                if has_neg and negative is None:
                    negative = entry
            if positive is not None and negative is not None and positive is not negative:
                key = getattr(group, "key", "") or (getattr(entries[0], "trigger", "") or "")[:30]
                findings.append(ConflictingLessonFinding(
                    group_key=str(key),
                    positive_sample=(getattr(positive, "outcome", "") or
                                      getattr(positive, "suggested_action", ""))[:60],
                    negative_sample=(getattr(negative, "outcome", "") or
                                      getattr(negative, "suggested_action", ""))[:60],
                ))
        return findings


# ── 时间门控（与 phase_g.py 同款模式）──────────────────────────────────────────

def _state_path(paths) -> Path:
    return paths.workdir_dir / _STATE_FILENAME


def _load_state(paths) -> dict:
    p = _state_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(paths, data: dict) -> None:
    p = _state_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def should_run_self_maintenance(paths, *, interval_hours: float = 24.0) -> bool:
    """时间门控：上次运行超过 interval_hours 小时则返回 True。"""
    state = _load_state(paths)
    last = float(state.get("last_run_at", 0.0) or 0.0)
    elapsed_hours = (time.time() - last) / 3600.0
    return elapsed_hours >= interval_hours


def record_self_maintenance_run(paths) -> None:
    state = _load_state(paths)
    state["last_run_at"] = time.time()
    _save_state(paths, state)


def append_digest_record(paths, extra: dict, initiator: str = "self_maintenance") -> None:
    """向 activity_digest.jsonl 追加一条记录（与 autonomous_loop._record_digest 同格式）。"""
    try:
        path = paths.workdir_dir / "activity_digest.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": time.time(), "initiator": initiator, **extra}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception:
        pass


def run_self_maintenance(paths, skill_loader=None, memory_backend=None) -> HealthReport:
    """整体运行入口：跑健康检查 + 生成建议 + 写入 activity_digest.jsonl。"""
    module = SelfMaintenanceModule()
    report = module.health_check(paths, skill_loader=skill_loader, memory_backend=memory_backend)

    if report.has_findings:
        suggestions = module.generate_repair_suggestions(report)
        append_digest_record(paths, {
            "type": "health_report",
            "summary": (
                f"自维护扫描：{len(report.stale_tools)} 个可能失效工具，"
                f"{len(report.stale_skills)} 个过时 skill，"
                f"{len(report.conflicting_lessons)} 组可能矛盾的经验"
            ),
            "suggestions": suggestions,
            **report.to_dict(),
        })

    try:
        record_self_maintenance_run(paths)
    except Exception:
        pass

    return report


__all__ = [
    "HealthReport",
    "StaleToolFinding",
    "StaleSkillFinding",
    "ConflictingLessonFinding",
    "SelfMaintenanceModule",
    "should_run_self_maintenance",
    "record_self_maintenance_run",
    "run_self_maintenance",
    "append_digest_record",
]
