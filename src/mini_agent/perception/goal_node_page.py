"""
perception/goal_node_page.py — 目标节点详情页（Stage 2）

见 next_doc/goal_tree_visibility_wiki_and_report_plan.md §2.1。

只读聚合，回答"这个节点做到哪了、上次产出了什么、有哪些事等我处理"，
面向人读，跟 `cycle_diagnostics.py`（面向调优决策的结构化字段）互补。

设计原则：**不新增数据源，只是重新排列已有数据**：
  - 进度相关字段（execution_phase_mode / recent_cycle_summaries /
    progress_notes_tail）直接复用 `cycle_diagnostics.build_cycle_
    diagnostics()` 的既有聚合结果，不重复实现一遍。
  - 产出相关字段（output_structure / output_readme_text）直接复用
    `output_workspace.py` 的既有扫描函数，不重复实现目录遍历。
  - 待处理项复用 `goal_tree_report.collect_pending_items_for_node()`，
    跟树级汇总报告共用同一份逻辑。
  - 反馈历史直接读 `GoalNode.user_feedback`（已有字段，Stage 4 会给它
    加状态字段，这里先原样展示）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog


@dataclass
class GoalNodePage:
    goal_id: str
    title: str
    found: bool = True
    error: Optional[str] = None

    status: str = ""
    level: str = ""                       # "goal" | "objective"
    path_from_root: list = field(default_factory=list)  # [{"id","title"}]，根→当前

    # ── 进度（复用 cycle_diagnostics 的既有聚合，不重复计算）──
    execution_phase_mode: str = "auto"
    recent_cycle_summaries: list = field(default_factory=list)
    progress_notes_tail: str = ""

    # ── 产出（复用 output_workspace 的既有扫描）──
    output_dir: str = ""
    output_structure: dict = field(default_factory=dict)
    output_readme_text: str = ""

    # ── 子节点（导航用，一行一个，不递归展开全部详情）──
    children: list = field(default_factory=list)  # [{"id","title","status","level"}]

    # ── 待处理项（复用 goal_tree_report 的同一份逻辑）──
    pending_items: dict = field(default_factory=dict)

    # ── 反馈历史 ──
    feedback_history: list = field(default_factory=list)  # [{"text","at"}]

    generated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _path_from_root(goal_backlog: "GoalBacklog", node) -> list:
    """从根到当前节点的面包屑（含当前节点自身）。父链断裂/成环时在能走到
    的地方停止，不抛异常——这是纯展示用的导航信息，不值得因为数据异常
    整份报告失败。"""
    chain = []
    seen = set()
    cur = node
    while cur is not None and cur.id not in seen:
        chain.append({"id": cur.id, "title": cur.title})
        seen.add(cur.id)
        cur = goal_backlog.get(cur.parent_id) if cur.parent_id else None
    chain.reverse()
    return chain


def build_goal_node_page(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    goal_id: str,
) -> GoalNodePage:
    """聚合出一个节点的详情页数据。纯读取，不修改任何状态。

    goal_id 不存在时返回 found=False 的报告（不抛异常），与
    `build_cycle_diagnostics()`/`build_goal_tree_report()` 风格一致。
    这里不要求节点必须是 Goal（`level == "goal"`）——Objective 也应该能
    看详情页，只是进度/产出这两块对 Objective 意义有限（Objective 没有
    独立的 execution_phase/cron 绑定），届时相应字段自然是空的。
    """
    node = goal_backlog.get(goal_id)
    if node is None:
        return GoalNodePage(
            goal_id=goal_id, title="", found=False,
            error=f"Goal/Objective '{goal_id}' not found",
            generated_at=time.time(),
        )

    page = GoalNodePage(
        goal_id=node.id,
        title=node.title,
        status=node.status,
        level=node.level,
        path_from_root=_path_from_root(goal_backlog, node),
    )

    # ── 进度：复用 cycle_diagnostics（对 Objective 也能跑，只是多数字段
    # 会是默认值——build_cycle_diagnostics 本身只在 level != "goal" 时才
    # 拒绝，这里改成即使不是 Goal 也尝试跑一次，取得到就要，取不到就空）──
    try:
        from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
        diag = build_cycle_diagnostics(paths, goal_backlog, goal_id)
        if diag.found:
            page.execution_phase_mode = diag.execution_phase_mode
            page.recent_cycle_summaries = diag.recent_cycle_summaries
            page.progress_notes_tail = diag.progress_notes_tail
            page.output_dir = diag.output_dir
    except Exception:
        pass

    # Objective（或 diag 未命中）时，进度/产出信息 diagnostics 拿不到，
    # 这里退回从节点自身字段直接取，保证详情页至少有基本信息可看。
    if not page.progress_notes_tail:
        page.progress_notes_tail = "\n".join((node.progress_notes or "").splitlines()[-10:])

    # ── 产出：机械扫描 output/ 目录，刻意不经过 LLM，保证客观 ──
    try:
        from mini_agent.evolution import output_workspace as ow
        page.output_structure = ow.scan_output_structure(paths, node.id)
        page.output_readme_text = ow.render_output_readme(paths, node.id)
        if not page.output_dir:
            page.output_dir = str(ow.goal_output_base_dir(paths, node.id).as_posix())
    except Exception:
        pass

    # ── 子节点导航 ──
    for cid in (node.children_ids or []):
        child = goal_backlog.get(cid)
        if child is not None:
            page.children.append({
                "id": child.id, "title": child.title,
                "status": child.status, "level": child.level,
            })

    # ── 待处理项：复用 goal_tree_report 的同一份逻辑 ──
    try:
        from mini_agent.perception.goal_tree_report import collect_pending_items_for_node
        page.pending_items = collect_pending_items_for_node(paths, node)
    except Exception:
        page.pending_items = {}

    # ── 反馈历史 ──
    page.feedback_history = list(node.user_feedback or [])

    page.generated_at = time.time()
    return page
