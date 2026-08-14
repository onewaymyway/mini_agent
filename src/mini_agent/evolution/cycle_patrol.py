"""evolution/cycle_patrol.py — 周期性 Goal/Cron 任务的主动巡检 + LLM 辅助推送
（能力 C，见 next_doc/goal_cron_cycle_proactive_patrol_and_health_overview_
plan.md §2）。

模式与 `evolution/cron_scheduler.py::_maybe_alert_consecutive_skip()` /
`evolution/next_action_advisor.py::check_persistent_attention_mismatch()`
完全一致（同一套"周期性主动检查 + 状态跟踪 + 节流推送"在项目里的第三次
应用），不新发明状态机、不新发明推送通道：

  - 规则先行：`build_cycle_diagnostics()`（零 LLM 成本）先聚合出每个
    recurring Goal 的健康信号，再用既有阈值筛出候选（§2.2）。
  - LLM 只做"候选已经存在，帮我把候选变成更好的呈现"这件事——单 Goal
    摘要复用 `cycle_diagnostics.summarize_report_with_llm()`，多 Goal
    命中时的合并降噪是本模块新增的唯一 LLM 调用点（§2.3）。LLM 失败
    一律静默回退到规则拼接的模板文本，不阻塞推送本身。
  - 去重节流：同一 Goal 同一輪"首次命中不推送，持续命中 + 过了冷却时间
    才推送"，状态存 `AgentPaths.cycle_patrol_state_path`，结构与
    `next_action_advisor._load_mismatch_state()` 同构（§2.5）。
  - 推送双通道：`NotificationDispatcher`（面向"用户可能不在看对话"）+
    `InputQueue.enqueue()`（面向"用户正在对话"），两条通道各自失败隔离
    （§2.6）。本模块只负责生成文案 + 调用 NotificationDispatcher；
    `InputQueue.enqueue()` 由调用方（`AutonomousLoop._tick_passive()`）
    完成，与 `check_persistent_attention_mismatch()` 的分工一致。

同时，每次巡检（不管有没有命中）都会把"当前每个 recurring Goal 的健康
判定快照"写进同一份状态文件的 `overview` 字段，供能力 D（看板全局健康
总览，见 §3.1）优先复用，避免总览面板每次打开都对所有 Goal 现算一遍。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.config.models import CyclePatrolConfig


# ── 状态文件读写（与 next_action_advisor._load_mismatch_state 同构）───────

def _load_state(paths: "AgentPaths") -> dict:
    p = paths.cycle_patrol_state_path
    if not p.exists():
        return {"last_run_at": 0.0, "signals": {}, "overview": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_at": 0.0, "signals": {}, "overview": {}}
    data.setdefault("last_run_at", 0.0)
    data.setdefault("signals", {})
    data.setdefault("overview", {})
    return data


def _save_state(paths: "AgentPaths", state: dict) -> None:
    p = paths.cycle_patrol_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 规则候选筛选（§2.2）─────────────────────────────────────────────────

@dataclass
class PatrolCandidate:
    goal_id: str
    goal_title: str
    report: "object"  # CycleDiagnosticsReport，避免顶层 import 造成循环
    signal_types: list = field(default_factory=list)


def _screen_candidate(
    report,
    skip_alert_threshold: int = 5,
    *,
    dedupe_cron_skip_alert: bool = True,
) -> Optional[list]:
    """基于已经聚合好的诊断报告，按既有阈值判定这个 Goal 是否"值得关注"。
    只做筛选（返回命中的信号类型列表），不产生新的判定标准——三类信号
    完全对应看板卡片/CLI diagnose 已经在展示的字段（§5 第 2 条）。"""
    signal_types: list = []
    if report.recent_health_alerts:
        for a in report.recent_health_alerts:
            msg = a.get("message", "") if isinstance(a, dict) else str(a)
            if "explore" in msg and "stuck_explore" not in signal_types:
                signal_types.append("stuck_explore")
            elif "stuck_explore" not in signal_types and "health_alert" not in signal_types:
                signal_types.append("health_alert")
    cron_health = report.cron_health or {}
    skip_count = cron_health.get("consecutive_skip_count") or 0
    # 阈值与 `_maybe_alert_consecutive_skip()` 用的 `cron.skip_alert_
    # threshold`（默认 5）同源。§6.2 开放问题在 Stage 3 中的落地决定：
    # 两条通知**保留各自定位**（cron 层 = 精确跨越阈值那一刻的技术性
    # 告警；巡检 = 跨越前的早期预警 + 周期性健康汇报），但为避免同一次
    # "连续跳过"在阈值附近产生两条高度重叠的通知，巡检对 `cron_skip`
    # 信号的判定默认只覆盖"跨越阈值之前"的窗口
    # `[threshold-1, threshold)`（不含 threshold 本身）——一旦
    # `skip_count >= threshold`，说明 cron 层本轮已经/即将发出它自己的
    # 告警，巡检不再把"纯 cron_skip"当作新信号重复提醒（但如果同时还有
    # 其它信号类型，Goal 依然会因为那些信号被巡检覆盖，不会被完全忽略）。
    # `dedupe_cron_skip_alert=False` 时退回 Stage 1/2 的原始行为（阈值-1
    # 及以上都算命中，不设上界），供需要"巡检也要覆盖阈值之后"场景使用。
    if skip_count:
        lower = max(1, skip_alert_threshold - 1)
        if dedupe_cron_skip_alert:
            if lower <= skip_count < skip_alert_threshold:
                signal_types.append("cron_skip")
        elif skip_count >= lower:
            signal_types.append("cron_skip")
    if not signal_types:
        return None
    return signal_types


def _severity_for(report, signal_types: list) -> str:
    cron_health = report.cron_health or {}
    skip_count = cron_health.get("consecutive_skip_count") or 0
    if skip_count >= 5 or (report.recent_health_alerts and len(report.recent_health_alerts) >= 2):
        return "red"
    if signal_types:
        return "yellow"
    return "green"


# ── 快照拼装（供本模块内部使用，也是能力 D 的数据来源）────────────────────

def _priority_score(report, signal_types: Optional[list]) -> int:
    """[§6.4 开放问题的 Stage 3 落地] 三档 severity 在 Goal 数量多、大量
    Goal 同处 yellow（比如都在 explore 阶段，属正常状态）时不够用户快速
    定位"真正紧急"的那几个，因此在总览条目里附加一个细粒度的排序权重，
    UI 侧可以在同一 severity 档位内再按这个分数降序排列。**不改变/新增
    健康判定标准本身**（§5 第 2 条边界不变）——权重只是把已有三个字段
    （告警条数、cron 连续跳过次数、是否长期卡在 explore）加权汇总成一个
    可排序的数字，权重系数是"看得到的输入"，不是黑盒判定。"""
    cron_health = report.cron_health or {}
    skip_count = cron_health.get("consecutive_skip_count") or 0
    alert_count = len(report.recent_health_alerts or [])
    explore_bonus = 3 if "stuck_explore" in (signal_types or []) else 0
    return alert_count * 10 + skip_count * 5 + explore_bonus


def _overview_entry_for(paths: "AgentPaths", report, signal_types: Optional[list], has_pending_tuning: bool) -> dict:
    cron_health = report.cron_health or {}
    return {
        "goal_id": report.goal_id,
        "title": report.goal_title,
        "severity": _severity_for(report, signal_types or []),
        "alert_count": len(report.recent_health_alerts or []),
        "cron_consecutive_skip": cron_health.get("consecutive_skip_count") or 0,
        "execution_phase_mode": report.execution_phase_mode,
        "next_run_at": cron_health.get("next_run_at"),
        "has_pending_tuning_proposal": has_pending_tuning,
        "priority_score": _priority_score(report, signal_types),
    }


def _compute_review_trigger_ratios(overview_goals: list) -> dict:
    """[Track 3，goal_cron_convergence_and_governance_improvement_plan.md
    §3] 纯规则统计，零 LLM 成本，复用 `overview_goals` 里已经在维护的
    `execution_phase_mode`/`alert_count` 字段，不新增任何采集面。"""
    total = len(overview_goals)
    if total == 0:
        return {
            "recurring_goal_count": 0,
            "explore_alert_ratio": 0.0,
            "explore_concurrency_ratio": 0.0,
        }
    explore_goals = [g for g in overview_goals if g.get("execution_phase_mode") == "explore"]
    explore_alert_goals = [g for g in explore_goals if (g.get("alert_count") or 0) > 0]
    return {
        "recurring_goal_count": total,
        "explore_alert_ratio": len(explore_alert_goals) / total,
        "explore_concurrency_ratio": len(explore_goals) / total,
    }


def _update_review_triggers(state: dict, ratios: dict, cfg: Optional["CyclePatrolConfig"]) -> dict:
    """更新 `state["review_triggers"]` 里两个方向各自的连续命中轮数，
    返回本轮最新的完整状态（供写入 overview 展示）。样本量不足
    （`recurring_goal_count < review_trigger_min_recurring_goals`）时两个
    方向的连续命中计数都重置为 0，不参与判断（§6 第 2 条）。"""
    min_sample = getattr(cfg, "review_trigger_min_recurring_goals", 5) if cfg is not None else 5
    alert_threshold = getattr(cfg, "review_trigger_explore_alert_ratio", 0.3) if cfg is not None else 0.3
    concurrency_threshold = getattr(cfg, "review_trigger_explore_concurrency_ratio", 0.5) if cfg is not None else 0.5
    consecutive_needed = getattr(cfg, "review_trigger_consecutive_rounds", 4) if cfg is not None else 4

    triggers = state.setdefault("review_triggers", {})
    entry_a = triggers.setdefault(
        "phase_aware_resource_estimation",
        {"consecutive_hits": 0, "last_ratio": 0.0, "active": False},
    )
    entry_d = triggers.setdefault(
        "cross_goal_explore_concurrency",
        {"consecutive_hits": 0, "last_ratio": 0.0, "active": False},
    )

    sample_ok = ratios.get("recurring_goal_count", 0) >= min_sample

    def _update_one(entry: dict, ratio: float, threshold: float) -> None:
        entry["last_ratio"] = ratio
        if sample_ok and ratio >= threshold:
            entry["consecutive_hits"] = entry.get("consecutive_hits", 0) + 1
        else:
            entry["consecutive_hits"] = 0
        entry["active"] = entry["consecutive_hits"] >= consecutive_needed

    _update_one(entry_a, ratios.get("explore_alert_ratio", 0.0), alert_threshold)
    _update_one(entry_d, ratios.get("explore_concurrency_ratio", 0.0), concurrency_threshold)

    triggers["sample_ok"] = sample_ok
    triggers["recurring_goal_count"] = ratios.get("recurring_goal_count", 0)
    return triggers


def _review_trigger_messages(triggers: dict) -> list:
    """把 state["review_triggers"] 转成看板可以直接展示的提示文案列表，
    只在 `active=True` 时出现，不 active 时列表为空（不产生任何噪音）。"""
    messages = []
    a = triggers.get("phase_aware_resource_estimation") or {}
    if a.get("active"):
        messages.append(
            f"检测到 {a.get('last_ratio', 0.0):.0%} 的周期性 Goal 长期处于 "
            "explore 阶段且伴随健康告警，可以评估是否需要启动「阶段感知资源"
            "估算接入执行侧」（见 goal_cron_task_optimization_holistic_"
            "plan.md §5 方向 A）。"
        )
    d = triggers.get("cross_goal_explore_concurrency") or {}
    if d.get("active"):
        messages.append(
            f"检测到 {d.get('last_ratio', 0.0):.0%} 的周期性 Goal 同时处于 "
            "explore 阶段，可以评估是否需要启动「跨 Goal 探索期并发治理」"
            "（见 goal_cron_task_optimization_holistic_plan.md §5 方向 D）。"
        )
    return messages


def _overview_sort_key(g: dict) -> tuple:
    # 先按 severity（红→黄→绿），同一档位内再按 priority_score 降序
    # （§6.4，Stage 3 新增），让"真正紧急"的排在同色区块靠前的位置。
    return ({"red": 0, "yellow": 1, "green": 2}.get(g["severity"], 3), -g.get("priority_score", 0))


def build_overview_live(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    *,
    skip_alert_threshold: int = 5,
    dedupe_cron_skip_alert: bool = True,
) -> dict:
    """[能力 D §3.1] 无巡检快照时的现算路径：只跑规则层（不调 LLM），
    对所有 recurring Goal 现跑一次 `build_cycle_diagnostics()`。成本跟
    看板每张卡片渲染时已经在做的诊断读取相当，与快照路径共用同一份
    `_overview_entry_for()` 拼装逻辑，保证两条路径字段结构完全一致。"""
    from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
    goals = []
    for node in goal_backlog.all_nodes():
        if not getattr(node, "is_goal", False) or not getattr(node, "recurring", False):
            continue
        try:
            report = build_cycle_diagnostics(paths, goal_backlog, node.id)
        except Exception:
            continue
        if not report.found:
            continue
        signal_types = _screen_candidate(
            report, skip_alert_threshold=skip_alert_threshold,
            dedupe_cron_skip_alert=dedupe_cron_skip_alert,
        )
        has_pending = _has_pending_tuning_proposal(paths, node.id)
        goals.append(_overview_entry_for(paths, report, signal_types, has_pending))
    goals.sort(key=_overview_sort_key)
    payload = {"data_source": "live", "generated_at": time.time(), "goals": goals}
    # [Track 3] 现算路径没有状态文件可以跨 tick 累积"连续命中轮数"，只能
    # 报告本次即时比例，不参与 active/consecutive_hits 判断（这两个字段
    # 恒为 0/False）——真正的复查判断只在 run_cycle_patrol() 的快照路径里
    # 生效，看板应优先展示快照数据，现算路径只是"巡检从未跑过"时的兜底。
    try:
        ratios = _compute_review_trigger_ratios(goals)
        payload["review_triggers"] = {
            "phase_aware_resource_estimation": {
                "consecutive_hits": 0, "last_ratio": ratios.get("explore_alert_ratio", 0.0), "active": False,
            },
            "cross_goal_explore_concurrency": {
                "consecutive_hits": 0, "last_ratio": ratios.get("explore_concurrency_ratio", 0.0), "active": False,
            },
            "sample_ok": ratios.get("recurring_goal_count", 0) >= 5,
            "recurring_goal_count": ratios.get("recurring_goal_count", 0),
            "consecutive_rounds_tracked": False,
        }
    except Exception:
        pass
    return payload


def load_overview(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    *,
    skip_alert_threshold: int = 5,
    dedupe_cron_skip_alert: bool = True,
) -> dict:
    """[能力 D §3.1/§3.2] 优先读巡检快照（`cycle_patrol.enabled=True` 且
    至少跑过一轮之后才会有），没有快照时退化为 `build_overview_live()`。
    快照里的条目可能是旧版本（无 `priority_score` 字段）写入的，读出时
    做一次兼容补齐，保证前端始终能拿到这个字段用于同 severity 内排序。
    """
    state = _load_state(paths)
    overview = state.get("overview") or {}
    goals = overview.get("goals")
    if goals is not None:
        for g in goals:
            g.setdefault(
                "priority_score",
                g.get("alert_count", 0) * 10 + g.get("cron_consecutive_skip", 0) * 5,
            )
        review_triggers = overview.get("review_triggers")
        if review_triggers is not None:
            review_triggers.setdefault("consecutive_rounds_tracked", True)
        return {
            "data_source": "patrol_snapshot",
            "generated_at": overview.get("generated_at", state.get("last_run_at", 0.0)),
            "goals": goals,
            "review_triggers": review_triggers,
        }
    return build_overview_live(
        paths, goal_backlog, skip_alert_threshold=skip_alert_threshold,
        dedupe_cron_skip_alert=dedupe_cron_skip_alert,
    )


def _has_pending_tuning_proposal(paths: "AgentPaths", goal_id: str) -> bool:
    try:
        from mini_agent.perception.cycle_tuning import list_proposals
        return any(p.status == "draft" for p in list_proposals(paths, goal_id))
    except Exception:
        return False


# ── 摘要/合并降噪文案（§2.3，失败静默回退到模板文本）───────────────────────

def _fallback_summary(report) -> str:
    cron_health = report.cron_health or {}
    skip_count = cron_health.get("consecutive_skip_count") or 0
    bits = []
    if skip_count:
        bits.append(f"cron 已连续跳过 {skip_count} 次")
    if report.recent_health_alerts:
        bits.append(f"{len(report.recent_health_alerts)} 条健康告警")
    detail = "，".join(bits) if bits else "存在需要关注的信号"
    return f"Goal「{report.goal_title}」{detail}。"


def _merge_fallback(entries: list) -> str:
    titles = "、".join(f"「{e['report'].goal_title}」" for e in entries)
    return f"本次巡检发现 {len(entries)} 个 Goal 需要关注：{titles}。详情可在看板查看。"


def _llm_merge_summary(entries: list, llm_ask: Callable) -> Optional[str]:
    """[§2.3 第 2 点] 多 Goal 同时命中时，把结构化摘要一次性交给 LLM，
    生成一条排了优先级的合并推送文案，而不是逐条摘要简单拼接。失败/无
    `llm_ask` 时返回 None，调用方回退到 `_merge_fallback()`。"""
    if llm_ask is None:
        return None
    payload = [
        {
            "goal_title": e["report"].goal_title,
            "signal_types": e["signal_types"],
            "recent_health_alerts": [
                a.get("message", "") if isinstance(a, dict) else str(a)
                for a in (e["report"].recent_health_alerts or [])
            ],
            "cron_consecutive_skip": (e["report"].cron_health or {}).get("consecutive_skip_count") or 0,
        }
        for e in entries
    ]
    prompt = (
        "以下是本轮巡检命中问题信号的多个周期性任务（Goal），已经过规则\n"
        "聚合，不需要你重新判断健康与否。请生成一段 2-4 句的中文自然语言\n"
        "推送文案，说明本次巡检发现几个 Goal 需要关注、其中最值得优先\n"
        "处理的是哪个及原因（不要编造数据里没有的字段或数值），不要用\n"
        "markdown 标题/列表，只输出一段连续文字：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        text = llm_ask(prompt)
        text = (text or "").strip()
        return text or None
    except Exception:
        return None


# ── 主入口：供 AutonomousLoop._tick_passive() 周期性调用 ───────────────────

def run_cycle_patrol(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    cfg: Optional["CyclePatrolConfig"],
    llm_ask: Optional[Callable[[str], str]] = None,
    *,
    app_cfg: Optional[object] = None,
) -> Optional[dict]:
    """执行一轮巡检（含节流），返回本轮需要推送的 payload：
    `{"body": str, "meta": {...}}`，调用方负责把 `body` 交给
    `InputQueue.enqueue()`；本函数内部已经尝试过 `NotificationDispatcher`
    （失败静默，不影响返回值）。没有命中/未到巡检间隔/总开关关闭时返回
    `None`。

    `cfg=None` 或 `cfg.enabled=False` 时直接返回 `None`，不读取任何状态
    文件（§2.7"对现有部署零影响"）。

    `app_cfg`：可选的完整 `AppConfig`，用于读取 `app_cfg.cron.
    skip_alert_threshold`（与 `_maybe_alert_consecutive_skip()` 同源阈值，
    见 `_screen_candidate()`）。不传时退回默认值 5，不引入额外的强依赖。
    """
    if cfg is None or not getattr(cfg, "enabled", False):
        return None

    skip_alert_threshold = 5
    try:
        skip_alert_threshold = getattr(getattr(app_cfg, "cron", None), "skip_alert_threshold", 5) or 5
    except Exception:
        skip_alert_threshold = 5

    now = time.time()
    state = _load_state(paths)
    last_run_at = state.get("last_run_at", 0.0)
    interval_hours = getattr(cfg, "interval_hours", 6.0)
    if last_run_at and (now - last_run_at) < interval_hours * 3600:
        return None

    from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics, summarize_report_with_llm

    signals: dict = state.setdefault("signals", {})
    overview_goals: list = []
    round_candidates: list = []  # [{"goal_id", "report", "signal_types"}]

    for node in goal_backlog.all_nodes():
        if not getattr(node, "is_goal", False) or not getattr(node, "recurring", False):
            continue
        try:
            report = build_cycle_diagnostics(paths, goal_backlog, node.id)
        except Exception:
            continue
        if not report.found:
            continue
        signal_types = _screen_candidate(
            report, skip_alert_threshold=skip_alert_threshold,
            dedupe_cron_skip_alert=getattr(cfg, "dedupe_cron_skip_alert", True),
        )
        has_pending = _has_pending_tuning_proposal(paths, node.id)
        overview_goals.append(_overview_entry_for(paths, report, signal_types, has_pending))
        if signal_types:
            round_candidates.append({
                "goal_id": node.id, "report": report, "signal_types": signal_types,
            })

    # 清理已消失的信号，避免跟踪记录无限增长（与 _load_mismatch_state 同策略）
    current_ids = {c["goal_id"] for c in round_candidates}
    for goal_id in list(signals.keys()):
        if goal_id not in current_ids:
            del signals[goal_id]

    cooldown_hours = getattr(cfg, "push_cooldown_hours", 24.0)
    to_push: list = []
    for c in round_candidates:
        goal_id = c["goal_id"]
        rec = signals.get(goal_id)
        if rec is None:
            signals[goal_id] = {
                "first_detected_at": now, "last_pushed_at": 0.0, "push_count": 0,
            }
            continue  # 首次命中不推送，避免单次抖动打扰用户（§2.5）
        last_pushed_at = rec.get("last_pushed_at", 0.0)
        first_detected_at = rec.get("first_detected_at", now)
        reference = max(last_pushed_at, first_detected_at) if last_pushed_at else first_detected_at
        if (now - reference) < cooldown_hours * 3600:
            continue
        to_push.append(c)

    overview_goals.sort(key=_overview_sort_key)
    overview_payload = {"generated_at": now, "goals": overview_goals}
    if getattr(cfg, "review_trigger_enabled", True):
        try:
            ratios = _compute_review_trigger_ratios(overview_goals)
            triggers = _update_review_triggers(state, ratios, cfg)
            overview_payload["review_triggers"] = dict(triggers)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.cycle_patrol.run_cycle_patrol.review_triggers")
    state["overview"] = overview_payload
    state["last_run_at"] = now

    if not to_push:
        _save_state(paths, state)
        return None

    max_push = getattr(cfg, "max_push_per_run", 3)
    llm_enabled = getattr(cfg, "llm_enabled", True) and llm_ask is not None
    generate_tuning_drafts = getattr(cfg, "generate_tuning_drafts", True)

    # 命中候选，顺带生成调优草案（§2.4，不自动 confirm/apply）
    tuning_hits: dict = {}
    if generate_tuning_drafts:
        try:
            from mini_agent.perception.cycle_tuning import suggest_tuning_from_diagnostics, save_proposal
            for c in to_push:
                proposal = suggest_tuning_from_diagnostics(c["report"])
                if proposal is not None:
                    save_proposal(paths, proposal)
                    tuning_hits[c["goal_id"]] = proposal.id
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.cycle_patrol.run_cycle_patrol.tuning_drafts")

    if len(to_push) > max_push:
        # 合并降噪成一条（§2.3 第 2 点）
        body = None
        if llm_enabled:
            body = _llm_merge_summary(to_push, llm_ask)
        if not body:
            body = _merge_fallback(to_push)
        if tuning_hits:
            body += " 其中部分 Goal 已生成调优草案待确认，可在看板里查看。"
        title = f"周期任务巡检：{len(to_push)} 个 Goal 需要关注"
    else:
        parts = []
        for c in to_push:
            text = None
            if llm_enabled:
                try:
                    text = summarize_report_with_llm(c["report"], llm_ask)
                except Exception:
                    text = None
            if not text:
                text = _fallback_summary(c["report"])
            if c["goal_id"] in tuning_hits:
                text += " 已生成一份调优草案待确认，可以在看板里查看。"
            parts.append(text)
        body = "\n".join(parts)
        title = f"周期任务巡检：{len(to_push)} 个 Goal 需要关注" if len(to_push) > 1 else "周期任务巡检提醒"

    for c in to_push:
        rec = signals[c["goal_id"]]
        rec["last_pushed_at"] = now
        rec["push_count"] = rec.get("push_count", 0) + 1

    _save_state(paths, state)

    # 通道 1：NotificationDispatcher（失败静默，不影响 InputQueue 推送）
    try:
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=title,
            body=body[:2000],
            source="cycle_patrol",
            meta={
                "goal_ids": [c["goal_id"] for c in to_push],
                "signal_types": sorted({s for c in to_push for s in c["signal_types"]}),
            },
        ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.evolution.cycle_patrol.run_cycle_patrol.dispatch")

    return {
        "body": body,
        "meta": {
            "source": "cycle_patrol",
            "goal_ids": [c["goal_id"] for c in to_push],
        },
    }
