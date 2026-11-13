"""Hacker News 热门：topstories 取前 30 个 id，逐条取详情。单条失败时跳过，不影响整体。"""
import datetime
import json

BASE = "https://hacker-news.firebaseio.com/v0"


def fetch(ctx):
    ids = json.loads(ctx.http_get(f"{BASE}/topstories.json").decode("utf-8"))[:30]
    rows = []
    for rank, sid in enumerate(ids, start=1):
        try:
            it = json.loads(ctx.http_get(f"{BASE}/item/{sid}.json").decode("utf-8"))
        except Exception:  # noqa: BLE001 - 单条失败跳过
            continue
        if not isinstance(it, dict) or it.get("type") != "story" or not it.get("title"):
            continue
        published = None
        if it.get("time"):
            published = datetime.datetime.fromtimestamp(int(it["time"]), tz=datetime.timezone.utc).isoformat()
        rows.append({
            "title": it["title"],
            "url": it.get("url") or f"https://news.ycombinator.com/item?id={sid}",
            "rank": rank,
            "heat": str(it.get("score", 0)),
            "summary": f"{it.get('descendants', 0)} 条评论",
            "published_at": published,
        })
    return rows
