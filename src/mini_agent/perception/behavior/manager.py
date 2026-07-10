"""
perception/behavior/manager.py — 用户行为感知总管理器

职责：
  1. 读取 BehaviorConfig，总开关关闭时什么都不做（不启动线程、不接受上报）。
  2. 按子开关启停各本机采集器（active_window / idle）。
  3. 提供 report_external() 给 HTTP 接口调用，用于接收浏览器插件等外部
     系统上报的事件（同样受总开关 + browser_report_enabled 子开关控制）。
  4. 提供 query() / export() / clear() 给 CLI 和 agent context 注入使用。
  5. 单例式使用（get_manager()），避免多处各自起线程。

这是一个"纯 Python 单例"，不依赖 AppConfig/loader，方便随时整体移除，
也方便在没有完整 mini_agent 运行环境（比如只装了浏览器插件配套的轻量
HTTP 服务）时单独使用。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from .config import BehaviorConfig, load_behavior_config, save_behavior_config, ensure_report_token
from .events import ActivityEvent, BehaviorEventStore
from .collectors import (
    ActiveWindowCollector, IdleCollector, CDPBrowserCollector,
    NowPlayingCollector, AppLifecycleCollector,
    install_git_hooks, generate_shell_hook_snippet, redact_command,
)
from .mobile_setup import android_usage_report_template, ios_shortcuts_template


class BehaviorPerceptionManager:
    def __init__(self, project_root: Optional[Path] = None) -> None:
        self._project_root = project_root
        self._cfg: BehaviorConfig = load_behavior_config(project_root)
        self._store = BehaviorEventStore()
        self._collectors: dict[str, object] = {}
        self._lock = threading.Lock()
        self._purge_thread: Optional[threading.Thread] = None
        self._purge_stop = threading.Event()

    # ── 配置 ──────────────────────────────────────────────────────────────

    @property
    def config(self) -> BehaviorConfig:
        return self._cfg

    @property
    def project_root(self):
        return self._project_root

    def reload_config(self) -> None:
        with self._lock:
            self._cfg = load_behavior_config(self._project_root)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._cfg.enabled = enabled
            save_behavior_config(self._cfg, self._project_root)
        if enabled:
            self.start()
        else:
            self.stop()

    def set_collector_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            if not hasattr(self._cfg, f"{name}_enabled"):
                raise ValueError(f"unknown collector: {name}")
            setattr(self._cfg, f"{name}_enabled", enabled)
            save_behavior_config(self._cfg, self._project_root)
        # 重新按最新配置启停
        self.start()

    def get_report_token(self) -> str:
        return ensure_report_token(self._cfg, self._project_root)

    # ── 启停 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """按当前配置启停各采集器。总开关关闭时全部停止。"""
        with self._lock:
            if not self._cfg.enabled:
                self._stop_all_collectors()
                self._stop_purge_loop()
                return

            if self._cfg.active_window_enabled:
                if "active_window" not in self._collectors:
                    c = ActiveWindowCollector(
                        self._store,
                        interval_sec=self._cfg.poll_interval_sec,
                        redact_title=self._cfg.redact_window_title,
                    )
                    self._collectors["active_window"] = c
                self._collectors["active_window"].start()  # type: ignore[union-attr]
            else:
                self._stop_collector("active_window")

            if self._cfg.idle_enabled:
                if "idle" not in self._collectors:
                    c = IdleCollector(
                        self._store,
                        interval_sec=max(5.0, self._cfg.poll_interval_sec),
                        threshold_sec=self._cfg.idle_threshold_sec,
                    )
                    self._collectors["idle"] = c
                self._collectors["idle"].start()  # type: ignore[union-attr]
            else:
                self._stop_collector("idle")

            # cdp_browser 不在这里自动启停：它需要拉起一个外部浏览器进程，
            # 必须由用户显式执行 /behavior browser start 触发，
            # 避免总开关一开就静默弹出一个浏览器窗口。
            if not self._cfg.cdp_browser_enabled:
                self._stop_collector("cdp_browser")

            if self._cfg.now_playing_enabled:
                if "now_playing" not in self._collectors:
                    self._collectors["now_playing"] = NowPlayingCollector(
                        self._store, interval_sec=max(5.0, self._cfg.poll_interval_sec)
                    )
                self._collectors["now_playing"].start()  # type: ignore[union-attr]
            else:
                self._stop_collector("now_playing")

            if self._cfg.app_lifecycle_enabled:
                if "app_lifecycle" not in self._collectors:
                    self._collectors["app_lifecycle"] = AppLifecycleCollector(
                        self._store, interval_sec=max(5.0, self._cfg.poll_interval_sec)
                    )
                self._collectors["app_lifecycle"].start()  # type: ignore[union-attr]
            else:
                self._stop_collector("app_lifecycle")

            self._start_purge_loop()

    def stop(self) -> None:
        with self._lock:
            self._stop_all_collectors()
            self._stop_purge_loop()

    def _stop_collector(self, name: str) -> None:
        c = self._collectors.get(name)
        if c is not None:
            c.stop()  # type: ignore[union-attr]

    def _stop_all_collectors(self) -> None:
        for c in self._collectors.values():
            c.stop()  # type: ignore[union-attr]

    # ── 专用调试浏览器（CDP 方案）──────────────────────────────────────────

    def browser_start(self) -> dict:
        """拉起专用调试浏览器并开始采集。需要总开关已打开。"""
        if not self._cfg.enabled:
            raise RuntimeError("总开关未打开，请先 /behavior on")

        with self._lock:
            c = self._collectors.get("cdp_browser")
            if c is None:
                c = CDPBrowserCollector(
                    self._store,
                    port=self._cfg.cdp_debug_port,
                    browser_path=self._cfg.cdp_browser_path,
                    user_data_dir=self._cfg.cdp_user_data_dir,
                    redact_url_path=self._cfg.redact_url_path,
                    redact_title=self._cfg.redact_window_title,
                    headless=self._cfg.cdp_headless,
                )
                self._collectors["cdp_browser"] = c

        if not c.is_browser_running():  # type: ignore[union-attr]
            c.launch_and_connect()  # type: ignore[union-attr]
        elif not c.is_running:  # type: ignore[union-attr]
            c.start()  # type: ignore[union-attr]

        with self._lock:
            self._cfg.cdp_browser_enabled = True
            save_behavior_config(self._cfg)

        return c.status()  # type: ignore[union-attr]

    def browser_stop(self, kill_browser: bool = False) -> dict:
        c = self._collectors.get("cdp_browser")
        if c is None:
            return {"browser_running": False, "ws_connected": False}
        c.stop(kill_browser=kill_browser)  # type: ignore[union-attr]
        with self._lock:
            self._cfg.cdp_browser_enabled = False
            save_behavior_config(self._cfg)
        return c.status()  # type: ignore[union-attr]

    def browser_status(self) -> dict:
        c = self._collectors.get("cdp_browser")
        if c is None:
            return {"browser_running": False, "ws_connected": False, "port": self._cfg.cdp_debug_port}
        return c.status()  # type: ignore[union-attr]

    def status(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "collectors": {
                name: {"enabled": getattr(self._cfg, f"{name}_enabled", None), "running": c.is_running}  # type: ignore[union-attr]
                for name, c in self._collectors.items()
            },
            "config": self._cfg.to_dict(),
        }

    # ── 外部上报（浏览器插件等）───────────────────────────────────────────

    def report_external(self, source: str, events: list[dict], token: str, kind: str = "browser") -> tuple[bool, str]:
        """接收外部系统上报的事件。返回 (ok, message)。

        kind 决定走哪个子开关校验：
          "browser"  -> browser_report_enabled  （浏览器插件）
          "git"      -> git_activity_enabled    （git hook）
          "terminal" -> terminal_command_enabled（shell hook，额外做服务端二次脱敏）
          "mobile"   -> mobile_report_enabled   （手机端 Tasker/快捷指令等，
                        额外强制剔除任何经纬度字段，只允许地理围栏标签）

        鉴权 + 双开关校验：
          - 总开关必须开
          - 对应 kind 的子开关必须开
          - token 必须匹配 report_token
        """
        if not self._cfg.enabled:
            return False, "behavior perception disabled"

        gate_map = {
            "browser": self._cfg.browser_report_enabled,
            "git": self._cfg.git_activity_enabled,
            "terminal": self._cfg.terminal_command_enabled,
            "mobile": self._cfg.mobile_report_enabled,
        }
        if kind not in gate_map:
            return False, f"unknown report kind: {kind}"
        if not gate_map[kind]:
            return False, f"{kind} report collector disabled"
        if not token or token != self._cfg.report_token:
            return False, "invalid token"

        parsed: list[ActivityEvent] = []
        for e in events:
            ev = ActivityEvent.from_dict(e)
            ev.source = source or ev.source or kind

            if kind == "terminal":
                # 服务端二次脱敏：客户端 hook 已经过滤过一遍，这里再兜底一次，
                # 避免客户端脚本被绕过/魔改后把敏感命令原样发过来。
                cmd = (ev.meta or {}).get("cmd", "")
                safe_cmd = redact_command(cmd) if cmd else None
                if cmd and not safe_cmd:
                    continue  # 命中敏感规则，整条丢弃，不落盘
                if safe_cmd:
                    ev.meta["cmd"] = safe_cmd

            if kind == "mobile" and ev.meta:
                # 硬性边界：手机端只允许上报"在家/在公司"这类地理围栏标签，
                # 绝不接受原始经纬度——即使客户端脚本被改出来发了坐标，服务端也剔除。
                for gps_key in ("lat", "lon", "latitude", "longitude", "gps", "coordinates"):
                    ev.meta.pop(gps_key, None)

            if self._cfg.redact_url_path:
                ev.url_path = None
            parsed.append(ev)
        if parsed:
            self._store.append_many(parsed)
        return True, f"accepted {len(parsed)} events"

    # ── Git / 终端命令 外部上报安装辅助 ─────────────────────────────────────

    def install_git_hooks(self, repo_path, report_url: str, api_token: str) -> list:
        token = self.get_report_token()
        return install_git_hooks(repo_path, report_url, api_token, token)

    def get_shell_hook_snippet(self, report_url: str, api_token: str) -> str:
        token = self.get_report_token()
        return generate_shell_hook_snippet(report_url, api_token, token)

    def get_mobile_template(self, platform: str, report_url: str, api_token: str) -> str:
        token = self.get_report_token()
        if platform == "android":
            return android_usage_report_template(report_url, api_token, token)
        if platform == "ios":
            return ios_shortcuts_template(report_url, api_token, token)
        raise ValueError(f"unknown platform: {platform}")

    # ── 查询 / 导出 / 清理 ──────────────────────────────────────────────

    def query(self, **kwargs) -> list[ActivityEvent]:
        return self._store.query(**kwargs)

    def clear(self) -> int:
        return self._store.clear()

    def _start_purge_loop(self) -> None:
        if self._purge_thread and self._purge_thread.is_alive():
            return
        self._purge_stop.clear()

        def _loop():
            while not self._purge_stop.is_set():
                try:
                    self._store.purge_older_than(self._cfg.retention_days)
                except Exception:
                    pass
                self._purge_stop.wait(3600)  # 每小时检查一次

        self._purge_thread = threading.Thread(target=_loop, name="behavior-purge", daemon=True)
        self._purge_thread.start()

        if self._cfg.daily_analysis_enabled:
            self._start_analysis_loop()
        else:
            self._stop_analysis_loop()

    def _start_analysis_loop(self) -> None:
        if getattr(self, "_analysis_thread", None) and self._analysis_thread.is_alive():
            return
        self._analysis_stop = threading.Event()

        def _loop():
            import datetime as _dt
            from .analyzer import generate_daily_summary

            last_run_day = None
            while not self._analysis_stop.is_set():
                now = _dt.datetime.now()
                if now.hour == self._cfg.daily_analysis_hour and last_run_day != now.date():
                    try:
                        generate_daily_summary(self, now.date().isoformat())
                        last_run_day = now.date()
                    except Exception:
                        pass
                self._analysis_stop.wait(60)

        self._analysis_thread = threading.Thread(target=_loop, name="behavior-analysis", daemon=True)
        self._analysis_thread.start()

    def _stop_analysis_loop(self) -> None:
        if getattr(self, "_analysis_stop", None):
            self._analysis_stop.set()
        if getattr(self, "_analysis_thread", None):
            self._analysis_thread.join(timeout=1.0)
        self._analysis_thread = None

    def _stop_purge_loop(self) -> None:
        self._purge_stop.set()
        if self._purge_thread:
            self._purge_thread.join(timeout=1.0)
        self._purge_thread = None
        self._stop_analysis_loop()


_manager: Optional[BehaviorPerceptionManager] = None
_manager_lock = threading.Lock()


def get_manager(project_root: Optional[Path] = None) -> BehaviorPerceptionManager:
    """获取全局单例。project_root 只在第一次创建时生效（决定 behavior_config.json
    读写的位置）；后续调用即使传入不同的 project_root 也会复用已经创建好的实例
    （同一进程通常只服务于一个项目根目录，不做"运行中途切换项目"这种事）。
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BehaviorPerceptionManager(project_root)
            if _manager.config.enabled:
                _manager.start()
        return _manager


def reset_manager_for_testing() -> None:
    """仅供测试使用：清空单例，让下一次 get_manager() 重新创建。"""
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.stop()
        _manager = None
