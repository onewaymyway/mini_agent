"""novel_writer/outlog.py — 子进程输出的单写者管道泵。

问题（见 run.log 覆盖事件）：父进程与子进程共享同一个日志文件句柄时，二者各自维护文件位置，
子进程的输出会覆盖父进程追加的日志行。

做法：子进程 stdout 接管道；本模块的线程是这份管道的唯一读取者，也是对应 agent.log 的唯一写入者。
run.log 只由 state.log_line 写入。每次 agent 调用的完整输出落在它自己的 agent.log 中。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Optional

AGENT_LOG_NAME = "agent.log"

# 实时镜像到 run.log 的上限（按镜像的行数计）：超出后只在 run.log 留一行提示，完整输出始终在 agent.log。
MIRROR_MAX_LINES = 300
MIRROR_MAX_CHARS = 400

# 结构化事件协议：子进程里框架调用工具时，invoke_child 把完整的工具调用 / 结果以一行 JSON 输出，
# 行首带这个标记。父进程只认标记行（它们来自框架的真实调用，不依赖框架的终端排版）。
EVENT_PREFIX = "@@NW_EVT@@ "

# 告警行（print_warning / print_error）：⚠ / ✗ + 两空格 + 正文。
_ALERT = re.compile(r"^[⚠✗]\s{2}\S.*$")

# 每个工具最主要的入参（按优先级），其余标量参数以 k=v 附在后面。
_PRIMARY_KEYS = {
    "bash": ("command",),
    "read_file": ("path", "file_path"), "write_file": ("path", "file_path"),
    "create_file": ("path", "file_path"), "delete_file": ("path", "file_path"),
    "patch_file": ("path", "file_path"), "list_dir": ("path",),
    "glob": ("pattern", "query"), "grep": ("pattern", "query"), "web_search": ("query",),
    "create_plan": ("goal",), "start_task": ("task_id",), "complete_task": ("task_id",),
    "fail_task": ("task_id",), "add_task": ("title",),
}
# 内容类入参（正文、补丁文本）只显示长度，不把正文塞进日志。
_BODY_KEYS = {"content", "new_str", "old_str", "text", "body", "diff", "patch"}


def is_progress_line(text: str) -> bool:
    return text.startswith(EVENT_PREFIX) or bool(_ALERT.match(text))


def _one_line(value: Any, limit: int = MIRROR_MAX_CHARS) -> str:
    text = " ⏎ ".join(part.strip() for part in str(value).splitlines() if part.strip())
    return text if len(text) <= limit else text[:limit] + "…"


def _args_text(name: str, inp: Dict[str, Any]) -> str:
    parts: List[str] = []
    primary = _PRIMARY_KEYS.get(name, ())
    for key in primary:
        if inp.get(key):
            parts.append(_one_line(inp[key]))
            break
    for key, val in inp.items():
        if key in primary:
            continue
        if key in _BODY_KEYS and isinstance(val, str):
            parts.append(f"{key}=<{len(val)} 字符>")
        elif isinstance(val, (str, int, float, bool)) and val != "":
            parts.append(f"{key}={_one_line(val, 80)}")
        elif isinstance(val, (list, dict)) and val:
            parts.append(f"{key}=<{len(val)} 项>")
    return "，".join(parts) if parts else ""


def format_event(ev: Dict[str, Any]) -> str:
    """把一条结构化事件格式化成 run.log 中的一行（不含时间戳和前缀）。"""
    if ev.get("kind") == "call":
        name = str(ev.get("name", ""))
        inp = ev.get("input") if isinstance(ev.get("input"), dict) else {}
        args = _args_text(name, inp)
        head = f"{ev.get('icon', '🔧')} {name}"
        return f"{head}  {args}" if args else head
    if ev.get("kind") == "result":
        first = next((ln.strip() for ln in str(ev.get("first", "")).splitlines() if ln.strip()), "")
        size = int(ev.get("len", 0) or 0)
        if size == 0:
            return "   ↳ 无返回内容"
        return f"   ↳ 返回 {size} 字符" + (f" · {_one_line(first, 200)}" if first else "")
    return ""


def emit_event(kind: str, **payload: Any) -> None:
    """子进程里调用：把一条事件写到标准输出（由父进程的泵收进 agent.log 和 run.log）。"""
    import json as _json
    import sys as _sys

    line = EVENT_PREFIX + _json.dumps({"kind": kind, **payload}, ensure_ascii=False, default=str)
    _sys.stdout.write("\n" + line + "\n")
    _sys.stdout.flush()


class _ProgressMirror:
    """逐行接收 agent 输出，只把结构化工具事件与告警行写进 run.log（经 state.log_line 单写者写入）。"""

    def __init__(self, ws: Any) -> None:
        from novel_writer import state as st

        self._st = st
        self._ws = ws
        self._count = 0

    def __call__(self, text: str) -> None:
        if text.startswith(EVENT_PREFIX):
            try:
                import json as _json

                shown = format_event(_json.loads(text[len(EVENT_PREFIX):]))
            except ValueError:
                shown = ""
        elif _ALERT.match(text):
            shown = text
        else:
            return
        if shown:
            self._emit(shown)

    def _emit(self, text: str) -> None:
        self._count += 1
        if self._count > MIRROR_MAX_LINES:
            if self._count == MIRROR_MAX_LINES + 1:
                self._st.log_line(self._ws, "  │ …（进度行过多，后续见本次调用的 agent.log）")
            return
        self._st.log_line(self._ws, f"  │ {text}")

    def finish(self) -> None:
        """调用结束（保留接口，当前无需补输出）。"""


def run_log_sink(ws: Any) -> Callable[[str], None]:
    """返回一个把 agent 进度镜像进 run.log 的接收器（只在父进程的泵线程里调用，经 state.log_line 单写者写入）。"""
    return _ProgressMirror(ws)


# 行接收器按线程保存：FrameworkInvoker 在创建泵时读取它，泵线程之后直接调用，不经过 invoker 的参数。
_sink_local = threading.local()


def set_line_sink(sink: Optional[Callable[[str], None]]) -> None:
    _sink_local.sink = sink


def current_line_sink() -> Optional[Callable[[str], None]]:
    return getattr(_sink_local, "sink", None)


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
