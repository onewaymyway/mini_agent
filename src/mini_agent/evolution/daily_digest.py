"""
evolution/daily_digest.py — 每日融合报告（设计方案第 4.1 节，阶段一）

职责：把三条已经存在但彼此割裂的数据线合并成一份用户能一眼看懂的日报：
  1. perception/behavior/analyzer.py::load_daily_summary()  — 行为时间分布
     （app_duration / domain_duration / git_repos 等，已经产出，不重算）
  2. perception/goal_backlog.py::GoalBacklog                — 当天 Goal/Objective 进展变化
  3. behavior 摘要里的 git_repos（analyzer 已采集提交次数，这里只做展示整合，
     不重新对接 git，避免和 analyzer 的采集逻辑产生第二套实现）

明确不做的事：
  - 不重新采集行为数据（复用 analyzer 的产出）
  - 不做任何"建议"（建议属于 next_action_advisor，两者职责分离，
    避免用户分不清"这是回顾还是建议"，见设计方案 4.3 节）

产出：
  - <project_root>/.agent/daily_reports/<YYYY-MM-DD>.md   人类可读
  - 返回的 dict 里额外带 shown_at=None，由 CLI 启动 hook 负责回填并去重展示
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths


def _fmt_min(sec: float) -> str:
    return f"{sec / 60:.0f} 分钟" if sec < 3600 else f"{sec / 3600:.1f} 小时"


def _yesterday(day: Optional[str] = None) -> str:
    import datetime as _dt

    if day:
        base = _dt.date.fromisoformat(day)
    else:
        base = _dt.date.today()
    return (base - _dt.timedelta(days=1)).isoformat()


def _goal_progress_delta(paths: AgentPaths, since_ts: float) -> list[dict]:
    """扫描 GoalBacklog，找出 last_touched_at 落在 [since_ts, now) 的节点，
    视为"当天有进展"，返回精简摘要（不引用 GoalBacklog 内部实现细节，
    只用其公开的 all_nodes()/to_dict()）。
    """
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog
    except Exception:
        return []

    try:
        backlog = load_goal_backlog(paths)
    except Exception:
        return []

    out = []
    for node in backlog.all_nodes():
        if node.last_touched_at and node.last_touched_at >= since_ts:
            out.append(
                {
                    "id": node.id,
                    "level": node.level,
                    "title": node.title,
                    "status": node.status,
                    "progress_notes": node.progress_notes,
                }
            )
    return out


def _error_log_summary(target_day: str, top_n: int = 5) -> dict:
    """统计 ~/.agent/logs/error.jsonl 里落在 target_day 这一天的异常。

    逻辑对齐 .claude/skills/error-log-analyzer/analyzer.py（按 exc_type
    计数、取高频 Top N），但不直接 import 那个 skill 模块——skill 目录
    是给 agent 在对话里按需加载用的工具脚本，不是稳定的 Python 包路径
    （没有 __init__.py，位置/命名都可能随 skill 迭代变化），日报生成是
    每天 cron 触发的核心链路，不应该依赖一个可能被移动/重命名的 skill
    文件是否还在原地。这里在 daily_digest 内部直接实现同一套统计口径，
    两边各自独立、互不影响。

    Returns:
        {"total": int, "top_types": [(exc_type, count), ...], "top_where": [(where, count), ...]}
        日志文件不存在或当天没有记录时，total 为 0，两个列表为空。
    """
    from collections import Counter
    import datetime as _dt

    empty = {"total": 0, "top_types": [], "top_where": []}
    try:
        from mini_agent.storage.paths import AgentPaths
        log_path = AgentPaths().global_error_log
    except Exception:
        return empty
    if not log_path.exists():
        return empty

    by_type: Counter = Counter()
    by_where: Counter = Counter()
    total = 0
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                ts_str = record.get("ts", "")
                try:
                    date = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date().isoformat()
                except Exception:
                    continue
                if date != target_day:
                    continue
                total += 1
                by_type[record.get("exc_type", "Unknown")] += 1
                by_where[record.get("where", "unknown")] += 1
    except Exception:
        return empty

    return {
        "total": total,
        "top_types": by_type.most_common(top_n),
        "top_where": by_where.most_common(top_n),
    }


def generate_daily_digest(paths: AgentPaths, day: Optional[str] = None) -> dict:
    """生成融合日报。day 默认是"昨天"（因为 sys:daily_digest 在当天 22 点跑，
    覆盖的是当天数据；若在次日凌晨补跑，day 应传前一天日期）。
    """
    from mini_agent.perception.behavior.analyzer import load_daily_summary

    target_day = day or _yesterday()
    since, _until = _day_bounds(target_day)

    behavior = load_daily_summary(target_day) or {}
    goal_deltas = _goal_progress_delta(paths, since)
    errors = _error_log_summary(target_day)

    data = {
        "day": target_day,
        "generated_at": time.time(),
        "behavior": behavior,
        "goal_deltas": goal_deltas,
        "errors": errors,
        "shown_at": None,
    }

    _write_json(paths, target_day, data)
    _write_markdown(paths, target_day, data)
    return data


def _day_bounds(day: str) -> tuple[float, float]:
    import calendar
    import datetime as _dt

    d = _dt.date.fromisoformat(day)
    start = calendar.timegm(d.timetuple())
    return float(start), float(start + 86400)


def _write_json(paths: AgentPaths, day: str, data: dict) -> None:
    d = paths.daily_reports_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_markdown(paths: AgentPaths, day: str, data: dict) -> None:
    behavior = data.get("behavior") or {}
    goal_deltas = data.get("goal_deltas") or []

    lines = [
        "---",
        f"title: 日报 {day}",
        "source_kind: daily_digest",
        f"created: {day}",
        f"updated: {day}",
        "tags: [daily-digest]",
        "---",
        "",
        f"# {day} 融合日报",
        "",
        "## 行为时间分布",
    ]

    app_duration = behavior.get("app_duration") or {}
    if app_duration:
        for app, sec in sorted(app_duration.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"- {app}：{_fmt_min(sec)}")
    else:
        lines.append("- （暂无行为数据，可能 behavior 采集未启用）")

    git_repos = behavior.get("git_repos") or {}
    lines.append("")
    lines.append("## 代码提交")
    if git_repos:
        for repo, count in git_repos.items():
            lines.append(f"- {repo}：{count} 次提交")
    else:
        lines.append("- （当天无提交记录）")

    lines.append("")
    lines.append("## 目标进展")
    if goal_deltas:
        for g in goal_deltas:
            note = f"：{g['progress_notes']}" if g.get("progress_notes") else ""
            lines.append(f"- [{g['level']}] {g['title']}（{g['status']}）{note}")
    else:
        lines.append("- （当天没有 Goal/Objective 有记录到的进展变化）")

    errors = data.get("errors") or {}
    lines.append("")
    lines.append("## 错误日志")
    if errors.get("total"):
        lines.append(f"- 当天共记录 {errors['total']} 条异常")
        top_types = errors.get("top_types") or []
        if top_types:
            lines.append("- 按类型 Top：")
            for exc_type, count in top_types:
                lines.append(f"  - {exc_type}：{count} 次")
        top_where = errors.get("top_where") or []
        if top_where:
            lines.append("- 按发生位置 Top：")
            for where, count in top_where:
                lines.append(f"  - {where}：{count} 次")
    else:
        lines.append("- （当天没有记录到异常，或 error.jsonl 不存在）")

    lines.append("")
    md = "\n".join(lines) + "\n"

    paths.daily_reports_dir.mkdir(parents=True, exist_ok=True)
    paths.daily_report_path(day).write_text(md, encoding="utf-8")


def render_startup_summary(data: dict) -> Optional[str]:
    """给 CLI 启动 hook 用的一行摘要，不展开推理过程。"""
    if not data:
        return None
    day = data.get("day", "")
    behavior = data.get("behavior") or {}
    git_repos = behavior.get("git_repos") or {}
    goal_deltas = data.get("goal_deltas") or []
    errors = data.get("errors") or {}

    commit_total = sum(git_repos.values()) if git_repos else 0
    parts = []
    if commit_total:
        parts.append(f"提交 {commit_total} 次")
    if goal_deltas:
        parts.append(f"{len(goal_deltas)} 个目标有进展")
    error_total = errors.get("total") or 0
    if error_total:
        # 只报数量，不在这一行里展开具体类型——避免这条本该"一眼扫过"的
        # 提示行过长；想看明细走 `/digest daily <day>` 看完整报告。
        parts.append(f"{error_total} 条异常")
    if not parts:
        return None
    return f"📋 {day} 日报：" + "，".join(parts) + f"（`/digest daily {day}` 查看完整内容）"


def load_pending_digest(paths: AgentPaths) -> Optional[dict]:
    """读取最近一份 shown_at 为空的日报，用于启动打印。找不到返回 None。"""
    d = paths.daily_reports_dir
    if not d.exists():
        return None
    candidates = sorted(d.glob("*.json"), reverse=True)
    for p in candidates[:3]:  # 只看最近 3 天，避免补打印过多历史日报
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data.get("shown_at"):
            return data
    return None


def mark_shown(paths: AgentPaths, day: str) -> None:
    p = paths.daily_reports_dir / f"{day}.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    data["shown_at"] = time.time()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
