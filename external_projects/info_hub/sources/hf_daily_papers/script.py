"""Hugging Face 每日论文：按官方列表顺序取条目，热度用点赞数（upvotes）。"""
import json

API = "https://huggingface.co/api/daily_papers"


def fetch(ctx):
    data = json.loads(ctx.http_get(API).decode("utf-8"))
    rows = []
    for rank, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        paper = item.get("paper") or {}
        pid = paper.get("id")
        title = item.get("title") or paper.get("title") or ""
        if not title or not pid:
            continue
        authors = [a.get("name", "") for a in (paper.get("authors") or []) if isinstance(a, dict)]
        summary = (paper.get("summary") or "").replace("\n", " ")[:200]
        if authors:
            summary = f"{', '.join(authors[:3])} 等 · {summary}"
        upvotes = paper.get("upvotes")
        rows.append({
            "title": title,
            "url": f"https://huggingface.co/papers/{pid}",
            "rank": rank,
            "heat": str(upvotes) if upvotes is not None else None,
            "summary": summary,
            "published_at": item.get("publishedAt") or paper.get("publishedAt") or None,
        })
    return rows
