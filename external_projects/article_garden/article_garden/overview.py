"""overview.py — 首页数据：流水线状态条、待办收件箱、下一步建议（纯规则，不调 LLM，方案 §19.3）。

P0 只有“导入”之前的状态；P1 起各阶段陆续接入真实数据。接口保持稳定：
  pipeline_status(ctx) -> [{key,label,state,detail,phase}]   state: done / todo / pending（尚未实现）
  inbox_items(ctx)     -> [{kind,title,detail,page}]          待人工处理的事项
  next_steps(ctx)      -> [{text,page,enabled,hint}]          规则生成的下一步建议
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .domains import DomainContext

STAGES = (
    ("import", "导入", "P1"), ("digest", "摘要", "P1"), ("concepts", "概念", "P2"), ("tree", "知识树", "P3"),
    ("gaps", "缺口", "P4"), ("topics", "选题", "P4"), ("research", "调研", "P5"), ("write", "成文", "P6"),
)


def _stage_state(ctx: DomainContext, key: str) -> Tuple[str, str]:
    """根据库内真实数据判断各阶段状态（P7 起不再是占位）。"""
    from .digest import runner as drunner
    from .planning import states as pstates
    from .library import catalog

    if key == "import":
        n = ctx.article_count()
        return ("done", f"{n} 篇") if n else ("todo", "尚无文章")
    if key == "digest":
        ok = sum(1 for r in catalog.list_articles(ctx)
                 if (drunner.load_digest(ctx, r["id"]) or {}).get("status") == "ok")
        return ("done", f"{ok} 篇已摘要") if ok else ("todo", "尚未摘要")
    if key == "concepts":
        n = len(list(ctx.path("wiki", "concepts").glob("*.md"))) if ctx.path("wiki", "concepts").is_dir() else 0
        return ("done", f"{n} 个概念页") if n else ("todo", "尚未构建概念")
    if key == "tree":
        from .wiki import tree as wtree
        data = wtree.load(ctx)
        n = len(wtree.nodes_at(data, wtree.TOPIC))
        return ("done", f"{n} 个主题") if n else ("todo", "尚未建树")
    topics = pstates.load_topics(ctx)
    if key in ("gaps", "topics"):
        return ("done", f"{len(topics)} 个选题") if topics else ("todo", "尚未检测缺口")
    if key == "research":
        n = sum(1 for t in topics if t["status"] in (pstates.RESEARCHED, pstates.RESEARCHING, pstates.DROPPED)
                and t.get("history"))
        return ("done", f"{n} 个选题已调研") if n else ("todo", "尚未调研")
    if key == "write":
        n = sum(1 for r in catalog.list_articles(ctx) if r.get("origin") == "generated")
        return ("done", f"{n} 篇生成文章") if n else ("todo", "尚未成文")
    return ("todo", "")


def pipeline_status(ctx: DomainContext) -> List[Dict[str, Any]]:
    rows = []
    for key, label, phase in STAGES:
        state, detail = _stage_state(ctx, key)
        rows.append({"key": key, "label": label, "state": state, "detail": detail, "phase": phase})
    return rows


def inbox_items(ctx: DomainContext) -> List[Dict[str, Any]]:
    """待办收件箱（方案 §19.3）：待放行选题、待审草稿、待确认的树提案、成文失败的选题。"""
    from .planning import states as pstates
    from .wiki import placement

    items: List[Dict[str, Any]] = []
    topics = pstates.load_topics(ctx)
    proposed = [t for t in topics if t["status"] == pstates.PROPOSED]
    if proposed:
        items.append({"kind": "topic_approve", "title": f"{len(proposed)} 个选题待放行", "page": "topics",
                      "detail": "；".join(t["title"] for t in proposed[:3])})
    drafted = [t for t in topics if t["status"] == pstates.DRAFTED]
    if drafted:
        items.append({"kind": "draft_review", "title": f"{len(drafted)} 篇草稿待审阅", "page": "review",
                      "detail": "；".join(t["title"] for t in drafted[:3])})
    for t in topics:
        if t["status"] == pstates.FAILED:
            items.append({"kind": "failed_topic", "id": t["id"], "title": f"选题 {t['id']} 成文失败",
                          "page": "review", "detail": t["title"]})
    if placement.load_proposal(ctx):
        items.append({"kind": "tree_change", "title": "知识树有待确认的挂载提案", "page": "tree",
                      "detail": "在知识树页面确认后才会应用"})
    return items


def next_steps(ctx: DomainContext) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    charter, _ = ctx.load_charter()
    if not charter["scope"]["in"]:
        steps.append({"text": "先在「设置 → 章程」里确认领域范围（scope.in），后续选题与建树都以它为准",
                      "page": "settings", "enabled": True, "hint": ""})
    if ctx.article_count() == 0:
        steps.append({"text": "导入文章库（Markdown 文件、zip 或目录）", "page": "library",
                      "enabled": False, "hint": "导入功能在 P1 提供；目前可把 .md 文件直接放进 library/imported/"})
    return steps
