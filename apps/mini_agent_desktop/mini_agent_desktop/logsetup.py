r"""S4：桌面端日志。pythonw 没有控制台，日志写文件才可排障。

位置：Windows `%LOCALAPPDATA%\mini_agent\logs\desktop.log`，其它系统 `~/.local/state/mini_agent/logs/desktop.log`。
滚动：单文件 1 MB，保留 3 份。只使用名为 mini_agent_desktop 的 logger，不污染全局。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

LOGGER_NAME = "mini_agent_desktop"
_MARK = "_mini_agent_desktop_handler"
MAX_BYTES = 1024 * 1024
BACKUPS = 3


def default_log_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "mini_agent" / "logs" / "desktop.log"
    return Path.home() / ".local" / "state" / "mini_agent" / "logs" / "desktop.log"


def setup_logging(path: Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if any(getattr(h, _MARK, False) for h in logger.handlers):
        return logger            # 幂等：重复调用不重复添加 handler
    logger.setLevel(level)
    logger.propagate = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()   # 日志目录不可写时静默降级，不影响启动
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    return logger
