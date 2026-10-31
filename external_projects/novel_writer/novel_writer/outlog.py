"""novel_writer/outlog.py — 子进程输出的单写者管道泵。

问题（见 run.log 覆盖事件）：父进程与子进程共享同一个日志文件句柄时，二者各自维护文件位置，
子进程的输出会覆盖父进程追加的日志行。

做法：子进程 stdout 接管道；本模块的线程是这份管道的唯一读取者，也是对应 agent.log 的唯一写入者。
run.log 只由 state.log_line 写入。每次 agent 调用的完整输出落在它自己的 agent.log 中。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import IO, Optional

AGENT_LOG_NAME = "agent.log"


class LinePump:
    """把一个二进制流逐行写入文件（后台线程）。line_count 记录已写入的行数。"""

    def __init__(self, stream: IO[bytes], path: Path) -> None:
        self._stream = stream
        self.path = Path(path)
        self.line_count = 0
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, name="agent-log-pump", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as f:
                for raw in iter(self._stream.readline, b""):
                    f.write(raw)
                    f.flush()
                    self.line_count += 1
        except BaseException as exc:  # noqa: BLE001 - 日志线程不能拖垮调用方
            self._error = exc

    def join(self, timeout: float = 10.0) -> bool:
        """等待读完（管道关闭）。返回 False 表示超时仍未结束（例如孙进程仍占着管道）。"""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @property
    def error(self) -> Optional[BaseException]:
        return self._error
