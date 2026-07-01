"""
tests/test_cognitive_anchor.py — [具身改进 C3] 认知锚点文件测试

覆盖：
  1. AgentPaths.workdir_cognitive_anchor 路径正确
  2. Agent._save_cognitive_anchor：基于最近 history 生成内容并写入文件
     （通过 fake LLM client 验证调用了正确的 prompt 渲染 + 写入路径）
  3. Agent._maybe_load_cognitive_anchor：存在锚点文件时注入 system_extra，
     并将原文件归档（重命名），避免重复注入
  4. 禁用开关 cognitive_anchor_enabled=False 时两个方法均 no-op
  5. 空 history / 无 LLM 时 _save_cognitive_anchor 静默跳过

实现说明：Agent.__init__ 涉及大量组件初始化（LLM client pool / tool
registry / session manager 等），为了单测 _save_cognitive_anchor /
_maybe_load_cognitive_anchor 这两个方法，不构造完整 Agent 实例，而是用
一个具备同名属性（cfg / _llm / _history）的轻量对象，以未绑定方法的方式
调用 `Agent._save_cognitive_anchor(fake_self)` —— 这两个方法只读取
self.cfg / self._llm / self._history，不依赖其他组件，这种方式是安全的。
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from mini_agent.agent import Agent
from mini_agent.storage.paths import AgentPaths


class _FakeLLMResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    """记录被调用的参数，返回固定文本。"""

    def __init__(self, text: str = "## 当时在想什么\n测试中的占位内容"):
        self._text = text
        self.last_call = None

    def chat_with_retry(self, *, messages, system, tools, max_retries=2):
        self.last_call = {"messages": messages, "system": system, "tools": tools}
        return _FakeLLMResponse(self._text)


def _make_fake_agent(project_root: Path, history=None, llm=None, anchor_enabled=True):
    cfg = types.SimpleNamespace(
        project_root=project_root,
        cognitive_anchor_enabled=anchor_enabled,
        system_extra="",
    )
    fake = types.SimpleNamespace(
        cfg=cfg,
        _llm=llm,
        _history=history if history is not None else [],
    )
    return fake


class TestAgentPathsCognitiveAnchor(unittest.TestCase):
    def test_path_under_workdir_dir(self):
        tmp = Path(tempfile.mkdtemp())
        paths = AgentPaths(tmp)
        self.assertEqual(
            paths.workdir_cognitive_anchor,
            paths.workdir_dir / "cognitive_anchor.md",
        )


class TestSaveCognitiveAnchor(unittest.TestCase):
    def test_writes_file_with_llm_generated_content(self):
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM(text="## 当时在想什么\n正在重构 tool_executor")
        history = [
            {"role": "user", "content": "帮我重构一下 tool_executor"},
            {"role": "assistant", "content": "好的，我先看看现有结构"},
        ]
        fake = _make_fake_agent(tmp, history=history, llm=llm)

        Agent._save_cognitive_anchor(fake)

        paths = AgentPaths(tmp)
        self.assertTrue(paths.workdir_cognitive_anchor.exists())
        content = paths.workdir_cognitive_anchor.read_text(encoding="utf-8")
        self.assertIn("重构 tool_executor", content)

        # 确认 system prompt 使用了 cognitive_anchor 模板（而不是别的模板）
        self.assertIn("cognitive anchor", llm.last_call["system"].lower())

    def test_noop_when_disabled(self):
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM()
        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=llm, anchor_enabled=False)

        Agent._save_cognitive_anchor(fake)

        paths = AgentPaths(tmp)
        self.assertFalse(paths.workdir_cognitive_anchor.exists())
        self.assertIsNone(llm.last_call)

    def test_noop_when_no_llm(self):
        tmp = Path(tempfile.mkdtemp())
        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=None)

        Agent._save_cognitive_anchor(fake)  # 不应抛异常

        paths = AgentPaths(tmp)
        self.assertFalse(paths.workdir_cognitive_anchor.exists())

    def test_noop_when_history_empty(self):
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM()
        fake = _make_fake_agent(tmp, history=[], llm=llm)

        Agent._save_cognitive_anchor(fake)

        self.assertIsNone(llm.last_call)

    def test_noop_when_llm_returns_empty_text(self):
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM(text="   ")
        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=llm)

        Agent._save_cognitive_anchor(fake)

        paths = AgentPaths(tmp)
        self.assertFalse(paths.workdir_cognitive_anchor.exists())

    def test_exception_in_llm_call_is_swallowed(self):
        tmp = Path(tempfile.mkdtemp())

        class _BrokenLLM:
            def chat_with_retry(self, **kwargs):
                raise RuntimeError("network down")

        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=_BrokenLLM())

        # 不应向上抛出异常
        Agent._save_cognitive_anchor(fake)


class TestMaybeLoadCognitiveAnchor(unittest.TestCase):
    def test_injects_fragment_and_archives_file(self):
        tmp = Path(tempfile.mkdtemp())
        paths = AgentPaths(tmp)
        paths.workdir_cognitive_anchor.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_cognitive_anchor.write_text(
            "## 当时在想什么\n正在调试一个并发 bug", encoding="utf-8"
        )

        fake = _make_fake_agent(tmp)
        Agent._maybe_load_cognitive_anchor(fake)

        self.assertIn("正在调试一个并发 bug", fake.cfg.system_extra)
        self.assertIn("认知锚点", fake.cfg.system_extra)
        # 原文件应已被归档（重命名），不再存在于原路径
        self.assertFalse(paths.workdir_cognitive_anchor.exists())
        # 但归档文件应该存在
        archived = list(paths.workdir_dir.glob("cognitive_anchor.*.md"))
        self.assertEqual(len(archived), 1)

    def test_noop_when_no_anchor_file(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp)
        Agent._maybe_load_cognitive_anchor(fake)
        self.assertEqual(fake.cfg.system_extra, "")

    def test_noop_when_disabled(self):
        tmp = Path(tempfile.mkdtemp())
        paths = AgentPaths(tmp)
        paths.workdir_cognitive_anchor.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_cognitive_anchor.write_text("内容", encoding="utf-8")

        fake = _make_fake_agent(tmp, anchor_enabled=False)
        Agent._maybe_load_cognitive_anchor(fake)

        self.assertEqual(fake.cfg.system_extra, "")
        # 禁用时不应该归档/删除原文件
        self.assertTrue(paths.workdir_cognitive_anchor.exists())

    def test_does_not_inject_empty_file(self):
        tmp = Path(tempfile.mkdtemp())
        paths = AgentPaths(tmp)
        paths.workdir_cognitive_anchor.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_cognitive_anchor.write_text("   \n  ", encoding="utf-8")

        fake = _make_fake_agent(tmp)
        Agent._maybe_load_cognitive_anchor(fake)

        self.assertEqual(fake.cfg.system_extra, "")

    def test_appends_to_existing_system_extra(self):
        tmp = Path(tempfile.mkdtemp())
        paths = AgentPaths(tmp)
        paths.workdir_cognitive_anchor.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_cognitive_anchor.write_text("一些恢复内容", encoding="utf-8")

        fake = _make_fake_agent(tmp)
        fake.cfg.system_extra = "已有的环境感知内容"
        Agent._maybe_load_cognitive_anchor(fake)

        self.assertIn("已有的环境感知内容", fake.cfg.system_extra)
        self.assertIn("一些恢复内容", fake.cfg.system_extra)


if __name__ == "__main__":
    unittest.main()
