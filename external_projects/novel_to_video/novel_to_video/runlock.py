"""runlock.py — 项目级运行锁与崩溃回收（设计方案 10.8）。

问题：章节开始运行时状态被置为 running。若进程在运行中被杀（断电、强退、
Streamlit 重启），状态会永远停在 running，界面与队列会误以为仍有任务在执行。

做法：
  * 运行前在 project/.runlock 写入 {pid, host, started_at, heartbeat}；
  * 运行期间由 heartbeat() 刷新时间戳；
  * 其他进程检测到锁存在，且 pid 不存活或心跳超过 STALE_SEC，则视为崩溃遗留，
    把对应章节回退到最近的稳定状态（pending / waiting_review 等），而不是 running；
  * 锁文件使用原子写入；获取锁时使用 O_EXCL 创建，避免两个进程同时运行同一项目。

注意：这只防止同一台机器上的并发写入，不是分布式锁。
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LOCK_FILE = ".runlock"
STALE_SEC = 300.0  # 心跳超过 5 分钟视为遗留


class LockBusy(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def inspect(project_dir: Path, now: Optional[float] = None) -> Dict[str, Any]:
    """返回锁状态：{held, stale, pid, reason}。stale 表示遗留锁，可以安全回收。"""
    path = project_dir / LOCK_FILE
    if not path.is_file():
        return {"held": False, "stale": False, "pid": None, "reason": ""}
    data = _read(path) or {}
    pid = int(data.get("pid") or 0)
    hb = float(data.get("heartbeat") or 0.0)
    now = time.time() if now is None else now
    same_host = data.get("host") == socket.gethostname()
    if same_host and not _pid_alive(pid):
        return {"held": False, "stale": True, "pid": pid, "reason": "持有进程已退出"}
    if now - hb > STALE_SEC:
        return {"held": False, "stale": True, "pid": pid, "reason": "心跳超时"}
    return {"held": True, "stale": False, "pid": pid, "reason": "其他进程正在运行"}


def acquire(project_dir: Path) -> None:
    """获取项目锁。若存在有效锁则抛出 LockBusy；遗留锁会被回收。"""
    path = project_dir / LOCK_FILE
    st = inspect(project_dir)
    if st["held"]:
        raise LockBusy(f"项目正在运行（pid {st['pid']}），请等待或先停止")
    if path.exists():
        path.unlink(missing_ok=True)
    body = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                       "started_at": datetime.now().isoformat(timespec="seconds"),
                       "heartbeat": time.time()}, ensure_ascii=False)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise LockBusy("项目锁被其他进程同时获取，请重试") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)


def heartbeat(project_dir: Path) -> bool:
    """刷新心跳。返回 False 表示这次没写成功（例如 Windows 上 .runlock 被短暂占用）。
    心跳失败不能中断正在运行的章节：调用方只需记录一条警告。"""
    from .project_store import replace_with_retry
    path = project_dir / LOCK_FILE
    try:
        data = _read(path) or {}
        if data.get("pid") != os.getpid():
            return True
        data["heartbeat"] = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        replace_with_retry(tmp, path)
        return True
    except OSError:
        return False


def release(project_dir: Path) -> None:
    path = project_dir / LOCK_FILE
    data = _read(path) or {}
    if data.get("pid") == os.getpid():
        path.unlink(missing_ok=True)


def recover_running_chapters(project_dir: Path) -> List[Dict[str, Any]]:
    """把遗留的 running 章节回退到稳定状态。只在锁已确认遗留（或不存在）时调用。
    阶段级状态（state.json）只有 done/failed/waiting/stale，不存在 running，无需回收。

    返回被回退的章节列表。回退规则：
      * 章节成片存在且全部镜头通过 → done
      * 存在镜头规划且有待审核镜头 → waiting_review
      * 其他 → pending（下次运行会从头检查各阶段，已完成且输入未变的阶段自动跳过）
    """
    from . import chapter_io as cio
    from .shot_review import shot_states

    st = inspect(project_dir)
    if st["held"]:
        raise LockBusy("项目仍在运行，不能回收")
    fixed: List[Dict[str, Any]] = []
    idx = cio.load_index(project_dir)
    for row in idx.get("chapters", []):
        if row.get("status") != "running":
            continue
        no = row["no"]
        cdir = cio.chapter_dir(project_dir, no)
        if (cdir / f"chapter_{no:03d}.mp4").is_file():
            new = "done"
        elif cio.load_shots(project_dir, no).get("shots"):
            states = shot_states(project_dir, no)
            new = "waiting_review" if any(v != "approved" for v in states.values()) else "pending"
        else:
            new = "pending"
        cio.set_chapter_status(project_dir, no, new, "进程中断后回收（原状态 running）")
        fixed.append({"no": no, "to": new})
    if fixed:
        from .project_store import append_progress
        append_progress(project_dir, "回收遗留 running 章节：" + "、".join(f"第 {f['no']} 章→{f['to']}" for f in fixed))
    return fixed
