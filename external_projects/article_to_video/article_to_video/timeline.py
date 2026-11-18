"""timeline.py — 运行时间线（步骤时间表）：从 logs/job.jsonl 的结构化事件汇总出每次运行、每个步骤的
开始/结束时间、用时、状态与失败原因。只读，不写任何文件。

事件由 joblog.run_start/run_end/step 写入。没有 event 字段的普通日志行会被忽略。
界面每隔几秒刷新一次，所以这里增量读取：只解析上次之后新增的部分，日志被截断或替换时自动重读。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .joblog import log_path

_EVENTS = ("run_start", "run_end", "step_start", "step_end")
_CACHE: Dict[str, Dict[str, Any]] = {}   # 日志路径 -> {"offset": 已读字节数, "rows": 结构化事件行}


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def read_events(project_dir: Path) -> List[Dict[str, Any]]:
    path = log_path(project_dir)
    if not path.is_file():
        return []
    key = str(path.resolve())
    size = path.stat().st_size
    c = _CACHE.get(key)
    if c is None or size < c["offset"]:
        c = _CACHE[key] = {"offset": 0, "rows": []}
    if size > c["offset"]:
        with path.open("rb") as f:
            f.seek(c["offset"])
            chunk = f.read()
        end = chunk.rfind(b"\n") + 1          # 只消费完整的行；最后一行可能还在写入
        for raw in chunk[:end].splitlines():
            if b'"event"' not in raw:
                continue
            try:
                row = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if row.get("event") in _EVENTS and row.get("run_id"):
                c["rows"].append(row)
        c["offset"] += end
    return c["rows"]


def build_timeline(project_dir: Path, *, busy: bool = False, now: Optional[datetime] = None,
                   scopes: Optional[Iterable[str]] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """最近 limit 次运行，按开始时间从新到旧。

    每次运行 {run_id, scope, run, started_at, ended_at, seconds, status, error, steps}；
    step = {stage, item, started_at, ended_at, seconds, status, error, detail}。
    status：ok / waiting / failed / stopped / running（进行中）/ interrupted（没有结束事件且当前没有在运行，
    多为进程被中止）。busy=True 表示当前确实有任务在运行，最后一个未结束的运行记为 running。
    """
    now = now or datetime.now()
    wanted = set(scopes) if scopes else None
    runs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for ev in read_events(project_dir):
        rid, kind = ev["run_id"], ev["event"]
        r = runs.get(rid)
        if r is None:
            r = runs[rid] = {"run_id": rid, "scope": ev.get("scope"), "run": ev.get("run"),
                             "started_at": ev.get("started_at") or ev.get("ts"), "ended_at": None,
                             "seconds": None, "status": "running", "error": None, "steps": []}
            order.append(rid)
        if kind == "step_start":
            r["steps"].append({"stage": ev.get("stage"), "item": ev.get("item"),
                               "started_at": ev.get("started_at") or ev.get("ts"), "ended_at": None,
                               "seconds": None, "status": "running", "error": None, "detail": None})
        elif kind == "step_end":
            st = next((x for x in reversed(r["steps"]) if x["stage"] == ev.get("stage")
                       and x["item"] == ev.get("item") and x["status"] == "running"), None)
            if st is None:
                continue
            st.update(ended_at=ev.get("ended_at") or ev.get("ts"), seconds=ev.get("seconds"),
                      status=ev.get("status") or "ok", error=ev.get("error"), detail=ev.get("detail"),
                      traceback=ev.get("traceback"))
        elif kind == "run_end":
            r.update(ended_at=ev.get("ended_at") or ev.get("ts"), status=ev.get("status") or "ok",
                     error=ev.get("error"), traceback=ev.get("traceback"))

    last_open = next((rid for rid in reversed(order) if runs[rid]["status"] == "running"), None)
    result: List[Dict[str, Any]] = []
    for rid in order:
        r = runs[rid]
        live = busy and rid == last_open
        if r["status"] == "running" and not live:
            r["status"] = "interrupted"
        for st in r["steps"]:
            if st["status"] == "running":
                if live:
                    s0 = _parse(st["started_at"])
                    st["seconds"] = round((now - s0).total_seconds(), 1) if s0 else None
                elif r["status"] in ("failed", "stopped"):
                    st["status"], st["error"] = r["status"], st["error"] or r["error"]
                    st["ended_at"] = st["ended_at"] or r["ended_at"]
                else:
                    st["status"] = "interrupted"
        s1 = _parse(r["started_at"])
        e1 = _parse(r["ended_at"]) or (now if r["status"] == "running" else None)
        r["seconds"] = round((e1 - s1).total_seconds(), 1) if s1 and e1 else None
        if not wanted or r["scope"] in wanted:
            result.append(r)
    result.sort(key=lambda x: x["started_at"] or "", reverse=True)
    return result[:limit]


STATUS_MARK = {"ok": "✅", "waiting": "⏸", "failed": "❌", "stopped": "⏹", "running": "⏳", "interrupted": "⚠️"}
STATUS_TEXT = {"ok": "完成", "waiting": "等待确认", "failed": "失败", "stopped": "已停止", "running": "进行中",
               "interrupted": "中断"}


STAGE_LABEL = {"outline": "大纲", "script": "口播稿", "voice": "配音", "storyboard": "分镜",
               "render": "画面渲染", "compose": "章合成", "export": "导出"}


def fmt_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    sec = float(sec)
    if sec < 60:
        return f"{sec:.1f} 秒"
    m, s = divmod(int(round(sec)), 60)
    if m < 60:
        return f"{m} 分 {s} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


def step_rows(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把一次运行的步骤整理成表格行（子步骤缩进显示）。"""
    rows = []
    for st in run["steps"]:
        name = STAGE_LABEL.get(st["stage"], st["stage"]) if not st.get("item") else f"　└ {st['item']}"
        rows.append({
            "步骤": name,
            "状态": f"{STATUS_MARK.get(st['status'], '')} {STATUS_TEXT.get(st['status'], st['status'])}",
            "开始": (st["started_at"] or "")[11:19],
            "结束": (st["ended_at"] or "")[11:19],
            "用时": fmt_seconds(st["seconds"]),
            "说明": st.get("error") or st.get("detail") or "",
        })
    return rows
