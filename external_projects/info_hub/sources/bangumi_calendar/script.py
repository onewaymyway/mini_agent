"""Bangumi 每日放送：取今天（按本机时区的星期几）播出的条目，按评分降序。"""
import datetime
import json

API = "https://api.bgm.tv/calendar"
HEADERS = {"User-Agent": "info_hub/1.0 (personal feed reader)", "Accept": "application/json"}


def fetch(ctx):
    days = json.loads(ctx.http_get(API, headers=HEADERS).decode("utf-8"))
    today = datetime.datetime.now().isoweekday()  # 1=周一 … 7=周日，与 Bangumi 的 weekday.id 一致
    rows = []
    for day in days:
        if (day.get("weekday") or {}).get("id") != today:
            continue
        for it in day.get("items") or []:
            sid = it.get("id")
            title = it.get("name_cn") or it.get("name") or ""
            if not title or not sid:
                continue
            rating = (it.get("rating") or {}).get("score") or 0
            rows.append({
                "title": title,
                "url": it.get("url") or f"https://bgm.tv/subject/{sid}",
                "heat": str(rating) if rating else None,
                "summary": (it.get("summary") or "")[:200],
                "published_at": it.get("air_date") or None,
                "_score": float(rating or 0),
            })
    rows.sort(key=lambda r: r["_score"], reverse=True)
    for idx, r in enumerate(rows, start=1):
        r.pop("_score", None)
        r["rank"] = idx
    return rows
