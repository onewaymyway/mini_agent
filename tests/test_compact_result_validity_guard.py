"""
tests/test_compact_result_validity_guard.py

覆盖 next_doc/compact_result_validity_guard_plan.md：

  P0 — compact_with_skills() 正常路径（run_turn）在 self.run_turn() 命中
       "畸形/半成品输出" 哨兵占位文本时，必须识别出该结果无效并退化到
       chunked compact 重新生成，不能把哨兵文本当真实摘要写进历史。
  P1 — TurnLoopMixin.last_turn_result_valid() 统一入口的基本行为。

真实场景复现（用户报告）：run_turn() 内部命中 result_sanity_check，把
final_text 替换成 "[系统提示：本轮未获得有效回复…]" 但仍然正常 return（不抛
异常），且该文本非空。旧代码里 compact_with_skills() 只判断
`if not result:`，于是把这段哨兵文本当成摘要，清空原始历史后写入
[session_resume, compact_summary(哨兵文本)] —— 原始历史永久丢失。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.agent.compaction import CompactionMixin


SENTINEL = (
    "[系统提示：本轮未获得有效回复，输出内容异常（可能是未闭合的"
    "工具调用标签或半成品文本），已作废，不应被当作真实结果使用。]"
)


class _FakeCompressCfg:
    project_root = None
    compact_precheck_enabled = False  # 跳过 token 预估，直接走"正常路径"
    decision_extraction_on_compact_with_skills_enabled = False


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


class _CompactHost(CompactionMixin):
    """
    最小宿主：只提供 compact_with_skills() 正常路径需要的属性/方法。

    run_turn_invalid: 控制 self.run_turn() 是否模拟"命中哨兵占位文本但
    不抛异常"这一真实场景（对应 turn_loop.py 里的 result_sanity_check）。
    """

    def __init__(self, run_turn_invalid: bool, chunked_result: str = "[chunked summary]"):
        self.cfg = _FakeCfg()
        self._history = [
            {"role": "user", "content": "帮我实现功能 X", "_type": "user_input"},
            {"role": "assistant", "content": "已实现功能 X", "_type": "assistant_reply"},
        ]
        self._hist = _FakeHist()
        self.skill_loader = None
        self._generating_compact_summary = False
        self._run_turn_invalid = run_turn_invalid
        self._chunked_result = chunked_result
        self.run_turn_calls = 0
        self.chunked_calls = 0
        # 模拟 turn_loop.py 的标志位
        self._last_turn_result_invalid = False

    # ── 被 compact_with_skills() 正常路径依赖的接口 ──────────────────────
    def run_turn(self, prompt):
        self.run_turn_calls += 1
        if self._run_turn_invalid:
            # 真实场景：result_sanity_check 命中后不抛异常，
            # 只置位标志 + 返回哨兵文本
            self._last_turn_result_invalid = True
            return SENTINEL
        self._last_turn_result_invalid = False
        return "## Goal\n实现功能 X\n\n## Work Completed\n已完成并通过测试"

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

    # 真实 Agent 通过 TurnLoopMixin 提供这个方法；测试宿主只继承了
    # CompactionMixin，这里手工提供同语义的实现（见 turn_loop.py 同名方法）。
    def last_turn_result_valid(self) -> bool:
        return not getattr(self, "_last_turn_result_invalid", False)


def test_valid_result_is_used_directly_no_chunked_fallback():
    host = _CompactHost(run_turn_invalid=False)
    result = host.compact_with_skills()
    assert result == "## Goal\n实现功能 X\n\n## Work Completed\n已完成并通过测试"
    assert host.chunked_calls == 0
    # 历史被正常替换为 [session_resume, compact_summary(真实摘要)]
    assert len(host._history) == 2
    assert host._history[1]["content"] == result


def test_invalid_sentinel_result_triggers_chunked_fallback_not_saved_as_summary():
    """核心回归用例：复现并验证修复了用户报告的问题。"""
    host = _CompactHost(run_turn_invalid=True, chunked_result="[real chunked summary]")
    result = host.compact_with_skills()

    # 哨兵文本绝不能作为最终摘要返回或写入历史
    assert SENTINEL not in result
    assert not any(SENTINEL in str(m.get("content", "")) for m in host._history)

    # 应该已经退化到 chunked compact 重新生成
    assert host.chunked_calls == 1
    assert result == "[real chunked summary]"
    assert host._history[1]["content"] == "[real chunked summary]"


def test_invalid_result_and_chunked_fallback_also_fails_aborts_without_touching_history():
    """两条路径都失败时：不能用脏数据覆盖历史，原样保留，返回空串表示本次压缩失败。"""
    host = _CompactHost(run_turn_invalid=True, chunked_result=None)
    original_history = list(host._history)

    result = host.compact_with_skills()

    assert result == ""
    assert host._history == original_history  # 历史完全未被破坏


def test_last_turn_result_valid_reflects_flag():
    host = _CompactHost(run_turn_invalid=False)
    assert host.last_turn_result_valid() is True
    host._last_turn_result_invalid = True
    assert host.last_turn_result_valid() is False
