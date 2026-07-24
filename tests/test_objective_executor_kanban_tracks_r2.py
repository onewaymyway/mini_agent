"""
tests/test_objective_executor_kanban_tracks_r2.py

覆盖 `next_doc/kanban_and_autonomy_improvement_plan.md` 第二轮延续的内容
（见 `next_doc/kanban_and_autonomy_improvement_implementation_record.md`
"第二轮"一节）：

- Track B 完整版：cancel(sync_goal_status=False) 不回写 GoalNode.status；
  find_running_execution_by_objective() 只在有 running/pending execution
  时命中。
- Track E：get_execution() 只读查询 + ExecutionStep.submitted_message 在
  _submit_step() 里被正确记录（trace 端点定位历史用的关键信息，路由层的
  HTTP 拼装本身不在这里测，只测数据是否被正确写入 step 上）。
- Track F 第二部分：耗尽重试次数后，提供 llm_redecompose_fn 时会替换剩余
  步骤并继续 running；不提供/返回空时保持原有"直接判 failed"行为不变。
- Track G：on_turn_done() 完成时会调用 artifacts_parse_fn 解析产出物路径
  写入 step.artifacts，且会出现在下一步 prompt 的"[前序步骤产出文件]"段。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_objective_executor_kanban_tracks_r2.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.objective_executor import (
    ObjectiveExecutor,
    MAX_STEP_RETRIES,
)


class _FakeGoalNode:
    def __init__(self, id_: str, title: str = "测试目标"):
        self.id = id_
        self.title = title
        self.progress_notes = ""


class _FakeGoalBacklog:
    """极简 stub：只记录 set_status 调用，不落盘，避免依赖真实 GoalBacklog
    的文件锁/磁盘 IO，让测试更快更聚焦。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def set_status(self, node_id: str, status: str) -> bool:
        self.calls.append((node_id, status))
        return True


def _make_executor(tmp_path, submit_fn=None, goal_backlog=None, **kwargs) -> ObjectiveExecutor:
    paths = AgentPaths(tmp_path)
    submitted_ids = iter(f"turn_{i}" for i in range(1000))

    def _default_submit(message, initiator, meta):
        return next(submitted_ids)

    return ObjectiveExecutor(
        paths=paths,
        submit_fn=submit_fn or _default_submit,
        llm_decompose_fn=lambda obj: ["第一步", "第二步", "第三步"],
        goal_backlog=goal_backlog,
        **kwargs,
    )


# ── Track B 完整版：反向同步的 cancel(sync_goal_status=False) ────────────────

class TestCancelSyncFlag:
    def test_cancel_default_syncs_goal_status(self, tmp_path):
        gb = _FakeGoalBacklog()
        oe = _make_executor(tmp_path, goal_backlog=gb)
        obj = _FakeGoalNode("obj_1")
        exec_id = oe.start(obj)
        assert exec_id is not None

        ok = oe.cancel(exec_id)
        assert ok is True
        assert ("obj_1", "cancelled") in gb.calls

    def test_cancel_with_sync_false_does_not_touch_goal_status(self, tmp_path):
        """[Track B 完整版] 反向同步路径：GoalNode.status 已经被用户显式
        改成别的值（比如 abandoned），cancel() 不应该再把它覆盖回
        cancelled。"""
        gb = _FakeGoalBacklog()
        oe = _make_executor(tmp_path, goal_backlog=gb)
        obj = _FakeGoalNode("obj_2")
        exec_id = oe.start(obj)
        assert exec_id is not None

        ok = oe.cancel(exec_id, sync_goal_status=False)
        assert ok is True
        assert gb.calls == []  # 完全没有调用 set_status

        ex = oe.get_execution(exec_id)
        assert ex.status == "cancelled"  # execution 本身确实停止了


class TestFindRunningExecutionByObjective:
    def test_finds_running_execution(self, tmp_path):
        oe = _make_executor(tmp_path)
        obj = _FakeGoalNode("obj_3")
        exec_id = oe.start(obj)
        found = oe.find_running_execution_by_objective("obj_3")
        assert found == exec_id

    def test_returns_none_when_no_running_execution(self, tmp_path):
        oe = _make_executor(tmp_path)
        assert oe.find_running_execution_by_objective("does_not_exist") is None

    def test_returns_none_after_completion(self, tmp_path):
        oe = _make_executor(tmp_path)
        obj = _FakeGoalNode("obj_4")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        # 手动把三步全部标记完成，模拟 Objective 走完
        for _ in range(3):
            cur_turn = ex.current_step.turn_id
            oe.on_turn_done(cur_turn, "done")
        assert ex.status == "completed"
        assert oe.find_running_execution_by_objective("obj_4") is None


