"""
tests/test_objective_executor_kanban_tracks_r3.py

覆盖 `next_doc/kanban_and_autonomy_improvement_plan.md` 第三轮延续的内容
（见 `next_doc/kanban_and_autonomy_improvement_implementation_record.md`
"第三轮"一节）：

- Track G 深化：`ObjectiveExecutor` 新增的 `artifacts_from_tools_fn` 优先于
  `artifacts_parse_fn`；前者返回非空时直接采用，返回空列表时退化到正则
  解析 `[ARTIFACTS]` 标记；两者都未提供/都拿不到时 `step.artifacts` 保持
  空列表（向后兼容）。
- `api/routes.py::_extract_tool_write_paths`：从一段模拟的 active history
  原始记录里，按工具名单（write_file/create_file/patch_file/
  patch_file_simple）+ 常见路径参数 key 提取真实路径，忽略其他工具调用
  （比如 bash/read_file），按出现顺序去重。
- `api/routes.py::_locate_step_history_entries`：能通过 submitted_message
  精确定位到某个 step 对应的历史片段（提取为公用函数后，Track E 的
  trace 端点与 Track G 深化的产出物提取复用同一份定位逻辑，这里只测试
  这个公用函数本身，不重复测 trace 端点的 HTTP 层）。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks_r3.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.objective_executor import ObjectiveExecutor


class _FakeGoalNode:
    def __init__(self, id_: str, title: str = "测试目标"):
        self.id = id_
        self.title = title
        self.progress_notes = ""


def _make_executor(tmp_path, submit_fn=None, **kwargs) -> ObjectiveExecutor:
    paths = AgentPaths(tmp_path)
    submitted_ids = iter(f"turn_{i}" for i in range(1000))

    def _default_submit(message, initiator, meta):
        return next(submitted_ids)

    return ObjectiveExecutor(
        paths=paths,
        submit_fn=submit_fn or _default_submit,
        llm_decompose_fn=lambda obj: ["第一步", "第二步"],
        **kwargs,
    )


# ── artifacts_from_tools_fn 优先于 artifacts_parse_fn ───────────────────────

class TestToolBasedArtifactsPriority:
    def test_tool_based_result_used_when_non_empty(self, tmp_path):
        """两个回调都提供时，tool-based 结果非空则直接采用，完全不落到
        正则解析（用一个如果被调用就抛异常的 artifacts_parse_fn 验证
        "完全不调用"这一点，而不只是"结果相同"）。"""

        def _artifacts_from_tools(submitted_message):
            return ["src/a.py", "src/a.py", "src/b.py"]  # 故意含重复，验证由上层去重也不炸

        def _artifacts_parse_should_not_be_called(result_summary):
            raise AssertionError("不应该走到正则解析这条退化路径")

        oe = _make_executor(
            tmp_path,
            artifacts_from_tools_fn=_artifacts_from_tools,
            artifacts_parse_fn=_artifacts_parse_should_not_be_called,
        )
        obj = _FakeGoalNode("obj_1")
        exec_id = oe.start(obj)
        assert exec_id is not None

        oe.on_turn_done("turn_0", "已完成第一步 [ARTIFACTS] should/not/be/used.py")

        ex = oe.get_execution(exec_id)
        assert ex.steps[0].artifacts == ["src/a.py", "src/a.py", "src/b.py"]

    def test_falls_back_to_regex_when_tool_based_empty(self, tmp_path):
        """tool-based 回调返回空列表（这一步没调用过写文件工具，比如纯
        查询类步骤）时，退化到 artifacts_parse_fn 的正则解析结果。"""

        def _artifacts_from_tools(submitted_message):
            return []

        def _artifacts_parse(result_summary):
            import re
            m = re.search(r"\[ARTIFACTS\]\s*(.+)", result_summary)
            if not m:
                return []
            return [p.strip() for p in m.group(1).split(",") if p.strip()]

        oe = _make_executor(
            tmp_path,
            artifacts_from_tools_fn=_artifacts_from_tools,
            artifacts_parse_fn=_artifacts_parse,
        )
        obj = _FakeGoalNode("obj_2")
        exec_id = oe.start(obj)
        assert exec_id is not None

        oe.on_turn_done("turn_0", "已完成第一步 [ARTIFACTS] docs/readme.md")

        ex = oe.get_execution(exec_id)
        assert ex.steps[0].artifacts == ["docs/readme.md"]

    def test_both_empty_or_missing_keeps_artifacts_empty(self, tmp_path):
        """两个回调都没提供（默认行为）——不影响 step 正常完成/推进，
        artifacts 保持空列表，与改造前完全一致。"""
        oe = _make_executor(tmp_path)
        obj = _FakeGoalNode("obj_3")
        exec_id = oe.start(obj)
        assert exec_id is not None

        oe.on_turn_done("turn_0", "已完成第一步")

        ex = oe.get_execution(exec_id)
        assert ex.steps[0].artifacts == []
        assert ex.status == "running"  # 正常推进到第二步，未受影响

    def test_tool_based_exception_falls_back_to_regex(self, tmp_path):
        """artifacts_from_tools_fn 调用异常时不应该让 step 完成流程崩溃，
        而是静默退化到正则解析（有提供的话）。"""

        def _artifacts_from_tools(submitted_message):
            raise RuntimeError("模拟历史定位失败")

        def _artifacts_parse(result_summary):
            return ["fallback.txt"] if "[ARTIFACTS]" in result_summary else []

        oe = _make_executor(
            tmp_path,
            artifacts_from_tools_fn=_artifacts_from_tools,
            artifacts_parse_fn=_artifacts_parse,
        )
        obj = _FakeGoalNode("obj_4")
        exec_id = oe.start(obj)
        assert exec_id is not None

        oe.on_turn_done("turn_0", "done [ARTIFACTS] whatever.txt")

        ex = oe.get_execution(exec_id)
        assert ex.steps[0].artifacts == ["fallback.txt"]
        assert ex.status == "running"


# ── api/routes.py 里的两个新提取函数（不依赖 FastAPI app，直接单测函数）──────

class TestExtractToolWritePaths:
    def test_extracts_paths_from_known_write_tools(self):
        from mini_agent.api.routes import _extract_tool_write_paths

        raw_entries = [
            {"_type": "user_input", "content": "步骤 1/2: 写文件"},
            {
                "_type": "assistant_reply",
                "content": [
                    {"type": "text", "text": "我先创建一个文件"},
                    {
                        "type": "tool_use",
                        "name": "write_file",
                        "input": {"path": "src/foo.py", "content": "x=1"},
                    },
                ],
            },
            {"_type": "tool_result", "content": "写入成功"},
            {
                "_type": "assistant_reply",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "patch_file",
                        "input": {"path": "src/foo.py", "diff": "..."},
                    },
                    {
                        "type": "tool_use",
                        "name": "bash",
                        "input": {"command": "ls src/foo.py"},
                    },
                ],
            },
        ]
        paths = _extract_tool_write_paths(raw_entries)
        # src/foo.py 被 write_file 和 patch_file 各写一次，去重后只出现一次；
        # bash 调用不在写文件工具名单内，不应该被提取。
        assert paths == ["src/foo.py"]

    def test_ignores_non_write_tools_and_missing_input(self):
        from mini_agent.api.routes import _extract_tool_write_paths

        raw_entries = [
            {
                "_type": "assistant_reply",
                "content": [
                    {"type": "tool_use", "name": "read_file", "input": {"path": "src/foo.py"}},
                    {"type": "tool_use", "name": "write_file", "input": {}},  # 没有 path
                ],
            },
        ]
        assert _extract_tool_write_paths(raw_entries) == []

    def test_empty_entries_returns_empty(self):
        from mini_agent.api.routes import _extract_tool_write_paths

        assert _extract_tool_write_paths([]) == []


class TestLocateStepHistoryEntries:
    class _FakeHistManager:
        def __init__(self, history):
            self.history = history

    def test_locates_last_match_and_stops_at_next_user_input(self):
        from mini_agent.api.routes import _locate_step_history_entries

        history = [
            {"_type": "user_input", "content": "步骤1"},
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "旧的第一次尝试"}]},
            {"_type": "user_input", "content": "步骤1"},  # 重试后再次提交，内容相同
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "最新一次尝试"}]},
            {"_type": "user_input", "content": "步骤2"},
            {"_type": "assistant_reply", "content": [{"type": "text", "text": "步骤2的内容"}]},
        ]
        hist_mgr = self._FakeHistManager(history)
        entries = _locate_step_history_entries(hist_mgr, "步骤1")
        # 应该定位到最后一次匹配（index 2），截止到下一条 user_input（index 4）之前
        assert entries == history[2:4]

    def test_returns_none_when_not_found(self):
        from mini_agent.api.routes import _locate_step_history_entries

        hist_mgr = self._FakeHistManager([{"_type": "user_input", "content": "别的内容"}])
        assert _locate_step_history_entries(hist_mgr, "找不到的内容") is None
