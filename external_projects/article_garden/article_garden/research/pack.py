"""research/pack.py — 证据包 pack.yaml（方案 §7 第 6 步）。

充分的条件（首版阈值，方案 §13 / §21-7 之后校准）：
  * 要点覆盖率 ≥ 60%（任一来源摘录包含该要点即算覆盖）；
  * 达到章程最低可信度的来源 ≥ 2 条；
  * 其中至少一条为 A 或 B 级。
不满足时写明原因（reasons），由调研流程决定放弃选题。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import store
from ..library import catalog
from . import sources

MIN_COVERAGE = 0.6
MIN_CREDIBLE = 2
RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(ctx, tid: str):
    return ctx.path("research", "packs", tid, "pack.yaml")


def read(ctx, tid: str) -> Optional[Dict[str, Any]]:
    data = store.read_yaml(_path(ctx, tid), default=None)
    return data if isinstance(data, dict) else None


def evaluate(ctx, tid: str, topic: Dict[str, Any], min_credibility: str = "B") -> Dict[str, Any]:
    cards = sources.list_cards(ctx, tid)
    points = [str(p) for p in (topic.get("must_cover") or []) if str(p).strip()]
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    for c in cards:
        cred = c.get("credibility")
        if cred in counts:
            counts[cred] += 1
    pts: List[Dict[str, Any]] = []
    covered = 0
    for p in points:
        srcs = [c["id"] for c in cards if p in str(c.get("excerpt") or "")]
        covered += 1 if srcs else 0
        pts.append({"name": p, "sources": srcs})
    coverage = covered / len(points) if points else 1.0
    min_cred = min_credibility if min_credibility in RANK else "B"
    credible = [c for c in cards if RANK.get(c.get("credibility"), 9) <= RANK[min_cred]]
    has_ab = any(c.get("credibility") in ("A", "B") for c in credible)

    reasons: List[str] = []
    if not cards:
        reasons.append("没有可用来源")
    if coverage < MIN_COVERAGE:
        reasons.append(f"要点覆盖率 {coverage:.0%} 低于 {MIN_COVERAGE:.0%}")
    if len(credible) < MIN_CREDIBLE:
        reasons.append(f"达到最低可信度（{min_cred}）的来源仅 {len(credible)} 条，至少需要 {MIN_CREDIBLE} 条")
    if cards and not has_ab:
        reasons.append("缺少 A/B 级来源")

    links = []
    if topic.get("links"):
        titles = {r["id"]: r["title"] for r in catalog.list_articles(ctx)}
        links = [{"id": a, "title": titles.get(a, a)} for a in topic["links"]]

    data = {"topic_id": tid, "points": pts, "coverage": round(coverage, 3),
            "sources_total": len(cards), "sources_credible": len(credible), "credibility": counts,
            "min_credibility": min_cred, "sufficient": not reasons, "reasons": reasons,
            "library_links": links, "updated_at": _now()}
    store.write_yaml_atomic(_path(ctx, tid), data)
    return data
