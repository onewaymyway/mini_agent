"""ui/jobs.py — 后台任务：放在线程里跑，界面不阻塞；可随时请求停止。

同一项目同一时间只允许一个任务。Streamlit 重跑脚本时模块状态保留，状态在各次刷新间可见。
任务线程绑定了项目日志（joblog.bind）：任务开始/结束、失败原因与堆栈都会写入 logs/job.jsonl，
界面的实时日志面板据此显示；业务代码里可用 joblog.bound_log() 写更细的过程信息。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from article_to_video import cancel, joblog

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, object]] = {}


def _key(pdir: Path) -> str:
    return str(Path(pdir).resolve())


def start(pdir: Path, label: str, fn: Callable[[], None], scope: str = "job") -> bool:
    k = _key(pdir)
    with _LOCK:
        cur = _JOBS.get(k)
        if cur and cur["state"] == "running":
            return False
        ev = cancel.begin(pdir)
        job: Dict[str, object] = {"label": label, "scope": scope, "state": "running", "message": "",
                                  "started": time.time(), "finished": None}
        _JOBS[k] = job

    def run() -> None:
        cancel.bind(ev)
        with joblog.bind(pdir, scope):
            joblog.log(pdir, scope, f"后台任务开始：{label}")
            try:
                res = fn()
                # 任务函数可返回 {state, message}（如流水线停在终审）：以它作为最终状态，而不是一律“完成”
                if isinstance(res, dict) and res.get("state") not in (None, "done"):
                    joblog.log(pdir, scope, f"后台任务结束：{label}：{res.get('message', '')}", level="warn")
                    job.update(state=res["state"], message=res.get("message", ""))
                else:
                    msg = res.get("message", "完成") if isinstance(res, dict) else "完成"
                    # 先写日志、再改状态：界面看到“已结束”时，日志里的最后一行一定已经在了
                    joblog.log(pdir, scope, f"后台任务完成：{label}", level="ok")
                    job.update(state="done", message=msg)
            except cancel.Cancelled:
                joblog.log(pdir, scope, f"后台任务已停止：{label}", level="warn")
                job.update(state="stopped", message="已停止（未完成的结果不会写入）")
            except Exception as exc:  # noqa: BLE001 — 界面展示错误文字
                joblog.log(pdir, scope, f"后台任务失败：{label}：{exc}", level="error",
                           traceback=joblog.format_tb(exc))
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
