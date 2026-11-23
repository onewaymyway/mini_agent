"""ui/placeholder_pages.py — 后续各期才实现的页面：统一给出“做什么/哪一期/现在能做什么”。"""

from __future__ import annotations

from ui import state
from ui.topbar import render_placeholder


def _page(title, phase, what, hint):
    def render() -> None:
        ctx = state.current_ctx()
        if ctx is not None:
            render_placeholder(ctx, title, phase, what, hint)
    return render


render_tree_page = _page("🌳 知识树", "P3",
                         "树 + 覆盖热力 + 节点下文章；pin/改名/移动/合并，结构变更先出 diff 清单再确认。",
                         "先在「设置 → 章程」里确认 scope.in，它将作为知识树的顶层方向骨架。")
render_graph_page = _page("🕸 概念图", "P2",
                          "概念页、关系图（graphviz）、冲突并排处理、疑似同一概念的合并建议。",
                          "需要先完成 P1 的文章导入与摘要。")
render_topics_page = _page("💡 选题队列", "P4",
                           "缺口检测生成候选选题：待审 / 已放行 / 进行中三列，带“为什么值得写”与分数明细。",
                           "需要先完成知识树与覆盖度（P3）。")
render_research_page = _page("🔎 调研", "P5",
                             "每个选题的证据包：来源卡、可信度徽章、手动补充链接或文本。",
                             "需要先有已放行的选题（P4）。")