# ── Track E：submitted_message 记录 + get_execution() 只读查询 ───────────────

class TestSubmittedMessageForTrace:
    def test_submitted_message_recorded_on_submit(self, tmp_path):
        oe = _make_executor(tmp_path)
        obj = _FakeGoalNode("obj_5", title="写一份周报")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        step = ex.steps[0]
        assert step.submitted_message  # 非空
        assert "写一份周报" in step.submitted_message
        assert "第一步" in step.submitted_message

    def test_get_execution_returns_none_for_unknown_id(self, tmp_path):
        oe = _make_executor(tmp_path)
        assert oe.get_execution("exec_不存在") is None


# ── Track F 第二部分：耗尽重试后先尝试重新分解剩余步骤 ───────────────────────

class TestRedecomposeOnExhaustedRetries:
    def test_redecompose_replaces_remaining_steps(self, tmp_path):
        new_steps_from_llm = ["换个做法的第二步", "换个做法的第三步"]

        def _redecompose(title, completed, remaining, reason):
            assert remaining  # 收到了剩余步骤描述
            assert reason      # 收到了失败原因
            return new_steps_from_llm

        oe = _make_executor(tmp_path, llm_redecompose_fn=_redecompose)
        obj = _FakeGoalNode("obj_6")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)

        # 让第一步反复失败到耗尽重试次数
        for _ in range(MAX_STEP_RETRIES):
            turn_id = ex.current_step.turn_id
            oe.on_turn_failed(turn_id, error="模拟失败")

        # 最后一次失败应该触发重新分解，而不是直接判 failed
        turn_id = ex.current_step.turn_id
        oe.on_turn_failed(turn_id, error="第三次也失败")

        assert ex.status == "running"  # 没有被判 failed
        assert ex.redecompose_attempted is True
        descs = [s.description for s in ex.steps[ex.current_step_idx:]]
        assert descs[0] in new_steps_from_llm or descs == new_steps_from_llm

    def test_only_attempts_redecompose_once(self, tmp_path):
        """重新分解出的新步骤如果继续失败，不应该再触发第二次重新分解——
        直接进入原有的 failed 判定，避免无限循环。"""
        call_count = {"n": 0}

        def _redecompose(title, completed, remaining, reason):
            call_count["n"] += 1
            return ["新的步骤A", "新的步骤B"]

        oe = _make_executor(tmp_path, llm_redecompose_fn=_redecompose)
        obj = _FakeGoalNode("obj_7")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)

        for _ in range(MAX_STEP_RETRIES + 1):
            turn_id = ex.current_step.turn_id
            oe.on_turn_failed(turn_id, error="失败")
        assert ex.status == "running"
        assert call_count["n"] == 1

        # 新步骤也反复失败到耗尽重试
        for _ in range(MAX_STEP_RETRIES + 1):
            turn_id = ex.current_step.turn_id
            oe.on_turn_failed(turn_id, error="新步骤也失败")

        assert call_count["n"] == 1  # 没有第二次调用
        assert ex.status == "failed"

    def test_without_redecompose_fn_falls_back_to_failed(self, tmp_path):
        """未提供 llm_redecompose_fn 时，行为必须和改造前完全一致：耗尽
        重试直接判 Objective failed，不应该有任何新行为泄漏进来。"""
        oe = _make_executor(tmp_path)  # 不传 llm_redecompose_fn
        obj = _FakeGoalNode("obj_8")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)

        for _ in range(MAX_STEP_RETRIES + 1):
            turn_id = ex.current_step.turn_id
            oe.on_turn_failed(turn_id, error="失败")

        assert ex.status == "failed"

    def test_redecompose_returning_empty_falls_back_to_failed(self, tmp_path):
        oe = _make_executor(tmp_path, llm_redecompose_fn=lambda *a: [])
        obj = _FakeGoalNode("obj_9")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)

        for _ in range(MAX_STEP_RETRIES + 1):
            turn_id = ex.current_step.turn_id
            oe.on_turn_failed(turn_id, error="失败")

        assert ex.status == "failed"


# ── Track G：产出物解析 + 前序步骤上下文里带上 artifacts ─────────────────────

