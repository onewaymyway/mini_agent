"""
perception/behavior/collectors/now_playing.py — 媒体"正在播放"元数据采集

只取标题/艺术家/来源 App，不采集音频/视频内容本身，也不区分具体播放的是
工作相关播客还是娱乐内容——这类判断留给分析层做归类。

平台适配（尽力而为，拿不到就返回 None，不影响其它采集器）：
  Windows : 尝试 winsdk（GlobalSystemMediaTransportControlsSessionManager），
            未安装则不可用
  macOS   : osascript 询问 Music.app / Spotify.app 是否在播放
  Linux   : playerctl（MPRIS），未安装则不可用
"""

from __future__ import annotations

import platform
import subprocess
import time
from typing import Optional

from ..events import ActivityEvent
from .base import BaseCollector


def _now_playing_macos() -> Optional[tuple[str, str, str]]:
    """返回 (app, title, artist)。"""
    for app in ("Music", "Spotify"):
        script = (
            f'if application "{app}" is running then\n'
            f'  tell application "{app}"\n'
            f'    if player state is playing then\n'
            f'      return (name of current track) & "||" & (artist of current track)\n'
            f'    end if\n'
            f'  end tell\n'
            f'end if\n'
        )
        try:
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
            if out.returncode == 0 and out.stdout.strip():
                parts = out.stdout.strip().split("||")
                title = parts[0] if parts else ""
                artist = parts[1] if len(parts) > 1 else ""
                return (app, title, artist)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.now_playing._now_playing_macos')
            continue
    return None


def _now_playing_linux() -> Optional[tuple[str, str, str]]:
    try:
        status = subprocess.run(["playerctl", "status"], capture_output=True, text=True, timeout=2)
        if status.returncode != 0 or status.stdout.strip() != "Playing":
            return None
        title = subprocess.run(
            ["playerctl", "metadata", "title"], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        artist = subprocess.run(
            ["playerctl", "metadata", "artist"], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        player = subprocess.run(
            ["playerctl", "-l"], capture_output=True, text=True, timeout=2
        ).stdout.strip().splitlines()
        app = player[0] if player else "unknown"
        return (app, title, artist)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.now_playing._now_playing_linux')
        return None


def _now_playing_windows() -> Optional[tuple[str, str, str]]:
    try:
        import asyncio
        from winsdk.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )

        async def _get():
            mgr = await MediaManager.request_async()
            session = mgr.get_current_session()
            if not session:
                return None
            info = await session.try_get_media_properties_async()
            return (session.source_app_user_model_id or "unknown", info.title or "", info.artist or "")

        return asyncio.run(_get())
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.now_playing._now_playing_windows')
        return None


def get_now_playing() -> Optional[tuple[str, str, str]]:
    system = platform.system()
    if system == "Darwin":
        return _now_playing_macos()
    if system == "Linux":
        return _now_playing_linux()
    if system == "Windows":
        return _now_playing_windows()
    return None


class NowPlayingCollector(BaseCollector):
    """轮询当前播放状态，切歌/停止播放时产出上一首的事件。"""

    name = "now_playing"

    def __init__(self, store, interval_sec: float = 5.0) -> None:
        super().__init__(store, interval_sec)
        self._current: Optional[tuple[str, str, str]] = None
        self._since: float = time.time()

    def poll(self):
        detected = get_now_playing()
        now = time.time()

        if detected is None:
            if self._current is not None:
                app, title, artist = self._current
                event = ActivityEvent(
                    timestamp=self._since,
                    source="now_playing",
                    event_type="media_play",
                    app_name=app,
                    window_title=f"{title} - {artist}" if artist else title,
                    duration_sec=round(now - self._since, 1),
                )
                self._current = None
                return [event]
            return None

        if self._current is None:
            self._current = detected
            self._since = now
            return None

        if detected == self._current:
            return None

        prev_app, prev_title, prev_artist = self._current
        event = ActivityEvent(
            timestamp=self._since,
            source="now_playing",
            event_type="media_play",
            app_name=prev_app,
            window_title=f"{prev_title} - {prev_artist}" if prev_artist else prev_title,
            duration_sec=round(now - self._since, 1),
        )
        self._current = detected
        self._since = now
        return [event]

    def is_available(self) -> bool:
        return get_now_playing() is not None
