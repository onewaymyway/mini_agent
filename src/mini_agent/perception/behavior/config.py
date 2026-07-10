"""
perception/behavior/config.py — 用户行为感知系统：开关与配置

设计要点（对应设计方案第二节"核心开关设计"）：
  - 总开关默认 False。总开关关闭时，manager 不启动任何采集线程，
    HTTP 上报接口也会直接拒绝 (403)。
  - 每个采集器有独立子开关，均默认 False，需要显式打开。
  - 配置独立于 AppConfig 落盘（~/.agent/behavior_config.json），
    不侵入现有 config/loader.py 的加载流程，方便随时整体移除。
  - window_title / url 等可能带敏感信息的文本字段支持"脱敏模式"：
    脱敏模式下只记录应用名 / 域名，不记录标题全文或完整路径。

隐私边界（不做的事情，详见设计方案）：
  - 不采集聊天软件的消息内容，只把聊天类 App 当作普通前台窗口对待
    （即只知道"用户在用微信"，不知道聊了什么）。
  - 不做剪贴板内容采集（只有"发生了复制"事件框架，默认不落地内容）。
  - 不做键盘按键内容记录（keylogger），只用于计算 idle 时长。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _behavior_dir() -> Path:
    return Path.home() / ".agent" / "behavior"


def _config_path() -> Path:
    return _behavior_dir() / "config.json"


@dataclass
class BehaviorConfig:
    """用户行为感知系统配置。默认全部关闭。"""

    # 总开关：为 False 时，无论子开关状态如何，一律不采集、不接受上报。
    enabled: bool = False

    # 采集器子开关
    active_window_enabled: bool = False   # 前台窗口/程序
    idle_enabled: bool = False            # 空闲/在场检测
    browser_report_enabled: bool = False  # 接受浏览器插件通过 HTTP 上报
    mobile_report_enabled: bool = False   # 接受手机端（Tasker/快捷指令等）通过 HTTP 上报
    clipboard_meta_enabled: bool = False  # 仅记录"发生了复制"及来源程序，不存内容
    cdp_browser_enabled: bool = False     # 专用调试浏览器采集（另一种浏览器行为采集方案）
    git_activity_enabled: bool = False    # 通过 git hook 上报 commit/checkout（外部上报源）
    terminal_command_enabled: bool = False  # 通过 shell hook 上报命令（外部上报源，客户端+服务端双重脱敏）
    now_playing_enabled: bool = False     # 媒体"正在播放"元数据（标题/来源，不含内容）
    app_lifecycle_enabled: bool = False   # 应用启动/退出事件（补充前台窗口，能看到后台常驻程序）

    # 分析层：定期把原始事件聚合成"工作/生活画像"摘要
    daily_analysis_enabled: bool = False
    daily_analysis_hour: int = 22         # 每天几点跑一次（24 小时制，本地时间）

    # 采集参数
    poll_interval_sec: float = 2.0        # 前台窗口轮询间隔
    idle_threshold_sec: float = 120.0     # 超过该时长无输入判定为 idle

    # 隐私参数
    redact_window_title: bool = True      # True: 只记录 app 名，不记录窗口标题原文
    redact_url_path: bool = True          # True: 浏览器上报只保留 domain，不保留完整 path/query
    retention_days: int = 30              # 事件保留天数，超期自动清理

    # HTTP 上报鉴权（独立于 mini_agent 主 API token，只允许 127.0.0.1）
    report_token: str = ""

    # 专用调试浏览器（CDP）方案参数
    cdp_debug_port: int = 9333
    cdp_browser_path: str = ""    # 空则自动探测 Chrome/Edge/Chromium
    cdp_user_data_dir: str = ""   # 空则用独立目录 ~/.agent/behavior/browser_profile
    cdp_headless: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BehaviorConfig":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def load_behavior_config() -> BehaviorConfig:
    """从 ~/.agent/behavior/config.json 加载配置；文件不存在时返回默认（全关闭）配置。"""
    path = _config_path()
    if not path.exists():
        return BehaviorConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BehaviorConfig.from_dict(data)
    except Exception:
        # 配置文件损坏时，安全兜底为默认关闭状态，不让感知系统"默认打开"。
        return BehaviorConfig()


def save_behavior_config(cfg: BehaviorConfig) -> None:
    d = _behavior_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _config_path()
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import stat
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def ensure_report_token(cfg: BehaviorConfig) -> str:
    """确保存在浏览器插件上报用的 token；不存在则生成并落盘。"""
    if cfg.report_token:
        return cfg.report_token
    import secrets
    cfg.report_token = secrets.token_hex(24)
    save_behavior_config(cfg)
    return cfg.report_token
