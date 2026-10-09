"""tests/test_objective_executor_async_prepare.py

[next_doc/tick_dispatch_only_execution_model_plan.md 阶段二]
ObjectiveExecutor 的 step 状态机（preparing / decomposing）：
  1. start() 在 LLM 拆解/路径声明挂死时立即返回，不阻塞调用线程；
  2. 结果就绪后由持锁线程继续提交（worker 回调拿 sched_lock，或下一轮 tick 的
     process_prepared()）；两条路径幂等、不重复提交；
  3. Track C 路径互斥语义不变（冲突 → blocked → 占用方释放后重试）；
  4. 声明失败/超时 → 退化为哨兵路径继续推进；
  5. 失败后的重新分解异步化：成功替换剩余步骤并提交；失败按原逻辑判 Objective failed；
  6. 取消、派发器繁忙、未注入锁/派发器（保持同步）等边界。
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import (
    MAX_STEP_RETRIES,
    ObjectiveExecutor,
    _UNKNOWN_PATH_SENTINEL,
)
from mini_agent.evolution.tick_dispatcher import TickDispatcher
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _wait_until(pred, timeout=5.0, step=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class _Submitter:
    def __init__(self):
        self.calls: list[dict] = []
        self._n = 0

    def __call__(self, message, initiator, meta):
        self._n += 1
        tid = f"turn_{self._n}"
        self.calls.append({"turn_id": tid, "message": message, "meta": meta})
        return tid


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _Submitter()
        self.sched_lock = threading.Lock()
        self.dispatcher = TickDispatcher(max_workers=4, default_timeout_seconds=60, grace_seconds=0)
        self.gates: list[threading.Event] = []

    def tearDown(self):
        for g in self.gates:
            g.set()
        # 等后台任务收尾再清理临时目录，避免 worker 还在落盘时 rmtree 竞态
        _wait_until(lambda: self.dispatcher.stats()["running"] == 0, timeout=3.0)
        time.sleep(0.05)
        self._tmp.cleanup()

    def gate(self) -> threading.Event:
        g = threading.Event()
        self.gates.append(g)
        return g

    def objective(self, title):
        goal = self.backlog.add_goal(title=f"{title}-goal", description="", source="user", priority=50)
        return self.backlog.add_objectives_for_goal(goal.id, [title])[0]

    def executor(self, *, decompose=None, declare=None, redecompose=None, async_on=True, lock=True):
        ex = ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=decompose,
            declare_paths_fn=declare,
            llm_redecompose_fn=redecompose,
            goal_backlog=self.backlog,
        )
        if async_on:
            ex.set_async_prepare(self.dispatcher, self.sched_lock if lock else None, timeout_seconds=60)
        return ex


class TestAsyncStart(_Base):
    def test_start_returns_immediately_while_decompose_hangs(self):
        gate = self.gate()

        def decompose(obj):
            gate.wait(10)
            return [f"{obj.title} 步骤1", f"{obj.title} 步骤2"]

        executor = self.executor(decompose=decompose, declare=lambda d: [])
        obj = self.objective("任务A")

        t0 = time.time()
        exec_id = executor.start(obj)
        self.assertLess(time.time() - t0, 1.0, "LLM 拆解挂死时 start() 也必须立即返回")
        self.assertIsNotNone(exec_id)
        ex = executor.get_execution(exec_id)
        self.assertEqual(ex.status, "running")
        self.assertTrue(ex.decomposing)
        self.assertEqual(ex.steps[0].status, "preparing")
        self.assertEqual(self.submitter.calls, [])
        self.assertTrue(executor.is_running(obj.id), "占位 execution 占用并发槽位、防止重复启动")
        self.assertIsNone(executor.start(obj))

        gate.set()
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        ex = executor.get_execution(exec_id)
        self.assertFalse(ex.decomposing)
        self.assertEqual(len(ex.steps), 2)
        self.assertEqual(ex.steps[0].status, "running")

    def test_decompose_failure_degrades_to_single_step(self):
        def decompose(obj):
            raise RuntimeError("llm down")

        executor = self.executor(decompose=decompose, declare=lambda d: [])
        obj = self.objective("任务B")
        exec_id = executor.start(obj)
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        ex = executor.get_execution(exec_id)
        self.assertEqual([s.description for s in ex.steps], [obj.title])

    def test_start_without_llm_decompose_still_prepares_paths_async(self):
        gate = self.gate()

        def declare(desc):
            gate.wait(10)
            return ["a.py"]

        executor = self.executor(decompose=None, declare=declare)
        obj = self.objective("任务C")
        t0 = time.time()
        exec_id = executor.start(obj)
        self.assertLess(time.time() - t0, 1.0)
        self.assertIsNotNone(exec_id, "preparing 视为已受理")
        ex = executor.get_execution(exec_id)
        self.assertEqual(ex.status, "running")
        self.assertEqual(ex.steps[0].status, "preparing")
        gate.set()
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        self.assertEqual(ex.steps[0].paths, ["a.py"])


class TestPickupPaths(_Base):
    def test_tick_pickup_while_lock_held_and_idempotent(self):
        """持锁线程（模拟 tick）可通过 process_prepared() 接手结果；worker 随后拿到锁时
        发现已被消费，不会重复提交。"""
        gate = self.gate()
        executor = self.executor(decompose=lambda o: ["s1", "s2"],
                                 declare=lambda d: (gate.wait(10), ["x.py"])[1])
        obj = self.objective("任务D")

        with self.sched_lock:  # 模拟 tick 线程持锁
            exec_id = executor.start(obj)
            self.assertIsNotNone(exec_id)
            gate.set()
            # worker 因 sched_lock 被本线程持有而拿不到锁，结果留在缓存里；
            # 由本线程（相当于 tick）通过 process_prepared() 驱动整条链路
            # （拆解结果 → 路径声明 → 提交）。
            self.assertTrue(_wait_until(lambda: executor.prepare_stats()["results_waiting"] >= 1))
            for _ in range(20):
                executor.process_prepared()
                if len(self.submitter.calls) == 1:
                    break
                time.sleep(0.05)
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        time.sleep(0.3)  # 给 worker 拿锁的机会
        self.assertEqual(len(self.submitter.calls), 1, "同一个准备结果不能被消费两次")


class TestPathMutexPreserved(_Base):
    def test_conflicting_objective_ends_blocked_then_retries(self):
        executor = self.executor(decompose=lambda o: [f"{o.title}-单步"],
                                 declare=lambda d: ["README.md"])
        a, b = self.objective("A"), self.objective("B")
        exec_a = executor.start(a)
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        exec_b = executor.start(b)
        self.assertIsNotNone(exec_b)
        step_b = executor.get_execution(exec_b).steps[0]
        self.assertTrue(_wait_until(lambda: step_b.status == "blocked"))
        self.assertEqual(len(self.submitter.calls), 1, "冲突的 step 不能提交")

        executor.on_turn_done("turn_1", "ok")  # A 完成，释放路径
        executor.retry_blocked_steps()
        self.assertEqual(step_b.status, "running")
        self.assertEqual(len(self.submitter.calls), 2)
        self.assertEqual(executor.get_execution(exec_a).status, "completed")


class TestDegradation(_Base):
    def test_declare_exception_falls_back_to_sentinel(self):
        def declare(desc):
            raise RuntimeError("llm down")

        executor = self.executor(decompose=lambda o: ["only"], declare=declare)
        exec_id = executor.start(self.objective("E"))
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        self.assertEqual(executor.get_execution(exec_id).steps[0].paths, [_UNKNOWN_PATH_SENTINEL])

    def test_hung_declare_is_reaped_then_degrades(self):
        gate = self.gate()
        self.dispatcher = TickDispatcher(max_workers=4, default_timeout_seconds=1, grace_seconds=0)
        executor = self.executor(decompose=lambda o: ["only"],
                                 declare=lambda d: gate.wait(30) and ["x"])
        executor.set_async_prepare(self.dispatcher, self.sched_lock, timeout_seconds=1)
        exec_id = executor.start(self.objective("F"))
        time.sleep(1.4)
        # 超时回收发生在 tick 线程（持锁）；这里直接调用 process_prepared() 模拟
        with self.sched_lock:
            for _ in range(4):
                executor.process_prepared()
                if self.submitter.calls:
                    break
                time.sleep(0.05)
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        self.assertEqual(executor.get_execution(exec_id).steps[0].paths, [_UNKNOWN_PATH_SENTINEL])


class TestAsyncRedecompose(_Base):
    def _fail_until_redecompose(self, executor, exec_id):
        # 初始提交 turn_1；MAX_STEP_RETRIES 次重试后再失败才会触发重新分解
        tid = "turn_1"
        for i in range(MAX_STEP_RETRIES + 1):
            self.assertTrue(_wait_until(lambda: tid in executor._turn_to_exec))
            executor.on_turn_failed(tid, "boom")
            tid = f"turn_{i + 2}"

    def test_redecompose_runs_in_background_and_installs_new_steps(self):
        gate = self.gate()

        def redecompose(title, completed, remaining, reason, external_context=None):
            gate.wait(10)
            return ["新步骤1", "新步骤2"]

        executor = self.executor(decompose=lambda o: ["旧步骤"], declare=lambda d: [],
                                 redecompose=redecompose)
        exec_id = executor.start(self.objective("G"))
        self._fail_until_redecompose(executor, exec_id)

        ex = executor.get_execution(exec_id)
        self.assertEqual(ex.status, "running", "重新分解进行中，execution 不能被判失败")
        self.assertTrue(ex.decomposing)
        calls_before = len(self.submitter.calls)

        gate.set()
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) > calls_before))
        self.assertFalse(ex.decomposing)
        self.assertEqual([s.description for s in ex.steps], ["新步骤1", "新步骤2"])
        self.assertEqual(ex.steps[0].status, "running")

    def test_redecompose_failure_marks_objective_failed(self):
        def redecompose(title, completed, remaining, reason, external_context=None):
            raise RuntimeError("llm down")

        executor = self.executor(decompose=lambda o: ["旧步骤"], declare=lambda d: [],
                                 redecompose=redecompose)
        exec_id = executor.start(self.objective("H"))
        self._fail_until_redecompose(executor, exec_id)
        ex = executor.get_execution(exec_id)
        self.assertTrue(_wait_until(lambda: ex.status == "failed"))
        self.assertFalse(ex.decomposing)
        self.assertIn("重新分解不可用", ex.progress_notes)


class TestEdges(_Base):
    def test_cancel_during_decompose_drops_result(self):
        gate = self.gate()

        def decompose(obj):
            gate.wait(10)
            return ["a", "b"]

        executor = self.executor(decompose=decompose, declare=lambda d: [])
        exec_id = executor.start(self.objective("I"))
        self.assertTrue(executor.cancel(exec_id))
        gate.set()
        time.sleep(0.5)
        self.assertEqual(self.submitter.calls, [])
        self.assertEqual(executor.get_execution(exec_id).status, "cancelled")

    def test_dispatcher_busy_queues_prepare_and_retries(self):
        self.dispatcher = TickDispatcher(max_workers=1, default_timeout_seconds=60, grace_seconds=0)
        blocker = self.gate()
        self.assertTrue(self.dispatcher.dispatch("blocker", lambda: blocker.wait(10), timeout=60))
        executor = self.executor(decompose=lambda o: ["only"], declare=lambda d: [])
        executor.set_async_prepare(self.dispatcher, self.sched_lock, timeout_seconds=60)
        exec_id = executor.start(self.objective("J"))
        self.assertIsNotNone(exec_id, "派发器繁忙不能让 Objective 启动失败")
        self.assertEqual(executor.prepare_stats()["pending_dispatch"], 1)
        self.assertEqual(self.submitter.calls, [])

        blocker.set()
        self.assertTrue(_wait_until(lambda: not self.dispatcher.is_running("blocker")))
        for _ in range(20):
            # 真实 tick 线程调用 process_prepared() 时持有 sched_lock；不持锁会与 worker
            # 线程的 on_done（持锁推进状态）并发改 executor 状态，偶发重复提交（测试自身竞态）
            with self.sched_lock:
                executor.process_prepared()
            if self.submitter.calls:
                break
            time.sleep(0.1)
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))
        time.sleep(0.3)
        self.assertEqual(len(self.submitter.calls), 1, "不得重复提交")

    def test_without_sched_lock_stays_synchronous(self):
        executor = self.executor(decompose=lambda o: ["s"], declare=lambda d: [], lock=False)
        self.assertFalse(executor.prepare_stats()["enabled"])
        exec_id = executor.start(self.objective("K"))
        self.assertEqual(len(self.submitter.calls), 1, "无共享锁时保持同步行为")
        self.assertFalse(executor.get_execution(exec_id).decomposing)

    def test_not_configured_stays_synchronous(self):
        executor = self.executor(decompose=lambda o: ["s"], declare=lambda d: [], async_on=False)
        exec_id = executor.start(self.objective("L"))
        self.assertEqual(len(self.submitter.calls), 1)
        self.assertEqual(executor.get_execution(exec_id).steps[0].status, "running")

    def test_pause_resume_while_preparing_retriggers_prepare(self):
        gate = self.gate()
        executor = self.executor(decompose=lambda o: ["s"], declare=lambda d: (gate.wait(10), ["p"])[1])
        exec_id = executor.start(self.objective("M"))
        ex = executor.get_execution(exec_id)
        self.assertTrue(_wait_until(lambda: ex.steps and not ex.decomposing and ex.steps[0].status == "preparing"))
        executor.pause_all()
        self.assertEqual(ex.status, "paused")
        gate.set()
        time.sleep(0.5)
        self.assertEqual(self.submitter.calls, [], "paused 期间准备结果被丢弃，不提交")
        executor.resume()
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))


class TestOnTurnDonePath(_Base):
    def test_on_turn_done_does_not_block_on_next_step_path_declaration(self):
        """审计 #13：on_turn_done 在持 sched_lock 的线程里推进下一步，原先会同步等
        路径声明 LLM。现在应立即返回，下一步进入 preparing，不被判失败。"""
        hang = {"on": False}
        gate = self.gate()

        def declare(desc):
            if hang["on"]:
                gate.wait(10)
            return ["p.py"]

        executor = self.executor(decompose=lambda o: ["s1", "s2"], declare=declare)
        exec_id = executor.start(self.objective("N"))
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 1))

        hang["on"] = True  # 之后的路径声明全部挂死
        t0 = time.time()
        with self.sched_lock:  # 与真实调用方一致：持锁调用 on_turn_done
            executor.on_turn_done("turn_1", "step1 ok")
        self.assertLess(time.time() - t0, 1.0)
        ex = executor.get_execution(exec_id)
        self.assertEqual(ex.status, "running", "preparing 不能被当成提交失败")
        self.assertEqual(ex.steps[1].status, "preparing")
        self.assertEqual(ex.current_step_idx, 1)

        gate.set()
        self.assertTrue(_wait_until(lambda: len(self.submitter.calls) == 2))
        self.assertEqual(ex.steps[1].status, "running")
        # 整条链路走完后正常完成
        executor.on_turn_done("turn_2", "step2 ok")
        self.assertEqual(ex.status, "completed")


if __name__ == "__main__":
    unittest.main()
