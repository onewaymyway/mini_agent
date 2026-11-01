"""llm_trace.py — 文本模型调用的完整输入/输出留痕（logs/llm_trace.jsonl，追加）。

目的：模型返回了什么、我们发给它什么，用户在界面上直接看到，不必去翻文件。
每条记录：
  {ts, scope, chapter, step, label, provider, system, prompt, response, seconds, ok, error}
  * scope   来源页面（chapter / assets），与 joblog 的 scope 一致；
  * chapter 章节号（全书级调用为 null）；
  * step    步骤标识（C-1 / C-3 / C-5 / G-2）。
同时向实时日志写一行摘要（字数与返回内容的开头），长时间运行时也能立刻发现“返回的不是预期格式”。

不记录任何 key；单个字段超过 MAX_FIELD_CHARS 时截断并标注。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import joblog

TRACE_FILE = "logs/llm_trace.jsonl"
MAX_FIELD_CHARS = 60000
PREVIEW_CHARS = 100
_LOCK = threading.Lock()


def _clip(text: Optional[str]) -> str:
    text = text or ""
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return text[:MAX_FIELD_CHARS] + f"\n…（已截断，原长 {len(text)} 字）"


def preview(text: str, n: int = PREVIEW_CHARS) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[:n] + "…"


def _append(project_dir: Path, row: Dict[str, Any]) -> None:
    path = Path(project_dir) / TRACE_FILE
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def ask_traced(project_dir: Path, text_llm: Any, prompt: str, *, system: str = "",
               scope: str, label: str, step: str = "", chapter: Optional[int] = None) -> str:
    """调用 text_llm.ask 并留痕。异常原样抛出（先记录）。"""
    t0 = time.monotonic()
    base = {"ts": datetime.now().isoformat(timespec="seconds"), "scope": scope, "chapter": chapter,
            "step": step, "label": label, "provider": getattr(text_llm, "name", "?"),
            "system": _clip(system), "prompt": _clip(prompt)}
    try:
        response = text_llm.ask(prompt, system=system)
    except Exception as exc:
        _append(project_dir, {**base, "response": "", "seconds": round(time.monotonic() - t0, 2),
                              "ok": False, "error": str(exc)})
        joblog.log(project_dir, scope, f"{label}：模型调用异常（已发送 {len(prompt)} 字）：{exc}", level="error")
        raise
    response = response if isinstance(response, str) else str(response)
    _append(project_dir, {**base, "response": _clip(response), "seconds": round(time.monotonic() - t0, 2),
                          "ok": True, "error": None})
    joblog.log(project_dir, scope,
               f"{label}：已发送 {len(prompt)} 字，收到 {len(response)} 字；返回开头：{preview(response)}")
    return response


def tail(project_dir: Path, *, limit: int = 20, chapter: Optional[int] = None,
         scopes: Optional[List[str]] = None, steps: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """最近 limit 条调用，按时间从新到旧。可按章节、scope、step 过滤。"""
    path = Path(project_dir) / TRACE_FILE
    if not path.is_file():
        return []
    keep: deque = deque(maxlen=max(1, limit))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 进程被中途终止时最后一行可能不完整
            if chapter is not None and row.get("chapter") != chapter:
                continue
            if scopes and row.get("scope") not in scopes:
                continue
            if steps and row.get("step") not in steps:
                continue
            keep.append(row)
    return list(reversed(keep))
