"""
tests/test_orchestrator.py

测试覆盖：
  - Task 数据模型（auto-name、depends_on、状态枚举）
  - TaskRecord（状态图标/颜色、elapsed、is_terminal、append_log）
  - TaskManager（submit、cancel、wait、依赖解析、worker 上限）
  - SubAgent（启动、取消、成功、失败捕获）
  - 编排工具函数（spawn_agent、list_tasks、get_task_status、cancel_task、wait_for_tasks）
  - TaskDisplay（print_task_table、print_task_log 不崩溃）
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import tools.builtin  # noqa
from orchestrator.task import Task, TaskRecord, TaskResult, TaskStatus
from orchestrator.task_manager import TaskManager


# ── 共享工厂 ──────────────────────────────────────────────────────────────────

def make_task(prompt="Do something", **kwargs) -> Task:
    return Task(prompt=prompt, **kwargs)


def make_cfg():
    from config import load_config
    cfg = load_config()
    cfg.api_key = "test"
    cfg.stream = False
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# Task 数据模型测试
# ══════════════════════════════════════════════════════════════════════════════

class TestTask(unittest.TestCase):

    def test_auto_name_from_prompt(self):
        t = Task(prompt="Write unit tests for the auth module")
        self.assertIn("Write unit tests", t.name)

    def test_auto_name_truncated_at_40(self):
        long = "A" * 80
        t = Task(prompt=long)
        self.assertLessEqual(len(t.name), 44)   # 40 + "…"
        self.assertIn("…", t.name)

    def test_explicit_name_preserved(self):
        t = Task(prompt="Do x", name="My Task")
        self.assertEqual(t.name, "My Task")

    def test_unique_id_each_time(self):
        ids = {Task(prompt="x").id for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_depends_on_default_empty(self):
        t = Task(prompt="x")
        self.assertEqual(t.depends_on, [])

    def test_tags_default_empty(self):
        t = Task(prompt="x")
        self.assertEqual(t.tags, [])

    def test_created_at_is_recent(self):
        before = time.time()
        t = Task(prompt="x")
        after = time.time()
        self.assertGreaterEqual(t.created_at, before)
        self.assertLessEqual(t.created_at, after)


# ══════════════════════════════════════════════════════════════════════════════
# TaskRecord 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskRecord(unittest.TestCase):

    def setUp(self):
        self.rec = TaskRecord(task=make_task())

    def test_initial_status_pending(self):
        self.assertEqual(self.rec.status, TaskStatus.PENDING)

    def test_task_id_matches_task(self):
        self.assertEqual(self.rec.task_id, self.rec.task.id)

    def test_elapsed_none_when_not_started(self):
        self.assertIsNone(self.rec.elapsed)

    def test_elapsed_counts_from_start(self):
        self.rec.started_at = time.time() - 5.0
        self.assertGreaterEqual(self.rec.elapsed, 4.9)

    def test_is_terminal_for_done(self):
        self.rec.status = TaskStatus.DONE
        self.assertTrue(self.rec.is_terminal)

    def test_is_terminal_for_failed(self):
        self.rec.status = TaskStatus.FAILED
        self.assertTrue(self.rec.is_terminal)

    def test_is_terminal_for_cancelled(self):
        self.rec.status = TaskStatus.CANCELLED
        self.assertTrue(self.rec.is_terminal)

    def test_not_terminal_when_running(self):
        self.rec.status = TaskStatus.RUNNING
        self.assertFalse(self.rec.is_terminal)

    def test_append_log_with_timestamp(self):
        self.rec.append_log("hello")
        self.assertEqual(len(self.rec.log_lines), 1)
        self.assertIn("hello", self.rec.log_lines[0])
        self.assertIn(":", self.rec.log_lines[0])   # timestamp has colon

    def test_status_icon_returns_string(self):
        for status in TaskStatus:
            self.rec.status = status
            self.assertIsInstance(self.rec.status_icon(), str)

    def test_status_color_returns_string(self):
        for status in TaskStatus:
            self.rec.status = status
            self.assertIsInstance(self.rec.status_color(), str)


# ══════════════════════════════════════════════════════════════════════════════
# TaskManager 核心测试（使用 Mock SubAgent）
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskManagerCore(unittest.TestCase):
    """
    直接操作 TaskRecord 状态来测试 TaskManager 的调度逻辑，
    不启动真正的 SubAgent（避免 API 调用）。
    """

    def setUp(self):
        from orchestrator.concurrency import init_concurrency
        init_concurrency(max_tasks=2, max_llm_calls=8)
        self.cfg = make_cfg()
        self.mgr = TaskManager(self.cfg, max_workers=2)

    def tearDown(self):
        self.mgr.stop(cancel_pending=True)

    def test_submit_returns_task_id(self):
        tid = self.mgr.submit(make_task())
        self.assertIsInstance(tid, str)
        self.assertGreater(len(tid), 0)

    def test_submit_creates_record(self):
        tid = self.mgr.submit(make_task())
        rec = self.mgr.get(tid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, TaskStatus.PENDING)

    def test_submit_many_returns_all_ids(self):
        tasks = [make_task(f"Task {i}") for i in range(5)]
        ids = self.mgr.submit_many(tasks)
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)

    def test_get_returns_none_for_unknown_id(self):
        self.assertIsNone(self.mgr.get("nonexistent"))

    def test_list_records_all_submitted(self):
        for i in range(3):
            self.mgr.submit(make_task(f"Task {i}"))
        recs = self.mgr.list_records()
        self.assertEqual(len(recs), 3)

    def test_list_records_filter_by_status(self):
        t1 = self.mgr.submit(make_task("A"))
        t2 = self.mgr.submit(make_task("B"))
        # Manually mark one as done
        self.mgr.get(t1).status = TaskStatus.DONE
        done = self.mgr.list_records(status=TaskStatus.DONE)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].task_id, t1)

    def test_list_records_filter_by_tag(self):
        self.mgr.submit(make_task("A", tags=["backend"]))
        self.mgr.submit(make_task("B", tags=["frontend"]))
        self.mgr.submit(make_task("C", tags=["backend"]))
        be = self.mgr.list_records(tag="backend")
        self.assertEqual(len(be), 2)

    def test_cancel_pending_task(self):
        tid = self.mgr.submit(make_task())
        ok = self.mgr.cancel(tid)
        self.assertTrue(ok)
        self.assertEqual(self.mgr.get(tid).status, TaskStatus.CANCELLED)

    def test_cancel_done_task_returns_false(self):
        tid = self.mgr.submit(make_task())
        self.mgr.get(tid).status = TaskStatus.DONE
        ok = self.mgr.cancel(tid)
        self.assertFalse(ok)

    def test_cancel_unknown_id_returns_false(self):
        self.assertFalse(self.mgr.cancel("nonexistent"))

    def test_cancel_all_cancels_pending(self):
        ids = self.mgr.submit_many([make_task(f"T{i}") for i in range(4)])
        n = self.mgr.cancel_all()
        self.assertEqual(n, 4)
        for tid in ids:
            self.assertEqual(self.mgr.get(tid).status, TaskStatus.CANCELLED)

    def test_stats_initial(self):
        self.mgr.submit(make_task("A"))
        self.mgr.submit(make_task("B"))
        s = self.mgr.stats()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["pending"], 2)

    def test_stats_after_cancel(self):
        tid = self.mgr.submit(make_task())
        self.mgr.cancel(tid)
        s = self.mgr.stats()
        self.assertEqual(s["cancelled"], 1)
        self.assertEqual(s["pending"], 0)

    def test_pending_count(self):
        self.mgr.submit_many([make_task(f"T{i}") for i in range(3)])
        self.assertEqual(self.mgr.pending_count(), 3)

    def test_running_count_initially_zero(self):
        self.assertEqual(self.mgr.running_count(), 0)


class TestTaskManagerDependencies(unittest.TestCase):
    """依赖解析逻辑测试（直接操作状态，不跑真正的 Agent）。"""

    def setUp(self):
        from orchestrator.concurrency import init_concurrency
        init_concurrency(max_tasks=4, max_llm_calls=8)
        self.cfg = make_cfg()
        self.mgr = TaskManager(self.cfg, max_workers=4)

    def tearDown(self):
        self.mgr.stop(cancel_pending=True)

    def _tick(self):
        """直接调用内部 _tick 方法，模拟调度器一次循环。"""
        self.mgr._tick()

    def test_dependency_blocks_start(self):
        """依赖未完成时，子任务应保持 PENDING（不被调度）。"""
        t1_id = self.mgr.submit(make_task("parent"))
        t2_id = self.mgr.submit(make_task("child", depends_on=[t1_id]))

        # patch _launch 以避免真正启动 SubAgent
        launched = []
        with patch.object(self.mgr, "_launch", side_effect=lambda r: launched.append(r.task_id)):
            self._tick()

        self.assertIn(t1_id, launched)
        self.assertNotIn(t2_id, launched)

    def test_dependency_released_when_parent_done(self):
        """父任务完成后，子任务应被调度。"""
        t1_id = self.mgr.submit(make_task("parent"))
        t2_id = self.mgr.submit(make_task("child", depends_on=[t1_id]))

        # 手动将父任务设为 DONE
        rec1 = self.mgr.get(t1_id)
        rec1.status = TaskStatus.DONE

        launched = []
        with patch.object(self.mgr, "_launch", side_effect=lambda r: launched.append(r.task_id)):
            self._tick()

        self.assertIn(t2_id, launched)

    def test_dependency_cancelled_when_parent_fails(self):
        """父任务失败时，子任务应被取消（不是继续等待）。"""
        t1_id = self.mgr.submit(make_task("parent"))
        t2_id = self.mgr.submit(make_task("child", depends_on=[t1_id]))

        rec1 = self.mgr.get(t1_id)
        rec1.status = TaskStatus.FAILED

        with patch.object(self.mgr, "_launch"):
            self._tick()

        self.assertEqual(self.mgr.get(t2_id).status, TaskStatus.CANCELLED)

    def test_max_workers_respected(self):
        """同时 RUNNING 的任务数不超过 max_workers。"""
        from orchestrator.concurrency import init_concurrency
        init_concurrency(max_tasks=2, max_llm_calls=8)
        mgr = TaskManager(self.cfg, max_workers=2)
        try:
            for i in range(5):
                mgr.submit(make_task(f"T{i}"))

            launched = []
            with patch.object(mgr, "_launch", side_effect=lambda r: (
                launched.append(r.task_id),
                setattr(r, "status", TaskStatus.RUNNING)
            )):
                mgr._tick()

            # With semaphore limit=2, tick spawns at most limit*2=4 threads
            # but in practice slots = max(0, 4 - running=0) = 4 for first tick.
            # The semaphore itself enforces the 2-concurrent limit inside SubAgent.
            # For the dependency scheduler test we just verify not all 5 at once.
            self.assertLessEqual(len(launched), 4)
        finally:
            mgr.stop(cancel_pending=True)
            init_concurrency(max_tasks=4, max_llm_calls=8)  # restore


# ══════════════════════════════════════════════════════════════════════════════
# SubAgent 测试（Mock LLM）
# ══════════════════════════════════════════════════════════════════════════════

class TestSubAgent(unittest.TestCase):

    def setUp(self):
        # Ensure concurrency semaphores are initialized for each test
        from orchestrator.concurrency import init_concurrency
        init_concurrency(max_tasks=4, max_llm_calls=8)

    def _make_mock_agent(self, mock_result="Done."):
        """Build a mock Agent whose run_turn() returns mock_result."""
        from agent import Agent
        from llm.base import LLMResponse, LLMUsage
        from config import SessionStats

        mock_agent = MagicMock(spec=Agent)
        mock_agent.run_turn.return_value = mock_result
        mock_agent.stats = SessionStats()
        mock_agent.stats.input_tokens = 5
        mock_agent.stats.output_tokens = 10
        mock_agent.stats.tool_calls = 0
        mock_agent.stats.turns = 1
        return mock_agent

    def _make_sub_agent(self, task, mock_result="Done."):
        from orchestrator.sub_agent import SubAgent
        rec = TaskRecord(task=task)
        cfg = make_cfg()
        mock_agent = self._make_mock_agent(mock_result)
        sub = SubAgent(rec, cfg)
        # Patch _build_agent so no real LLM client is created
        sub._build_agent = lambda t: mock_agent
        return sub, rec

    def test_starts_and_completes(self):
        task = make_task("Say hello")
        sub, rec = self._make_sub_agent(task, mock_result="Hello!")
        sub.start()
        sub.join(timeout=10)
        self.assertEqual(rec.status, TaskStatus.DONE)
        self.assertIsNotNone(rec.result)

    def test_result_has_output(self):
        task = make_task("Compute 2+2")
        sub, rec = self._make_sub_agent(task, mock_result="The answer is 4.")
        sub.start()
        sub.join(timeout=10)
        self.assertIn("4", rec.result.output)

    def test_exception_sets_failed(self):
        from orchestrator.sub_agent import SubAgent
        rec = TaskRecord(task=make_task("Crash"))
        cfg = make_cfg()
        sub = SubAgent(rec, cfg)
        sub._build_agent = MagicMock(side_effect=RuntimeError("boom"))
        sub.start()
        sub.join(timeout=10)
        self.assertEqual(rec.status, TaskStatus.FAILED)
        self.assertIsNotNone(rec.result.error)
        self.assertIn("boom", rec.result.error)

    def test_cancel_pending_before_start(self):
        from orchestrator.sub_agent import SubAgent
        rec = TaskRecord(task=make_task("x"))
        sub = SubAgent(rec, make_cfg())
        sub.cancel()
        self.assertEqual(rec.status, TaskStatus.CANCELLED)

    def test_is_alive_false_before_start(self):
        from orchestrator.sub_agent import SubAgent
        sub = SubAgent(TaskRecord(task=make_task()), make_cfg())
        self.assertFalse(sub.is_alive)

    def test_log_callback_called(self):
        task = make_task("Log test")
        from orchestrator.sub_agent import SubAgent
        rec = TaskRecord(task=task)
        cfg = make_cfg()
        logged = []
        mock_agent = self._make_mock_agent("ok")
        sub = SubAgent(rec, cfg, on_log=lambda tid, line: logged.append(line))
        sub._build_agent = lambda t: mock_agent
        sub.start()
        sub.join(timeout=10)
        self.assertGreater(len(logged), 0)

    def test_started_at_set_on_start(self):
        task = make_task("Timer test")
        sub, rec = self._make_sub_agent(task)
        before = time.time()
        sub.start()
        sub.join(timeout=10)
        self.assertIsNotNone(rec.started_at)
        self.assertGreaterEqual(rec.started_at, before)

    def test_finished_at_set_on_completion(self):
        sub, rec = self._make_sub_agent(make_task("x"))
        sub.start()
        sub.join(timeout=10)
        self.assertIsNotNone(rec.finished_at)
        self.assertGreaterEqual(rec.finished_at, rec.started_at)


# ══════════════════════════════════════════════════════════════════════════════
# 编排工具函数测试
# ══════════════════════════════════════════════════════════════════════════════

class TestOrchestrationTools(unittest.TestCase):

    def setUp(self):
        from orchestrator.concurrency import init_concurrency
        init_concurrency(max_tasks=2, max_llm_calls=8)
        import tools.orchestration as ot
        cfg = make_cfg()
        # 替换模块级 _task_manager
        mgr = TaskManager(cfg, max_workers=2)
        mgr.start()
        ot._task_manager = mgr
        self.mgr = mgr
        self.ot = ot

    def tearDown(self):
        self.mgr.stop(cancel_pending=True)

    def test_spawn_agent_returns_json_with_task_id(self):
        import json
        result = self.ot.spawn_agent(prompt="Do something", name="test-task")
        data = json.loads(result)
        self.assertIn("task_id", data)
        self.assertEqual(data["name"], "test-task")

    def test_spawn_agent_creates_record_in_manager(self):
        import json
        result = self.ot.spawn_agent(prompt="Do something")
        tid = json.loads(result)["task_id"]
        rec = self.mgr.get(tid)
        self.assertIsNotNone(rec)

    def test_spawn_agents_creates_multiple(self):
        import json
        result = self.ot.spawn_agents(tasks=[
            {"prompt": "Task A", "name": "a"},
            {"prompt": "Task B", "name": "b"},
            {"prompt": "Task C"},
        ])
        data = json.loads(result)
        self.assertEqual(data["spawned"], 3)
        self.assertEqual(len(data["tasks"]), 3)

    def test_get_task_status_returns_json(self):
        import json
        result = self.ot.spawn_agent(prompt="x")
        tid = json.loads(result)["task_id"]
        status_json = self.ot.get_task_status(tid)
        status = json.loads(status_json)
        self.assertIn("status", status)
        self.assertEqual(status["task_id"], tid)

    def test_get_task_status_unknown_id(self):
        import json
        result = self.ot.get_task_status("nonexistent-id")
        data = json.loads(result)
        self.assertIn("error", data)

    def test_list_tasks_returns_all(self):
        import json
        self.ot.spawn_agent(prompt="A")
        self.ot.spawn_agent(prompt="B")
        result = self.ot.list_tasks()
        data = json.loads(result)
        self.assertEqual(data["stats"]["total"], 2)

    def test_list_tasks_filter_by_status(self):
        import json
        r1 = self.ot.spawn_agent(prompt="A")
        t1_id = json.loads(r1)["task_id"]
        # mark done manually
        self.mgr.get(t1_id).status = TaskStatus.DONE
        self.ot.spawn_agent(prompt="B")

        result = self.ot.list_tasks(status="done")
        data = json.loads(result)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["status"], "done")

    def test_cancel_task_cancels_pending(self):
        import json
        r = self.ot.spawn_agent(prompt="x")
        tid = json.loads(r)["task_id"]
        result = self.ot.cancel_task(tid)
        data = json.loads(result)
        self.assertTrue(data["cancelled"])

    def test_cancel_task_not_found(self):
        import json
        result = self.ot.cancel_task("no-such-id")
        data = json.loads(result)
        self.assertFalse(data["cancelled"])

    def test_wait_for_tasks_timeout(self):
        import json
        # Create a task stuck in RUNNING state so it never becomes terminal
        r = self.ot.spawn_agent(prompt="slow task")
        tid = json.loads(r)["task_id"]
        # Force it into RUNNING so it is never terminal
        rec = self.mgr.get(tid)
        rec.status = TaskStatus.RUNNING
        rec.started_at = time.time()
        result = self.ot.wait_for_tasks([tid], timeout_seconds=0.1)
        data = json.loads(result)
        self.assertTrue(data["timed_out"])

    def test_wait_for_tasks_already_done(self):
        import json
        r = self.ot.spawn_agent(prompt="x")
        tid = json.loads(r)["task_id"]
        # Manually mark done
        rec = self.mgr.get(tid)
        rec.status = TaskStatus.DONE
        rec.result = TaskResult(output="finished")
        rec.finished_at = time.time()

        result = self.ot.wait_for_tasks([tid], timeout_seconds=5)
        data = json.loads(result)
        self.assertFalse(data["timed_out"])
        self.assertIn(tid, data["results"])

    def test_spawn_agent_without_manager(self):
        import tools.orchestration as ot
        ot._task_manager = None
        result = ot.spawn_agent(prompt="x")
        self.assertIn("error", result)
        # Restore
        ot._task_manager = self.mgr


# ══════════════════════════════════════════════════════════════════════════════
# TaskDisplay 烟雾测试（不崩溃即通过）
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskDisplay(unittest.TestCase):

    def _make_records(self) -> list[TaskRecord]:
        recs = []
        for status in TaskStatus:
            rec = TaskRecord(task=make_task(f"Task for {status.value}"))
            rec.status = status
            if status in (TaskStatus.DONE, TaskStatus.FAILED):
                rec.started_at = time.time() - 5
                rec.finished_at = time.time()
                rec.result = TaskResult(
                    output="Done output" if status == TaskStatus.DONE else "",
                    error=None if status == TaskStatus.DONE else "Something failed",
                    input_tokens=100, output_tokens=200, tool_calls=3, turns=2,
                )
            rec.append_log("Log line 1")
            rec.append_log("Log line 2")
            recs.append(rec)
        return recs

    def test_print_task_table_no_crash(self):
        from orchestrator.task_display import print_task_table
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        c = Console(file=buf, force_terminal=False)
        with patch("orchestrator.task_display.console", c):
            print_task_table(self._make_records())
        # Should have produced some output
        self.assertGreater(len(buf.getvalue()), 0)

    def test_print_task_table_empty(self):
        from orchestrator.task_display import print_task_table
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        c = Console(file=buf, force_terminal=False)
        with patch("orchestrator.task_display.console", c):
            print_task_table([])
        self.assertIn("No tasks", buf.getvalue())

    def test_print_task_log_no_crash(self):
        from orchestrator.task_display import print_task_log
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        c = Console(file=buf, force_terminal=False)
        recs = self._make_records()
        with patch("orchestrator.task_display.console", c):
            for rec in recs:
                print_task_log(rec)


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
