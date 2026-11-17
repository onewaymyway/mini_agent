"""timeline.py — 章节流水线运行时间线：从 logs/job.jsonl 的结构化事件汇总出每章每次尝试、每个主步骤的
开始/结束时间、实际用时、状态与完整失败原因，以及重试记录。只读，不写任何文件。

事件（由 joblog.heartbeat、chapter_pipeline、batch._recover_failure 写入，带 event 字段）：
  chapter_start / chapter_end / stage_start / stage_end / retry
没有 event 字段的旧日志行会被忽略。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .joblog import LOG_FILE


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def read_events(project_dir: Path) -> List[Dict[str, Any]]:
    path = Path(project_dir) / LOG_FILE
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"event"' not in line:    # 先做廉价过滤：日志里有大量模型完整输出的行
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") and row.get("chapter") is not None:
                out.append(row)
    return out


def build_timeline(project_dir: Path, *, busy: bool = False, now: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """返回 {章号: {attempts, retries, total_seconds, retry_count}}。

    attempts：每次尝试 {attempt, started_at, ended_at, status, error, stages: [...]}，
      stage = {stage, started_at, ended_at, seconds, status, error}；
      status：ok / failed / stopped / running（进行中）/ interrupted（日志里没有结束事件且当前没有运行，多为进程被中止）。
    """
    now = now or datetime.now()
    chapters: Dict[int, Dict[str, Any]] = {}

    def ch(no: int) -> Dict[str, Any]:
        return chapters.setdefault(no, {"attempts": [], "retries": [], "total_seconds": 0.0, "retry_count": 0})

    def open_attempt(c: Dict[str, Any], started_at: Optional[str]) -> Dict[str, Any]:
        a = {"attempt": len(c["attempts"]) + 1, "started_at": started_at, "ended_at": None,
             "status": "running", "error": None, "stages": []}
        c["attempts"].append(a)
        return a

    def current(c: Dict[str, Any], ts: Optional[str]) -> Dict[str, Any]:
        if not c["attempts"] or c["attempts"][-1]["status"] != "running":
            return open_attempt(c, ts)
        return c["attempts"][-1]

    for ev in read_events(project_dir):
        no = int(ev["chapter"])
        c = ch(no)
        kind = ev["event"]
        if kind == "chapter_start":
            if c["attempts"] and c["attempts"][-1]["status"] == "running":
                c["attempts"][-1]["status"] = "interrupted"     # 上一次尝试没有结束事件就又开始了
            open_attempt(c, ev.get("started_at") or ev.get("ts"))
        elif kind == "stage_start":
            current(c, ev.get("started_at") or ev.get("ts"))["stages"].append(
                {"stage": ev.get("stage"), "started_at": ev.get("started_at") or ev.get("ts"), "ended_at": None,
                 "seconds": None, "status": "running", "error": None})
        elif kind == "stage_end":
            a = current(c, ev.get("started_at") or ev.get("ts"))
            st = next((s for s in reversed(a["stages"]) if s["stage"] == ev.get("stage") and s["status"] == "running"), None)
            if st is None:
                st = {"stage": ev.get("stage"), "started_at": ev.get("started_at")}
                a["stages"].append(st)
            st.update(ended_at=ev.get("ended_at") or ev.get("ts"), seconds=ev.get("seconds"),
                      status=ev.get("status") or "ok", error=ev.get("error"))
        elif kind == "chapter_end":
            a = current(c, ev.get("ts"))
            a.update(ended_at=ev.get("ended_at") or ev.get("ts"), status=ev.get("status") or "ok", error=ev.get("error"))
        elif kind == "retry":
            c["retries"].append({"attempt": ev.get("attempt"), "at": ev.get("at") or ev.get("ts"),
                                 "error": ev.get("error"), "wait_sec": ev.get("wait_sec"), "next_at": ev.get("next_at")})

    for c in chapters.values():
        last = c["attempts"][-1] if c["attempts"] else None
        for a in c["attempts"]:
            is_last = a is last
            for st in a["stages"]:
                if st["status"] == "running":
                    started = _parse(st["started_at"])
                    if busy and is_last:
                        st["seconds"] = round((now - started).total_seconds(), 1) if started else None
                    else:
                        st["status"] = "interrupted"
            if a["status"] == "running" and not (busy and is_last):
                a["status"] = "interrupted"
            s, e = _parse(a["started_at"]), _parse(a["ended_at"]) or (now if a["status"] == "running" else None)
            a["seconds"] = round((e - s).total_seconds(), 1) if s and e else None
        c["total_seconds"] = round(sum(a["seconds"] or 0 for a in c["attempts"]), 1)
        c["retry_count"] = len(c["retries"])
    return chapters
