"""插件配对：由看板开启一个短时间窗口，窗口内插件可以免手填地取走连接令牌。

流程：看板“配对插件”→ 写入 data/state/pairing.json（until = 现在 + 5 分钟）→ 插件在专用浏览器里向接收服务
POST /v1/pair（不带令牌，但必须是 chrome-extension:// 来源）→ 窗口有效则返回令牌，并立即关闭窗口（一次性），
记录配对来源与时间。窗口之外任何配对请求都被拒绝，所以平时令牌不会被动泄露。

状态文件由看板写入、接收服务读取（两者是不同进程），因此每次请求都重新读文件。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.store import atomic_write_json, file_lock, read_json

WINDOW = timedelta(minutes=5)


def pairing_path(state_dir: Path) -> Path:
    return Path(state_dir) / "pairing.json"


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now().astimezone()


def _read(state_dir: Path) -> dict[str, Any]:
    try:
        data = read_json(pairing_path(state_dir), default={})
    except (OSError, ValueError):
        return {}  # 状态文件损坏视为未开启，不影响接收服务
    return data if isinstance(data, dict) else {}


def _parse(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def open_window(state_dir: Path, now: Optional[datetime] = None) -> dict[str, Any]:
    """开启（或重新开启）配对窗口。保留上一次的配对记录以便看板展示。"""
    current = _now(now)
    path = pairing_path(state_dir)
    with file_lock(path):
        data = _read(state_dir)
        data["until"] = (current + WINDOW).isoformat(timespec="seconds")
        atomic_write_json(path, data)
    return status(state_dir, now=current)


def close_window(state_dir: Path) -> dict[str, Any]:
    path = pairing_path(state_dir)
    with file_lock(path):
        data = _read(state_dir)
        data["until"] = None
        atomic_write_json(path, data)
    return status(state_dir)


def status(state_dir: Path, now: Optional[datetime] = None) -> dict[str, Any]:
    current = _now(now)
    data = _read(state_dir)
    until = _parse(data.get("until"))
    remaining = int((until - current).total_seconds()) if until else 0
    paired = data.get("paired") if isinstance(data.get("paired"), dict) else None
    return {"open": remaining > 0, "remaining_seconds": max(remaining, 0), "paired": paired}


def consume(state_dir: Path, origin: str, now: Optional[datetime] = None) -> bool:
    """窗口有效时一次性消耗并记录配对来源，返回是否允许取令牌。"""
    current = _now(now)
    path = pairing_path(state_dir)
    with file_lock(path):
        data = _read(state_dir)
        until = _parse(data.get("until"))
        if not until or until <= current:
            return False
        data["until"] = None
        data["paired"] = {"origin": origin, "time": current.isoformat(timespec="seconds")}
        atomic_write_json(path, data)
    return True
