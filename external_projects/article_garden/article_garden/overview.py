"""overview.py — 首页数据：流水线状态条、待办收件箱、下一步建议（纯规则，不调 LLM，方案 §19.3）。

P0 只有“导入”之前的状态；P1 起各阶段陆续接入真实数据。接口保持稳定：
  pipeline_status(ctx) -> [{key,label,state,detail,phase}]   state: done / todo / pending（尚未实现）
  inbox_items(ctx)     -> [{kind,title,detail,page}]          待人工处理的事项
  next_steps(ctx)      -> [{text,page,enabled,hint}]          规则生成的下一步建议
"""

from __future__ import annotations

from typing import Any, Dict, List

from .domains import DomainContext

STAGES = (
    ("import", "导入", "P1"), ("digest", "摘要", "P1"), ("concepts", "概念", "P2"), ("tree", "知识树", "P3"),
    ("gaps", "缺口", "P4"), ("topics", "选题", "P4"), ("research", "调研", "P5"), ("write", "成文", "P6"),
)


def pipeline_status(ctx: DomainContext) -> List[Dict[str, Any]]:
    n = ctx.article_count()
    rows = []
    for key, label, phase in STAGES:
        if key == "import":
            state, detail = ("done", f"{n} 篇") if n else ("todo", "尚无文章")
        else:
            state, detail = "pending", f"{phase} 实现"
        rows.append({"key": key, "label": label, "state": state, "detail": detail, "phase": phase})
    return rows


def inbox_items(ctx: DomainContext) -> List[Dict[str, Any]]:
    """待办收件箱。P0 没有可产生待办的环节，返回空；后续各期按 kind 追加：
    topic_approve / draft_review / tree_change / concept_conflict / failed_topic。"""
    return []


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
