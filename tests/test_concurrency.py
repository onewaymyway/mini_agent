"""
tests/test_concurrency.py

并发控制层的完整测试，覆盖：
  - CountingSemaphore：正常获取、排队、超上限阻塞、limit 动态调整
  - 并发上限实际生效（多线程验证）
  - 模块级单例 init/get/set
  - concurrency_snapshot 结构
  - StatusBar 渲染不崩溃
  - ProviderMixin 织入信号量后行为正确（Mock provider）
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.orchestrator.concurrency import (
    CountingSemaphore,
    init_concurrency,
    get_task_sem,
    get_llm_sem,
    set_max_tasks,
    set_max_llm_calls,
    concurrency_snapshot,
)


# ══════════════════════════════════════════════════════════════════════════════
# CountingSemaphore 单元测试
# ══════════════════════════════════════════════════════════════════════════════

class TestCountingSemaphoreBasic(unittest.TestCase):

    def setUp(self):
        self.sem = CountingSemaphore(limit=3, kind="test")

    def test_initial_state(self):
        self.assertEqual(self.sem.limit, 3)
        self.assertEqual(self.sem.active_count, 0)
        self.assertEqual(self.sem.waiting_count, 0)
        self.assertEqual(self.sem.available, 3)

    def test_acquire_increments_active(self):
        with self.sem.acquire("a"):
            self.assertEqual(self.sem.active_count, 1)
            self.assertEqual(self.sem.available, 2)

    def test_release_decrements_active(self):
        with self.sem.acquire("a"):
            pass
        self.assertEqual(self.sem.active_count, 0)
        self.assertEqual(self.sem.available, 3)

    def test_multiple_acquires_up_to_limit(self):
        results = []
        def worker(label):
            with self.sem.acquire(label):
                results.append(self.sem.active_count)
                time.sleep(0.02)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)

        self.assertEqual(len(results), 3)
        self.assertLessEqual(max(results), 3)   # never exceeded limit

    def test_try_acquire_succeeds_when_available(self):
        ok = self.sem.try_acquire()
        self.assertTrue(ok)
        self.assertEqual(self.sem.active_count, 1)
        self.sem.release()

    def test_try_acquire_fails_when_full(self):
        # fill all slots
        for _ in range(3):
            self.sem.try_acquire()
        ok = self.sem.try_acquire()
        self.assertFalse(ok)
        self.assertEqual(self.sem.active_count, 3)
        for _ in range(3):
            self.sem.release()

    def test_release_decrements(self):
        self.sem.try_acquire()
        self.sem.release()
        self.assertEqual(self.sem.active_count, 0)

    def test_snapshot_structure(self):
        snap = self.sem.snapshot_status()
        self.assertIn("kind", snap)
        self.assertIn("limit", snap)
        self.assertIn("active", snap)
        self.assertIn("waiting", snap)
        self.assertIn("available", snap)
        self.assertIn("waiters", snap)

    def test_snapshot_values_correct(self):
        self.sem.try_acquire()
        snap = self.sem.snapshot_status()
        self.assertEqual(snap["active"], 1)
        self.assertEqual(snap["available"], 2)
        self.sem.release()

    def test_label_in_waiter(self):
        """排队者的 label 应出现在 snapshot 里。"""
        # Fill all slots
        for _ in range(3):
            self.sem.try_acquire()

        # Start a thread that will queue
        queued = threading.Event()
        done   = threading.Event()

        def waiter_thread():
            with self.sem.acquire("my-special-label"):
                queued.set()
                done.wait(timeout=2)

        t = threading.Thread(target=waiter_thread)
        t.start()
        time.sleep(0.05)   # let thread start and queue

        snap = self.sem.snapshot_status()
        labels = [w["label"] for w in snap["waiters"]]
        self.assertIn("my-special-label", labels)

        # Release one slot so waiter can proceed
        self.sem.release()
        queued.wait(timeout=2)
        done.set()
        t.join(timeout=5)
        for _ in range(2):
            self.sem.release()


class TestCountingSemaphoreLimit(unittest.TestCase):
    """验证并发上限实际生效（多线程竞争）。"""

    def test_never_exceeds_limit(self):
        sem = CountingSemaphore(limit=2, kind="test")
        max_observed = [0]
        errors = []
        lock = threading.Lock()

        def worker():
            with sem.acquire("w"):
                current = sem.active_count
                with lock:
                    if current > max_observed[0]:
                        max_observed[0] = current
                    if current > sem.limit:
                        errors.append(f"exceeded limit: {current} > {sem.limit}")
                time.sleep(0.01)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)

        self.assertEqual(errors, [], f"Limit violated: {errors}")
        self.assertLessEqual(max_observed[0], 2)

    def test_all_workers_complete(self):
        """所有线程都应该能完成，即使超过并发上限（排队后继续）。"""
        sem = CountingSemaphore(limit=2, kind="test")
        completed = []
        lock = threading.Lock()

        def worker(i):
            with sem.acquire(f"w{i}"):
                time.sleep(0.01)
            with lock:
                completed.append(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=15)

        self.assertEqual(len(completed), 8)

    def test_waiting_count_accurate(self):
        """waiting_count 应在线程排队时正确反映队列长度。"""
        sem = CountingSemaphore(limit=1, kind="test")
        sem.try_acquire()   # occupy the single slot
        done = threading.Event()

        def waiter():
            with sem.acquire("waiter"):
                done.wait(timeout=5)

        threads = [threading.Thread(target=waiter) for _ in range(2)]
        for t in threads: t.start()
        time.sleep(0.08)

        # 2 threads should be waiting
        self.assertEqual(sem.waiting_count, 2)

        sem.release()   # release original slot → one waiter proceeds
        time.sleep(0.05)
        done.set()
        for t in threads: t.join(timeout=5)
        sem.release()   # release the slot taken by the waiter that succeeded


class TestCountingSemaphoreDynamicLimit(unittest.TestCase):
    """动态调整 limit 的行为测试。"""

    def test_increase_limit_wakes_waiters(self):
        """增大 limit 后排队线程应该被唤醒。"""
        sem = CountingSemaphore(limit=1, kind="test")
        sem.try_acquire()   # fill the slot

        woke = threading.Event()

        def waiter():
            with sem.acquire("w"):
                woke.set()

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        self.assertFalse(woke.is_set())

        # Increase limit → waiter should wake
        sem.limit = 2
        woke.wait(timeout=3)
        self.assertTrue(woke.is_set())
        t.join(timeout=5)
        sem.release()

    def test_limit_minimum_is_one(self):
        sem = CountingSemaphore(limit=3, kind="test")
        sem.limit = 0
        self.assertEqual(sem.limit, 1)

    def test_decrease_limit_does_not_crash(self):
        """减小 limit 不应崩溃（现有持有者继续，新请求受新 limit 限制）。"""
        sem = CountingSemaphore(limit=4, kind="test")
        for _ in range(3):
            sem.try_acquire()
        sem.limit = 2     # shrink below current active count
        # Future acquires should be gated at 2
        for _ in range(3):
            sem.release()


# ══════════════════════════════════════════════════════════════════════════════
# 模块级单例测试
# ══════════════════════════════════════════════════════════════════════════════

class TestModuleSingletons(unittest.TestCase):

    def setUp(self):
        init_concurrency(max_tasks=4, max_llm_calls=8)

    def test_get_task_sem_returns_semaphore(self):
        sem = get_task_sem()
        self.assertIsInstance(sem, CountingSemaphore)
        self.assertEqual(sem.limit, 4)

    def test_get_llm_sem_returns_semaphore(self):
        sem = get_llm_sem()
        self.assertIsInstance(sem, CountingSemaphore)
        self.assertEqual(sem.limit, 8)

    def test_set_max_tasks_updates_limit(self):
        set_max_tasks(6)
        self.assertEqual(get_task_sem().limit, 6)
        set_max_tasks(4)   # restore

    def test_set_max_llm_calls_updates_limit(self):
        set_max_llm_calls(3)
        self.assertEqual(get_llm_sem().limit, 3)
        set_max_llm_calls(8)   # restore

    def test_concurrency_snapshot_structure(self):
        snap = concurrency_snapshot()
        self.assertIn("tasks", snap)
        self.assertIn("llm", snap)
        for key in ("limit", "active", "waiting", "available", "waiters"):
            self.assertIn(key, snap["tasks"])
            self.assertIn(key, snap["llm"])

    def test_concurrency_snapshot_values(self):
        snap = concurrency_snapshot()
        self.assertEqual(snap["tasks"]["limit"], 4)
        self.assertEqual(snap["llm"]["limit"], 8)
        self.assertEqual(snap["tasks"]["active"], 0)
        self.assertEqual(snap["llm"]["active"], 0)

    def test_reinit_resets_counts(self):
        """重新初始化应重置所有计数。"""
        get_task_sem().try_acquire()
        self.assertEqual(get_task_sem().active_count, 1)
        init_concurrency(max_tasks=2, max_llm_calls=4)
        self.assertEqual(get_task_sem().active_count, 0)
        self.assertEqual(get_task_sem().limit, 2)
        init_concurrency(max_tasks=4, max_llm_calls=8)   # restore


# ══════════════════════════════════════════════════════════════════════════════
# StatusBar 渲染测试（不崩溃即通过）
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusBar(unittest.TestCase):

    def setUp(self):
        init_concurrency(max_tasks=4, max_llm_calls=8)

    def test_render_idle_returns_empty(self):
        """空闲时 _build_lines 不包含 LLM 队列信息。"""
        from mini_agent.orchestrator.status_bar import _build_lines
        with patch("mini_agent.tools.orchestration.get_task_manager", return_value=None), \
             patch("mini_agent.orchestrator.concurrency.concurrency_snapshot",
                   return_value={"llm": {"active": 0, "waiting": 0, "limit": 8, "waiters": []}}):
            lines = _build_lines()
        self.assertIsInstance(lines, list)
        self.assertFalse(any("🤖" in line for line in lines))

    def test_render_with_active_tasks_has_content(self):
        """有活跃任务时，_build_lines 返回包含计数信息的行。"""
        from mini_agent.orchestrator.status_bar import _build_lines
        mgr = MagicMock()
        mgr.stats.return_value = {
            "running": 2, "pending": 1, "done": 0,
            "failed": 0, "cancelled": 0, "total": 3,
        }
        mgr.max_workers = 4
        mgr.list_records.return_value = []
        with patch("mini_agent.tools.orchestration.get_task_manager", return_value=mgr), \
             patch("mini_agent.orchestrator.concurrency.concurrency_snapshot",
                   return_value={"llm": {"active": 0, "waiting": 0, "limit": 8, "waiters": []}}):
            lines = _build_lines()
        self.assertTrue(any("2/4" in line for line in lines))

    def test_render_with_llm_active_has_content(self):
        """有活跃 LLM 请求时，行中包含计数信息。"""
        from mini_agent.orchestrator.status_bar import _build_lines
        with patch("mini_agent.tools.orchestration.get_task_manager", return_value=None), \
             patch("mini_agent.orchestrator.concurrency.concurrency_snapshot",
                   return_value={"llm": {"active": 3, "waiting": 0, "limit": 8, "waiters": []}}):
            lines = _build_lines()
        self.assertTrue(any("3/8" in line for line in lines))

    def test_start_stop_no_crash(self):
        from mini_agent.orchestrator.status_bar import StatusBar
        bar = StatusBar()
        bar.start()
        time.sleep(0.05)
        bar.stop()   # should not raise

    def test_context_manager(self):
        from mini_agent.orchestrator.status_bar import StatusBar
        with StatusBar():
            time.sleep(0.05)
        # Should reach here without exception

    def test_module_singleton_start_stop(self):
        from mini_agent.orchestrator.status_bar import start_status_bar, stop_status_bar, get_status_bar
        from mini_agent.ui.terminal import get_terminal

        start_status_bar()
        self.assertIsNotNone(get_status_bar())
        # 启动后 Terminal 注册了内容提供者回调
        self.assertIsNotNone(get_terminal()._statusbar_provider)

        stop_status_bar()
        # 停止后回调被清除（单例对象本身依然存在）
        self.assertIsNone(get_terminal()._statusbar_provider)



# ══════════════════════════════════════════════════════════════════════════════
# ProviderMixin LLM 信号量集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderMixinLLMSemaphore(unittest.TestCase):
    """验证 LLM 信号量在 _traced_chat/_traced_stream 中正确生效。"""

    def setUp(self):
        init_concurrency(max_tasks=4, max_llm_calls=2)

    def tearDown(self):
        init_concurrency(max_tasks=4, max_llm_calls=8)

    def _make_provider(self):
        """构造带 Mock SDK 的 AnthropicProvider。"""
        from mini_agent.llm.providers.anthropic import AnthropicProvider
        from mini_agent.llm.base import LLMConfig
        cfg = LLMConfig(provider="anthropic", model="claude-opus-4-5", api_key="test")
        with patch.object(AnthropicProvider, "_build_client", return_value=MagicMock()):
            return AnthropicProvider(cfg)

    def _make_sdk_response(self, text="ok"):
        """构造 _do_chat 的返回值（统一的 LLMResponse，而非原始 SDK 对象）。"""
        from mini_agent.llm.base import LLMResponse, LLMUsage
        return LLMResponse(
            text=text,
            tool_calls=[],
            usage=LLMUsage(input_tokens=5, output_tokens=10),
            stop_reason="end_turn",
        )

    def test_llm_semaphore_limits_concurrent_calls(self):
        """同时超过 max_llm_calls 的请求应排队，不并发超限。"""
        provider = self._make_provider()
        max_concurrent = [0]
        lock = threading.Lock()
        call_count = [0]
        errors = []

        def slow_do_chat(messages, system, tools):
            with lock:
                call_count[0] += 1
                if call_count[0] > 2:
                    errors.append(f"Too many concurrent: {call_count[0]}")
                if call_count[0] > max_concurrent[0]:
                    max_concurrent[0] = call_count[0]
            time.sleep(0.05)
            with lock:
                call_count[0] -= 1
            return self._make_sdk_response()

        provider._do_chat = slow_do_chat

        threads = [
            threading.Thread(target=provider.chat, args=([], "sys", []))
            for _ in range(6)
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=15)

        self.assertEqual(errors, [], f"Concurrency violated: {errors}")
        self.assertLessEqual(max_concurrent[0], 2)

    def test_all_llm_calls_complete(self):
        """即使排队，所有 LLM 信号量获取都应成功完成（直接测信号量，不走 provider 链路）。"""
        sem = get_llm_sem()
        completed = []
        lock = threading.Lock()

        def worker(i):
            with sem.acquire(f"worker-{i}"):
                time.sleep(0.005)
            with lock:
                completed.append(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=30)

        self.assertEqual(len(completed), 8)

    def test_llm_semaphore_waiting_count_visible(self):
        """排队等待时 waiting_count 应大于 0。"""
        provider = self._make_provider()
        sem = get_llm_sem()
        # Pre-occupy all slots
        sem.try_acquire()
        sem.try_acquire()

        waiting_counts = []
        done = threading.Event()

        def waiter():
            with sem.acquire("test-waiter"):
                waiting_counts.append(1)
                done.wait(timeout=3)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        self.assertGreater(sem.waiting_count, 0)

        sem.release()
        done.set()
        t.join(timeout=5)
        sem.release()


# ══════════════════════════════════════════════════════════════════════════════
# 并发快照中的 Waiter 信息测试
# ══════════════════════════════════════════════════════════════════════════════

class TestWaiterInfo(unittest.TestCase):

    def setUp(self):
        init_concurrency(max_tasks=1, max_llm_calls=1)

    def tearDown(self):
        init_concurrency(max_tasks=4, max_llm_calls=8)

    def test_waiter_has_label(self):
        sem = get_task_sem()
        sem.try_acquire()
        done = threading.Event()

        def waiter():
            with sem.acquire("task-xyz"):
                done.wait(timeout=3)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)

        waiters = sem.snapshot_waiters()
        self.assertTrue(any("task-xyz" in w.label for w in waiters))

        sem.release()
        done.set()
        t.join(timeout=5)

    def test_waiter_waited_seconds_increases(self):
        sem = get_llm_sem()
        sem.try_acquire()
        done = threading.Event()

        def waiter():
            with sem.acquire("llm-call"):
                done.wait(timeout=3)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.15)

        waiters = sem.snapshot_waiters()
        if waiters:
            self.assertGreaterEqual(waiters[0].waited_seconds, 0.1)

        sem.release()
        done.set()
        t.join(timeout=5)

    def test_snapshot_waiters_reflects_queue(self):
        sem = get_task_sem()
        sem.try_acquire()    # fill the 1 slot
        done = threading.Event()
        ready = threading.Event()

        def make_waiter(label):
            def fn():
                with sem.acquire(label):
                    ready.set()
                    done.wait(timeout=3)
            return fn

        threads = [threading.Thread(target=make_waiter(f"w{i}")) for i in range(3)]
        for t in threads: t.start()
        time.sleep(0.1)

        snapshot = concurrency_snapshot()
        self.assertGreaterEqual(snapshot["tasks"]["waiting"], 2)

        sem.release()
        ready.wait(timeout=3)
        done.set()
        for t in threads: t.join(timeout=5)
        for _ in range(2):
            try: sem.release()
            except Exception: pass


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
