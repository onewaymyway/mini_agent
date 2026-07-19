"""
tests/test_wiki_slash_completer.py — /wiki 命令行补全提示回归测试

背景：
    《wiki 知识库改进计划 · 下一阶段》给 `/wiki` 新增了 `gap-scan`/
    `fallback-cleanup` 两个子命令（`cli/commands/wiki.py`），但
    `ui/terminal.py::_COMMANDS`（驱动 REPL 里 Tab 补全 / `/` 弹出列表的
    命令定义表）里从未注册过 `/wiki` 这个顶级命令本身——不影响命令
    实际执行（`handle_wiki_cmd` 独立解析 `args`），但用户在交互式终端里
    敲 `/w` 或 `/wiki ` 时不会看到任何补全提示，新加的两个子命令更是
    无从发现。

本文件验证：
    1. `/wiki` 已经出现在 `_COMMANDS` 里。
    2. `/wiki` 的子命令列表覆盖 `cli/commands/wiki.py::handle_wiki_cmd`
       实际支持的全部子命令（含本轮新增的 `gap-scan`/`fallback-cleanup`）。
    3. `gap-scan`/`fallback-cleanup` 各自的选项（`--max-results`/
       `--dispatch`/`--days`）也在补全候选里，而不只是子命令名本身。
"""

from __future__ import annotations

import re

import pytest


def _sub_name(entry):
    return entry if isinstance(entry, str) else entry[0]


def _sub_children(entry):
    return entry[1] if isinstance(entry, tuple) else []


class TestWikiSlashCompleterRegistration:
    def test_wiki_command_registered(self):
        from mini_agent.ui.terminal import _COMMANDS

        names = [c[0] for c in _COMMANDS]
        assert "/wiki" in names, "/wiki 未注册进 _COMMANDS，交互式终端不会弹出补全提示"

    def test_wiki_subcommands_match_handler(self):
        from mini_agent.ui.terminal import _COMMANDS

        wiki_entry = next(c for c in _COMMANDS if c[0] == "/wiki")
        completer_subs = {_sub_name(s) for s in wiki_entry[2]}

        # 从 handle_wiki_cmd 源码里反推它实际 elif 分支支持的子命令，
        # 保证补全表和真实处理逻辑不会出现"补全里有、处理不了"或
        # "能处理、但补全不出来"的不一致。
        import inspect

        from mini_agent.cli.commands import wiki as wiki_module

        src = inspect.getsource(wiki_module.handle_wiki_cmd)
        handled_subs = set(re.findall(r'sub == "([\w-]+)"', src))

        assert handled_subs, "未能从 handle_wiki_cmd 源码解析出任何子命令，测试本身可能需要更新"
        missing = handled_subs - completer_subs
        assert not missing, f"这些 /wiki 子命令能被处理但没有补全提示: {missing}"

    def test_new_subcommands_present_with_options(self):
        from mini_agent.ui.terminal import _COMMANDS

        wiki_entry = next(c for c in _COMMANDS if c[0] == "/wiki")
        subs_by_name = {_sub_name(s): _sub_children(s) for s in wiki_entry[2]}

        assert "gap-scan" in subs_by_name
        gap_scan_opts = {_sub_name(o) for o in subs_by_name["gap-scan"]}
        assert {"--max-results", "--dispatch"} <= gap_scan_opts

        assert "fallback-cleanup" in subs_by_name
        cleanup_opts = {_sub_name(o) for o in subs_by_name["fallback-cleanup"]}
        assert "--days" in cleanup_opts

        assert "lifecycle-scan" in subs_by_name
        lifecycle_opts = {_sub_name(o) for o in subs_by_name["lifecycle-scan"]}
        assert "--days" in lifecycle_opts


class TestWikiSlashCompleterBehavior:
    """通过 _build_slash_completer() 实际跑一遍补全，而不只是核对表结构。"""

    def _get_completions(self, text: str):
        prompt_toolkit = pytest.importorskip("prompt_toolkit")
        from prompt_toolkit.document import Document

        from mini_agent.ui.terminal import _build_slash_completer

        completer = _build_slash_completer()
        if completer is None:
            pytest.skip("prompt_toolkit completion 模块不可用")
        doc = Document(text=text, cursor_position=len(text))
        return list(completer.get_completions(doc, None))

    def test_slash_w_suggests_wiki(self):
        completions = self._get_completions("/w")
        texts = {c.text for c in completions}
        assert "/wiki" in texts or any("wiki" in t for t in texts)

    def test_wiki_space_suggests_new_subcommands(self):
        completions = self._get_completions("/wiki ")
        texts = {c.text for c in completions}
        assert "gap-scan" in texts
        assert "fallback-cleanup" in texts

    def test_wiki_gap_scan_space_suggests_options(self):
        completions = self._get_completions("/wiki gap-scan ")
        texts = {c.text for c in completions}
        assert "--max-results" in texts
        assert "--dispatch" in texts
