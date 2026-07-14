"""
tests/test_cognitive_anchor.py — [具身改进 C3 + 认知锚点 session 化] 认知锚点测试

覆盖：
  1. Agent._save_cognitive_anchor：基于最近 history 生成内容并写入
     **当前 session 自己目录下**的 cognitive_anchor.md
     （通过 fake LLM client 验证调用了正确的 prompt 渲染 + 写入路径）
  2. Agent._maybe_load_cognitive_anchor(session_id)：存在锚点文件时注入
     system_extra，并将原文件归档（重命名），避免重复注入
  3. 禁用开关 cognitive_anchor_enabled=False 时两个方法均 no-op
  4. 空 history / 无 LLM 时 _save_cognitive_anchor 静默跳过
  5. **session 隔离**（本次改造的核心诉求）：session-A 留下的锚点，
     resume session-B 时不会被读到；只有 resume 回 session-A 本身才会读到

实现说明：Agent.__init__ 涉及大量组件初始化（LLM client pool / tool
registry / session manager 等），为了单测 _save_cognitive_anchor /
_maybe_load_cognitive_anchor 这两个方法，不构造完整 Agent 实例，而是用
一个具备同名属性（cfg / _llm / _history / _session_mgr / _session）的
轻量对象，以未绑定方法的方式调用
`Agent._save_cognitive_anchor(fake)` / `Agent._maybe_load_cognitive_anchor(fake, session_id)`
—— 这两个方法只读取这几个属性，不依赖其他组件，这种方式是安全的。
`_cognitive_anchor_path` 同理，以未绑定方法方式调用。
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from mini_agent.agent import Agent


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


class _FakeSessionMgr:
    """最小可用的 SessionManager 替身，只提供 session_dir 属性。"""

    def __init__(self, sessions_root: Path):
        self.session_dir = sessions_root


def _make_fake_agent(
    project_root: Path,
    history=None,
    llm=None,
    anchor_enabled=True,
    session_id: str = "sess0001",
    with_session=True,
):
    cfg = types.SimpleNamespace(
        project_root=project_root,
        cognitive_anchor_enabled=anchor_enabled,
        system_extra="",
    )
    sessions_root = project_root / ".agent" / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    fake = types.SimpleNamespace(
        cfg=cfg,
        _llm=llm,
        _history=history if history is not None else [],
        _session_mgr=_FakeSessionMgr(sessions_root),
        _session=types.SimpleNamespace(id=session_id) if with_session else None,
    )
    # _save_cognitive_anchor / _maybe_load_cognitive_anchor 内部会调用
    # self._cognitive_anchor_path(...)——fake 是 SimpleNamespace，没有这个
    # 方法会导致 AttributeError（被两个方法自身的 try/except 静默吞掉，
    # 测试表现为"莫名其妙什么都没发生"），这里手动绑定一份等价实现。
    fake._cognitive_anchor_path = lambda sid: Agent._cognitive_anchor_path(fake, sid)
    return fake


def _anchor_path_for(fake, session_id: str) -> Path:
    return Agent._cognitive_anchor_path(fake, session_id)


class TestCognitiveAnchorPath(unittest.TestCase):
    def test_path_under_session_dir(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="abc12345")
        expected = tmp / ".agent" / "sessions" / "abc12345" / "cognitive_anchor.md"
        self.assertEqual(_anchor_path_for(fake, "abc12345"), expected)


class TestSaveCognitiveAnchor(unittest.TestCase):
    def test_writes_file_under_current_session_dir(self):
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM(text="## 当时在想什么\n正在重构 tool_executor")
        history = [
            {"role": "user", "content": "帮我重构一下 tool_executor"},
            {"role": "assistant", "content": "好的，我先看看现有结构"},
        ]
        fake = _make_fake_agent(tmp, history=history, llm=llm, session_id="sessA")

        Agent._save_cognitive_anchor(fake)

        anchor_path = _anchor_path_for(fake, "sessA")
        self.assertTrue(anchor_path.exists())
        content = anchor_path.read_text(encoding="utf-8")
        self.assertIn("重构 tool_executor", content)

        # 确认 system prompt 使用了 cognitive_anchor 模板（而不是别的模板）
        self.assertIn("cognitive anchor", llm.last_call["system"].lower())

    def test_noop_when_disabled(self):
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM()
        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=llm, anchor_enabled=False)

        Agent._save_cognitive_anchor(fake)

        self.assertFalse(_anchor_path_for(fake, fake._session.id).exists())
        self.assertIsNone(llm.last_call)

    def test_noop_when_no_llm(self):
        tmp = Path(tempfile.mkdtemp())
        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=None)

        Agent._save_cognitive_anchor(fake)  # 不应抛异常

        self.assertFalse(_anchor_path_for(fake, fake._session.id).exists())

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

        self.assertFalse(_anchor_path_for(fake, fake._session.id).exists())

    def test_exception_in_llm_call_is_swallowed(self):
        tmp = Path(tempfile.mkdtemp())

        class _BrokenLLM:
            def chat_with_retry(self, **kwargs):
                raise RuntimeError("network down")

        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=_BrokenLLM())

        # 不应向上抛出异常
        Agent._save_cognitive_anchor(fake)

    def test_noop_when_no_session(self):
        """没有 self._session（理论上不应发生，但防御性兜底）时不应报错。"""
        tmp = Path(tempfile.mkdtemp())
        llm = _FakeLLM()
        history = [{"role": "user", "content": "hello"}]
        fake = _make_fake_agent(tmp, history=history, llm=llm, with_session=False)

        Agent._save_cognitive_anchor(fake)  # 不应抛异常
        self.assertIsNone(llm.last_call)


class TestMaybeLoadCognitiveAnchor(unittest.TestCase):
    def test_injects_fragment_and_archives_file(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA")
        anchor_path = _anchor_path_for(fake, "sessA")
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text("## 当时在想什么\n正在调试一个并发 bug", encoding="utf-8")

        Agent._maybe_load_cognitive_anchor(fake, "sessA")

        self.assertIn("正在调试一个并发 bug", fake.cfg.system_extra)
        self.assertIn("认知锚点", fake.cfg.system_extra)
        # 原文件应已被归档（重命名），不再存在于原路径
        self.assertFalse(anchor_path.exists())
        # 但归档文件应该存在，且仍在同一 session 目录下
        archived = list(anchor_path.parent.glob("cognitive_anchor.*.md"))
        self.assertEqual(len(archived), 1)

    def test_noop_when_no_anchor_file(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA")
        Agent._maybe_load_cognitive_anchor(fake, "sessA")
        self.assertEqual(fake.cfg.system_extra, "")

    def test_noop_when_disabled(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA", anchor_enabled=False)
        anchor_path = _anchor_path_for(fake, "sessA")
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text("内容", encoding="utf-8")

        Agent._maybe_load_cognitive_anchor(fake, "sessA")

        self.assertEqual(fake.cfg.system_extra, "")
        # 禁用时不应该归档/删除原文件
        self.assertTrue(anchor_path.exists())

    def test_does_not_inject_empty_file(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA")
        anchor_path = _anchor_path_for(fake, "sessA")
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text("   \n  ", encoding="utf-8")

        Agent._maybe_load_cognitive_anchor(fake, "sessA")

        self.assertEqual(fake.cfg.system_extra, "")

    def test_appends_to_existing_system_extra(self):
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA")
        anchor_path = _anchor_path_for(fake, "sessA")
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text("一些恢复内容", encoding="utf-8")
        fake.cfg.system_extra = "已有的环境感知内容"

        Agent._maybe_load_cognitive_anchor(fake, "sessA")

        self.assertIn("已有的环境感知内容", fake.cfg.system_extra)
        self.assertIn("一些恢复内容", fake.cfg.system_extra)

    # ── session 隔离（本次改造的核心诉求）────────────────────────────────────

    def test_anchor_from_session_a_not_visible_when_loading_session_b(self):
        """session-A 被打断留下锚点后，resume 一个完全不相关的 session-B
        不应该读到 session-A 的锚点——这正是本次"认知锚点 session 化"要
        解决的串味问题。"""
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA")

        # session-A 自己保存了一份锚点
        anchor_a = _anchor_path_for(fake, "sessA")
        anchor_a.parent.mkdir(parents=True, exist_ok=True)
        anchor_a.write_text("session-A 的思路：正在调试 dispatcher", encoding="utf-8")

        # resume 到一个不相关的 session-B
        Agent._maybe_load_cognitive_anchor(fake, "sessB")

        self.assertEqual(fake.cfg.system_extra, "")  # 没有读到 session-A 的锚点
        self.assertTrue(anchor_a.exists())  # session-A 的锚点原封不动，未被误消费

    def test_anchor_from_session_a_visible_when_loading_session_a_itself(self):
        """resume 回锚点真正归属的那个 session 时，应该正常读到。"""
        tmp = Path(tempfile.mkdtemp())
        fake = _make_fake_agent(tmp, session_id="sessA")

        anchor_a = _anchor_path_for(fake, "sessA")
        anchor_a.parent.mkdir(parents=True, exist_ok=True)
        anchor_a.write_text("session-A 的思路：正在调试 dispatcher", encoding="utf-8")

        Agent._maybe_load_cognitive_anchor(fake, "sessA")

        self.assertIn("正在调试 dispatcher", fake.cfg.system_extra)
        self.assertFalse(anchor_a.exists())  # 已被消费并归档


if __name__ == "__main__":
    unittest.main()
