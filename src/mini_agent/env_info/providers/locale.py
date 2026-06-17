"""
env_info/providers/locale.py — 本地化信息

采集：
  - 时区（IANA 名称 + UTC 偏移）
  - 系统语言 / locale
"""

from __future__ import annotations

import locale
import os
import time

from mini_agent.env_info.base import EnvInfoProvider


def _get_timezone() -> str | None:
    """返回时区字符串，如 'Asia/Shanghai (UTC+8)'。"""
    # 尝试 IANA 时区名
    tz_name: str | None = None

    # 1. 环境变量 TZ
    tz_env = os.environ.get("TZ")
    if tz_env:
        tz_name = tz_env

    # 2. Python 3.9+ zoneinfo
    if tz_name is None:
        try:
            from zoneinfo import ZoneInfo  # type: ignore
            import datetime
            local_tz = datetime.datetime.now().astimezone().tzinfo
            tz_name = str(local_tz)
        except Exception:
            pass

    # 3. 读取 /etc/timezone (Linux)
    if tz_name is None:
        try:
            with open("/etc/timezone", encoding="utf-8") as f:
                candidate = f.read().strip()
                if "/" in candidate:
                    tz_name = candidate
        except Exception:
            pass

    # 4. /etc/localtime 软链接 (Linux / macOS)
    if tz_name is None:
        try:
            import os as _os
            link = _os.readlink("/etc/localtime")
            for prefix in ("/usr/share/zoneinfo/", "/var/db/timezone/zoneinfo/"):
                if prefix in link:
                    tz_name = link.split(prefix, 1)[1]
                    break
        except Exception:
            pass

    # 计算 UTC 偏移
    try:
        offset_sec = -time.timezone if not time.daylight else -time.altzone
        offset_h = offset_sec // 3600
        offset_m = abs(offset_sec % 3600) // 60
        sign = "+" if offset_h >= 0 else "-"
        if offset_m:
            utc_str = f"UTC{sign}{abs(offset_h)}:{offset_m:02d}"
        else:
            utc_str = f"UTC{sign}{abs(offset_h)}"
    except Exception:
        utc_str = ""

    if tz_name and utc_str:
        return f"{tz_name} ({utc_str})"
    elif tz_name:
        return tz_name
    elif utc_str:
        return utc_str
    return None


def _get_locale() -> str | None:
    """返回系统 locale，如 'zh_CN.UTF-8'。"""
    # 优先环境变量
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val and val != "C" and val != "POSIX":
            return val

    # 回退 Python locale
    try:
        lc = locale.getlocale()[0]
        if lc and lc not in ("C", "POSIX"):
            return lc
    except Exception:
        pass

    return None


class LocaleInfoProvider(EnvInfoProvider):
    """采集时区和语言环境信息。"""

    name = "builtin.locale"

    def __init__(
        self,
        include_locale: bool = True,
        include_timezone: bool = True,
    ) -> None:
        self._include_locale = include_locale
        self._include_timezone = include_timezone

    def collect(self) -> dict[str, str]:
        info: dict[str, str] = {}

        if self._include_timezone:
            tz = _get_timezone()
            if tz:
                info["Timezone"] = tz

        if self._include_locale:
            lc = _get_locale()
            if lc:
                info["Locale"] = lc

        return info
