"""
repl_input.py — 交互式输入

使用 prompt_toolkit 写到 stdout，StatusBar 写到 stderr，两者完全独立。
不需要 pause/resume，没有终端控制序列冲突。

功能：
  - 光标正常显示，输入字符实时可见
  - 方向键编辑，Home/End，Ctrl-A/E
  - ↑↓ 翻历史（进程内）
  - Tab 补全斜杠命令
  - Ctrl-C 清空当前行继续
  - Ctrl-D 退出
"""

from __future__ import annotations

import sys
from typing import Optional

_SLASH_COMMANDS = [
    "/help", "/clear",
    "/skills", "/skill on", "/skill off",
    "/stats", "/verbose",
    "/model", "/compact", "/prompts",
    "/tasks", "/tasks dashboard", "/tasks log", "/tasks cancel",
    "/tasks cancel-all", "/tasks workers",
    "/concurrency", "/concurrency tasks", "/concurrency llm", "/cc",
    "/provider", "/provider list", "/provider switch",
    "/exit", "/quit",
]


class REPLInput:
    """
    prompt_toolkit 输入封装。
    失败时自动降级为带颜色提示符的 input()。
    """

    def __init__(self) -> None:
        self._session = None
        self._use_ptk = self._init_session()

    def _init_session(self) -> bool:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.completion import WordCompleter

            self._session = PromptSession(
                history=InMemoryHistory(),
                auto_suggest=AutoSuggestFromHistory(),
                completer=WordCompleter(
                    _SLASH_COMMANDS,
                    sentence=True,
                    ignore_case=True,
                ),
                complete_while_typing=True,
                enable_history_search=True,
                mouse_support=False,
                # 关键：输入/输出都走 stdout，不指定特殊 output
                # prompt_toolkit 自己会处理好终端原始模式
            )
            return True
        except Exception:
            return False

    def prompt(self) -> str:
        """
        显示提示符，读取并返回一行输入（已 strip）。
        KeyboardInterrupt → 上层 catch 并 continue
        EOFError → 上层 catch 并退出
        """
        if self._use_ptk and self._session is not None:
            return self._ptk_prompt()
        return self._fallback_prompt()

    def _ptk_prompt(self) -> str:
        try:
            from prompt_toolkit.formatted_text import HTML
            from prompt_toolkit.styles import Style

            result = self._session.prompt(
                HTML("<b><ansgreen>You</ansgreen></b><ansicyan> ❯ </ansicyan>"),
                style=Style.from_dict({
                    "ansgreen": "bold #00cc00",
                    "ansicyan": "bold #00cccc",
                }),
            )
            return (result or "").strip()
        except KeyboardInterrupt:
            raise
        except EOFError:
            raise
        except Exception:
            self._use_ptk = False
            return self._fallback_prompt()

    def _fallback_prompt(self) -> str:
        """纯终端降级方案（无 prompt_toolkit）。"""
        sys.stdout.write("\n\033[1;32mYou\033[0m\033[1;36m ❯ \033[0m")
        sys.stdout.flush()
        return input().strip()


_instance: Optional[REPLInput] = None


def get_repl_input() -> REPLInput:
    global _instance
    if _instance is None:
        _instance = REPLInput()
    return _instance
