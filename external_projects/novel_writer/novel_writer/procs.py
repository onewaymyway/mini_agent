"""novel_writer/procs.py — 后台 worker 进程的识别与强制终止（依赖 psutil）。

进程号会被系统回收复用，所以只记录 pid 不够：worker 启动时同时记录进程的创建时间，
终止前校验两者都一致，才认定是我们的 worker，避免误杀其它进程。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

_CREATE_TIME_TOLERANCE = 1.0  # 秒


class ProcessControlError(RuntimeError):
    pass


def _psutil():
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
        raise ProcessControlError("缺少依赖 psutil，请运行 pip install psutil 后重试") from exc
    return psutil


def process_create_time(pid: Optional[int] = None) -> Optional[float]:
    """进程创建时间；读取失败返回 None（此时无法做可靠的进程识别）。"""
    try:
        return _psutil().Process(pid or os.getpid()).create_time()
    except Exception:  # noqa: BLE001 - 无法读取时由调用方降级
        return None


def is_same_process(pid: Optional[int], create_time: Optional[float]) -> bool:
    """进程仍然存在，且创建时间与记录一致（排除进程号被复用的情况）。"""
    if not pid or create_time is None:
        return False
    try:
        proc = _psutil().Process(int(pid))
        return abs(proc.create_time() - float(create_time)) < _CREATE_TIME_TOLERANCE
    except Exception:  # noqa: BLE001 - 进程不存在或无权访问
        return False


def terminate_tree(pid: int, create_time: float, grace_sec: float = 3.0) -> Dict[str, Any]:
    """强制终止 worker 进程及其全部子进程。

    顺序：先终止子进程（工具调用等），再终止父进程，避免父进程在收尾时又拉起新子进程；
    宽限期后仍存活的一律 kill。调用前必须已经用 is_same_process 校验过身份。
    """
    psutil = _psutil()
    root = psutil.Process(int(pid))
    children = root.children(recursive=True)
    targets = children + [root]
    for proc in children:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    try:
        root.terminate()
    except psutil.Error:
        pass
    _, alive = psutil.wait_procs(targets, timeout=grace_sec)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2)
    still = [p.pid for p in alive if p.is_running()]
    return {"children": len(children), "killed_by_force": len(alive), "still_running": still}
