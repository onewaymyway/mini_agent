"""桌面客户端自身配置（与 mini_agent 的 agent_config.json 无关）。

存放位置：
  - Windows：%LOCALAPPDATA%\\mini_agent\\desktop.json
  - 其它系统：~/.config/mini_agent/desktop.json
  - 环境变量 MINI_AGENT_DESKTOP_CONFIG 可覆盖路径（测试/多实例用）

所有"会主动做事"的开关默认关闭（保守 opt-in）。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional


@dataclass
class DesktopConfig:
    # 要连接的 mini_agent 项目根目录；空 = 使用命令行 --project-root，再空 = 当前目录
    project_root: str = ""
    # daemon HTTP 端口；daemon_info.json 存在时以它为准
    http_port: int = 8765
    # "打开管理控制台"打开的地址（Streamlit 看板默认端口）
    console_url: str = "http://localhost:8501"
    # 摘要快照的兜底刷新间隔（秒）；SSE 事件到来时会立即刷新，这只是兜底
    poll_interval_seconds: float = 30.0
    # 保守 opt-in：daemon 没运行时，是否由桌面端自动拉起。默认关闭，
    # 关闭时托盘菜单提供手动"启动 Agent"。
    auto_start_daemon: bool = False
    # S2：全局热键唤起快速面板。默认关闭（保守 opt-in，避免抢占其它程序的快捷键）。
    hotkey_enabled: bool = False
    # 热键组合，格式见 hotkey.parse_hotkey，例如 "Ctrl+Alt+Space" / "Win+Shift+K"
    hotkey: str = "Ctrl+Alt+Space"
    # S3：Toast 总开关（Windows 原生气泡），默认关闭
    toast_enabled: bool = False
    # S3：免打扰时段，如 "23:00-08:00"；空 = 不设。阻塞类提醒不受影响
    quiet_hours: str = ""


def config_path() -> Path:
    override = os.environ.get("MINI_AGENT_DESKTOP_CONFIG")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "mini_agent" / "desktop.json"
    return Path.home() / ".config" / "mini_agent" / "desktop.json"


def load_config(path: Optional[Path] = None) -> DesktopConfig:
    """读取配置；文件不存在/损坏/字段类型不对都退回默认值，不抛异常。"""
    p = path or config_path()
    cfg = DesktopConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for f in fields(DesktopConfig):
        if f.name not in raw:
            continue
        default = getattr(cfg, f.name)
        value = raw[f.name]
        # bool 是 int 的子类，要先判 bool，避免 True 被当成端口号
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(default, float):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, str)
        if ok:
            setattr(cfg, f.name, type(default)(value))
    return cfg


def save_config(cfg: DesktopConfig, path: Optional[Path] = None) -> None:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
