"""wiki/coverage.py — 覆盖度（方案 §5.3 覆盖度，纯代码计算，不调用模型）。

每个节点的指标：
  article_count    覆盖的文章数（节点下概念的出处文章并集）
  avg_chars        出处文章平均字数（“深度”之一）
  claims           节点相关的断言数（断言账本）
  levels           文章层级分布（入门/进阶/专题）
  evidence_share   概念中有依据定义（status ok）的占比（“证据强度”）
  freshness        最新导入时间（新鲜度）
  dangling         出处文章中“提到但未讲清”的概念条数
  color            red 空洞 / yellow 偏浅 / green 充足

阈值按库规模分档（方案 §21-3）：文章 < 50 篇时，充足需 ≥ 2 篇且平均 ≥ 1500 字；其它规模取同档默认。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from .. import store
from ..digest import runner as drunner
from ..library import catalog
from . import model, tree

SMALL_LIBRARY = 50


def thresholds(total_articles: int) -> Dict[str, Any]:
    if total_articles < SMALL_LIBRARY:
        return {"tier": "small", "min_articles": 2, "min_avg_chars": 1500}
    return {"tier": "normal", "min_articles": 3, "min_avg_chars": 2000}


def _color(article_count: int, avg_chars: float, th: Dict[str, Any]) -> str:
    if article_count == 0:
        return "red"
    if article_count < th["min_articles"] or avg_chars < th["min_avg_chars"]:
        return "yellow"
    return "green"


def compute(ctx) -> Dict[str, Any]:
    data = tree.load(ctx)
    rows = {r["id"]: r for r in catalog.list_articles(ctx)}
    th = thresholds(len(rows))
    pages = {m["id"]: m for m in model.list_concepts(ctx)}
    claims = model.read_jsonl(ctx, "claims.jsonl")
    digests = {aid: drunner.load_digest(ctx, aid) or {} for aid in rows}

    def node_metrics(nid: str) -> Dict[str, Any]:
        concept_ids = _concepts_under(data, nid)
        arts: Set[str] = set()
        for cid in concept_ids:
            arts |= set((pages.get(cid) or {}).get("articles") or [])
        arts = {a for a in arts if a in rows}
        ok = sum(1 for cid in concept_ids if (pages.get(cid) or {}).get("status") == "ok")
        cl = sum(1 for c in claims if set(c.get("concepts") or []) & set(concept_ids))
        levels: Dict[str, int] = {}
        dangling: Set[str] = set()
        for aid in arts:
            lv = digests[aid].get("level")
            if lv:
                levels[lv] = levels.get(lv, 0) + 1
            dangling |= set(digests[aid].get("dangling") or [])
        chars = [rows[a].get("chars", 0) for a in arts]
        avg = sum(chars) / len(chars) if chars else 0.0
        return {
            "article_count": len(arts),
            "avg_chars": round(avg),
            "claims": cl,
            "levels": levels,
            "evidence_share": round(ok / len(concept_ids), 2) if concept_ids else 0.0,
            "freshness": max((rows[a].get("imported_at", "") for a in arts), default=""),
            "dangling": len(dangling),
            "color": _color(len(arts), avg, th),
        }

    nodes = {nid: node_metrics(nid) for nid in data["nodes"]}
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "thresholds": th, "library_articles": len(rows), "nodes": nodes}
    store.write_json_atomic(ctx.path("wiki", "coverage.json"), out)
    return out


def _concepts_under(data: Dict[str, Any], nid: str) -> List[str]:
    node = data["nodes"][nid]
    if node.get("level") == tree.TOPIC:
        return list(node.get("concepts") or [])
    out: List[str] = []
    for cid_node, child in tree.children(data, nid):
        out += list(child.get("concepts") or [])
    return out


def read(ctx) -> Dict[str, Any]:
    return store.read_json(ctx.path("wiki", "coverage.json"), default={}) or {}
