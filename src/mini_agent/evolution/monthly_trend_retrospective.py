"""evolution/monthly_trend_retrospective.py — 月度战略回顾（外部知识反馈
闭环计划 P5）。

设计背景见
next_doc/external_knowledge_feedback_loop_improvement_plan.md §1 第5点/
§3 P5：`daily_digest`（天）、`external_trend_capability_link`（周）之外，
缺一个跨越数周、更高层的综合回看——每天/每周的信号颗粒度太细，容易只见
树木不见森林。

本模块新增 cron job `sys:monthly_trend_retrospective`（`cron:0 0 1 * *`，
每月 1 日一次），零 LLM 成本（纯规则聚合已有状态文件/wiki 统计，不引入
新的 LLM 调用），汇总三路信号：

  1. **`external_trend_capability_link` 候选采纳情况**：读取该模块的状态
     文件（`AgentPaths.external_trend_capability_link_state_path`），统计
     过去 `RETROSPECTIVE_WINDOW_SECONDS`（28 天，约等于"过去 4 周"）内
     产出过的候选（按 `produced_keys` 的时间戳筛选，而不是只看当前仍未
     过期的 `candidates` 列表——已经被后续 warmup 清理的候选也应计入
     "这个月产出过多少条"的分母），再逐条与 `GoalBacklog` 里现存的目标
     标题做匹配（复用 `soft_goal_deriver.py::_reverify_candidate_signal()`
     里 `source_tag == "external_knowledge"` 分支同款的标题拼接规则：
     `f"改善 {domain} 的执行可靠性（外部动态参考）"`），判断有多少条被
     实际采纳成了 Goal。
  2. **wiki 专题页增长**：复用 `wiki/stats.py::compute_stats()` 拿到当前
     `by_source_kind` 分布快照，与上一轮运行时保存的快照（本模块自己的
     状态文件）做差值，得到"这个周期内各类外部知识页面各新增了多少"。
     不依赖任何专门的时间序列存储——按运行节奏（每月一次）快照对比，
     跟 `relevance_threshold_calibration.py` 的"游标 + 状态快照"风格
     一致。
  3. **`self_eval`/`capability_map` 能力变化趋势**：复用
     `evolution/consolidation.py::load_capability_map()`，同样用"当前
     快照 vs 上一轮保存的快照"算出每个能力域的置信度环比变化，重点列出
     变化幅度最大的若干条（不论涨跌，涨说明该域真的在变强，跌是需要
     关注的信号）。

产出只有一份人类可读的月度文档
（`AgentPaths.monthly_trend_retrospective_path(month)`），格式风格对齐
`external_trend_capability_link.py::_write_candidates_md()`——供
`decision_profile_update`/`soft_goal_deriver` 人工/后续机制参考，不自动
创建 Goal、不自动修改代码、不引入任何新的候选消费链路（这一点与
P1-P4 的"只生产结构化候选供下游消费"不同——P5 本身就是终点，是给人看的
回顾，见计划 §3 P5 原设计"生成一份月度回顾文档，供
decision_profile_update/soft_goal_deriver 参考"）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:monthly_trend_retrospective"

# 回顾窗口：约等于"过去 4 周"，与计划 §3 P5 原设计描述一致。
RETROSPECTIVE_WINDOW_SECONDS = 28 * 24 * 3600

# 能力变化趋势只列出置信度变化幅度最大的若干条，避免每月回顾文档被
# 长长的能力域全量列表淹没重点。
MAX_CAPABILITY_HIGHLIGHTS = 10

# 与 external_trend_capability_link.py::_reverify_candidate_signal() 里
# source_tag == "external_knowledge" 分支使用的同一条标题拼接规则，用来
# 判断某条候选是否已经被采纳为 Goal——两处不做 import 复用（互不依赖），
# 只保持字符串格式一致，模块注释里互相指向对方，跟
# external_trend_capability_link.py 里 CONFIDENCE_LOW/MIN_CALLS_FOR_KNOWN
# 的既有取舍一致。
def _external_knowledge_goal_title(domain: str) -> str:
    return f"改善 {domain} 的执行可靠性（外部动态参考）"


@dataclass
class RetrospectiveSummary:
    window_days: int = 28
    trend_candidates_produced: int = 0
    trend_candidates_adopted: int = 0
    wiki_growth: dict = field(default_factory=dict)          # source_kind -> delta
    capability_deltas: list = field(default_factory=list)    # [(domain, prev, cur, delta)]
    report_path: Optional[str] = None


def _load_state(paths: "AgentPaths") -> dict:
    p = paths.monthly_trend_retrospective_state_path
    if not p.exists():
        return {"last_run_at": 0.0, "wiki_snapshot": {}, "capability_snapshot": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("last_run_at", 0.0)
        data.setdefault("wiki_snapshot", {})
        data.setdefault("capability_snapshot", {})
        return data
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.monthly_trend_retrospective._load_state")
        return {"last_run_at": 0.0, "wiki_snapshot": {}, "capability_snapshot": {}}


def _save_state(paths: "AgentPaths", state: dict) -> None:
    p = paths.monthly_trend_retrospective_state_path
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.monthly_trend_retrospective._save_state")


def _collect_trend_adoption(paths: "AgentPaths", now: float) -> tuple[int, int]:
    """统计过去 RETROSPECTIVE_WINDOW_SECONDS 内 external_trend_capability_link
    产出过的候选数，以及其中有多少已经被采纳成 Goal。读取/解析失败时
    静默返回 (0, 0)，不阻塞其余两路信号。"""
    produced = 0
    adopted = 0
    try:
        state_path = paths.external_trend_capability_link_state_path
        if not state_path.exists():
            return 0, 0
        data = json.loads(state_path.read_text(encoding="utf-8"))
        produced_keys: dict = data.get("produced_keys", {}) or {}

        recent_domains: set = set()
        for key, produced_at in produced_keys.items():
            try:
                if now - float(produced_at) < RETROSPECTIVE_WINDOW_SECONDS:
                    produced += 1
                    domain = key.split("|", 1)[0]
                    if domain:
                        recent_domains.add(domain)
            except Exception:
                continue

        if recent_domains:
            from mini_agent.perception.goal_backlog import load_goal_backlog

            backlog = load_goal_backlog(paths)
            existing_titles = {n.title for n in backlog.all_nodes()}
            for domain in recent_domains:
                if _external_knowledge_goal_title(domain) in existing_titles:
                    adopted += 1
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.monthly_trend_retrospective._collect_trend_adoption")
        return 0, 0
    return produced, adopted


def _collect_wiki_growth(paths: "AgentPaths", prev_snapshot: dict) -> tuple[dict, dict]:
    """返回 (本轮 by_source_kind 快照, 相对上一轮快照的增量 dict)。
    读取失败时快照与增量都返回空 dict，不阻塞其余两路信号。"""
    try:
        from mini_agent.wiki.stats import compute_stats

        stats = compute_stats(paths)
        cur_snapshot = dict(stats.by_source_kind)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.monthly_trend_retrospective._collect_wiki_growth")
        return {}, {}

    growth = {}
    for kind, cur_count in cur_snapshot.items():
        prev_count = int(prev_snapshot.get(kind, 0))
        delta = int(cur_count) - prev_count
        if delta != 0:
            growth[kind] = delta
    return cur_snapshot, growth


def _collect_capability_deltas(paths: "AgentPaths", prev_snapshot: dict) -> tuple[dict, list]:
    """返回 (本轮 domain -> confidence 快照, 按变化幅度降序排列的
    [(domain, prev_confidence, cur_confidence, delta), ...] 列表，只保留
    Top MAX_CAPABILITY_HIGHLIGHTS 条）。上一轮快照里没有的新能力域，
    prev_confidence 记为 0.0（视作"从无到有"）。"""
    try:
        from mini_agent.evolution.consolidation import load_capability_map

        entries = load_capability_map(paths)
        cur_snapshot = {e.capability_name: round(e.confidence, 4) for e in entries}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.monthly_trend_retrospective._collect_capability_deltas")
        return {}, []

    deltas = []
    for domain, cur_conf in cur_snapshot.items():
        prev_conf = float(prev_snapshot.get(domain, 0.0))
        delta = cur_conf - prev_conf
        if abs(delta) > 1e-9:
            deltas.append((domain, prev_conf, cur_conf, delta))
    deltas.sort(key=lambda x: -abs(x[3]))
    return cur_snapshot, deltas[:MAX_CAPABILITY_HIGHLIGHTS]


def _write_retrospective_md(paths: "AgentPaths", month: str, summary: RetrospectiveSummary) -> None:
    lines = [
        "---",
        "title: 月度战略回顾",
        "source_kind: monthly_trend_retrospective",
        f"month: {month}",
        f"updated: {time.strftime('%Y-%m-%d', time.localtime())}",
        "tags: [monthly-trend-retrospective]",
        "---",
        "",
        f"# 月度战略回顾 · {month}",
        "",
        "> 本文档由 `sys:monthly_trend_retrospective` 周期性汇总生成，只是"
        "供人工审核/参考的回顾——不会自动创建 Goal，也不会自动修改代码。",
        "",
        "## 1. 外部技术趋势候选采纳情况",
        "",
        f"- 过去 {summary.window_days} 天内 `external_trend_capability_link` "
        f"产出的候选数：{summary.trend_candidates_produced}",
        f"- 其中已被采纳为 Goal 的能力域数：{summary.trend_candidates_adopted}",
        "",
    ]

    lines.append("## 2. wiki 专题页增长")
    lines.append("")
    if summary.wiki_growth:
        for kind in sorted(summary.wiki_growth):
            delta = summary.wiki_growth[kind]
            sign = "+" if delta >= 0 else ""
            lines.append(f"- `{kind}`：{sign}{delta}")
    else:
        lines.append("_本周期内各来源页面数量无变化，或首次运行无可比快照。_")
    lines.append("")

    lines.append("## 3. 能力变化趋势（置信度变化幅度 Top "
                  f"{MAX_CAPABILITY_HIGHLIGHTS}）")
    lines.append("")
    if summary.capability_deltas:
        for domain, prev_conf, cur_conf, delta in summary.capability_deltas:
            sign = "+" if delta >= 0 else ""
            lines.append(
                f"- `{domain}`：{prev_conf:.2%} → {cur_conf:.2%}（{sign}{delta:.2%}）"
            )
    else:
        lines.append("_本周期内能力域置信度无明显变化，或首次运行无可比快照。_")
    lines.append("")

    paths.monthly_trend_retrospective_dir.mkdir(parents=True, exist_ok=True)
    paths.monthly_trend_retrospective_path(month).write_text(
        "\n".join(lines), encoding="utf-8"
    )


def run_monthly_trend_retrospective_once(paths: "AgentPaths") -> RetrospectiveSummary:
    """cron 触发入口：汇总三路信号 → 写月度回顾文档 → 保存本轮快照供下一轮
    算增量。纯规则聚合，零 LLM 成本，不需要 llm_helper。"""
    now = time.time()
    state = _load_state(paths)

    produced, adopted = _collect_trend_adoption(paths, now)
    wiki_snapshot, wiki_growth = _collect_wiki_growth(paths, state.get("wiki_snapshot", {}))
    capability_snapshot, capability_deltas = _collect_capability_deltas(
        paths, state.get("capability_snapshot", {})
    )

    summary = RetrospectiveSummary(
        window_days=RETROSPECTIVE_WINDOW_SECONDS // 86400,
        trend_candidates_produced=produced,
        trend_candidates_adopted=adopted,
        wiki_growth=wiki_growth,
        capability_deltas=capability_deltas,
    )

    month = time.strftime("%Y-%m", time.localtime(now))
    _write_retrospective_md(paths, month, summary)
    summary.report_path = str(paths.monthly_trend_retrospective_path(month))

    state["last_run_at"] = now
    state["wiki_snapshot"] = wiki_snapshot
    state["capability_snapshot"] = capability_snapshot
    _save_state(paths, state)

    return summary


def ensure_monthly_trend_retrospective_job(
    paths: "AgentPaths",
    cron_scheduler,
    *,
    schedule: str = "cron:0 0 1 * *",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:monthly_trend_retrospective`
    job，并注册本地回调 handler，跟 P1-P3 的 `ensure_*_job` 同构。默认每月
    1 日一次（计划 §3 P5 推荐节奏），零 LLM 成本，默认 enabled（不像 P4
    `ecosystem_positioning_scan` 那样依赖人工配置种子——本模块纯粹是对
    P1-P4 已有状态的只读聚合，不需要任何前置配置就能产出有意义的输出，
    首次运行时各项增量/变化会显示为"无可比快照"，不影响可用性）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="月度战略回顾",
        schedule=schedule,
        description=(
            "汇总过去4周 external_trend_capability_link 候选采纳情况、wiki "
            "专题页增长、capability_map 能力置信度变化趋势，生成一份月度"
            "回顾文档，供 decision_profile_update/soft_goal_deriver 参考。"
            "零 LLM 成本，纯规则聚合，不自动创建 Goal、不自动修改代码。"
        ),
        tags=["evolution", "wiki", "monthly_trend_retrospective"],
    )

    def _handler(job, _paths=paths) -> bool:
        run_monthly_trend_retrospective_once(_paths)
        return True

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "RETROSPECTIVE_WINDOW_SECONDS",
    "RetrospectiveSummary",
    "run_monthly_trend_retrospective_once",
    "ensure_monthly_trend_retrospective_job",
]
