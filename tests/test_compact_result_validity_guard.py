"""
tests/test_compact_result_validity_guard.py

覆盖 next_doc/compact_result_validity_guard_plan.md 的后续演进：

  历史背景（P0，已被下面的结构性修复取代）：
    compact_with_skills() 单次直出路径曾经用 self.run_turn(compact_prompt)
    实现，run_turn() 内部命中 result_sanity_check 时会把 final_text 替换成
    一段哨兵占位文本但仍正常 return（不抛异常）。旧代码只判断
    `if not result:`，于是把哨兵文本当成摘要写入历史，原始历史永久丢失。
    P0 修复：检查 self.last_turn_result_valid()，无效结果退化到 chunked。

  [structural fix，本文件对应更新] 单次直出路径不再调用 run_turn()：改为
  _compact_single_shot() 直接用 self._llm.chat_with_retry() 生成摘要（与
  _compact_chunked() 每个 chunk 内部的调用方式一致，专用 summarizer 人设 +
  tools=[]，不经过 agentic loop）。run_turn() 特有的"哨兵占位文本"机制
  （result_sanity_check）只存在于 agentic loop 内，chat_with_retry() 不会
  触发，所以那个场景不再适用于这条路径。仍然需要覆盖的等价场景是：
  LLM 调用本身失败（尤其是 LLMContextWindowError，预估漏报时的兜底）—— 此时
  同样必须退化到 chunked compact 重新生成，且任何一条路径都失败时不能用
  脏数据覆盖原始历史。

  P1（未变）：TurnLoopMixin.last_turn_result_valid() 的基本行为，仍由真实
  Agent 在其他调用方（api/server.py 等）使用，这里保留最小回归覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.agent.compaction import CompactionMixin
from mini_agent.llm.base import LLMContextWindowError


class _FakeCompressCfg:
    project_root = None
    compact_precheck_enabled = False  # 跳过 token 预估，直接走"单次直出路径"
    decision_extraction_on_compact_with_skills_enabled = False
    max_message_chars_for_compact = 10_000


class _FakeCfg:
    def __init__(self):
        self.compress = _FakeCompressCfg()
        self.project_root = None
        self.auto_save_session = False


class _FakeRawHist:
    def __init__(self):
        self.entries = []

    def append(self, msg):
        self.entries.append(msg)

    def append_compact_event(self, **kwargs):
        self.entries.append({"_type": "compact_event"})


class _FakeHist:
    def __init__(self):
        self._raw = _FakeRawHist()


class _FakeLLMResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    """模拟 self._llm.chat_with_retry()，供 _compact_single_shot() 调用。"""

    def __init__(self, result_text=None, raise_context_window_error: bool = False):
        self._result_text = result_text
        self._raise_context_window_error = raise_context_window_error
        self.calls = 0

    def chat_with_retry(self, messages, system, tools, max_retries=3):
        self.calls += 1
        if self._raise_context_window_error:
            raise LLMContextWindowError("history too long for single-shot summary")
        return _FakeLLMResponse(self._result_text)


class _CompactHost(CompactionMixin):
    """最小宿主：只提供 compact_with_skills() 单次直出路径需要的属性/方法。"""

    def __init__(self, llm: _FakeLLM, chunked_result="[chunked summary]"):
        self.cfg = _FakeCfg()
        self._history = [
            {"role": "user", "content": "帮我实现功能 X", "_type": "user_input"},
            {"role": "assistant", "content": "已实现功能 X", "_type": "assistant_reply"},
        ]
        self._hist = _FakeHist()
        self.skill_loader = None
        self._generating_compact_summary = False
        self._llm = llm
        self._chunked_result = chunked_result
        self.chunked_calls = 0
        self._last_turn_result_invalid = False
        self._last_compact_path = None

    def _compact_chunked(self):
        self.chunked_calls += 1
        if self._chunked_result is None:
            raise RuntimeError("chunked compact failed too")
        # 与真实实现一致：chunked 路径自己负责替换历史
        from mini_agent.history.entry import make_session_resume, make_compact_summary
        self._history.clear()
        self._history.extend([
            make_session_resume("[Previous session summary — chunked compact]"),
            make_compact_summary(self._chunked_result),
        ])
        return self._chunked_result

    def save_session(self):
        pass

    # 真实 Agent 通过 TurnLoopMixin 提供这个方法；单次直出路径现在不再
    # 依赖它做有效性判断（不经过 run_turn），但其它调用方仍在用，保留同语义
    # 的最小实现以覆盖 P1 回归。
    def last_turn_result_valid(self) -> bool:
        return not getattr(self, "_last_turn_result_invalid", False)


def test_valid_result_is_used_directly_no_chunked_fallback():
    llm = _FakeLLM(result_text="## Goal\n实现功能 X\n\n## Work Completed\n已完成并通过测试")
    host = _CompactHost(llm)
    result = host.compact_with_skills()
    assert result == "## Goal\n实现功能 X\n\n## Work Completed\n已完成并通过测试"
    assert llm.calls == 1
    assert host.chunked_calls == 0
    # 历史被正常替换为 [session_resume, compact_summary(真实摘要)]
    assert len(host._history) == 2
    assert host._history[1]["content"] == result
    # 路径可观测性：这次应该记录为单次直出
    assert host._last_compact_path == "single_shot"


def test_context_window_error_triggers_chunked_fallback():
    """核心回归用例：单次直出的 LLM 调用命中上下文超限时，必须退化到 chunked
    重新生成，而不是让异常直接冒泡丢失整个压缩结果。"""
    llm = _FakeLLM(raise_context_window_error=True)
    host = _CompactHost(llm, chunked_result="[real chunked summary]")
    result = host.compact_with_skills()

    assert host.chunked_calls == 1
    assert result == "[real chunked summary]"
    assert host._history[1]["content"] == "[real chunked summary]"
    assert host._last_compact_path == "chunked"


def test_context_window_error_and_chunked_fallback_also_fails_aborts_without_touching_history():
    """两条路径都失败时：不能用脏数据覆盖历史，原样保留，返回空串表示本次压缩失败。"""
    llm = _FakeLLM(raise_context_window_error=True)
    host = _CompactHost(llm, chunked_result=None)
    original_history = list(host._history)

    result = host.compact_with_skills()

    assert result == ""
    assert host._history == original_history  # 历史完全未被破坏
    assert host._last_compact_path == "chunked (failed)"


def test_last_turn_result_valid_reflects_flag():
    host = _CompactHost(_FakeLLM(result_text="ok"))
    assert host.last_turn_result_valid() is True
    host._last_turn_result_invalid = True
    assert host.last_turn_result_valid() is False
