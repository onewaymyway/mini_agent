"""writing/brief.py — 选题简报 brief.yaml（方案 §8.1 第 1 步）：读者、角度、必须覆盖的要点、
互链文章、不得重复的内容、证据清单、章程文风与禁区。落盘后可人工修改（本期只读取，不回填）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import store
from ..digest import runner as drunner
from ..library import catalog
from ..planning import states
from ..research import pack, sources


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build(ctx, tid: str, feedback: Optional[List[str]] = None) -> Dict[str, Any]:
    topic = states.get(ctx, tid)
    pk = pack.read(ctx, tid) or {}
    charter = ctx.load_charter()[0]
    style = charter.get("style") or {}
    risk = charter.get("risk") or {}
    existing = []
    for row in catalog.list_articles(ctx):
        rec = drunner.load_digest(ctx, row["id"]) or {}
        if rec.get("status") == "ok":
            existing.append({"id": row["id"], "title": rec.get("title") or row["title"],
                             "origin": row.get("origin", "imported"),
                             "concepts": list(rec.get("concepts") or [])[:12]})
    srcs = []
    for c in sources.list_cards(ctx, tid):
        srcs.append({"id": c["id"], "title": c.get("title", ""), "url": c.get("url", ""),
                     "credibility": c.get("credibility", "C"), "source_type": c.get("source_type", "web"),
                     "excerpt": str(c.get("excerpt") or "")[:300]})
    brief = {
        "topic_id": tid,
        "title": topic["title"],
        "kind": topic.get("kind") or "概念讲解",
        "level": topic.get("level") or "入门",
        "angle": topic.get("angle") or "",
        "must_cover": list(topic.get("must_cover") or []),
        "library_links": list(pk.get("library_links") or []),
        "audience": (charter.get("audience") or {}).get("primary") or "",
        "style": {"tone": style.get("tone") or "平实克制",
                  "length": list(style.get("length") or [1500, 3500]),
                  "must_have": list(style.get("must_have") or []),
                  "ai_disclosure": bool(style.get("ai_disclosure", True))},
        "terms": charter.get("terms") or {},
        "scope": {"in": list((charter.get("scope") or {}).get("in") or []),
                  "out": list((charter.get("scope") or {}).get("out") or [])},
        "risk": {"disclaimer": risk.get("disclaimer") or "",
                 "forbidden_claims": list(risk.get("forbidden_claims") or [])},
        "existing_articles": existing,
        "sources": srcs,
        "feedback": [str(f) for f in (feedback or []) if str(f).strip()],
        "built_at": _now(),
    }
    store.write_yaml_atomic(ctx.path("drafts", tid, "brief.yaml"), brief)
    return brief
