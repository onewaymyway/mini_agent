"""novel_writer/outlog.py — 子进程输出的单写者管道泵。

问题（见 run.log 覆盖事件）：父进程与子进程共享同一个日志文件句柄时，二者各自维护文件位置，
子进程的输出会覆盖父进程追加的日志行。

做法：子进程 stdout 接管道；本模块的线程是这份管道的唯一读取者，也是对应 agent.log 的唯一写入者。
run.log 只由 state.log_line 写入。每次 agent 调用的完整输出落在它自己的 agent.log 中。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import IO, Any, Callable, Optional

AGENT_LOG_NAME = "agent.log"

# 实时镜像到 run.log 的上限：超出后只在 run.log 留一行提示，完整输出始终在 agent.log。
MIRROR_MAX_LINES = 4000
MIRROR_MAX_CHARS = 240

# 行接收器按线程保存：FrameworkInvoker 在创建泵时读取它，泵线程之后直接调用，不经过 invoker 的参数。
_sink_local = threading.local()


def set_line_sink(sink: Optional[Callable[[str], None]]) -> None:
    _sink_local.sink = sink


def current_line_sink() -> Optional[Callable[[str], None]]:
    return getattr(_sink_local, "sink", None)


def run_log_sink(ws: Any) -> Callable[[str], None]:
    """返回一个把 agent 输出镜像进 run.log 的接收器（只在父进程的泵线程里调用，经 state.log_line 单写者写入）。"""
    from novel_writer import state as st

    count = {"n": 0}

    def sink(text: str) -> None:
        count["n"] += 1
        if count["n"] > MIRROR_MAX_LINES:
            if count["n"] == MIRROR_MAX_LINES + 1:
                st.log_line(ws, f"  │ …（输出过长，后续内容见本次调用的 agent.log）")
            return
        if len(text) > MIRROR_MAX_CHARS:
            text = text[:MIRROR_MAX_CHARS] + "…"
        st.log_line(ws, f"  │ {text}")

    return sink


class LinePump:
    """把一个二进制流逐行写入文件（后台线程）。line_count 记录已写入的行数。

    on_line：可选的行接收器，每写入一行后调用一次（文本已去掉行尾换行）。接收器的异常被吞掉，
    不能拖垮 agent.log 的写入。
    """

    def __init__(self, stream: IO[bytes], path: Path,
                 on_line: Optional[Callable[[str], None]] = None) -> None:
        self._stream = stream
        self.path = Path(path)
        self.line_count = 0
        self._on_line = on_line
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
                    if self._on_line is not None:
                        try:
                            self._on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                        except Exception:  # noqa: BLE001 - 镜像失败不影响 agent.log
                            pass
        except BaseException as exc:  # noqa: BLE001 - 日志线程不能拖垮调用方
            self._error = exc

    def join(self, timeout: float = 10.0) -> bool:
        """等待读完（管道关闭）。返回 False 表示超时仍未结束（例如孙进程仍占着管道）。"""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @property
    def error(self) -> Optional[BaseException]:
        return self._error
