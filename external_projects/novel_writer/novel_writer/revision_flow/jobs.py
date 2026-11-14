"""后台任务与实时日志（revision_flow）。

每个任务对应 _revision/jobs/<name>.log（逐行追加，带时间戳）和 <name>.status.json（running / done / failed）。
  * log(...)：写一行进度（作者看到的是“第 N 章开始判定 / 第 K 次尝试 / 结果”这一层）；
  * make_invoker(...)：包装执行器，把 agent 的逐行输出（框架的 outlog 行接收器）实时追加到同一份日志；
  * start(...)：在守护线程里运行任务，结束后写入结果或错误。界面用 tail() 定时读取。
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from novel_writer import outlog
from novel_writer.revision_flow.common import revision_root, save_json

_LOCK = threading.Lock()


def _dir(ws) -> Path:
    d = revision_root(ws) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def log_path(ws, name: str) -> Path:
    return _dir(ws) / f"{name}.log"


def status_path(ws, name: str) -> Path:
    return _dir(ws) / f"{name}.status.json"


def log(ws, name: str, text: str) -> None:
    line = f"[{_now()}] {text}\n"
    with _LOCK, log_path(ws, name).open("a", encoding="utf-8") as fh:
        fh.write(line)


def tail(ws, name: str, lines: int = 200) -> str:
    p = log_path(ws, name)
    if not p.exists():
        return ""
    data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def status(ws, name: str) -> Dict[str, Any]:
    data = None
    p = status_path(ws, name)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            data = None
    return data or {"state": "unknown"}


def _set_status(ws, name: str, **fields: Any) -> None:
    current = status(ws, name)
    current.update(fields)
    save_json(status_path(ws, name), current)


def make_invoker(ws, name: str, invoker: Callable[..., Any]) -> Callable[..., Any]:
    """包装执行器：调用期间把 agent 的每一行输出写入任务日志。"""
    def wrapped(**kwargs):
        previous = outlog.current_line_sink()

        def sink(text: str) -> None:
            log(ws, name, f"  │ {text}")

        outlog.set_line_sink(sink)
        try:
            return invoker(**kwargs)
        finally:
            outlog.set_line_sink(previous)
    return wrapped


def progress_for(ws, name: str) -> Callable[[str], None]:
    return lambda text: log(ws, name, text)


def start(ws, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """后台运行 fn(*args, progress=..., **kwargs)。fn 的返回值（可 JSON 化）记入状态的 result。"""
    if status(ws, name).get("state") == "running":
        raise RuntimeError(f"任务 {name} 仍在运行")
    log_path(ws, name).write_text("", encoding="utf-8")
    _set_status(ws, name, state="running", started_at=_now(), result=None, error=None)
    log(ws, name, "开始")

    def runner() -> None:
        try:
            result = fn(*args, progress=progress_for(ws, name), **kwargs)
            log(ws, name, "完成")
            _set_status(ws, name, state="done", result=result)
        except BaseException as exc:  # 记录后退出，不让线程静默消失
            log(ws, name, f"失败：{exc}")
            _set_status(ws, name, state="failed", error=str(exc))

    threading.Thread(target=runner, name=f"rv-{name}", daemon=True).start()
