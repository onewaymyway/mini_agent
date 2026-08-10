"""
errors.py — 全局异常日志

统一记录项目中所有"预期外"的异常到 ~/.agent/logs/error.jsonl（JSON Lines，
一行一条完整记录：时间戳、pid、线程名、发生位置、异常类型、异常信息、完整堆栈、
可选的上下文字段），便于事后统一排查、grep、或接入 Stage 6+ 的可观测性系统
（traces.jsonl / anomaly detection）做关联分析。

三层覆盖，保证"所有异常都能落盘"：

1. 显式捕获点：业务代码里 `except Exception as e:` 时主动调用
   `log_exception(e, where=..., extra=...)`，替代原来的 `pass`/`print`。

2. 现有 logging 调用兜底：项目里已有不少模块用 `logger.exception(...)` /
   `logger.error(...)`，这些调用默认只是打到各自的 logger（有的甚至没有
   handler，直接被吞掉）。`install_global_error_logging()` 会在 root logger
   上挂一个 handler，把所有 level>=ERROR 的日志记录**额外**转发一份到全局
   错误日志文件，不影响原有输出行为。

3. 进程级最终兜底：`install_global_error_logging()` 同时接管
   `sys.excepthook`（主线程未捕获异常）和 `threading.excepthook`（子线程
   未捕获异常），确保真正"漏网"的异常也会留下记录。

用法：
    # 1) 程序入口处安装一次（cli/app.py::main() 最开始）
    from mini_agent.errors import install_global_error_logging
    install_global_error_logging()

    # 2) 业务代码里替代 `except Exception: pass`
    from mini_agent.errors import log_exception
    try:
        risky_call()
    except Exception as e:
        log_exception(e, where="tools.builtin.read_file", extra={"path": path})
        # 按需决定是否继续降级处理，日志记录本身不会抛出新异常
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

from mini_agent.time_utils import iso_local, now_str

_LOCK = threading.Lock()
_FILE_LOGGER: Optional[logging.Logger] = None
_INSTALLED = False

_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_BACKUP_COUNT = 5

# [next_doc/errors_tool_executor_log_toggle_plan.md] 是否把 `where` 前缀为
# `_TOOL_EXECUTOR_WHERE_PREFIX` 的记录写入全局错误日志文件。默认 True（保持
# 改动前行为不变）；由 `configure_tool_executor_log_saving()` 在
# `config/loader.py::load_config()` 里根据 `AppConfig.save_tool_executor_
# error_logs`（对应 agent_config.json 的 `save_tool_executor_error_logs`
# 字段）同步。之所以做成模块级开关而不是每次调用 `log_exception()` 时传参：
# `tool_executor.py` 里几十处调用点分散在整个文件，统一在这里收口判断，
# 不需要逐一改造调用方（用户明确要求"改 errors.py 的逻辑，而不是调用的
# 地方"）。
_SAVE_TOOL_EXECUTOR_ERROR_LOGS = True
_TOOL_EXECUTOR_WHERE_PREFIX = "mini_agent.tool_executor"


def configure_tool_executor_log_saving(enabled: bool) -> None:
    """设置是否把 tool_executor 来源的记录写入全局错误日志文件。

    典型调用方：`config/loader.py::load_config()`，每次成功加载配置后
    同步一次。也可以在测试里直接调用，临时切换行为。
    """
    global _SAVE_TOOL_EXECUTOR_ERROR_LOGS
    _SAVE_TOOL_EXECUTOR_ERROR_LOGS = bool(enabled)


def _is_tool_executor_where(where: str) -> bool:
    return bool(where) and where.startswith(_TOOL_EXECUTOR_WHERE_PREFIX)


def _error_log_path() -> Path:
    """解析全局错误日志文件路径。

    优先复用 AgentPaths（与 ~/.agent/ 的其它路径保持一致，含未来
    MINI_AGENT_HOME 等自定义 home 目录的演进）；若因循环导入等原因不可用，
    退化为直接读 MINI_AGENT_HOME 环境变量 / ~/.agent。
    """
    try:
        from mini_agent.storage.paths import AgentPaths

        return AgentPaths().global_error_log
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.errors._error_log_path')
        home_override = os.environ.get("MINI_AGENT_HOME")
        base = Path(home_override) if home_override else (Path.home() / ".agent")
        return base / "logs" / "error.jsonl"


def _get_file_logger() -> logging.Logger:
    global _FILE_LOGGER
    if _FILE_LOGGER is not None:
        return _FILE_LOGGER
    with _LOCK:
        if _FILE_LOGGER is not None:
            return _FILE_LOGGER
        path = _error_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("mini_agent._errors_sink")
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


def _infer_caller(depth: int = 3) -> str:
    """从调用栈里推断 '模块:行号'，用于 where 未显式传入时的兜底。"""
    try:
        frame = sys._getframe(depth)
        return f"{frame.f_globals.get('__name__', '?')}:{frame.f_lineno}"
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.errors._infer_caller')
        return "?"


def _safe_json(obj: Any) -> Any:
    """确保 extra 字段一定能被 json.dumps 序列化，不可序列化的值转为 repr。"""
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.errors._safe_json')
        if isinstance(obj, dict):
            return {str(k): _safe_json_value(v) for k, v in obj.items()}
        return repr(obj)


def _safe_json_value(v: Any) -> Any:
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.errors._safe_json_value')
        return repr(v)


def log_exception(
    exc: BaseException,
    *,
    where: str = "",
    extra: Optional[dict[str, Any]] = None,
    level: int = logging.ERROR,
    reraise: bool = False,
) -> None:
    """记录一条异常到全局错误日志文件（~/.agent/logs/error.jsonl）。

    这个函数本身绝不向调用方抛出新异常（除非 reraise=True 时重新抛出
    传入的 exc 本身）——日志系统故障不应该影响主流程。

    Args:
        exc: 捕获到的异常对象（`except Exception as e:` 里的 e）
        where: 发生位置的可读标识，建议用 "module.function" 形式，
            如 "tools.builtin.read_file"；不传时自动从调用栈推断。
        extra: 附加上下文，如 {"path": path, "session_id": sid}，
            会被 JSON 序列化，不可序列化字段自动转为字符串。
        level: 日志级别，默认 ERROR；无关紧要的降级路径可传 logging.WARNING。
        reraise: 若为 True，记录完成后重新抛出 exc（用于"记录后仍要中断"的场景）。
    """
    resolved_where = where or _infer_caller()

    # [next_doc/errors_tool_executor_log_toggle_plan.md] 开关关闭且这条
    # 记录来自 tool_executor 时，直接跳过——不构造 record/不格式化
    # traceback，省掉这条记录唯一有意义的开销；`reraise` 语义不受影响
    # （日志是否落盘跟"要不要把异常继续抛出去"是两件独立的事）。
    if not _SAVE_TOOL_EXECUTOR_ERROR_LOGS and _is_tool_executor_where(resolved_where):
        if reraise:
            raise exc
        return

    record: dict[str, Any] = {
        "ts": iso_local(),
        "ts_str": now_str(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "where": resolved_where,
        "exc_type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    }
    if extra:
        record["extra"] = _safe_json(extra)

    try:
        _get_file_logger().log(level, json.dumps(record, ensure_ascii=False))
    except Exception as log_err:  # 日志系统自身故障不能中断主流程
        from mini_agent.errors import log_exception
        log_exception(log_err, where='mini_agent.errors.log_exception')
        sys.stderr.write(
            f"[mini_agent.errors] 写入全局错误日志失败: {log_err!r}; "
            f"原始异常: {record.get('exc_type')}: {record.get('message')}\n"
        )

    if reraise:
        raise exc


class _RootErrorRouteHandler(logging.Handler):
    """挂到 root logger 上的转发 handler。

    项目里已经存在的 `logger.error(...)` / `logger.exception(...)` 调用
    不需要逐一改造：只要级别 >= ERROR，就会被这个 handler 额外转发一份到
    全局错误日志文件，原有的 handler/输出行为不受影响（不会重复消费同一条
    log record，也不会阻止其传播给其它 handler）。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # [next_doc/errors_tool_executor_log_toggle_plan.md] 同样的
            # 开关也覆盖这条路径——如果项目里未来某处改成用
            # `logging.getLogger("mini_agent.tool_executor")` 而不是直接
            # 调用 `log_exception()`，行为应该保持一致。
            if not _SAVE_TOOL_EXECUTOR_ERROR_LOGS and _is_tool_executor_where(record.name or ""):
                return
            payload: dict[str, Any] = {
                "ts": iso_local(),
                "ts_str": now_str(),
                "pid": os.getpid(),
                "thread": threading.current_thread().name,
                "where": f"{record.name}:{record.lineno}",
                "level": record.levelname,
                "message": record.getMessage(),
            }
            if record.exc_info:
                exc_type, exc_value, exc_tb = record.exc_info
                if exc_type is not None:
                    payload["exc_type"] = exc_type.__name__
                    payload["traceback"] = "".join(
                        traceback.format_exception(exc_type, exc_value, exc_tb)
                    )
            _get_file_logger().error(json.dumps(payload, ensure_ascii=False))
        except Exception as _mini_agent_exc:
            # handler 内部异常绝不能再抛出（logging 模块会用 self.handleError,
            # 这里直接吞掉即可，避免死循环）
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.errors._RootErrorRouteHandler.emit')
            pass


