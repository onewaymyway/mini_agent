"""notification/config.py — .agent/notification/config.yaml 加载（P1）。

设计背景见 next_doc/watchlist_notification_goal_design.md §3.3。
密钥类字段支持 `${ENV:VAR_NAME}` 占位符，运行时从环境变量读取，避免明文
写进会被提交的 yaml（§9.4 #12：config.yaml 本身也应该在部署时加进
.gitignore，这属于运维约定，不是代码要处理的事）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_ENV_PLACEHOLDER = re.compile(r"^\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}$")

# kanban 是恒真的兜底渠道，不可通过配置关闭（§3.3 / §9.3 #8：
# 任何 tier 的 notify_channels 在实际 dispatch 时都隐式带上 kanban，
# 保证"至少在看板能看到"这件事本身可追溯）。
ALWAYS_ON_CHANNEL = "kanban"


def _resolve_env(value):
    """把 "${ENV:VAR}" 占位符替换成环境变量值；不是占位符格式的原样返回。"""
    if isinstance(value, str):
        m = _ENV_PLACEHOLDER.match(value.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return value


@dataclass
class NotificationConfig:
    default_channels: list[str] = field(default_factory=lambda: ["kanban"])
    channels: dict = field(default_factory=dict)  # channel_name -> cfg dict（已解析 ${ENV:...}）

    def channel_config(self, name: str) -> dict:
        return self.channels.get(name, {})

    def is_enabled(self, name: str) -> bool:
        if name == ALWAYS_ON_CHANNEL:
            return True
        return bool(self.channel_config(name).get("enabled", False))


def _resolve_env_recursive(d):
    if isinstance(d, dict):
        return {k: _resolve_env_recursive(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_resolve_env_recursive(v) for v in d]
    return _resolve_env(d)


def load_notification_config(paths: "AgentPaths") -> NotificationConfig:
    """读取 .agent/notification/config.yaml。文件不存在、YAML 库缺失、内容
    非法都退化为"只有 kanban 兜底渠道"的最小配置，不抛异常——跟项目里
    配置类加载"单点配置错误不该拖垮整体"的一贯风格一致。"""
    default = NotificationConfig()
    p = paths.notification_config
    if not p.exists() or not _HAS_YAML:
        return default
    try:
        raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.config.load_notification_config")
        return default
    if not isinstance(raw, dict):
        return default
    default_channels = raw.get("default_channels")
    if not isinstance(default_channels, list) or not default_channels:
        default_channels = ["kanban"]
    channels_raw = raw.get("channels") or {}
    if not isinstance(channels_raw, dict):
        channels_raw = {}
    channels = {
        str(name): _resolve_env_recursive(cfg or {})
        for name, cfg in channels_raw.items()
    }
    return NotificationConfig(default_channels=[str(c) for c in default_channels], channels=channels)
