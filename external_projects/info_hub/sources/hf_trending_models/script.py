"""Hugging Face 热门模型：按官方 trendingScore 排序。模型链接直接拼接为 https://huggingface.co/<id>。"""
import json

API = "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit=30"


def fetch(ctx):
    data = json.loads(ctx.http_get(API).decode("utf-8"))
    rows = []
    for rank, m in enumerate(data, start=1):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mid = m["id"]
        parts = [str(m.get("pipeline_tag") or "")]
        if m.get("likes") is not None:
            parts.append(f"♥ {m['likes']}")
        if m.get("downloads") is not None:
            parts.append(f"下载 {m['downloads']}")
        score = m.get("trendingScore")
        rows.append({
            "title": mid,
            "url": f"https://huggingface.co/{mid}",
            "rank": rank,
            "heat": str(score) if score is not None else None,
            "summary": " · ".join(p for p in parts if p),
            "published_at": m.get("lastModified") or None,
        })
    return rows
