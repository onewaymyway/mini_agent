"""
browser-core/impl/session_manager.py — 维护"探索子agent这一轮调用期间共用
的一个浏览器会话"，解决本次改动的核心诉求：browser-core 不应该局限于无头
浏览器，因为有时候登录需要用户手动操作。

三种会话模式（通过 `browser_navigate` 的可选 `session` 参数，或进程环境变量
`BROWSER_CORE_MODE`/`BROWSER_CORE_PORT` 指定；未指定时默认 "auto"）：

- **attach**：连接一个**已经在运行**的浏览器实例（通过其
  `--remote-debugging-port`）。这是"登录场景"的标准用法——由使用者提前手动
  启动一个普通的、有界面的 Chrome（例如平时自己用的那个，或者专门为这类
  任务准备的一个持久 profile），手动登录好目标网站，再把调试端口告诉
  browser-core。探索子agent不需要、也不应该尝试自己完成登录，`explorer/
  prompt.md` 已经要求"遇到登录墙如实报告，不尝试绕过"——attach 模式提供的
  是另一条路径：登录这件事从一开始就不在探索子agent的职责范围内，由人在
  探索开始之前就做好。
- **launch_headless**：不需要登录、不需要人工介入的纯抓取场景，本模块自己
  拉起一个全新的、无 GUI 的浏览器实例。
- **launch_headed**：本模块自己拉起一个全新的、有 GUI 窗口的浏览器实例
  （全新 profile，不带任何登录状态）。主要用于本地调试"看得见浏览器在做
  什么"，不是登录场景的解法（见 browser_launch.py 文件头）。
- **auto**（默认）：先尝试 attach 到默认端口，能连上就说明使用者已经准备好
  了一个浏览器（可能已登录），直接复用；连不上则退化为 launch_headless，
  保证"没有特意准备浏览器"时仍然能跑通纯抓取场景，不强制每次都要求先手动
  启动一个浏览器。

会话在**当前进程**内以 (host, port) 为 key 复用（模块级字典，风格与
`mini_agent.skills.generative_capability.tool_runtime.py` 的
`set_tool_executor`/`get_tool_executor` 注入点一致），同一次探索/同一次
`capability_call` 调用过程中多次调用 `browser_navigate`/`browser_click` 等
工具会复用同一个 tab、同一个已登录状态，不会每次都重新连接。
"""
from __future__ import annotations

import atexit
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Optional

from cdp_client import CDPSession, connect_tab, is_debug_port_alive, list_tabs, new_tab
from browser_launch import spawn_browser, wait_port_alive

DEFAULT_PORT = 9222


@dataclass
class _SessionEntry:
    session: CDPSession
    host: str
    port: int
    proc: Optional[subprocess.Popen] = None  # 仅 launch 模式持有；attach 模式为 None，不由我们负责关闭


_sessions: dict[tuple[str, int], _SessionEntry] = {}
_lock = threading.Lock()


def _cleanup_all() -> None:
    with _lock:
        for entry in _sessions.values():
            try:
                entry.session.close()
            except Exception:
                pass
            # attach 模式的浏览器是使用者自己启动的，不由我们杀掉；
            # 只清理我们自己 spawn_browser 拉起的进程。
            if entry.proc is not None:
                try:
                    entry.proc.terminate()
                except Exception:
                    pass


atexit.register(_cleanup_all)


def _resolve_config(session_cfg: Optional[dict]) -> dict:
    cfg = dict(session_cfg or {})
    cfg.setdefault("mode", os.environ.get("BROWSER_CORE_MODE", "auto"))
    cfg.setdefault("host", os.environ.get("BROWSER_CORE_HOST", "127.0.0.1"))
    cfg.setdefault("port", int(os.environ.get("BROWSER_CORE_PORT", DEFAULT_PORT)))
    cfg.setdefault("headless", True)  # 仅 mode 落到 launch_headless 时使用
    cfg.setdefault("user_data_dir", None)
    return cfg


def get_or_create_session(session_cfg: Optional[dict] = None) -> CDPSession:
    """
    返回一个可用的 CDPSession；同一 (host, port) 在进程生命周期内只建立一次
    连接/只启动一次浏览器进程，后续调用直接复用。

    `session_cfg` 只在**第一次**为某个 (host, port) 建立会话时生效；已存在
    的会话不会因为后续调用传入不同的 mode/headless 而重建——这是刻意的，
    避免探索循环中途因为参数微小差异而反复重启浏览器、丢失已经导航到的
    页面状态/已登录状态。
    """
    cfg = _resolve_config(session_cfg)
    host, port = cfg["host"], cfg["port"]
    key = (host, port)

    with _lock:
        existing = _sessions.get(key)
        if existing is not None:
            return existing.session

        mode = cfg["mode"]
        proc: Optional[subprocess.Popen] = None

        if mode == "attach":
            if not is_debug_port_alive(host, port):
                raise RuntimeError(
                    f"mode='attach' 但 {host}:{port} 上没有可连接的浏览器调试端口。"
                    f"请先手动启动一个带 --remote-debugging-port={port} 的浏览器"
                    f"（如果这个抓取目标需要登录，应该在这一步手动登录好），再重试。"
                )
        elif mode in ("launch_headless", "launch_headed"):
            if is_debug_port_alive(host, port):
                raise RuntimeError(
                    f"mode='{mode}' 但端口 {port} 已被占用（可能是之前一次探索"
                    f"遗留的进程，或使用者自己启动的浏览器）。如果想复用它，请改用"
                    f"mode='attach'；如果想用一个新端口，请显式指定不同的 port。"
                )
            proc = spawn_browser(
                port=port,
                headless=(mode == "launch_headless"),
                user_data_dir=cfg["user_data_dir"],
            )
            ok, err = wait_port_alive(lambda: is_debug_port_alive(host, port), proc=proc)
            if not ok:
                raise RuntimeError(f"启动浏览器后等待调试端口就绪失败: {err or '超时'}")
        elif mode == "auto":
            if is_debug_port_alive(host, port):
                pass  # 复用使用者已经准备好的浏览器（可能已登录）
            else:
                proc = spawn_browser(port=port, headless=True, user_data_dir=cfg["user_data_dir"])
                ok, err = wait_port_alive(lambda: is_debug_port_alive(host, port), proc=proc)
                if not ok:
                    raise RuntimeError(f"auto 模式启动 headless 浏览器失败: {err or '超时'}")
        else:
            raise RuntimeError(f"未知的 session.mode={mode!r}，支持: attach/launch_headless/launch_headed/auto")

        tabs = list_tabs(host, port)
        target = tabs[0] if tabs else new_tab(host=host, port=port)
        session = connect_tab(target, host=host, port=port)
        _sessions[key] = _SessionEntry(session=session, host=host, port=port, proc=proc)
        return session


def reset_session(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    """关闭并移除一个会话，供测试或显式"结束这次浏览会话"使用。"""
    with _lock:
        entry = _sessions.pop((host, port), None)
    if entry is not None:
        try:
            entry.session.close()
        except Exception:
            pass
        if entry.proc is not None:
            try:
                entry.proc.terminate()
            except Exception:
                pass
