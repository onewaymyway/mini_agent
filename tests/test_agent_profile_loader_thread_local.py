"""
test_agent_profile_loader_thread_local.py

覆盖 next_doc/workflow_directory_mode_design.md "已知限制" 的修复：
set_effective_profile_loader / get_effective_profile_loader 必须是
thread-local，并发场景下每个线程只能看到自己注册的 loader，不会被
其他线程"最后一次写入"覆盖。
"""

from __future__ import annotations

import threading
import unittest

from mini_agent.orchestrator.agent_profiles import (
    AgentProfileLoader,
    get_effective_profile_loader,
    set_effective_profile_loader,
)


class TestEffectiveProfileLoaderThreadLocal(unittest.TestCase):
    def tearDown(self):
        # 避免测试间串状态：清空当前线程（主线程）的注册值。
        set_effective_profile_loader(None)

    def test_unset_falls_back_to_global_singleton(self):
        self.assertIsNone(get_effective_profile_loader())

    def test_set_and_get_in_same_thread(self):
        loader = AgentProfileLoader(dirs=[])
        set_effective_profile_loader(loader)
        self.assertIs(get_effective_profile_loader(), loader)

    def test_concurrent_threads_do_not_overwrite_each_other(self):
        loader_a = AgentProfileLoader(dirs=[])
        loader_b = AgentProfileLoader(dirs=[])
        seen: dict[str, object] = {}
        barrier = threading.Barrier(2)

        def worker(name: str, loader: AgentProfileLoader) -> None:
            set_effective_profile_loader(loader)
            # 等两个线程都完成注册后再读取，最大化暴露"被覆盖"的竞态。
            barrier.wait(timeout=5)
            seen[name] = get_effective_profile_loader()

        t1 = threading.Thread(target=worker, args=("a", loader_a))
        t2 = threading.Thread(target=worker, args=("b", loader_b))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertIs(seen["a"], loader_a)
        self.assertIs(seen["b"], loader_b)

    def test_thread_without_own_registration_falls_back_to_global(self):
        # 主线程注册了一个 loader，但没有主动为它注册的子线程不应该"看到"它——
        # 这正是把模块级全局变量改成 thread-local 要修复的问题。
        loader = AgentProfileLoader(dirs=[])
        set_effective_profile_loader(loader)

        seen: dict[str, object] = {}

        def worker() -> None:
            seen["child"] = get_effective_profile_loader()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        self.assertIsNot(seen["child"], loader)


if __name__ == "__main__":
    unittest.main()
