"""evolution/failure_pattern_store.py — 统一失败模式库（F2）

背景见 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
断点 C2：`dead_ends`（GoalState）、`ObjectiveExecution.steps[].error_msg`
两处失败记录目前各自独立存在，`capability_map` 的置信度更新只看
`sys:self_eval` 的工具调用成功率粗统计，看不到"同一类任务反复卡在同一个
语义原因"这种更高层的模式。

本模块只做"读取既有失败记录 → 按 task_category 聚合 → 持久化" 这一件
事，不改变 dead_ends/ObjectiveExecution 本身的记录逻辑，不引入 LLM。

数据源（当前版本，均为只读扫描 + 一处轻量事件追加写入）：
  1. `.agent/objective_executions.json` 里各 execution 的 steps[].error_msg
     （非空即计一次失败），按 `objective_title` 归一化后的 task_category
     分组。
  2. `.agent/sessions/<sid>/goal_state.json` 里的 dead_ends 列表（按
     goal_text 归一化后的 task_category 分组）——这份数据其实早就由
     `goal_mode/runner.py::_record_dead_end()` 持久化，本模块只是新增
     了读取和聚合，不需要额外补丁。
  3. `.agent/turn_judge_stuck_events.jsonl`——`role_agents`/`agent/role_judge.py`
     里 TurnJudge 场景判定 `StuckSignal.GIVE_UP` 时追加写入（见
     `record_turn_judge_stuck_event()`）。这是唯一需要新增持久化点的
     数据源：`StuckDetector` 本身仍是纯内存状态机，不落盘完整判定历史，
     这里只在"确实判定为卡住且放弃"时追加一条最小记录，不改变
     `StuckDetector` 本身的实现。

聚类规则（刻意保守，不引入语义相似度/LLM）：
  - task_category：复用看板改造方案 Track H 的"标题归一化"思路——把
    objective_title / goal_text 转小写、去标点、取前若干词作为分组 key，
    不做更细的语义聚类。
  - root_cause_tag：从 error_msg 里做极简规则匹配（超时/权限/工具不存在/
    其它），不做 NLP 分类。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:failure_pattern_aggregation"

_RECENT_SESSIONS_SCAN_LIMIT = 50  # 只扫最近 N 个 session 目录的 goal_state.json
_CATEGORY_WORDS = 6  # 标题归一化取前 N 个词作为 task_category

_ROOT_CAUSE_RULES: list[tuple[str, re.Pattern]] = [
    ("timeout", re.compile(r"超时|timeout|timed out", re.IGNORECASE)),
    ("permission", re.compile(r"权限|permission|denied|拒绝", re.IGNORECASE)),
    ("tool_missing", re.compile(r"不存在|not found|no such|未找到", re.IGNORECASE)),
    ("rate_limit", re.compile(r"rate.?limit|限速|429", re.IGNORECASE)),
]
_ROOT_CAUSE_OTHER = "other"


@dataclass
class FailurePattern:
    """一个聚合后的失败模式。"""

    pattern_id: str
    source: str            # "objective" | "dead_end"
    task_category: str
    root_cause_tag: str
    occurrence_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    example_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "source": self.source,
            "task_category": self.task_category,
            "root_cause_tag": self.root_cause_tag,
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "example_summary": self.example_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FailurePattern":
        return cls(
            pattern_id=d.get("pattern_id", ""),
            source=d.get("source", ""),
            task_category=d.get("task_category", ""),
            root_cause_tag=d.get("root_cause_tag", ""),
            occurrence_count=int(d.get("occurrence_count", 0) or 0),
            first_seen=float(d.get("first_seen", 0.0) or 0.0),
            last_seen=float(d.get("last_seen", 0.0) or 0.0),
            example_summary=d.get("example_summary", ""),
        )


@dataclass
class FailurePatternAggregationSummary:
    patterns: list[FailurePattern] = field(default_factory=list)
    sources_read: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.errors


def _normalize_category(text: str) -> str:
    """标题归一化：小写、去标点、取前 N 个词——不做语义聚类，参见模块说明。"""
    if not text:
        return "unknown"
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text.lower())
    words = cleaned.split()
    return " ".join(words[:_CATEGORY_WORDS]) or "unknown"


def _root_cause_tag(error_msg: str) -> str:
    for tag, pattern in _ROOT_CAUSE_RULES:
        if pattern.search(error_msg or ""):
            return tag
    return _ROOT_CAUSE_OTHER


def _read_objective_failures(paths: "AgentPaths") -> list[tuple[str, str, str, float]]:
    """返回 [(task_category, root_cause_tag, example_summary, ts), ...]。"""
    out: list[tuple[str, str, str, float]] = []
    exec_path = paths.workdir_dir / "objective_executions.json"
    if not exec_path.exists():
        return out
    try:
        data = json.loads(exec_path.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store._read_objective_failures")
        return out

    for ed in data.get("executions", []) or []:
        title = ed.get("objective_title", "") or ""
        category = _normalize_category(title)
        finished_at = float(ed.get("finished_at", 0.0) or 0.0) or time.time()
        for step in ed.get("steps", []) or []:
            error_msg = step.get("error_msg", "") or ""
            if not error_msg:
                continue
            out.append((category, _root_cause_tag(error_msg), error_msg[:150], finished_at))
    return out


def _read_dead_end_failures(paths: "AgentPaths") -> list[tuple[str, str, str, float]]:
    """扫描最近若干 session 的 goal_state.json，聚合 dead_ends。"""
    out: list[tuple[str, str, str, float]] = []
    try:
        sessions_root = paths.sessions_dir
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store._read_dead_end_failures")
        return out
    if not sessions_root.exists():
        return out

    session_dirs = sorted(
        [d for d in sessions_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[:_RECENT_SESSIONS_SCAN_LIMIT]

    for sd in session_dirs:
        goal_state_path = sd / "goal_state.json"
        if not goal_state_path.exists():
            continue
        try:
            gs = json.loads(goal_state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        goal_text = gs.get("goal_text", "") or ""
        category = _normalize_category(goal_text)
        mtime = goal_state_path.stat().st_mtime
        for dead_end in gs.get("dead_ends", []) or []:
            if isinstance(dead_end, dict):
                # [系统关联性断点改进方案 F2 追加] GoalRunner._record_dead_end()
                # 落盘的实际结构是 {"round":.., "progress":.., "reason":..}
                # （见 goal_mode/runner.py），优先取 reason 文本本身做根因匹配，
                # 而不是整条 dict 序列化后再匹配（后者会把 "round": 3 这类
                # 数字噪音也混进正则匹配，降低 root_cause_tag 的准确度）。
                text = dead_end.get("reason") or json.dumps(dead_end, ensure_ascii=False)
                # [next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
                # 方案 D.2] 优先使用 GoalJudge 结构化给出的 stuck_category
                # （比正则猜测的 root_cause_tag 更准确），字段缺失（未开启
                # 归因分类 / 旧版本落盘数据）时回退到原有的正则分类，完全
                # 向后兼容。
                explicit_category = dead_end.get("stuck_category")
                tag = (
                    explicit_category
                    if isinstance(explicit_category, str) and explicit_category and explicit_category != "unknown"
                    else _root_cause_tag(text)
                )
            else:
                text = str(dead_end)
                tag = _root_cause_tag(text)
            out.append((category, tag, text[:150], mtime))
    return out


def _turn_judge_stuck_log_path(paths: "AgentPaths"):
    return paths.workdir_dir / "turn_judge_stuck_events.jsonl"


def _goal_spec_preflight_log_path(paths: "AgentPaths"):
    return paths.workdir_dir / "goal_spec_preflight_events.jsonl"


def record_goal_spec_preflight_issue(paths: "AgentPaths", *, goal_text: str, issues: list[str]) -> None:
    """[next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
    方案 D.3] 记录一次 GoalSpec 冻结前的验收标准可验证性自检发现的问题
    （见 `goal_mode/spec.py::GoalSpec.validate_verifiability`），供后续按
    task_category 聚合，回答"哪类目标描述容易生成不可验证的验收标准"。

    与 `record_turn_judge_stuck_event` 同样的设计取舍：只做最简单的追加
    写入，不做去重/聚合，聚合统一在 `run_failure_pattern_aggregation_once()`
    里完成；异常不应向上抛出，这是冻结流程里的一个旁路记录动作。
    """
    if not issues:
        return
    try:
        p = _goal_spec_preflight_log_path(paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "goal_text": (goal_text or "")[:200],
                "issue_count": len(issues),
                "example_issue": (issues[0] or "")[:200],
            }, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store.record_goal_spec_preflight_issue")


def _read_goal_spec_preflight_issues(paths: "AgentPaths") -> list[tuple[str, str, str, float]]:
    """读取 GoalSpec 预检问题日志，返回格式与其余数据源一致的
    [(task_category, root_cause_tag, example_summary, ts), ...]。这里的
    root_cause_tag 固定用一个专属标签，代表"验收标准可验证性问题"这一类，
    不需要正则猜测（预检记录本身语义已经明确）。
    """
    out: list[tuple[str, str, str, float]] = []
    p = _goal_spec_preflight_log_path(paths)
    if not p.exists():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store._read_goal_spec_preflight_issues")
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        category = _normalize_category(rec.get("goal_text", ""))
        example = rec.get("example_issue", "")
        ts = float(rec.get("ts", 0.0) or 0.0) or time.time()
        out.append((category, "unverifiable_acceptance_criteria", example[:150], ts))
    return out


def record_turn_judge_stuck_event(paths: "AgentPaths", *, task_hint: str, reason: str) -> None:
    """[系统关联性断点改进方案 F2 追加] 供 `agent/role_judge.py::_maybe_run_turn_judge`
    在 TurnJudge 场景判定 StuckSignal.GIVE_UP 时调用——这是此前唯一没有
    持久化记录的 stuck 信号来源（GoalRunner 场景已经通过既有的
    `_record_dead_end()` 落盘到 `goal_state.json`，见方案文档"实施记录"）。

    只做最简单的追加写入（jsonl，一行一条），不做去重/聚合——聚合逻辑
    统一在 `run_failure_pattern_aggregation_once()` 里做，保持"事件记录"
    和"周期性聚合"两个职责分离，与其余数据源（objective_executions、
    goal_state dead_ends）的处理方式一致。
    """
    try:
        p = _turn_judge_stuck_log_path(paths)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "task_hint": (task_hint or "")[:200],
                "reason": (reason or "")[:200],
            }, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store.record_turn_judge_stuck_event")


def _read_turn_judge_stuck_events(paths: "AgentPaths") -> list[tuple[str, str, str, float]]:
    """读取 TurnJudge stuck 事件日志，返回格式与其余两路数据源一致的
    [(task_category, root_cause_tag, example_summary, ts), ...]。"""
    out: list[tuple[str, str, str, float]] = []
    p = _turn_judge_stuck_log_path(paths)
    if not p.exists():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store._read_turn_judge_stuck_events")
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        category = _normalize_category(rec.get("task_hint", ""))
        reason = rec.get("reason", "")
        ts = float(rec.get("ts", 0.0) or 0.0) or time.time()
        out.append((category, _root_cause_tag(reason), reason[:150], ts))
    return out


def _store_path(paths: "AgentPaths"):
    return getattr(paths, "failure_pattern_store_path", None) or (
        paths.workdir_dir / "failure_pattern_store.json"
    )


def _load_store(paths: "AgentPaths") -> dict[str, dict]:
    p = _store_path(paths)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # [P0 补测试同类修复] 与 suggestion_feedback_ledger._load_ledger
        # 相同的边界情况：文件内容是合法 JSON 但顶层不是 dict 时，
        # data.get(...) 会抛 AttributeError，这里统一退化为空 store。
        if not isinstance(data, dict):
            return {}
        return {
            pat.get("pattern_id"): pat
            for pat in data.get("patterns", []) or []
            if isinstance(pat, dict) and pat.get("pattern_id")
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store._load_store")
        return {}


def _save_store(paths: "AgentPaths", patterns: list[FailurePattern]) -> None:
    p = _store_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {"ran_at": time.time(), "patterns": [pat.to_dict() for pat in patterns]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.evolution.failure_pattern_store._save_store")


def run_failure_pattern_aggregation_once(paths: "AgentPaths") -> FailurePatternAggregationSummary:
    """扫描 objective_executions.json + 最近 session 的 goal_state.json，
    按 (source, task_category, root_cause_tag) 聚合为 FailurePattern，
    与既有记录合并（occurrence_count 累加，first_seen 保留最早）。"""
    summary = FailurePatternAggregationSummary()
    raw: list[tuple[str, str, str, str, float]] = []  # (source, category, tag, summary, ts)

    try:
        for category, tag, ex_summary, ts in _read_objective_failures(paths):
            raw.append(("objective", category, tag, ex_summary, ts))
        summary.sources_read.append("objective_executions")
    except Exception as exc:
        summary.errors.append(f"objective_failed: {exc}")

    try:
        for category, tag, ex_summary, ts in _read_dead_end_failures(paths):
            raw.append(("dead_end", category, tag, ex_summary, ts))
        summary.sources_read.append("goal_state_dead_ends")
    except Exception as exc:
        summary.errors.append(f"dead_end_failed: {exc}")

    try:
        for category, tag, ex_summary, ts in _read_turn_judge_stuck_events(paths):
            raw.append(("turn_judge_stuck", category, tag, ex_summary, ts))
        summary.sources_read.append("turn_judge_stuck_events")
    except Exception as exc:
        summary.errors.append(f"turn_judge_stuck_failed: {exc}")

    try:
        for category, tag, ex_summary, ts in _read_goal_spec_preflight_issues(paths):
            raw.append(("goal_spec_preflight", category, tag, ex_summary, ts))
        summary.sources_read.append("goal_spec_preflight_events")
    except Exception as exc:
        summary.errors.append(f"goal_spec_preflight_failed: {exc}")

    existing = {k: FailurePattern.from_dict(v) for k, v in _load_store(paths).items()}

    grouped: dict[str, list[tuple[str, str, str, float]]] = {}
    for source, category, tag, ex_summary, ts in raw:
        pattern_id = f"{source}:{category}:{tag}"
        grouped.setdefault(pattern_id, []).append((source, category, ex_summary, ts))

    merged: list[FailurePattern] = []
    now = time.time()
    for pattern_id, occurrences in grouped.items():
        source, category, _, _ = occurrences[0]
        tag = pattern_id.rsplit(":", 1)[-1]
        prior = existing.get(pattern_id)
        occurrence_count = len(occurrences) + (prior.occurrence_count if prior else 0)
        first_seen = prior.first_seen if prior else min(o[3] for o in occurrences)
        last_seen = max([o[3] for o in occurrences] + ([prior.last_seen] if prior else []))
        example_summary = occurrences[-1][2] or (prior.example_summary if prior else "")
        merged.append(FailurePattern(
            pattern_id=pattern_id, source=source, task_category=category,
            root_cause_tag=tag, occurrence_count=occurrence_count,
            first_seen=first_seen, last_seen=last_seen, example_summary=example_summary,
        ))

    # 保留既有但本轮未命中的 pattern（不会因为这次扫描窗口较窄就被丢弃）
    for pattern_id, pat in existing.items():
        if pattern_id not in grouped:
            merged.append(pat)

    merged.sort(key=lambda p: -p.occurrence_count)
    summary.patterns = merged

    try:
        _save_store(paths, merged)
    except Exception as exc:
        summary.errors.append(f"save_failed: {exc}")

    return summary


def get_patterns_for_category(paths: "AgentPaths", category_text: str, *, min_occurrence: int = 3) -> list[FailurePattern]:
    """供 `soft_goal_deriver.py` 等消费方查询：给定一段标题/描述文本，
    返回归一化后命中的高频失败模式（occurrence_count >= min_occurrence）。
    只读查询，不触发重新聚合。"""
    category = _normalize_category(category_text)
    store = _load_store(paths)
    return [
        FailurePattern.from_dict(v) for v in store.values()
        if v.get("task_category") == category and int(v.get("occurrence_count", 0) or 0) >= min_occurrence
    ]


def format_pattern_warning(patterns: list["FailurePattern"], *, max_patterns: int = 3) -> str:
    """[daemon_stability_and_ux_improvement_plan.md 第 7 项 / P3-7]
    把 `get_patterns_for_category()` 命中的高频失败模式格式化为一段可以
    直接拼进 step 消息的提示文本；命中列表为空时返回空字符串（调用方按
    "空字符串不拼接"处理，不需要额外判空）。

    只做格式化，不做二次过滤/排序——排序（按 occurrence_count 降序）已经
    由 `run_failure_pattern_aggregation_once()` 落盘时完成，这里只截断到
    `max_patterns` 条，避免同一个 task_category 下多个 root_cause_tag
    都命中时提示堆得过长。
    """
    if not patterns:
        return ""
    lines = []
    for pat in patterns[:max_patterns]:
        example = (pat.example_summary or "").strip()
        example_part = f"：{example}" if example else ""
        lines.append(
            f"- 曾因「{pat.root_cause_tag}」类原因失败 {pat.occurrence_count} 次{example_part}"
        )
    return (
        "\n\n[已知失败模式提醒]\n过去在类似任务上有以下已知失败模式，请注意规避，"
        "不要重复同样的做法：\n" + "\n".join(lines)
    )


def load_failure_patterns(paths: "AgentPaths") -> list[dict]:
    """供看板/晨报只读消费：返回当前全部已聚合的失败模式（按频次排序）。"""
    store = _load_store(paths)
    patterns = [FailurePattern.from_dict(v) for v in store.values()]
    patterns.sort(key=lambda p: -p.occurrence_count)
    return [p.to_dict() for p in patterns]


def get_stuck_category_breakdown(paths: "AgentPaths", category_text: str) -> dict[str, int]:
    """[next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
    方案 D.2] 给定一段标题/描述文本，返回该 task_category 下按
    stuck_category（root_cause_tag）分布的出现次数字典，例如
    `{"env_blocked": 5, "goal_ambiguous": 2}`。

    这是对 `get_patterns_for_category()` 的一层薄封装：后者按
    occurrence_count 阈值过滤后返回 FailurePattern 列表，本函数只是把
    "同一 task_category 下有哪些具体原因、各自出现几次"整理成更便于
    `sys:self_eval` 精准降置信度使用的字典形式，不改变底层存储或聚合逻辑。
    min_occurrence=1，因为这里关心的是分布本身，不是"是否达到警示阈值"。
    """
    patterns = get_patterns_for_category(paths, category_text, min_occurrence=1)
    breakdown: dict[str, int] = {}
    for pat in patterns:
        breakdown[pat.root_cause_tag] = breakdown.get(pat.root_cause_tag, 0) + pat.occurrence_count
    return breakdown


def ensure_failure_pattern_aggregation_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:failure_pattern_aggregation`
    （零 LLM 成本，本地回调 handler，与 improvement_backlog_merge.py 同构）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="失败模式聚合",
        schedule="interval:86400",
        description=(
            "把 ObjectiveExecution 步骤失败 + Goal dead_ends 按任务类别聚合为"
            "统一的失败模式清单，零 LLM 成本，供 soft_goal_deriver 等消费方查询。"
        ),
        tags=["maintenance", "self_awareness"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_failure_pattern_aggregation_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "FailurePattern",
    "FailurePatternAggregationSummary",
    "run_failure_pattern_aggregation_once",
    "get_patterns_for_category",
    "format_pattern_warning",
    "load_failure_patterns",
    "ensure_failure_pattern_aggregation_job",
    "record_turn_judge_stuck_event",
]
