"""
tests/test_objective_executor_kanban_tracks_r4.py

覆盖 `next_doc/kanban_and_autonomy_improvement_plan.md` 第十一轮延续的内容
（见 `next_doc/kanban_and_autonomy_improvement_implementation_record.md`
"第十一轮"一节）：

- Track E 边界情况修复：`_locate_entries_in_list()`（从 `_locate_step_
  history_entries()` 里抽出的通用版本，可直接传入任意条目列表）在
  active history 找不到匹配时，`get_objective_step_trace()` 会改用
  `hist_mgr.raw_history.entries` 兜底查找——因为 raw history 只追加、
  永不被 compact 压缩，能找回 compact 之前的 step 记录。

只测试 `_locate_entries_in_list()` 这个纯函数本身（`_locate_step_history_
entries()` 的行为不变，已由 r3 覆盖），不重复测 trace 端点的 HTTP 层
（HTTP 层依赖 pydantic/FastAPI，环境里可能缺依赖，端点级别的行为已经在
`get_objective_step_trace()` 的 docstring 里据实说明）。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks_r4.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestLocateEntriesInList:
    """`_locate_entries_in_list()`：`_locate_step_history_entries()` 的通用版本。"""

    def test_behaves_identically_to_locate_step_history_entries(self):
        from mini_agent.api.routes import _locate_entries_in_list, _locate_step_history_entries

        history = [
            {"_type": "user_input", "content": "步骤1"},
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "第一次尝试"}]},
            {"_type": "user_input", "content": "步骤1"},
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "最新一次尝试"}]},
            {"_type": "user_input", "content": "步骤2"},
        ]

        class _FakeHistManager:
            def __init__(self, h):
                self.history = h

        via_hist_mgr = _locate_step_history_entries(_FakeHistManager(history), "步骤1")
        via_list = _locate_entries_in_list(history, "步骤1")
        assert via_hist_mgr == via_list == history[2:4]

    def test_returns_none_when_not_found(self):
        from mini_agent.api.routes import _locate_entries_in_list

        assert _locate_entries_in_list([{"_type": "user_input", "content": "别的"}], "找不到") is None

    def test_empty_list_returns_none(self):
        from mini_agent.api.routes import _locate_entries_in_list

        assert _locate_entries_in_list([], "任何内容") is None


class TestCompactFallbackToRawHistory:
    """模拟 `get_objective_step_trace()` 里 active history 未命中、退化查
    raw_history 的核心判定逻辑：active history 找不到时用
    `_locate_entries_in_list(raw_history_entries, ...)` 兜底，且能标记
    `from_raw_history=True`。这里直接复现路由函数里的判定片段，不依赖
    FastAPI/pydantic，专注测试"何时该走兜底路径"这条逻辑本身。
    """

    @staticmethod
    def _resolve(active_history, raw_history_entries, submitted_message):
        from mini_agent.api.routes import _locate_entries_in_list

        raw_entries = _locate_entries_in_list(active_history, submitted_message)
        from_raw_history = False
        if raw_entries is None:
            raw_entries = _locate_entries_in_list(raw_history_entries, submitted_message)
            if raw_entries is not None:
                from_raw_history = True
        return raw_entries, from_raw_history

    def test_prefers_active_history_when_present(self):
        active = [
            {"_type": "user_input", "content": "步骤1"},
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "当前"}]},
        ]
        raw = active + [{"_type": "compact_event", "content": "..."}]
        entries, from_raw = self._resolve(active, raw, "步骤1")
        assert from_raw is False
        assert entries == active

    def test_falls_back_to_raw_history_after_compact(self):
        # 模拟 compact 之后：active history 里已经不含这一步的记录了，
        # 但 raw_history（只追加、不压缩）里还保留着完整记录。
        active_after_compact = [
            {"_type": "compact_summary", "content": "……对话已压缩……"},
            {"_type": "user_input", "content": "步骤2"},
        ]
        raw_history_entries = [
            {"_type": "user_input", "content": "步骤1"},
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "压缩前的原始记录"}]},
            {"_type": "compact_event", "content": "compact happened"},
            {"_type": "user_input", "content": "步骤2"},
        ]
        entries, from_raw = self._resolve(active_after_compact, raw_history_entries, "步骤1")
        assert from_raw is True
        assert entries == raw_history_entries[0:3]

    def test_neither_active_nor_raw_history_has_it_returns_none(self):
        active = [{"_type": "user_input", "content": "无关内容"}]
        raw = [{"_type": "user_input", "content": "也无关"}]
        entries, from_raw = self._resolve(active, raw, "步骤1")
        assert entries is None
        assert from_raw is False
