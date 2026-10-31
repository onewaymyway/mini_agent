"""novel_writer/outlog.py — 子进程输出的单写者管道泵。

问题（见 run.log 覆盖事件）：父进程与子进程共享同一个日志文件句柄时，二者各自维护文件位置，
子进程的输出会覆盖父进程追加的日志行。

做法：子进程 stdout 接管道；本模块的线程是这份管道的唯一读取者，也是对应 agent.log 的唯一写入者。
run.log 只由 state.log_line 写入。每次 agent 调用的完整输出落在它自己的 agent.log 中。
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Optional

AGENT_LOG_NAME = "agent.log"

# 实时镜像到 run.log 的上限（按镜像的行数计）：超出后只在 run.log 留一行提示，完整输出始终在 agent.log。
MIRROR_MAX_LINES = 300
MIRROR_MAX_CHARS = 160

# 镜像只保留「可读的进度」：框架 verbose 模式下的工具调用标题（非 ASCII 图标 + 小写工具名 + 两空格 + 摘要），
# 以及 print_warning / print_error 的告警行（⚠ / ✗ + 两空格 + 正文）。
# 工具图标都是非 ASCII 字符，所以堆栈行（如「in execute_all」）不会被误认为工具调用。
# 助手正文、流式推理、工具返回内容都是多行长文本，一律不进 run.log（它们在 agent.log 中）。
_TOOL_HEADER = re.compile(r"^(?P<icon>[^\x00-\x7f]{1,3})\s+(?P<name>[a-z][a-z0-9_]*)(?:\s{2}(?P<rest>.*))?$")
_ALERT = re.compile(r"^[⚠✗]\s{2}\S.*$")

# 与框架 ui/renderer.py 的 _tool_summary 取值规则保持一致（框架渲染标题时入参可能还是空的，见下）。
_SUMMARY_KEYS = {
    "bash": ("command",),
    "read_file": ("path", "file_path"), "write_file": ("path", "file_path"),
    "create_file": ("path", "file_path"), "delete_file": ("path", "file_path"),
    "patch_file": ("path", "file_path"), "list_dir": ("path",),
    "glob": ("pattern", "query"), "grep": ("pattern", "query"), "web_search": ("query",),
    "create_plan": ("goal",), "start_task": ("task_id",), "complete_task": ("task_id",),
    "fail_task": ("task_id",), "add_task": ("title",),
}
_DEFAULT_KEYS = ("path", "file_path", "pattern", "query", "command", "goal", "task_id", "title")
_JSON_MAX_LINES = 400   # 标题之后等待入参 JSON 的最大行数，超过则放弃补全，原样输出标题


def is_progress_line(text: str) -> bool:
    return bool(_TOOL_HEADER.match(text) or _ALERT.match(text))


def _summary_from_input(name: str, inp: Any) -> str:
    if not isinstance(inp, dict):
        return ""
    for key in _SUMMARY_KEYS.get(name, _DEFAULT_KEYS):
        val = inp.get(key)
        if val:
            return str(val)
    return ""


# 行接收器按线程保存：FrameworkInvoker 在创建泵时读取它，泵线程之后直接调用，不经过 invoker 的参数。
_sink_local = threading.local()


def set_line_sink(sink: Optional[Callable[[str], None]]) -> None:
    _sink_local.sink = sink


def current_line_sink() -> Optional[Callable[[str], None]]:
    return getattr(_sink_local, "sink", None)


class _ProgressMirror:
    """逐行接收 agent 输出，只把进度行写进 run.log。

    标题带摘要时直接输出；标题没有摘要（入参在标题之后以 JSON 打印）时，读完紧随的 JSON，
    取出关键字段补全摘要后再输出一行。
    """

    def __init__(self, ws: Any) -> None:
        from novel_writer import state as st

        self._st = st
        self._ws = ws
        self._count = 0
        self._pending: Optional[Dict[str, str]] = None   # 等待入参 JSON 的标题
        self._json: List[str] = []

    # —— 输出 ——
    def _emit(self, text: str) -> None:
        self._count += 1
        if self._count > MIRROR_MAX_LINES:
            if self._count == MIRROR_MAX_LINES + 1:
                self._st.log_line(self._ws, "  │ …（进度行过多，后续见本次调用的 agent.log）")
            return
        if len(text) > MIRROR_MAX_CHARS:
            text = text[:MIRROR_MAX_CHARS] + "…"
        self._st.log_line(self._ws, f"  │ {text}")

    def _emit_header(self, head: Dict[str, str], summary: str = "") -> None:
        rest = summary or head.get("rest") or ""
        self._emit(f"{head['icon']} {head['name']}" + (f"  {rest}" if rest else ""))

    def _flush_pending_as_is(self) -> None:
        if self._pending is not None:
            self._emit_header(self._pending)
        self._pending, self._json = None, []

    # —— 输入 ——
    def __call__(self, text: str) -> None:
        if self._pending is not None:
            if not self._json and not text.startswith("{"):
                self._flush_pending_as_is()          # 标题后没有入参 JSON：按原样输出
            else:
                self._json.append(text)
                if text.strip() == "}" or len(self._json) >= _JSON_MAX_LINES:
                    self._finish_pending()
                return
        m = _TOOL_HEADER.match(text)
        if m:
            head = m.groupdict()
            if head.get("rest"):
                self._emit_header(head)              # 标题已带摘要，之后的入参 JSON 不再镜像
                self._pending = None
            else:
                self._pending = head                 # 等待入参 JSON 补全摘要
                self._json = []
            return
        if _ALERT.match(text):
            self._emit(text)

    def _finish_pending(self) -> None:
        head = self._pending or {}
        summary = ""
        try:
            inp = json.loads("\n".join(self._json))
            summary = _summary_from_input(head.get("name", ""), inp)
        except ValueError:
            summary = ""
        self._emit_header(head, summary)
        self._pending, self._json = None, []

    def finish(self) -> None:
        """调用结束：仍在等待入参的标题按原样输出。"""
        self._flush_pending_as_is()


def run_log_sink(ws: Any) -> Callable[[str], None]:
    """返回一个把 agent 进度镜像进 run.log 的接收器（只在父进程的泵线程里调用，经 state.log_line 单写者写入）。"""
    return _ProgressMirror(ws)


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
