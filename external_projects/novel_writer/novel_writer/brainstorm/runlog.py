"""捕获 agent 的全部过程输出（工具调用、工具结果、提示、警告……），供对话区显示。

框架的 agent 通过 `mini_agent.ui.renderer.R.print_*` 输出过程信息。`Capture` 在上下文内临时包装这些方法：
记录事件后仍调用原函数，因此终端输出不变。退出上下文时恢复原状。

框架里有两种调用方式：`R.print_xxx`（R 是命名空间对象）和 `import mini_agent.ui.renderer as R` 后的
`R.print_xxx`（此时 R 是模块本身，tool_executor / turn_loop 就是这样）。所以两处都要包装，否则工具调用与
工具结果在界面上看不到，只在终端出现。

注意：包装是进程级的，同一时间只允许一个 Capture（用锁保证）。捕获期间进程内其它线程的 agent 输出也会被记录。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict

CAPTURED = (
    "print_tool_call", "print_tool_result", "print_tool_error",
    "print_info", "print_warning", "print_error", "print_success",
    "print_stats", "print_skill_loaded", "print_retry_banner",
    "print_markdown", "print_debug_block", "print_assistant_prefix",
)
RESULT_LIMIT = 20000
_MISSING = object()
_LOCK = threading.Lock()


def describe(name: str, args: tuple) -> Dict[str, str]:
    """把一次 print 调用整理成 {kind, text}。不截断工具结果（上限很大，只为防止异常大的输出）。"""
    kind = name[len("print_"):] if name.startswith("print_") else name
    if name == "print_tool_call" and len(args) >= 2:
        try:
            arg_text = json.dumps(args[1], ensure_ascii=False)
        except (TypeError, ValueError):
            arg_text = str(args[1])
        return {"kind": "tool_call", "text": f"{args[0]}  {arg_text}"}
    if name == "print_tool_result" and len(args) >= 2:
        return {"kind": "tool_result", "text": f"{args[0]} →\n{str(args[1])[:RESULT_LIMIT]}"}
    if name == "print_tool_error" and len(args) >= 2:
        return {"kind": "tool_error", "text": f"{args[0]}：{args[1]}"}
    if name == "print_debug_block" and len(args) >= 2:
        return {"kind": "debug", "text": f"{args[0]}\n{args[1]}"}
    return {"kind": kind, "text": " ".join(str(a) for a in args if a is not None)}


class Capture:
    """with Capture(sink): ...  —— sink 接收 {kind, text}。"""

    def __init__(self, sink: Callable[[Dict[str, str]], None]):
        self._sink = sink
        self._saved: Dict[str, Any] = {}
        self._saved_module: Dict[str, Any] = {}
        self._target = None
        self._module = None

    def __enter__(self) -> "Capture":
        from mini_agent.ui import renderer as RD

        _LOCK.acquire()
        try:
            self._target = RD.R
            self._module = RD
            for name in CAPTURED:
                orig = getattr(self._target, name, None)
                if orig is not None:
                    self._saved[name] = self._target.__dict__.get(name, _MISSING)
                    setattr(self._target, name, self._wrap(name, orig))
                # 模块级函数：框架里多数调用方是 `import mini_agent.ui.renderer as R`，走的是这里
                mod_orig = getattr(RD, name, None)
                if mod_orig is not None:
                    self._saved_module[name] = mod_orig
                    setattr(RD, name, self._wrap(name, mod_orig))
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _wrap(self, name: str, orig: Callable) -> Callable:
        sink = self._sink

        def wrapper(*args, **kwargs):
            try:
                sink(describe(name, args))
            except Exception:  # noqa: BLE001 - 记录失败不能影响 agent 本身
                pass
            return orig(*args, **kwargs)

        return wrapper

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            for name, prev in self._saved.items():
                if prev is _MISSING:
                    try:
                        delattr(self._target, name)
                    except AttributeError:
                        pass
                else:
                    setattr(self._target, name, prev)
            for name, prev in self._saved_module.items():
                setattr(self._module, name, prev)
            self._saved.clear()
            self._saved_module.clear()
        finally:
            if _LOCK.locked():
                _LOCK.release()