def install_global_error_logging() -> None:
    """安装进程级全局异常日志。应在程序入口调用一次（幂等，重复调用无副作用）。

    覆盖范围：
      - root logger 上 level>=ERROR 的所有 logging 调用（转发，不改变原行为）
      - 主线程未捕获异常（sys.excepthook）
      - 子线程未捕获异常（threading.excepthook，Python 3.8+）

    典型调用位置：cli/app.py::main() 最开始、daemon 启动入口、
    以及 HTTP API 的进程入口（api/server.py 启动 uvicorn 之前）。
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # 1) root logger 转发
    root_logger = logging.getLogger()
    if not any(isinstance(h, _RootErrorRouteHandler) for h in root_logger.handlers):
        route_handler = _RootErrorRouteHandler(level=logging.ERROR)
        root_logger.addHandler(route_handler)

    # 2) 主线程未捕获异常
    _prev_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        if exc_type is KeyboardInterrupt:
            _prev_excepthook(exc_type, exc_value, exc_tb)
            return
        try:
            log_exception(exc_value, where="uncaught.main_thread")
        finally:
            _prev_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    # 3) 子线程未捕获异常
    if hasattr(threading, "excepthook"):
        _prev_thread_excepthook = threading.excepthook

        def _thread_excepthook(args) -> None:
            thread_name = args.thread.name if args.thread is not None else "?"
            try:
                log_exception(
                    args.exc_value, where=f"uncaught.thread:{thread_name}"
                )
            finally:
                _prev_thread_excepthook(args)

        threading.excepthook = _thread_excepthook


def error_log_path() -> Path:
    """暴露给外部（如 `/debug` CLI 命令）查询当前全局错误日志文件的位置。"""
    return _error_log_path()


def error_log_stats(
    *,
    scope: str = "all",
    exclude_tool_executor: bool = False,
    top_n: int = 20,
) -> dict[str, Any]:
    """统计全局错误日志（~/.agent/logs/error.jsonl）中的错误类型分布。

    看板"错误日志"标签页的数据来源，逻辑与 evolution/daily_digest.py::
    _error_log_summary() 对齐（按 exc_type 计数），但支持更灵活的范围/过滤：

    Args:
        scope: "all" 统计全部记录；"today" 只统计本地时区当天的记录。
        exclude_tool_executor: 为 True 时，剔除 where 以
            "mini_agent.tool_executor" 开头的记录——这类记录（工具执行过程
            中被 `log_exception` 兜底记录的异常）占比往往极高，但多数是
            工具调用失败等预期内的降级路径，参考价值低，容易淹没其它真正
            需要关注的错误，因此提供开关单独剔除。
        top_n: exc_type / where 分布各自返回的最大条目数。

    Returns:
        {
          "total": 总记录数（scope 过滤后、exclude 过滤前）,
          "excluded": 被 exclude_tool_executor 剔除的记录数,
          "shown": 实际参与统计（剔除后）的记录数,
          "by_type": [{"name": exc_type, "count": n}, ...]  按 count 降序,
          "by_where": [{"name": where, "count": n}, ...]    按 count 降序,
          "scope": scope,
          "exclude_tool_executor": exclude_tool_executor,
          "log_path": 日志文件路径字符串,
          "log_exists": 日志文件是否存在,
        }
        解析失败的行会被跳过，不影响其余行的统计。
    """
    import datetime as _dt
    from collections import Counter

    log_path = _error_log_path()
    result: dict[str, Any] = {
        "total": 0,
        "excluded": 0,
        "shown": 0,
        "by_type": [],
        "by_where": [],
        "scope": scope,
        "exclude_tool_executor": exclude_tool_executor,
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
    }
    if not log_path.exists():
        return result

    today = _dt.date.today().isoformat()
    by_type: Counter = Counter()
    by_where: Counter = Counter()
    total = 0
    excluded = 0

    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue

                if scope == "today":
                    ts_str = record.get("ts", "")
                    try:
                        date = _dt.datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        ).date().isoformat()
                    except Exception:
                        continue
                    if date != today:
                        continue

                total += 1

                where = record.get("where", "unknown") or "unknown"
                if exclude_tool_executor and where.startswith("mini_agent.tool_executor"):
                    excluded += 1
                    continue

                exc_type = record.get("exc_type") or record.get("level") or "Unknown"
                by_type[exc_type] += 1
                by_where[where] += 1
    except Exception as _mini_agent_exc:
        log_exception(_mini_agent_exc, where="mini_agent.errors.error_log_stats")

    result["total"] = total
    result["excluded"] = excluded
    result["shown"] = total - excluded
    result["by_type"] = [
        {"name": name, "count": count} for name, count in by_type.most_common(top_n)
    ]
    result["by_where"] = [
        {"name": name, "count": count} for name, count in by_where.most_common(top_n)
    ]
    return result