class TestArtifactsParsing:
    def test_artifacts_parsed_and_recorded(self, tmp_path):
        def _parse(result_summary):
            if "[ARTIFACTS]" in result_summary:
                return ["src/foo.py", "docs/readme.md"]
            return []

        oe = _make_executor(tmp_path, artifacts_parse_fn=_parse)
        obj = _FakeGoalNode("obj_10")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)

        turn_id = ex.current_step.turn_id
        oe.on_turn_done(turn_id, "完成了第一步。\n[ARTIFACTS] src/foo.py, docs/readme.md")

        assert ex.steps[0].artifacts == ["src/foo.py", "docs/readme.md"]

        # 下一步（第二步）的 submitted_message 里应该带上前序产出物
        next_step = ex.current_step
        assert "src/foo.py" in next_step.submitted_message
        assert "docs/readme.md" in next_step.submitted_message

    def test_no_artifacts_fn_leaves_empty_list(self, tmp_path):
        oe = _make_executor(tmp_path)  # 不传 artifacts_parse_fn
        obj = _FakeGoalNode("obj_11")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)

        turn_id = ex.current_step.turn_id
        oe.on_turn_done(turn_id, "完成了第一步。[ARTIFACTS] src/foo.py")

        assert ex.steps[0].artifacts == []  # 向后兼容：不提供回调不解析


class TestArtifactsFromToolCalls:
    """[Track G 深化] 优先从 write_file/patch_file 等工具调用记录里精确
    提取路径，而不是依赖模型自觉输出 `[ARTIFACTS] ...` 标记。"""

    @staticmethod
    def _assistant_entry_with_tool_calls(calls: list[tuple]) -> dict:
        """构造一条 _type=="assistant_reply" 的 history 条目，content 里
        混杂 text 块和若干 tool_use 块（每项 (tool_name, path)）。"""
        content = [{"type": "text", "text": "正在处理..."}]
        for name, path in calls:
            content.append({"type": "tool_use", "name": name, "input": {"path": path}})
        return {"_type": "assistant_reply", "content": content}

    def test_extracts_paths_from_write_and_patch_tools(self, tmp_path):
        oe = _make_executor(tmp_path)  # 不提供 artifacts_parse_fn，纯测工具调用路径
        obj = _FakeGoalNode("obj_12")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id

        history_segment = [
            {"_type": "user_input", "content": ex.steps[0].submitted_message},
            self._assistant_entry_with_tool_calls([
                ("write_file", "src/a.py"),
                ("patch_file", "src/b.py"),
                ("read_file", "src/c.py"),  # 非写入类工具，不应该被收集
            ]),
            {"_type": "tool_result", "content": "ok"},
        ]
        oe.on_turn_done(turn_id, "完成了第一步。", history_segment=history_segment)

        assert ex.steps[0].artifacts == ["src/a.py", "src/b.py"]

    def test_tool_calls_take_priority_over_text_marker(self, tmp_path):
        """两种来源都命中时，工具调用记录优先——因为它更可靠。"""
        def _regex_fn(text):
            return ["从文本解析出来的.py"]

        oe = _make_executor(tmp_path, artifacts_parse_fn=_regex_fn)
        obj = _FakeGoalNode("obj_13")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id

        history_segment = [
            self._assistant_entry_with_tool_calls([("create_file", "src/real.py")]),
        ]
        oe.on_turn_done(
            turn_id, "完成了第一步。[ARTIFACTS] 从文本解析出来的.py",
            history_segment=history_segment,
        )
        assert ex.steps[0].artifacts == ["src/real.py"]

    def test_falls_back_to_text_marker_when_no_tool_calls_found(self, tmp_path):
        def _regex_fn(text):
            return ["文本兜底.py"] if "[ARTIFACTS]" in text else []

        oe = _make_executor(tmp_path, artifacts_parse_fn=_regex_fn)
        obj = _FakeGoalNode("obj_14")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id

        # history_segment 里没有任何写入类工具调用
        history_segment = [self._assistant_entry_with_tool_calls([("read_file", "src/x.py")])]
        oe.on_turn_done(
            turn_id, "完成了第一步。[ARTIFACTS] 文本兜底.py",
            history_segment=history_segment,
        )
        assert ex.steps[0].artifacts == ["文本兜底.py"]

    def test_empty_or_missing_history_segment_does_not_crash(self, tmp_path):
        oe = _make_executor(tmp_path)
        obj = _FakeGoalNode("obj_15")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id

        # 不传 history_segment（默认 None）—— 行为应与改造前一致
        oe.on_turn_done(turn_id, "完成了第一步。")
        assert ex.steps[0].artifacts == []

    def test_deduplicates_repeated_paths(self, tmp_path):
        oe = _make_executor(tmp_path)
        obj = _FakeGoalNode("obj_16")
        exec_id = oe.start(obj)
        ex = oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id

        history_segment = [
            self._assistant_entry_with_tool_calls([
                ("write_file", "src/dup.py"),
                ("patch_file", "src/dup.py"),  # 同一个文件先写后改，不应重复出现
            ]),
        ]
        oe.on_turn_done(turn_id, "完成了第一步。", history_segment=history_segment)
        assert ex.steps[0].artifacts == ["src/dup.py"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
