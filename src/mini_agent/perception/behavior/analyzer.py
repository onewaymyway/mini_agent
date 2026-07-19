"""
perception/behavior/analyzer.py — 分析层：把原始事件聚合成"工作/生活画像"日报

这是采集层之上的一层：BehaviorEventStore 里躺着的是原始事件，agent 真正
需要的是"今天大部分时间在哪个项目/哪类事情上、专注度如何、工作生活节奏
怎么样"这种结构化摘要，方便直接读或者注入到 agent 的上下文里。

输出落盘在 ~/.agent/behavior/analysis/<YYYY-MM-DD>.json（结构化）和
.md（给人看/给 agent 直接读的可读版本）。
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path
from typing import Optional

from .config import _behavior_dir
from .events import ActivityEvent


# ── 简单的分类启发式（可按需在这里扩展，不做成配置是为了保持简单）────────────

_WORK_APP_HINTS = (
    "code", "pycharm", "idea", "terminal", "iterm", "wt", "cmd", "powershell",
    "xcode", "android studio", "docker", "postman", "notion", "obsidian",
    "excel", "word", "powerpoint", "slack", "outlook", "teams", "figma",
)
_ENTERTAINMENT_APP_HINTS = (
    "steam", "epicgames", "netflix", "spotify", "music", "youtube",
    "bilibili", "wechat", "qq", "discord", "game",
)

_WORK_DOMAIN_HINTS = (
    "github.com", "gitlab.com", "stackoverflow.com", "docs.", "developer.",
    "atlassian.net", "jira", "confluence", "notion.so", "figma.com",
    "console.aws.amazon.com", "azure.com", "cloud.google.com",
)
_ENTERTAINMENT_DOMAIN_HINTS = (
    "youtube.com", "bilibili.com", "netflix.com", "twitch.tv", "weibo.com",
    "twitter.com", "x.com", "instagram.com", "tiktok.com", "douyin.com",
    "taobao.com", "jd.com", "amazon.com",
)


def _categorize(name: str, work_hints: tuple, ent_hints: tuple) -> str:
    n = (name or "").lower()
    if any(h in n for h in work_hints):
        return "work"
    if any(h in n for h in ent_hints):
        return "entertainment"
    return "other"


def _day_bounds(day: str) -> tuple[float, float]:
    d = _dt.date.fromisoformat(day)
    start = _dt.datetime.combine(d, _dt.time.min).timestamp()
    end = start + 86400
    return start, end


def _analysis_dir() -> Path:
    d = _behavior_dir() / "analysis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_daily_summary(mgr, day: str) -> dict:
    """基于 mgr（BehaviorPerceptionManager）里的原始事件，生成某天的画像摘要并落盘。"""
    since, until = _day_bounds(day)
    events = mgr.query(since=since, until=until, limit=100000)

    # ── 工作画像 ──────────────────────────────────────────────────────────
    app_duration: dict[str, float] = {}
    app_switch_count = 0
    for e in events:
        if e.source.endswith("_active_window") and e.event_type == "app_focus":
            app_switch_count += 1
            if e.app_name and e.duration_sec:
                app_duration[e.app_name] = app_duration.get(e.app_name, 0.0) + e.duration_sec

    domain_duration: dict[str, float] = {}
    for e in events:
        if e.event_type == "page_visit" and e.domain and e.duration_sec:
            domain_duration[e.domain] = domain_duration.get(e.domain, 0.0) + e.duration_sec

    git_repos: dict[str, int] = {}
    for e in events:
        if e.source == "git":
            repo = (e.meta or {}).get("repo", "unknown")
            git_repos[repo] = git_repos.get(repo, 0) + 1

    terminal_cmd_count = sum(1 for e in events if e.source == "terminal")

    started_apps = sorted({e.app_name for e in events if e.source == "app_lifecycle" and e.event_type == "app_start" and e.app_name})

    idle_events = [e for e in events if e.event_type in ("idle_start", "idle_end")]
    total_idle_sec = 0.0
    idle_open: Optional[float] = None
    for e in sorted(idle_events, key=lambda x: x.timestamp):
        if e.event_type == "idle_start":
            idle_open = e.timestamp
        elif e.event_type == "idle_end" and idle_open is not None:
            total_idle_sec += e.timestamp - idle_open
            idle_open = None

    media_events = [e for e in events if e.source == "now_playing"]
    media_total_sec = sum(e.duration_sec or 0 for e in media_events)
    media_titles = [e.window_title for e in media_events if e.window_title]

    # 手机端（Tasker/快捷指令等外部上报，source in {"android_usage","ios_shortcuts",...}）
    mobile_events = [e for e in events if e.source in ("android_usage", "ios_shortcuts") or e.event_type in ("screen_unlock", "screen_off", "geofence", "health_daily")]
    mobile_app_duration: dict[str, float] = {}
    unlock_count = 0
    geofence_labels: list[str] = []
    health_daily: dict = {}
    for e in mobile_events:
        if e.event_type == "app_focus" and e.app_name and e.duration_sec:
            mobile_app_duration[e.app_name] = mobile_app_duration.get(e.app_name, 0.0) + e.duration_sec
        elif e.event_type == "screen_unlock":
            unlock_count += 1
        elif e.event_type == "geofence":
            label = (e.meta or {}).get("label")
            if label:
                geofence_labels.append(label)
        elif e.event_type == "health_daily":
            health_daily.update(e.meta or {})
    mobile_total_sec = sum(mobile_app_duration.values())

    work_app_sec = sum(v for k, v in app_duration.items() if _categorize(k, _WORK_APP_HINTS, _ENTERTAINMENT_APP_HINTS) == "work")
    ent_app_sec = sum(v for k, v in app_duration.items() if _categorize(k, _WORK_APP_HINTS, _ENTERTAINMENT_APP_HINTS) == "entertainment")
    work_domain_sec = sum(v for k, v in domain_duration.items() if _categorize(k, _WORK_DOMAIN_HINTS, _ENTERTAINMENT_DOMAIN_HINTS) == "work")
    ent_domain_sec = sum(v for k, v in domain_duration.items() if _categorize(k, _WORK_DOMAIN_HINTS, _ENTERTAINMENT_DOMAIN_HINTS) == "entertainment")

    if events:
        first_ts = min(e.timestamp for e in events)
        last_ts = max(e.timestamp for e in events)
        session_span_hr = round((last_ts - first_ts) / 3600, 1)
        first_str = time.strftime("%H:%M", time.localtime(first_ts))
        last_str = time.strftime("%H:%M", time.localtime(last_ts))
    else:
        session_span_hr, first_str, last_str = 0, "-", "-"

    top_apps = sorted(app_duration.items(), key=lambda kv: -kv[1])[:8]
    top_domains = sorted(domain_duration.items(), key=lambda kv: -kv[1])[:8]

    data = {
        "date": day,
        "generated_at": time.time(),
        "event_count": len(events),
        "work": {
            "session_span": [first_str, last_str, session_span_hr],
            "app_switch_count": app_switch_count,
            "top_apps_sec": top_apps,
            "top_domains_sec": top_domains,
            "work_app_sec": round(work_app_sec, 1),
            "entertainment_app_sec": round(ent_app_sec, 1),
            "work_domain_sec": round(work_domain_sec, 1),
            "entertainment_domain_sec": round(ent_domain_sec, 1),
            "git_repos": git_repos,
            "terminal_cmd_count": terminal_cmd_count,
            "background_apps_started": started_apps,
            "idle_sec": round(total_idle_sec, 1),
        },
        "life": {
            "media_total_sec": round(media_total_sec, 1),
            "media_titles": media_titles[:20],
            "mobile_total_sec": round(mobile_total_sec, 1),
            "mobile_top_apps_sec": sorted(mobile_app_duration.items(), key=lambda kv: -kv[1])[:5],
            "screen_unlock_count": unlock_count,
            "geofence_labels": geofence_labels,
            "health_daily": health_daily,
        },
    }
    data["markdown"] = _render_markdown(data)

    path = _analysis_dir() / f"{day}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _fmt_min(sec: float) -> str:
    return f"{round(sec / 60)} 分钟"


def _render_markdown(data: dict) -> str:
    w = data["work"]
    l = data["life"]
    lines = [f"# {data['date']} 行为画像日报\n"]

    span = w["session_span"]
    lines.append(f"**活跃时段**：{span[0]} ~ {span[1]}（跨度约 {span[2]} 小时），"
                 f"空闲累计约 {_fmt_min(w['idle_sec'])}\n")

    lines.append("## 工作画像\n")
    lines.append(f"- 前台窗口切换 {w['app_switch_count']} 次"
                 f"（切换越频繁，通常意味着越碎片化）")
    if w["top_apps_sec"]:
        apps_str = "、".join(f"{name}({_fmt_min(sec)})" for name, sec in w["top_apps_sec"][:5])
        lines.append(f"- 使用时长最高的程序：{apps_str}")
    if w["top_domains_sec"]:
        domains_str = "、".join(f"{name}({_fmt_min(sec)})" for name, sec in w["top_domains_sec"][:5])
        lines.append(f"- 访问时长最高的网站：{domains_str}")
    if w["git_repos"]:
        repos_str = "、".join(f"{repo}（{n} 次提交/切换）" for repo, n in w["git_repos"].items())
        lines.append(f"- Git 活动：{repos_str}")
    if w["terminal_cmd_count"]:
        lines.append(f"- 终端命令执行 {w['terminal_cmd_count']} 次")
    if w["background_apps_started"]:
        lines.append(f"- 今天新启动的后台程序：{', '.join(w['background_apps_started'][:10])}")
    lines.append(f"- 工作类 App/网站时长估算：约 {_fmt_min(w['work_app_sec'] + w['work_domain_sec'])}")
    lines.append(f"- 娱乐类 App/网站时长估算：约 {_fmt_min(w['entertainment_app_sec'] + w['entertainment_domain_sec'])}")

    lines.append("\n## 生活画像\n")
    if l["media_total_sec"]:
        lines.append(f"- 媒体播放累计约 {_fmt_min(l['media_total_sec'])}")
    if l["media_titles"]:
        lines.append(f"- 播放过：{', '.join(l['media_titles'][:8])}")
    if l["mobile_total_sec"]:
        lines.append(f"- 手机端 App 使用累计约 {_fmt_min(l['mobile_total_sec'])}")
    if l["mobile_top_apps_sec"]:
        apps_str = "、".join(f"{name}({_fmt_min(sec)})" for name, sec in l["mobile_top_apps_sec"])
        lines.append(f"- 手机端使用最多：{apps_str}")
    if l["screen_unlock_count"]:
        lines.append(f"- 手机解锁 {l['screen_unlock_count']} 次")
    if l["geofence_labels"]:
        lines.append(f"- 地点切换：{' → '.join(l['geofence_labels'])}")
    if l["health_daily"]:
        health_str = "、".join(f"{k}: {v}" for k, v in l["health_daily"].items())
        lines.append(f"- 健康数据：{health_str}")
    if not any([l["media_total_sec"], l["media_titles"], l["mobile_total_sec"], l["screen_unlock_count"], l["geofence_labels"], l["health_daily"]]):
        lines.append("- （暂无生活画像数据）")

    return "\n".join(lines) + "\n"


def load_daily_summary(day: str) -> Optional[dict]:
    """读取已经生成过的摘要；不存在返回 None（由调用方决定是否现算）。"""
    path = _analysis_dir() / f"{day}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.analyzer.load_daily_summary')
        return None
