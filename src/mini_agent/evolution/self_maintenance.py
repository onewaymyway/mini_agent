"""
evolution/self_maintenance.py — 自维护模块（具身改进 v3 C4）

具身来源：Varela 的自创生（autopoiesis）——生物体不只是被动响应环境扰动，
还主动维持自身边界和内部一致性（细胞修复膜损伤、免疫系统清除异常细胞）。
当前 Agent 对自身健康状况是纯被动的：工具调用失败了才知道工具可能有问题，
skill 内容过时了要等到产生错误建议才会被发现，记忆库里出现自相矛盾的
lesson 也不会被主动揪出来——这是"失去免疫系统"的状态。

归属：daemon 后台维护层，与 巩固循环（能力/技能层面的扫描）并列，但关注点
不同——巩固循环 回答"我学到了什么、该不该提升"，SelfMaintenanceModule 回答
"我自己有没有哪里坏了"。

实现取舍（复用已有数据源，不新增追踪基础设施）：
  - stale_tools：原计划文档描述为"最近 N 天未被成功调用的工具"，但核对
    代码库后发现并不存在跨 session 持久化的"每个工具最后一次成功调用时间"
    记录（SessionStats.tool_stats 只在单个 session 内有效）。改用已经持久化
    的信号：扫描最近若干 session 的 traces.jsonl 里 phase="tool_call" 记录，
    统计每个工具最近调用的失败率——失败率异常高（且样本量足够）的工具，
    比"许久没调用"更直接地提示"这个工具可能已经失效，需要排查"。
  - stale_skills：直接复用 consolidation.py::_days_since_last_use() 同款的
    skill_loader.tracker 基础设施（prune_skills 已经在用，这里只是从
    "高成本+未使用→建议剪枝"的角度换成"长期未使用→可能过时，建议复核"
    的角度，阈值和触发条件都不同，因此不与 prune_skills 合并实现）。
  - conflicting_lessons：复用 perception/lesson_review.py::group_lessons()
    的聚类结果（与 B2 LessonToReminderBridge 同款基础设施）——同一聚类内
    若同时出现"正面建议"（成功/应该/建议/可以）和"负面信号"（失败/不行/
    不应该/出错）的 outcome 文本，标记为可能矛盾。这是启发式而非精确判断，
    生成的是"建议人工复核"，不是确定性结论。
  - 触发方式：与 巩固循环 同款"时间门控"模式（不需要常驻线程）——
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
from mini_agent.time_utils import ts_to_str

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

# skill 结果有效性审计参数（自诊断闭环深化 P4）
_EFFECTIVENESS_RECENT_SESSIONS = 30   # 最多扫描最近多少个 session 的 meta.json
_EFFECTIVENESS_MIN_SESSIONS_PER_GROUP = 3  # 激活组/对照组样本量都需达到此值才下结论
_EFFECTIVENESS_RATE_DIFF_THRESHOLD = 0.15  # 失败率差异超过此值才判定为有实质差异


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
class SkillEffectivenessFinding:
    """skill 结果有效性审计（自诊断闭环深化 P4）：对比"激活了该 skill 的
    session"与"未激活该 skill 的对照 session"之间整体工具调用失败率的差异，
    与 stale_skills 的新鲜度启发式并存、互不替代——新鲜度回答"多久没用"，
    这里回答"用了之后任务是否因此顺利"。"""
    skill_name: str
    active_sessions: int
    baseline_sessions: int
    active_failure_rate: float
    baseline_failure_rate: float
    verdict: str  # "effective" | "low_effectiveness" | "inconclusive"

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "active_sessions": self.active_sessions,
            "baseline_sessions": self.baseline_sessions,
            "active_failure_rate": round(self.active_failure_rate, 3),
            "baseline_failure_rate": round(self.baseline_failure_rate, 3),
            "verdict": self.verdict,
        }


@dataclass
class HealthReport:
    stale_tools: list[StaleToolFinding] = field(default_factory=list)
    stale_skills: list[StaleSkillFinding] = field(default_factory=list)
    conflicting_lessons: list[ConflictingLessonFinding] = field(default_factory=list)
    skill_effectiveness: list[SkillEffectivenessFinding] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    @property
    def has_findings(self) -> bool:
        return bool(
            self.stale_tools or self.stale_skills or self.conflicting_lessons
            or self.skill_effectiveness
        )

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at,
            "stale_tools": [f.to_dict() for f in self.stale_tools],
            "stale_skills": [f.to_dict() for f in self.stale_skills],
            "conflicting_lessons": [f.to_dict() for f in self.conflicting_lessons],
            "skill_effectiveness": [f.to_dict() for f in self.skill_effectiveness],
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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
            pass
        try:
            report.stale_skills = self._check_skill_freshness(skill_loader)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
            pass
        try:
            report.conflicting_lessons = self._check_memory_conflicts(memory_backend)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
            pass
        try:
            report.skill_effectiveness = self._check_skill_effectiveness(paths, skill_loader)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
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
                "建议审查是否仍然相关，或考虑用 /evolve consolidate 评估剪枝。"
            )
        for f in report.conflicting_lessons:
            suggestions.append(
                f"发现可能矛盾的经验（{f.group_key}）："
                f"「{f.positive_sample}」 vs 「{f.negative_sample}」，建议人工判断保留哪条。"
            )
        for f in report.skill_effectiveness:
            if f.verdict == "low_effectiveness":
                suggestions.append(
                    f"Skill `{f.skill_name}` 激活时所在 session 的工具失败率"
                    f"（{f.active_failure_rate:.0%}，{f.active_sessions} 个 session）"
                    f"明显高于未激活时（{f.baseline_failure_rate:.0%}，"
                    f"{f.baseline_sessions} 个 session），建议复核该 skill 内容是否有效，"
                    "或是否被用在了不适合的场景。"
                )
        return suggestions

    # ── stale_tools ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_tool_health(paths) -> list[StaleToolFinding]:
        """扫描最近若干 session 的 traces.jsonl，统计每个工具的近期失败率。"""
        try:
            sessions_root = paths.sessions_dir
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance.SelfMaintenanceModule._check_tool_health')
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance.SelfMaintenanceModule._check_tool_health')
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
        """复用 consolidation.py 同款 tracker，找出长期未使用但仍激活的 skill。"""
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance.SelfMaintenanceModule._check_skill_freshness')
                rec = None
            last_ts = getattr(rec, "last_used_at", 0.0) if rec is not None else 0.0
            if not last_ts:
                continue  # 从未记录过使用时间，不下"过时"结论（可能是刚激活）
            days_ago = (now - last_ts) / 86400.0
            if days_ago >= _SKILL_STALE_DAYS:
                findings.append(StaleSkillFinding(skill_name=name, last_used_days_ago=days_ago))

        findings.sort(key=lambda f: -f.last_used_days_ago)
        return findings

    # ── skill_effectiveness（自诊断闭环深化 P4）───────────────────────────────

    @staticmethod
    def _check_skill_effectiveness(paths, skill_loader) -> list["SkillEffectivenessFinding"]:
        """复用 SessionStats 已持久化到各 session `meta.json` 的
        `skill_activations`/`tool_stats`（`agent/lifecycle.py::save_session()`
        写入，不新增埋点）：把最近若干 session 按"是否激活了该 skill"分成
        激活组/对照组，比较两组整体工具调用失败率的差异，作为"用了之后
        任务是否顺利"的结果信号，与 `_check_skill_freshness()` 的新鲜度
        信号并存、互不替代。"""
        if skill_loader is None:
            return []
        active_skill_names = list(getattr(skill_loader, "active", []) or [])
        if not active_skill_names:
            return []

        try:
            sessions_root = paths.sessions_dir
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance.SelfMaintenanceModule._check_skill_effectiveness')
            return []
        if not sessions_root.exists():
            return []

        session_dirs = sorted(
            [d for d in sessions_root.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[:_EFFECTIVENESS_RECENT_SESSIONS]

        # 每个 session 读一次 meta.json，得到 (skill_activations 名单, 整体失败率)
        sessions_data: list[tuple[set, Optional[float]]] = []
        for sd in session_dirs:
            meta_path = sd / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            stats = meta.get("stats", {}) or {}
            skill_activations = stats.get("skill_activations", {}) or {}
            activated = {
                name for name, rec in skill_activations.items()
                if (rec or {}).get("activations", 0) > 0
            }
            tool_stats = stats.get("tool_stats", {}) or {}
            calls = sum(int(v.get("calls", 0) or 0) for v in tool_stats.values())
            fails = sum(int(v.get("fail", 0) or 0) for v in tool_stats.values())
            failure_rate = (fails / calls) if calls else None
            sessions_data.append((activated, failure_rate))

        findings: list[SkillEffectivenessFinding] = []
        for skill_name in active_skill_names:
            active_rates = [
                r for activated, r in sessions_data
                if skill_name in activated and r is not None
            ]
            baseline_rates = [
                r for activated, r in sessions_data
                if skill_name not in activated and r is not None
            ]
            if (len(active_rates) < _EFFECTIVENESS_MIN_SESSIONS_PER_GROUP
                    or len(baseline_rates) < _EFFECTIVENESS_MIN_SESSIONS_PER_GROUP):
                continue  # 样本量不足，不下结论（两组都需要够）

            active_rate = sum(active_rates) / len(active_rates)
            baseline_rate = sum(baseline_rates) / len(baseline_rates)
            delta = active_rate - baseline_rate
            if delta >= _EFFECTIVENESS_RATE_DIFF_THRESHOLD:
                verdict = "low_effectiveness"
            elif delta <= -_EFFECTIVENESS_RATE_DIFF_THRESHOLD:
                verdict = "effective"
            else:
                verdict = "inconclusive"

            findings.append(SkillEffectivenessFinding(
                skill_name=skill_name,
                active_sessions=len(active_rates),
                baseline_sessions=len(baseline_rates),
                active_failure_rate=active_rate,
                baseline_failure_rate=baseline_rate,
                verdict=verdict,
            ))

        # 只把有实质结论的排在前面（low_effectiveness 最值得关注），
        # inconclusive 保留在结果里（供 P1 backlog/回看使用）但不优先展示。
        _order = {"low_effectiveness": 0, "effective": 1, "inconclusive": 2}
        findings.sort(key=lambda f: (_order.get(f.verdict, 9), -f.active_failure_rate))
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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance.SelfMaintenanceModule._check_memory_conflicts')
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


# ── 时间门控（与 consolidation.py 同款模式）──────────────────────────────────────────

def _state_path(paths) -> Path:
    return paths.workdir_dir / _STATE_FILENAME


def _load_state(paths) -> dict:
    p = _state_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance._load_state')
        return {}


def _save_state(paths, data: dict) -> None:
    p = _state_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
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
        _now = time.time()
        record = {"at": _now, "at_str": ts_to_str(_now), "initiator": initiator, **extra}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
        pass


def run_self_maintenance(paths, skill_loader=None, memory_backend=None) -> HealthReport:
    """整体运行入口：跑健康检查 + 生成建议 + 写入 activity_digest.jsonl。"""
    module = SelfMaintenanceModule()
    report = module.health_check(paths, skill_loader=skill_loader, memory_backend=memory_backend)

    if report.has_findings:
        suggestions = module.generate_repair_suggestions(report)
        low_effectiveness_count = sum(
            1 for f in report.skill_effectiveness if f.verdict == "low_effectiveness"
        )
        append_digest_record(paths, {
            "type": "health_report",
            "summary": (
                f"自维护扫描：{len(report.stale_tools)} 个可能失效工具，"
                f"{len(report.stale_skills)} 个过时 skill，"
                f"{low_effectiveness_count} 个低有效性 skill，"
                f"{len(report.conflicting_lessons)} 组可能矛盾的经验"
            ),
            "suggestions": suggestions,
            **report.to_dict(),
        })

    try:
        record_self_maintenance_run(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.self_maintenance')
        pass

    return report


__all__ = [
    "HealthReport",
    "StaleToolFinding",
    "StaleSkillFinding",
    "ConflictingLessonFinding",
    "SkillEffectivenessFinding",
    "SelfMaintenanceModule",
    "should_run_self_maintenance",
    "record_self_maintenance_run",
    "run_self_maintenance",
    "append_digest_record",
]
