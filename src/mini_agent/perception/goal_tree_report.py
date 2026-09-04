"""
perception/goal_tree_report.py — 目标树级汇总报告（Stage 1，Stage 4 追加
待处理反馈字段）

见 next_doc/goal_tree_visibility_wiki_and_report_plan.md §3、§4。

只读聚合，回答"这棵目标树整体状态如何、有哪些事在等用户处理"，粒度从
`cycle_diagnostics.py` 的单 Goal 提升到子树。设计原则跟
`cycle_diagnostics.py` 一致，不重复发明：

  - 不做任何新的判定逻辑——健康信号直接复用
    `execution_phase.check_phase_health()`，不新增一套"综合评分"算法。
  - 不引入 LLM——分组统计、待办清单都是对已有结构化字段的机械分类，
    保证零成本、可离线跑。可选的自然语言总结层留给后续 Stage。
  - 遍历时只读取"生成报告所需的轻量字段"，不像 `cycle_diagnostics`
    那样为单节点拉取完整的历史轮次摘要；产出摘要只取最近一条。
  - 任一子数据源缺失/异常时报告仍要能生成，对应字段留空，不整体报错，
    与 `cycle_diagnostics.py` 的降级风格一致。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode


@dataclass
class GoalTreeReport:
    root_id: Optional[str] = None
    root_title: Optional[str] = None
    found: bool = True
    error: Optional[str] = None

    node_count: int = 0

    # ── 按维度分组（每组是一份 [{"id":..., "title":...}] 列表）──
    by_phase: dict = field(default_factory=dict)          # phase_mode -> [{"id","title"}]
    by_status: dict = field(default_factory=dict)          # status -> [{"id","title"}]
    stuck_or_alerted: list = field(default_factory=list)   # [{"id","title","message"}]
    cron_unhealthy: list = field(default_factory=list)     # [{"id","title","consecutive_skip_count",...}]

    # ── 全局待办清单 ──
    pending_decompose_candidates: list = field(default_factory=list)
    pending_focus_confirmation: list = field(default_factory=list)
    pending_tuning_proposals: list = field(default_factory=list)
    pending_execution_specs: list = field(default_factory=list)

    # ── [Stage 4 能力 C] 待处理反馈：跨节点收集仍是 pending 状态的用户
    # 反馈，让"报告里回顾"覆盖到树级视角，不只是单节点详情页 ──
    pending_feedback: list = field(default_factory=list)

    # ── 产出速览：每个活跃 Goal 节点最近一次产出的一句话摘要 ──
    recent_outputs_digest: list = field(default_factory=list)

    # ── Stage 5（可选，默认不生成）：LLM 自然语言总述 ──
    llm_summary: Optional[str] = None

    generated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _collect_subtree(backlog: "GoalBacklog", root_id: Optional[str]) -> list["GoalNode"]:
    """给定 root_id，收集该子树内所有节点（含 root 自身）；root_id 为 None
    时收集全部 parent_id 为空的顶层节点及其整棵子树（即全局森林）。
    BFS 遍历，重复 id（理论上不应出现环，双重保险防止死循环）跳过。
    """
    all_by_id = {n.id: n for n in backlog.all_nodes()}
    if root_id is not None:
        root = all_by_id.get(root_id)
        if root is None:
            return []
        roots = [root]
    else:
        roots = [n for n in all_by_id.values() if not n.parent_id]

    seen: set[str] = set()
    out: list["GoalNode"] = []
    queue = list(roots)
    while queue:
        node = queue.pop(0)
        if node.id in seen:
            continue
        seen.add(node.id)
        out.append(node)
        for cid in (node.children_ids or []):
            child = all_by_id.get(cid)
            if child is not None and child.id not in seen:
                queue.append(child)
    return out


def _cron_health_for_goal(paths: "AgentPaths", node: "GoalNode") -> Optional[dict]:
    if not node.is_goal or not node.recurring or not node.recurrence_cron_job_id:
        return None
    try:
        from mini_agent.evolution.cron_scheduler import load_cron_scheduler
        scheduler = load_cron_scheduler(paths)
        job = scheduler.get(node.recurrence_cron_job_id)
        if job is None:
            return None
        return {
            "consecutive_skip_count": job.consecutive_skip_count,
            "run_count": job.run_count,
            "enabled": job.enabled,
        }
    except Exception:
        return None


def collect_pending_items_for_node(paths: "AgentPaths", node: "GoalNode") -> dict:
    """收集单个节点的四类待处理项（decompose 候选/焦点确认/调优草案/执行
    规范），供 `build_goal_tree_report()` 和节点详情页
    （`goal_node_page.py`）共用，避免同一份逻辑写两遍。

    返回结构：
    {
      "decompose_candidates": [{"candidate_id","title"}],
      "focus_confirmation": bool,        # 该节点自身是否待确认焦点
      "tuning_proposals": [{"proposal_id","status"}],
      "execution_specs": [{"version"}],  # 至多一条（草稿未确认）
    }
    """
    out = {
        "decompose_candidates": [],
        "focus_confirmation": bool(node.children_ids and not node.current_focus_ids),
        "tuning_proposals": [],
        "execution_specs": [],
    }

    for cand in (node.decompose_candidates or []):
        out["decompose_candidates"].append({
            "candidate_id": cand.get("id") if isinstance(cand, dict) else None,
            "title": cand.get("title") if isinstance(cand, dict) else str(cand),
        })

    try:
        from mini_agent.perception.cycle_tuning import list_proposals
        for prop in list_proposals(paths, node.id):
            if prop.status in ("draft", "confirmed"):
                out["tuning_proposals"].append({"proposal_id": prop.id, "status": prop.status})
    except Exception:
        pass

    try:
        from mini_agent.perception.goal_execution_spec import load_spec
        spec = load_spec(paths, node.id)
        if spec is not None and not spec.confirmed:
            out["execution_specs"].append({"version": spec.version})
    except Exception:
        pass

    return out


def build_goal_tree_report(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    root_id: Optional[str] = None,
    *,
    cron_skip_alert_threshold: int = 5,
) -> GoalTreeReport:
    """聚合出一棵目标（子）树的汇总报告。纯读取，不修改任何状态。

    root_id 不存在时返回 found=False 的报告（不抛异常），与
    `build_cycle_diagnostics()` 风格一致。
    """
    from mini_agent.perception.goal_backlog import GoalBacklog  # noqa: F401  (type hint 只用)

    root_title = None
    if root_id is not None:
        root_node = goal_backlog.get(root_id)
        if root_node is None:
            return GoalTreeReport(
                root_id=root_id, found=False,
                error=f"Goal/Objective '{root_id}' not found",
                generated_at=time.time(),
            )
        root_title = root_node.title

    nodes = _collect_subtree(goal_backlog, root_id)
    report = GoalTreeReport(root_id=root_id, root_title=root_title, node_count=len(nodes))

    from mini_agent.perception import execution_phase as ep

    for node in nodes:
        ref = {"id": node.id, "title": node.title}

        report.by_status.setdefault(node.status, []).append(ref)

        # ── 阶段分组 + 健康告警（复用 check_phase_health，不重新判定）──
        phase_mode = None
        try:
            phase_state = ep.load_phase(paths, node.id)
            effective_mode = ep.last_known_effective_mode(phase_state)
            phase_mode = effective_mode
            alert = ep.check_phase_health(phase_state, effective_mode)
            if alert:
                report.stuck_or_alerted.append({**ref, "message": alert})
        except Exception:
            phase_mode = None
        if phase_mode:
            report.by_phase.setdefault(phase_mode, []).append(ref)

        # ── cron 健康 ──
        cron_health = _cron_health_for_goal(paths, node)
        if cron_health and cron_health.get("consecutive_skip_count", 0) >= cron_skip_alert_threshold:
            report.cron_unhealthy.append({**ref, **cron_health})

        # ── 待办：四类（decompose 候选/焦点确认/调优草案/执行规范），
        # 复用 collect_pending_items_for_node()，跟节点详情页共用同一份逻辑 ──
        pending = collect_pending_items_for_node(paths, node)
        for cand in pending["decompose_candidates"]:
            report.pending_decompose_candidates.append({
                **ref,
                "candidate_id": cand["candidate_id"],
                "title": cand["title"],
                "parent_id": node.id,
                "parent_title": node.title,
            })
        if pending["focus_confirmation"]:
            report.pending_focus_confirmation.append(ref)
        for prop in pending["tuning_proposals"]:
            report.pending_tuning_proposals.append({**ref, **prop})
        for spec_info in pending["execution_specs"]:
            report.pending_execution_specs.append({**ref, **spec_info})

        # ── [Stage 4 能力 C] 待处理反馈：user_feedback 里 status 仍是
        # pending（含 Stage 4 之前写入、没有 status 字段的旧数据，视同
        # pending）的条目，让反馈闭环在树级报告里也能回顾 ──
        for fb in (node.user_feedback or []):
            if fb.get("status", "pending") != "addressed":
                report.pending_feedback.append({
                    **ref,
                    "text": fb.get("text"),
                    "about": fb.get("about"),
                    "at": fb.get("at"),
                })

        # ── 产出速览：只取最近一条 manifest（活跃 Goal 节点）──
        if node.is_goal and node.is_active:
            try:
                from mini_agent.evolution import output_workspace as ow
                base_dir = ow.goal_output_base_dir(paths, node.id)
                manifests = ow.read_all_manifests(base_dir)
                if manifests:
                    latest = manifests[-1]
                    report.recent_outputs_digest.append({
                        **ref,
                        "task_summary": (latest.get("task_summary") or "")[:200],
                        "completed_at": latest.get("completed_at") or latest.get("created_at"),
                    })
            except Exception:
                pass

    report.generated_at = time.time()
    return report
