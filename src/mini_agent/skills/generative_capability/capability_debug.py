"""
capability_debug.py — generative-capability 引擎全链路调试日志

对应用户需求（阶段二十一）：新增一个由 agent_config.json 控制的调试开关，
打开后详细记录 generative-capability 全流程（resolve/execute/explore/
distill、registry 桥接、各 skill 自己的底层实现代码）的调用关系与细节，
落盘到独立日志文件，便于事后排查——例如上一次真实复现的 `browser_navigate`
`Unknown tool` 问题，本该能直接从这份日志里看到"registry 是否被替换成
私有副本""每个 domain 工具是否注册成功"，不需要再靠肉眼读 stdout 猜。

设计原则（与项目既有的 `errors.py::log_exception()` 全局错误日志是同一种
组织方式，刻意保持风格一致，便于一起排查/关联分析）：

- **开关判断收口在这里，调用方不需要关心配置从哪来**：`config/loader.py::
  load_config()` 每次成功加载配置后调用 `configure_capability_debug()`
  同步一次模块级开关（同 `errors.py::configure_tool_executor_log_saving()`
  的组织方式）。`capability_debug_log()` 内部检查这个开关，关闭时直接
  提前返回，不构造任何 dict/不做任何 IO，零开销。
- **skill 的实现代码也可以直接调用**（这是本次新增的关键诉求）：
  `.claude/skills/<name>/impl/*.py` 这类 skill 自己的实现代码，只需要
  `from mini_agent.skills.generative_capability.capability_debug import
  capability_debug_log` 然后直接调用，不需要自己判断开关状态、不需要
  自己解析日志路径——这两件事都是"通用 skill 系统机制"，按项目约定
  （"skill 具体功能代码放 skill 目录，项目代码只放通用机制"）留在这个
  项目级模块里，skill 目录里不会出现任何开关判断或路径拼接逻辑。
- 日志路径固定为 `~/.agent/logs/capability_debug.jsonl`（或
  `MINI_AGENT_HOME` 指向的目录），与 `error.jsonl` 同目录、同轮转策略
  （10MB 一个文件，保留最近 5 个），见 `storage/paths.py::
  AgentPaths.global_capability_debug_log`。
- 记录格式与 `errors.py`/`llm/debug_logger.py` 一致的 JSON Lines 风格：
  每行一条 `{ts, ts_str, pid, thread, where, event, details}`，`where`
  建议用 `"module.function"` 或 `"<skill_name>/impl/xxx.py:func"` 这样
  的可读标识，`details` 是任意可 JSON 序列化的 dict（不可序列化字段自动
  转为字符串，不会因为传了个奇怪的对象就整条记录丢失或抛异常）。
- 这个模块本身绝不向调用方抛出异常：日志系统故障不应该影响 skill/引擎
  的主流程，任何内部异常都吞掉（不静默重复输出到 stdout 造成刷屏，只在
  日志系统自身也失败时才退化为一次 stderr 提示）。

用法：
    from mini_agent.skills.generative_capability.capability_debug import (
        capability_debug_log,
    )
    capability_debug_log(
        "domain_tool_registered",
        {"tool_name": "browser_navigate", "skill": "browser-site-scraper"},
        where="explorer_runtime.build_subagent_explorer",
    )
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from mini_agent.time_utils import iso_local, now_str

_LOCK = threading.Lock()
_FILE_LOGGER: Optional[logging.Logger] = None

_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_BACKUP_COUNT = 5

# 模块级开关：默认关闭。由 config/loader.py::load_config() 在每次加载配置后
# 通过 configure_capability_debug() 同步（与 errors.py 的
# _SAVE_TOOL_EXECUTOR_ERROR_LOGS 是同一种组织方式）——调用方（包括 skill
# 自己的 impl 代码）永远不需要自己传一个 enabled 参数进来。
_CAPABILITY_DEBUG_ENABLED = False


def configure_capability_debug(enabled: bool) -> None:
    """设置 generative-capability 调试日志开关。

    典型调用方：`config/loader.py::load_config()`，每次成功加载配置后同步
    一次。也可以在测试里直接调用，临时切换行为。
    """
    global _CAPABILITY_DEBUG_ENABLED
    _CAPABILITY_DEBUG_ENABLED = bool(enabled)


def is_capability_debug_enabled() -> bool:
    """供调用方在写日志之外，还想额外做一点"仅调试模式下才做"的事情时查询
    （例如探索子agent调试模式下多附带一份截图）。绝大多数场景不需要用到
    这个函数，直接调用 capability_debug_log() 即可——它内部已经做了这个
    判断。"""
    return _CAPABILITY_DEBUG_ENABLED


def _log_path() -> Path:
    """解析调试日志文件路径。优先复用 AgentPaths（与 error.jsonl 等其它
    ~/.agent/ 路径保持一致）；因循环导入等原因不可用时退化为直接读
    MINI_AGENT_HOME 环境变量 / ~/.agent。"""
    try:
        from mini_agent.storage.paths import AgentPaths

        return AgentPaths().global_capability_debug_log
    except Exception:  # noqa: BLE001 — 日志系统自身的路径解析失败不应该抛出
        home_override = os.environ.get("MINI_AGENT_HOME")
        base = Path(home_override) if home_override else (Path.home() / ".agent")
        return base / "logs" / "capability_debug.jsonl"


def _get_file_logger() -> logging.Logger:
    global _FILE_LOGGER
    if _FILE_LOGGER is not None:
        return _FILE_LOGGER
    with _LOCK:
        if _FILE_LOGGER is not None:
            return _FILE_LOGGER
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("mini_agent._capability_debug_sink")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        _FILE_LOGGER = logger
        return logger


def _safe_json(obj: Any) -> Any:
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except Exception:  # noqa: BLE001
        if isinstance(obj, dict):
            return {str(k): _safe_json_value(v) for k, v in obj.items()}
        return repr(obj)


def _safe_json_value(v: Any) -> Any:
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception:  # noqa: BLE001
        return repr(v)


def capability_debug_log(
    event: str,
    details: Optional[dict[str, Any]] = None,
    *,
    where: str = "",
) -> None:
    """记录一条 generative-capability 调试事件。

    开关关闭（默认）时直接返回，不构造 record、不做任何 IO——项目通用
    引擎代码和各 skill 的 impl 代码都可以放心地直接调用，不需要自己包一层
    `if debug_enabled:` 判断。

    Args:
        event: 事件名，建议用简短的 snake_case，如
            "registry_replaced_with_private_copy" / "domain_tool_registered" /
            "browser_navigate_called"。
        details: 任意可 JSON 序列化的上下文字段，如
            {"tool_name": "browser_navigate", "session_mode": "attach"}。
        where: 发生位置，建议 "module.function" 或
            "<skill_name>/impl/xxx.py:func"（skill 自己的实现代码调用时，
            直接写 skill 内的相对路径 + 函数名即可，不需要遵循项目模块
            命名规则）。不传时留空，不做栈帧推断（调试日志追求"调用方
            明确写清楚是哪"，比自动推断更可靠）。
    """
    if not _CAPABILITY_DEBUG_ENABLED:
        return

    record: dict[str, Any] = {
        "ts": iso_local(),
        "ts_str": now_str(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "where": where,
        "event": event,
    }
    if details:
        record["details"] = _safe_json(details)

    try:
        _get_file_logger().debug(json.dumps(record, ensure_ascii=False))
    except Exception as log_err:  # noqa: BLE001 — 日志系统自身故障不能中断主流程
        sys.stderr.write(
            f"[capability_debug] 写入调试日志失败: {log_err!r}; "
            f"原始事件: {event}\n"
        )


def capability_debug_log_path() -> Path:
    """暴露给外部（如 `/debug` CLI 命令）查询当前调试日志文件的位置。"""
    return _log_path()
