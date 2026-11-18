"""ui/jobs.py — 后台任务：写稿放在线程里跑，界面不阻塞；可随时请求停止。

同一项目同一时间只允许一个任务。Streamlit 重跑脚本时模块状态保留，状态在各次刷新间可见。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from article_to_video import cancel

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, object]] = {}


def _key(pdir: Path) -> str:
    return str(Path(pdir).resolve())


def start(pdir: Path, label: str, fn: Callable[[], None]) -> bool:
    k = _key(pdir)
    with _LOCK:
        cur = _JOBS.get(k)
        if cur and cur["state"] == "running":
            return False
        ev = cancel.begin(pdir)
        job: Dict[str, object] = {"label": label, "state": "running", "message": "",
                                  "started": time.time(), "finished": None}
        _JOBS[k] = job

    def run() -> None:
        cancel.bind(ev)
        try:
            fn()
            job.update(state="done", message="完成")
        except cancel.Cancelled:
            job.update(state="stopped", message="已停止（未完成的结果不会写入）")
        except Exception as exc:  # noqa: BLE001 — 界面展示错误文字
            job.update(state="error", message=str(exc))
        finally:
            job["finished"] = time.time()

    threading.Thread(target=run, daemon=True).start()
    return True


def status(pdir: Path) -> Optional[Dict[str, object]]:
    with _LOCK:
        job = _JOBS.get(_key(pdir))
        return dict(job) if job else None


def stop(pdir: Path) -> None:
    cancel.request(pdir)
