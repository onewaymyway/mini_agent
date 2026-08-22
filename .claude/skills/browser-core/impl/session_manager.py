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
- **launch_headed**：本模块自己拉起一个有 GUI 窗口的浏览器实例。**默认使用
  一个固定的、持久化的用户数据目录**（`DEFAULT_PERSISTENT_PROFILE_DIR`，
  不随进程/端口变化，跨多次运行保留），这是本次改动新增的行为——第一次用
  `launch_headed` 打开浏览器手动登录过某个网站后，后续再用 `launch_headed`
  打开（哪怕是全新的 agent 进程/全新的探索）会复用同一份 cookies/登录态，
  不需要每次都重新登录。如果需要多个互不影响的登录身份（比如测试多账号），
  可以通过 `session.user_data_dir` 显式指定一个不同的目录来覆盖这个默认值。
- **auto**（默认）：先尝试 attach 到默认端口，能连上就说明使用者已经准备好
  了一个浏览器（可能已登录），直接复用；连不上则退化为 **launch_headed**
  （阶段十六起，此前是 launch_headless）——默认打开一个看得见的普通浏览器
  窗口，而不是无头浏览器，这样调试/首次遇到登录墙时使用者可以直接在这个
  窗口里手动登录，且因为 launch_headed 默认使用持久化 profile，登录一次后
  后续调用（哪怕是全新进程）都会带着登录态。纯后台抓取场景可以显式传
  `session.mode="launch_headless"` 跳过界面。

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
from pathlib import Path
from typing import Optional

from cdp_client import (
    CDPSession,
    connect_tab,
    get_browser_version,
    is_debug_port_alive,
    list_tabs,
    new_tab,
)
from browser_launch import spawn_browser, wait_port_alive

DEFAULT_PORT = 9222

# launch_headed 模式默认使用的、跨进程/跨端口持久化的用户数据目录——这是
# "打开普通浏览器时默认使用同一个数据目录，让用户登录的数据可以一直沿用"
# 这条要求的落地：只要没有显式传 `session.user_data_dir`，每一次
# `launch_headed` 都会打开同一个 profile，登录状态自然延续。可以通过
# 环境变量 `BROWSER_CORE_PROFILE_DIR` 整体覆盖（比如需要隔离多台机器/多个
# agent 实例各自的登录态时），也可以在单次调用里通过
# `session.user_data_dir` 覆盖（比如需要多个互不干扰的登录身份）。
DEFAULT_PERSISTENT_PROFILE_DIR = Path(
    os.environ.get("BROWSER_CORE_PROFILE_DIR")
    or (Path.home() / ".mini_agent" / "browser-core" / "profile")
)


