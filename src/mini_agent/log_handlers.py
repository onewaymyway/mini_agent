"""
log_handlers.py — 多进程友好的轮转日志 handler

背景（next_doc/log_rotation_multiprocess_safe_plan.md）：daemon 进程与交互式
CLI 进程会同时向 ``~/.agent/logs/error.jsonl`` 写日志。标准库的
``RotatingFileHandler`` 在 handler 创建后会一直持有文件句柄；Windows 上 Python
打开的文件不带 ``FILE_SHARE_DELETE``，只要任一进程持有句柄，其他进程轮转时的
``os.rename`` 就会抛 ``PermissionError: [WinError 32]``。该异常被 logging 模块的
``Handler.handleError`` 吞掉、只在 stderr 打一行 "--- Logging error ---"，
那条日志就丢了，``errors.py`` 里的兜底逻辑也不会被触发。

本模块的 ``SafeRotatingFileHandler`` 做三件事：

1. **写入时才打开，写完立即关闭**：持有文件的时间缩短到单次写入（毫秒级），
   不再长期占着文件，其他进程轮转几乎不会撞上。
2. **轮转遇到 PermissionError 时短暂重试**，仍失败则**跳过本次轮转、继续追加**
   （文件会暂时超过 maxBytes，但记录不丢），并通过 ``on_failure`` 回调转存一条
   记录到 ``logging_failures.log``。轮转失败后进入冷却期（默认 30 秒），冷却期内
   不再重试，避免文件一直超限时每条日志都额外阻塞。
3. **轮转失败不破坏备份链**：先把当前文件改名为临时文件（唯一可能被别的进程
   阻止的一步），成功后才移动旧备份。标准库的 ``doRollover`` 在最后一步失败时，
   前面已经删除/移动过备份，重试会再丢一代。

``on_failure`` 回调签名：``(where: str, err: BaseException, record_text: str | None)``。
回调内部的任何异常都会被吞掉，绝不影响主流程。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from typing import Callable, Optional

FailureCallback = Callable[[str, BaseException, Optional[str]], None]


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """写入时打开、写完即关，轮转失败不丢日志的 RotatingFileHandler。"""

    def __init__(
        self,
        filename,
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: Optional[str] = "utf-8",
        *,
        rotate_retries: int = 3,
        rotate_retry_delay: float = 0.05,
        rotate_cooldown: float = 30.0,
        open_retries: int = 3,
        open_retry_delay: float = 0.02,
        on_failure: Optional[FailureCallback] = None,
    ) -> None:
        # delay=True：构造时不打开文件；本类也从不把流保存在 self.stream 上
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding, delay=True)
        self.rotate_retries = max(0, rotate_retries)
        self.rotate_retry_delay = rotate_retry_delay
        self.rotate_cooldown = rotate_cooldown
        self.open_retries = max(0, open_retries)
        self.open_retry_delay = open_retry_delay
        self.on_failure = on_failure
        self._rotate_blocked_until = 0.0  # time.monotonic() 截止时间

    # ---- 失败上报（绝不抛出） ----
    def _report(self, where: str, err: BaseException, text: Optional[str]) -> None:
        cb = self.on_failure
        if cb is None:
            return
        try:
            cb(where, err, text)
        except Exception:
            pass

    # ---- 轮转 ----
    def _current_size(self) -> int:
        try:
            return os.stat(self.baseFilename).st_size
        except OSError:
            return 0

    def _need_rollover(self, incoming: int) -> bool:
        if self.maxBytes <= 0:
            return False
        return self._current_size() + incoming >= self.maxBytes

    def _rotate_safely(self, incoming: int) -> None:
        """尝试轮转；失败则保持原状（不丢任何备份），并上报。"""
        if time.monotonic() < self._rotate_blocked_until:
            return
        tmp = f"{self.baseFilename}.{os.getpid()}.rolling"
        last_err: Optional[BaseException] = None
        for attempt in range(self.rotate_retries + 1):
            # 重试前复查：别的进程可能已经完成了轮转，此时不应再轮转一个新的小文件
            if not self._need_rollover(incoming):
                return
            try:
                os.replace(self.baseFilename, tmp)
                last_err = None
                break
            except FileNotFoundError:
                return  # 文件已被别的进程轮转走
            except OSError as e:  # 含 PermissionError / WinError 32
                last_err = e
                if attempt < self.rotate_retries:
                    time.sleep(self.rotate_retry_delay)
        if last_err is not None:
            # 当前文件被其他进程占用：跳过本次轮转，继续追加，进入冷却
            self._rotate_blocked_until = time.monotonic() + self.rotate_cooldown
            self._report(
                f"{self.__class__.__name__}.rotate({self.baseFilename})", last_err, None
            )
            return
        # 当前文件已移开，后续步骤出错也不会影响继续写入新文件
        try:
            if self.backupCount > 0:
                for i in range(self.backupCount - 1, 0, -1):
                    src = f"{self.baseFilename}.{i}"
                    if os.path.exists(src):
                        os.replace(src, f"{self.baseFilename}.{i + 1}")
                os.replace(tmp, f"{self.baseFilename}.1")
            else:
                os.remove(tmp)
        except OSError as e:
            self._rotate_blocked_until = time.monotonic() + self.rotate_cooldown
            self._report(
                f"{self.__class__.__name__}.shift_backups({self.baseFilename})", e, None
            )

    # ---- 写入 ----
    def _append(self, text: str) -> None:
        last_err: Optional[BaseException] = None
        for attempt in range(self.open_retries + 1):
            try:
                with open(self.baseFilename, "a", encoding=self.encoding, errors=getattr(self, "errors", None)) as fh:
                    fh.write(text)
                return
            except OSError as e:
                last_err = e
                if attempt < self.open_retries:
                    time.sleep(self.open_retry_delay)
        assert last_err is not None
        raise last_err

    def emit(self, record: logging.LogRecord) -> None:
        # Handler.handle() 已经持有 self.lock，这里无需再加锁（同进程线程安全）
        msg: Optional[str] = None
        try:
            msg = self.format(record)
            text = msg + self.terminator
            if self.maxBytes > 0:
                incoming = len(text.encode(self.encoding or "utf-8", "replace"))
                if self._need_rollover(incoming):
                    self._rotate_safely(incoming)
            self._append(text)
        except Exception as e:
            # 写入彻底失败：转存到 logging_failures.log，再交给标准库的 handleError
            self._report(f"{self.__class__.__name__}.emit({self.baseFilename})", e, msg)
            self.handleError(record)


def _default_on_failure(where: str, err: BaseException, record_text: Optional[str]) -> None:
    """默认上报：转存到 errors.py 的 logging_failures.log（延迟导入避免循环依赖）。"""
    import json

    from mini_agent.errors import _log_internal_failure

    original = None
    if record_text:
        try:
            parsed = json.loads(record_text)
            original = parsed if isinstance(parsed, dict) else {"message": record_text}
        except Exception:
            original = {"message": record_text}
    _log_internal_failure(where, err, original=original)


def build_safe_rotating_handler(path, max_bytes: int, backup_count: int) -> SafeRotatingFileHandler:
    """项目内三处日志 sink 共用的构造入口。"""
    return SafeRotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        on_failure=_default_on_failure,
    )
