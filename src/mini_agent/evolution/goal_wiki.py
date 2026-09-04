"""
evolution/goal_wiki.py — 目标产出 Wiki 落盘镜像（Stage 3）

见 next_doc/goal_tree_visibility_wiki_and_report_plan.md §2.2。

把 perception/goal_node_page.py 里"节点详情页"的聚合结果，机械渲染成
Markdown 落盘到 `<outputs_root>/goals_wiki/<goal_id>/index.md`，让整棵
目标树变成可以像 wiki 一样点击浏览的静态目录：
  - 每个节点一份 index.md，子节点用相对链接 `<child_id>/index.md` 指向
    对应子页，天然对应目标树结构，不需要额外维护一份导航索引。
  - 根节点页/全局根索引就是整个目标 wiki 的入口，点子节点链接一路下钻。
  - 生成时机是"机械重新生成"，不做增量 diff、不经过 LLM——跟
    `output_workspace.render_output_readme()` 同一个取舍，保证这份 wiki
    反映的始终是客观当前状态，而不是某一次的"整理报告"快照。
  - 与通用知识 wiki（`wiki/` 模块）不共用存储：本模块不读写 `wiki/` 下
    任何文件，两者语义、生命周期都不同（见方案 §1 共享原则第三条）。
    "相关知识链接"这层增强，方案 §6 建议先不做，留到 wiki 页本身用起来
    之后再评估要不要加，本模块暂不实现。

只读聚合优先：本模块不新增任何判定逻辑，`render_goal_wiki_page()` 直接
复用 `perception/goal_node_page.py::build_goal_node_page()` 已经拼好的
数据，只做"渲染成 Markdown + 写盘"这一步；批量遍历复用
`perception/goal_tree_report.py::_collect_subtree()` 的同一份 BFS 逻辑，
跟树级汇总报告口径一致，不重新实现一遍。

幂等性：每次都是"整份 index.md 原样重写"，不追加、不留历史版本文件，
重复调用不会产生垃圾文件——`goals_wiki/` 下的文件数只跟"当前树里有多少
节点"挂钩，不随调用次数增长。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog


def goals_wiki_root(paths: "AgentPaths") -> Path:
    """`goals_wiki/` 顶层目录，与 `output_workspace.py` 现有的
    `<outputs_root>/goals/<goal_id>/...` 约定平行，不侵入既有产出目录。"""
    from mini_agent.evolution.output_workspace import outputs_root
    return outputs_root(paths) / "goals_wiki"


def goal_wiki_dir(paths: "AgentPaths", goal_id: str) -> Path:
    return goals_wiki_root(paths) / goal_id


def goal_wiki_index_path(paths: "AgentPaths", goal_id: str) -> Path:
    return goal_wiki_dir(paths, goal_id) / "index.md"


def _render_markdown(page) -> str:
    """把 `GoalNodePage`（perception/goal_node_page.py）渲染成 Markdown
    正文。纯字符串拼接，不做任何数据判定——判定逻辑全部在
    `build_goal_node_page()` 里已经做完，这里只负责排版。"""
    lines: list[str] = [f"# {page.title}", ""]

    if page.path_from_root:
        crumbs = []
        for c in page.path_from_root:
            if c["id"] == page.goal_id:
                crumbs.append(c["title"])
            else:
                crumbs.append(f"[{c['title']}](../{c['id']}/index.md)")
        lines.append("路径：" + " / ".join(crumbs))
        lines.append("")

    lines.append(f"- id: `{page.goal_id}`")
    lines.append(f"- 状态: {page.status}  |  层级: {page.level}  |  执行阶段: {page.execution_phase_mode}")
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(page.generated_at)) if page.generated_at else "未知"
    lines.append(f"- 生成时间: {when}（机械生成，非 agent 手写，下次刷新会整份覆盖）")
    lines.append("")

    if page.progress_notes_tail:
        lines.append("## 最近进展")
        lines.append("```")
        lines.append(page.progress_notes_tail)
        lines.append("```")
        lines.append("")

    if page.recent_cycle_summaries:
        lines.append("## 最近轮次")
        for s in page.recent_cycle_summaries[-10:]:
            summary = (s.get("task_summary") or "")[:80]
            lines.append(
                f"- cycle={s.get('cycle')} status={s.get('status')} "
                f"artifacts={s.get('artifact_count')}  {summary}"
            )
        lines.append("")

    lines.append("## 产出")
    lines.append(f"产出目录：`{page.output_dir}`")
    lines.append("")
    if page.output_readme_text:
        lines.append(page.output_readme_text.rstrip())
        lines.append("")

    lines.append("## 子节点")
    if page.children:
        for c in page.children:
            lines.append(f"- [{c['title']}]({c['id']}/index.md) — status={c['status']} level={c['level']}")
    else:
        lines.append("（无子节点）")
    lines.append("")

    pending = page.pending_items or {}
    pending_count = (
        len(pending.get("decompose_candidates", []))
        + (1 if pending.get("focus_confirmation") else 0)
        + len(pending.get("tuning_proposals", []))
        + len(pending.get("execution_specs", []))
    )
    lines.append(f"## 待处理事项（共 {pending_count} 项）")
    for cand in pending.get("decompose_candidates", []):
        lines.append(f"- 分解候选：{cand.get('title')}（{cand.get('candidate_id')}）")
    if pending.get("focus_confirmation"):
        lines.append("- 焦点未确认（有子节点但 current_focus_ids 为空）")
    for prop in pending.get("tuning_proposals", []):
        lines.append(f"- 调优草案：{prop.get('proposal_id')} status={prop.get('status')}")
    for spec_info in pending.get("execution_specs", []):
        lines.append(f"- 执行规范草稿未确认：version={spec_info.get('version')}")
    if pending_count == 0:
        lines.append("（当前没有待处理事项）")
    lines.append("")

    if page.feedback_history:
        lines.append(f"## 反馈历史（{len(page.feedback_history)} 条）")
        for fb in page.feedback_history[-10:]:
            lines.append(f"- {fb.get('text')}")
        lines.append("")

    return "\n".join(lines) + "\n"


def render_goal_wiki_page(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    goal_id: str,
) -> Optional[str]:
    """为单个节点渲染 + 落盘一份 wiki 页（方案 §2.2）。

    节点不存在时返回 `None`（调用方据此跳过，不写任何文件）；存在则整份
    覆盖写入 `goal_wiki_index_path()`，返回写入的 Markdown 文本。
    """
    from mini_agent.perception.goal_node_page import build_goal_node_page

    page = build_goal_node_page(paths, goal_backlog, goal_id)
    if not page.found:
        return None

    text = _render_markdown(page)
    index_path = goal_wiki_index_path(paths, goal_id)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(text, encoding="utf-8")
    return text


def _render_global_root_index(goal_backlog: "GoalBacklog") -> str:
    """全局根索引 `goals_wiki/index.md`（方案 §2.2 导航体验）——列出全部
    顶层节点（`parent_id` 为空），各自链接到 `<goal_id>/index.md`。跟
    单节点页一样机械生成，不经过 LLM。"""
    roots = [n for n in goal_backlog.all_nodes() if not n.parent_id]
    lines = ["# 目标树 Wiki", "", f"（机械生成，非 agent 手写；共 {len(roots)} 个顶层节点）", ""]
    for n in sorted(roots, key=lambda x: x.title):
        lines.append(f"- [{n.title}]({n.id}/index.md) — status={n.status}")
    if not roots:
        lines.append("（当前没有任何目标节点）")
    return "\n".join(lines) + "\n"


def build_goal_wiki_tree(
    paths: "AgentPaths",
    goal_backlog: "GoalBacklog",
    root_id: Optional[str] = None,
) -> list[str]:
    """批量遍历（子）树，逐节点调用 `render_goal_wiki_page()`（方案 §2.2
    "生成时机"）。

    `root_id` 为 `None` 时遍历全局森林（所有顶层节点及其整棵子树），并
    额外刷新 `goals_wiki/index.md` 全局入口；传 `root_id` 时只遍历该
    （子）树，不碰全局入口——子树的根节点会作为普通节点被渲染，但它未必
    是全局顶层节点，不适合冒充全局入口。

    复用 `goal_tree_report._collect_subtree()` 的同一份遍历逻辑，跟树级
    汇总报告口径一致，不重新实现一遍 BFS。单个节点渲染失败不中断整棵树
    的批量生成，跳过继续处理下一个（与方案共享原则"任一子数据源异常时
    整体仍要能生成"一致）。

    返回本次成功渲染的 goal_id 列表（用于日志/CLI 展示条数）。
    """
    from mini_agent.perception.goal_tree_report import _collect_subtree

    nodes = _collect_subtree(goal_backlog, root_id)
    rendered: list[str] = []
    for node in nodes:
        try:
            text = render_goal_wiki_page(paths, goal_backlog, node.id)
        except Exception:
            continue
        if text is not None:
            rendered.append(node.id)

    if root_id is None:
        try:
            root_index_path = goals_wiki_root(paths) / "index.md"
            root_index_path.parent.mkdir(parents=True, exist_ok=True)
            root_index_path.write_text(_render_global_root_index(goal_backlog), encoding="utf-8")
        except Exception:
            pass

    return rendered


__all__ = [
    "goals_wiki_root",
    "goal_wiki_dir",
    "goal_wiki_index_path",
    "render_goal_wiki_page",
    "build_goal_wiki_tree",
]