@dataclass
class _SessionEntry:
    session: CDPSession
    host: str
    port: int
    proc: Optional[subprocess.Popen] = None  # 仅 launch 模式持有；attach 模式为 None，不由我们负责关闭
    mode: str = "auto"  # 建立这个会话时实际落地的模式：attach/launch_headless/launch_headed
    # 是否有头：launch_* 模式下我们自己拉起进程，这个值是确定已知的；attach
    # 模式下浏览器不是我们启动的，这里先记 None（未知），真正展示时用
    # `detect_headless_hint()` 做一次尽力而为的启发式探测（见该函数说明）。
    headless: Optional[bool] = None


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
        # 实际落地的会话来源与是否有头——auto 分支会在下面按实际走向覆盖；
        # 其余分支在各自的 if/elif 里直接确定。
        resolved_mode = mode
        resolved_headless: Optional[bool] = None

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
            user_data_dir = cfg["user_data_dir"]
            if mode == "launch_headed" and not user_data_dir:
                # 普通（有界面）浏览器默认复用同一个持久化 profile，让登录态
                # 跨多次启动延续；headless 场景默认仍是按端口区分的临时目录
                # （见 browser_launch.spawn_browser 的默认值），因为大多数
                # headless 抓取是无状态的一次性任务，不需要强行持久化。
                user_data_dir = str(DEFAULT_PERSISTENT_PROFILE_DIR)
            proc = spawn_browser(
                port=port,
                headless=(mode == "launch_headless"),
                user_data_dir=user_data_dir,
            )
            ok, err = wait_port_alive(lambda: is_debug_port_alive(host, port), proc=proc)
            if not ok:
                raise RuntimeError(f"启动浏览器后等待调试端口就绪失败: {err or '超时'}")
            resolved_headless = (mode == "launch_headless")
        elif mode == "auto":
            if is_debug_port_alive(host, port):
                # 复用使用者已经准备好的浏览器（可能已登录）——这个浏览器不是
                # 本次调用拉起的，我们不知道它有头无头，记为 attach + 未知，
                # 交给 list_sessions()/detect_headless_hint() 做启发式探测。
                resolved_mode = "attach(auto)"
            else:
                # 阶段十六：默认退化为有界面浏览器（而不是无头），并复用与
                # launch_headed 相同的持久化 profile 默认值，方便调试/登录。
                user_data_dir = cfg["user_data_dir"] or str(DEFAULT_PERSISTENT_PROFILE_DIR)
                proc = spawn_browser(port=port, headless=False, user_data_dir=user_data_dir)
                ok, err = wait_port_alive(lambda: is_debug_port_alive(host, port), proc=proc)
                if not ok:
                    raise RuntimeError(f"auto 模式启动有界面浏览器失败: {err or '超时'}")
                resolved_mode = "launch_headed(auto)"
                resolved_headless = False
        else:
            raise RuntimeError(f"未知的 session.mode={mode!r}，支持: attach/launch_headless/launch_headed/auto")

        tabs = list_tabs(host, port)
        target = tabs[0] if tabs else new_tab(host=host, port=port)
        session = connect_tab(target, host=host, port=port)
        _sessions[key] = _SessionEntry(
            session=session, host=host, port=port, proc=proc,
            mode=resolved_mode, headless=resolved_headless,
        )
        return session


def detect_headless_hint(host: str, port: int, session: Optional[CDPSession] = None) -> dict:
    """
    对一个**未知来源**（通常是 attach 到的、不是我们自己拉起的）浏览器会话，
    尽力而为地猜测它是有头还是无头，返回 `{"headless": True/False/None,
    "confidence": "low"|"medium", "signals": [...]}`。

    没有任何 CDP 协议层面 100% 可靠的"是否有头"判据——尤其 `--headless=new`
    刻意让自己在协议/UA 层面和有头浏览器几乎无法区分（这是 Chrome 团队有意
    为之，用于反"网站按 UA 拒绝无头浏览器"的检测）。这里综合两个信号：

    1. `/json/version` 的 `Browser` 字段：命中 "Headless" 字样 -> 高置信度
       判定为无头（这个信号只对旧版 `--headless` 有效，对 `--headless=new`
       无效，所以命中时可信，没命中不能反推为有头）。
    2. `navigator.webdriver` + `window.chrome`：无头 Chrome（不管新旧版本）
       默认都不会注入 `window.chrome` 这个对象（该对象只在有头/正常渲染路径
       下由 Chrome 自己的扩展框架注入），而有头浏览器（不管是不是本 skill
       自己拉起的、要不要 `--enable-automation`）通常都有。这个信号比信号 1
       更能覆盖 `--headless=new` 的情况，但仍不是官方承诺的协议特性，只是
       目前版本 Chrome 的实际行为，标记为 "medium" 置信度。

    两个信号都拿不到时返回 `headless: None`（诚实报告"无法判断"，不瞎猜）。
    """
    signals: list[str] = []
    headless: Optional[bool] = None
    confidence = "low"

    try:
        version_info = get_browser_version(host, port)
        browser_field = str(version_info.get("Browser", ""))
        if "Headless" in browser_field:
            headless = True
            confidence = "high"
            signals.append(f"/json/version Browser={browser_field!r} 含 Headless 字样")
        else:
            signals.append(f"/json/version Browser={browser_field!r}（未命中旧版 Headless 字样，不能判定为有头）")
    except Exception as e:  # noqa: BLE001
        signals.append(f"/json/version 查询失败: {e}")

    if headless is None and session is not None:
        try:
            has_window_chrome = session.eval_js("!!window.chrome")
            signals.append(f"window.chrome 是否存在: {has_window_chrome}")
            if has_window_chrome is False:
                headless = True
                confidence = "medium"
            elif has_window_chrome is True:
                headless = False
                confidence = "medium"
        except Exception as e:  # noqa: BLE001
            signals.append(f"探测 window.chrome 失败: {e}")

    return {"headless": headless, "confidence": confidence if headless is not None else "unknown", "signals": signals}


