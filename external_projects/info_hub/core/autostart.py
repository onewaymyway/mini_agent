"""启动后自动恢复：看板（info_hub）每次启动时，若开启该设置，则拉起当前未运行的反馈接收服务与专用浏览器。

- 设置保存在 data/state/runtime_config.json 的 service_autostart.enabled（看板“服务管理”可改）；
  未保存过时回退到 config/settings.yaml 的 service_autostart.enabled（默认 false）。
- 只启动“当前未运行”的服务：已由看板或终端运行的接收服务、已打开的专用浏览器都保持不动。
- 两个服务互不影响：接收服务启动失败（例如端口被占用）不会阻止专用浏览器启动。
- 每次运行的结果写入 data/state/autostart_last.json，看板据此显示“上次自动恢复”的结论。
- 只在看板进程启动时运行一次（由 app.py 调用），不在页面刷新时重复运行。
- 注意：开启后，即使用户手动关闭过这两个服务，下次看板启动也会重新拉起。
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any

from core import dedicated_browser as db
from core import services
from core.settings import Settings, read_runtime_config
from core.store import atomic_write_json, read_json

KEY = "service_autostart"


def _runtime_path(settings: Settings) -> Path:
    return settings.paths.state_dir / "runtime_config.json"


def _last_path(settings: Settings) -> Path:
    return settings.paths.state_dir / "autostart_last.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def is_enabled(settings: Settings) -> bool:
    """看板保存的值优先；没有保存过则读 settings.yaml 默认值。"""
    rt = read_runtime_config(_runtime_path(settings)).get(KEY)
    if isinstance(rt, dict) and isinstance(rt.get("enabled"), bool):
        return rt["enabled"]
    default = (settings.raw or {}).get(KEY) or {}
    return bool(default.get("enabled", False)) if isinstance(default, dict) else False


def set_enabled(settings: Settings, enabled: bool) -> bool:
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是 true 或 false")
    path = _runtime_path(settings)
    data = read_runtime_config(path)
    data[KEY] = {"enabled": enabled}
    data["updated_at"] = _now()
    atomic_write_json(path, data)
    return enabled


def last_report(settings: Settings) -> dict[str, Any] | None:
    data = read_json(_last_path(settings), default=None)
    return data if isinstance(data, dict) else None


def _receiver_step(settings: Settings) -> dict[str, Any]:
    try:
        st = services.status(settings)
        if st["running"]:
            return {"action": "already_running", "mode": st["mode"], "port": st["port"]}
        started = services.start(settings)
        return {"action": "started", "port": started["port"], "pid": started.get("pid")}
    except services.ServiceError as exc:
        return {"action": "failed", "message": str(exc).splitlines()[0] if str(exc) else "启动失败"}


def _browser_step(settings: Settings) -> dict[str, Any]:
    cfg = db.load_config(settings.paths.root)
    st = db.browser_status(settings.paths.root, cfg)
    if st["state"] == "running":
        return {"action": "already_running"}
    if st["state"] == "unknown":
        return {"action": "skipped", "message": "无法检测专用浏览器是否在运行（请安装 psutil），未自动启动"}
    try:
        db.open_url(settings.paths.root, cfg, "about:blank")
    except FileNotFoundError as exc:
        return {"action": "failed", "message": str(exc)}
    except OSError as exc:
        return {"action": "failed", "message": f"启动浏览器失败：{exc}"}
    return {"action": "started"}


def run(settings: Settings) -> dict[str, Any]:
    """按设置执行一次自动恢复。设置关闭时什么也不做。异常被记录，不会向外抛出。"""
    if not is_enabled(settings):
        return {"enabled": False}
    report: dict[str, Any] = {"enabled": True, "ran_at": _now()}
    try:
        report["receiver"] = _receiver_step(settings)
    except Exception as exc:  # noqa: BLE001 - 后台线程不能静默崩溃，错误要可见
        report["receiver"] = {"action": "failed", "message": f"{type(exc).__name__}: {exc}"}
    try:
        report["browser"] = _browser_step(settings)
    except Exception as exc:  # noqa: BLE001
        report["browser"] = {"action": "failed", "message": f"{type(exc).__name__}: {exc}"}
    atomic_write_json(_last_path(settings), report)
    return report


_RECEIVER_TEXT = {
    "already_running": "接收服务已在运行，未重复启动",
    "started": "接收服务已自动启动",
    "failed": "接收服务启动失败",
}
_BROWSER_TEXT = {
    "already_running": "专用浏览器已在运行，未重复启动",
    "started": "专用浏览器已自动启动",
    "skipped": "专用浏览器未自动启动",
    "failed": "专用浏览器启动失败",
}


def describe(report: dict[str, Any] | None) -> list[str]:
    """把一次运行结果转成看板上显示的几行中文结论。"""
    if not report or not report.get("enabled"):
        return []
    lines = []
    rc = report.get("receiver") or {}
    bw = report.get("browser") or {}
    if rc:
        text = _RECEIVER_TEXT.get(rc.get("action"), "接收服务状态未知")
        lines.append(text + (f"：{rc['message']}" if rc.get("message") else ""))
    if bw:
        text = _BROWSER_TEXT.get(bw.get("action"), "专用浏览器状态未知")
        lines.append(text + (f"：{bw['message']}" if bw.get("message") else ""))
    return lines
