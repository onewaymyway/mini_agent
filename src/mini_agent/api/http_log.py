"""
api/http_log.py — HTTP 请求访问日志

背景（next_doc/http_server_blocking_call_guard_plan.md）：uvicorn 默认单进程
单事件循环，一旦某个路由内部出现不 await 的同步阻塞调用，会把整个事件循环
卡住——同一进程里其他所有请求（包括跟它无关的看板轮询、心跳）全部要排队
等它结束。事后排查这类"http server 长时间卡住又自己恢复"的问题，光靠客户端
日志里的 ConnectionResetError 定位不到根因（那只是恢复后处理一个已经死掉的
连接时的收尾报错，是结果不是原因），需要服务端自己留下"每个请求实际耗时
多久"的记录，才能反查出当时具体是哪个路由把事件循环拖住了。

本模块提供一个独立的访问日志中间件：每条请求落一行 JSON 到专门的日志文件
（默认与 mini_agent.errors 的全局错误日志同目录），记录时间戳、pid、线程名、
method、path、query、client_ip、status_code、耗时(ms)。是否记录 / 记录到
哪个文件均可通过 HttpConfig（access_log_enabled / access_log_path）配置。

请求开始和结束各落一行（type: "request_start" / "request_end"），而不是只在
结束时落一行：如果进程在处理某个请求期间被杀掉/彻底卡死到无法恢复，日志里
最后一条孤零零的 request_start（没有对应的 request_end）本身就能直接指向
"卡在哪个请求上"，不需要靠耗时数字反推。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import threading
import time
from pathlib import Path
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from mini_agent.time_utils import iso_local, now_str

_LOCK = threading.Lock()
_MAX_BYTES = 10 * 1024 * 1024  # 10MB，与 errors.py 的全局错误日志保持一致
_BACKUP_COUNT = 5

# 单次请求耗时超过这个阈值时额外打一个 slow=true 标记，方便直接
# grep 出可疑的慢请求，不用逐行读 duration_ms 再肉眼比对。
_SLOW_THRESHOLD_MS = 3000.0

# path -> Logger，同一路径复用同一个 logger/handler，避免每次请求都重新
# open 文件句柄；不同 path（用户显式配置了别的位置）各自独立一份。
_LOGGERS: dict[str, logging.Logger] = {}


def _default_log_path() -> Path:
    """未显式配置 access_log_path 时的默认落盘位置——与全局错误日志同目录。"""
    try:
        from mini_agent.storage.paths import AgentPaths

        return AgentPaths().global_http_access_log
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.api.http_log._default_log_path")
        home_override = os.environ.get("MINI_AGENT_HOME")
        base = Path(home_override) if home_override else (Path.home() / ".agent")
        return base / "logs" / "http_access.jsonl"


def resolve_log_path(configured_path: str = "") -> Path:
    """解析最终使用的日志文件路径：显式配置优先，否则退回默认位置。"""
    if configured_path:
        return Path(configured_path).expanduser()
    return _default_log_path()


def _get_logger(log_path: Path) -> logging.Logger:
    key = str(log_path)
    logger = _LOGGERS.get(key)
    if logger is not None:
        return logger
    with _LOCK:
        logger = _LOGGERS.get(key)
        if logger is not None:
            return logger
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"mini_agent._http_access_sink.{key}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        if not logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        _LOGGERS[key] = logger
        return logger


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _write_record(logger: logging.Logger, record: dict) -> None:
    try:
        logger.info(json.dumps(record, ensure_ascii=False))
    except Exception as _mini_agent_exc:
        # 日志本身失败绝不能影响正常请求处理，落一条到全局错误日志兜底即可。
        from mini_agent.errors import log_exception

        log_exception(_mini_agent_exc, where="mini_agent.api.http_log._write_record")


class HttpAccessLogMiddleware(BaseHTTPMiddleware):
    """记录每个请求的开始/结束到独立的 JSONL 日志文件。

    通过 enabled 开关控制是否记录；关闭时 dispatch 直接透传，不做任何额外
    I/O，开销为零。启用时开销是每请求两次 JSON 序列化 + 文件 append，
    可忽略不计（RotatingFileHandler 内部已有缓冲/锁，不会成为新的阻塞点——
    它是纯本地文件写入，跟"同步调用远程 LLM"完全是两回事）。
    """

    def __init__(self, app, enabled: bool = True, log_path: str = "") -> None:
        super().__init__(app)
        self._enabled = bool(enabled)
        self._log_path = log_path
        self._logger: Optional[logging.Logger] = None
        if self._enabled:
            self._logger = _get_logger(resolve_log_path(log_path))

    async def dispatch(self, request: Request, call_next):
        if not self._enabled or self._logger is None:
            return await call_next(request)

        pid = os.getpid()
        thread_name = threading.current_thread().name
        method = request.method
        path = request.url.path
        query = str(request.url.query or "")
        client_ip = _client_ip(request)
        # 用单调时钟量耗时，wall clock（ts）只用于展示；避免系统时间被
        # NTP 调整时把耗时算错。
        start_monotonic = time.monotonic()

        _write_record(self._logger, {
            "type": "request_start",
            "ts": iso_local(), "ts_str": now_str(),
            "pid": pid, "thread": thread_name,
            "method": method, "path": path, "query": query,
            "client_ip": client_ip,
        })

        status_code = None
        error_text = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as e:
            error_text = f"{type(e).__name__}: {e}"
            raise
        finally:
            duration_ms = round((time.monotonic() - start_monotonic) * 1000, 1)
            record = {
                "type": "request_end",
                "ts": iso_local(), "ts_str": now_str(),
                "pid": pid, "thread": thread_name,
                "method": method, "path": path, "query": query,
                "client_ip": client_ip,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "slow": duration_ms >= _SLOW_THRESHOLD_MS,
            }
            if error_text:
                record["error"] = error_text
            _write_record(self._logger, record)