def list_sessions() -> list[dict]:
    """
    列出**当前进程**已经建立/复用过的浏览器会话（即通过本模块的
    `get_or_create_session()` 至少调用过一次的那些 (host, port)）。

    这不是"扫描系统里所有正在运行的 Chrome 实例"——CDP 协议本身没有提供
    "枚举本机所有调试端口"的能力，只能对一个已知的 (host, port) 探测是否
    有浏览器在监听。所以能看到的范围是：本模块自己启动过的、或者本模块
    attach 过至少一次的浏览器。如果要检查一个还没被 attach 过的端口是否有
    浏览器在监听，用 `is_debug_port_alive(host, port)` 或直接调用
    `browser_navigate` 传入对应 `session.port`（会触发一次真正的 attach）。
    """
    with _lock:
        entries = list(_sessions.items())

    result = []
    for (host, port), entry in entries:
        alive = is_debug_port_alive(host, port)
        item: dict = {
            "host": host,
            "port": port,
            "mode": entry.mode,
            "alive": alive,
            "spawned_by_us": entry.proc is not None,
            "pid": entry.proc.pid if entry.proc is not None else None,
        }
        if entry.headless is not None:
            item["headless"] = entry.headless
            item["headless_confidence"] = "certain"  # 我们自己拉起的，不是猜的
        elif alive:
            hint = detect_headless_hint(host, port, session=entry.session if alive else None)
            item["headless"] = hint["headless"]
            item["headless_confidence"] = hint["confidence"]
            item["headless_signals"] = hint["signals"]
        else:
            item["headless"] = None
            item["headless_confidence"] = "unknown"
        result.append(item)
    return result


def reset_session(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    """关闭并移除一个会话，供测试或显式"结束这次浏览会话"使用。"""
    close_session(host=host, port=port)


def close_session(host: str = "127.0.0.1", port: int = DEFAULT_PORT, kill_process: bool = True) -> dict:
    """
    关闭一个已建立的会话，供用户/explorer 显式清理"之前遗留的调试浏览器"。

    - 断开我们这边的 CDP WebSocket 连接（不管是不是我们启动的浏览器，这一步
      都做，因为这个连接本来就是我们建立的）。
    - `kill_process=True`（默认）且这个会话是本模块自己 `spawn_browser` 拉起
      的（`entry.proc is not None`）时，才会真的终止浏览器进程——`attach`
      模式连接的浏览器是使用者自己启动的，不由我们负责关闭（与
      `_cleanup_all()` 的既有约定一致），即使 `kill_process=True` 也不会碰。
    - 对没有对应会话记录的 (host, port)（比如从未被这个进程 attach 过、只是
      用户口头说"帮我关掉 9222 那个"），本函数无能为力返回
      `closed_our_session=False`，调用方应提示用户自己去关闭该进程，或者先
      用 `session.mode="attach"` 建立一次连接后再调用本函数。

    返回 `{"closed_our_session": bool, "killed_process": bool, "pid": int|None}`。
    """
    with _lock:
        entry = _sessions.pop((host, port), None)

    if entry is None:
        return {"closed_our_session": False, "killed_process": False, "pid": None}

    try:
        entry.session.close()
    except Exception:
        pass

    killed = False
    pid = entry.proc.pid if entry.proc is not None else None
    if kill_process and entry.proc is not None:
        try:
            entry.proc.terminate()
            killed = True
        except Exception:
            pass

    return {"closed_our_session": True, "killed_process": killed, "pid": pid}


def close_all_sessions(kill_process: bool = True) -> list[dict]:
    """对 `list_sessions()` 能看到的每一个会话调用一次 `close_session()`。"""
    with _lock:
        keys = list(_sessions.keys())
    results = []
    for host, port in keys:
        r = close_session(host=host, port=port, kill_process=kill_process)
        r.update({"host": host, "port": port})
        results.append(r)
    return results
