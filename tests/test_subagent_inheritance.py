"""
tests/test_subagent_inheritance.py — Stage 3 / 3.3（Phase E）验证

对应 self_evolution_implementation_plan.md Stage 3.3：
  SubAgent 信息继承 —— 主 agent 当前激活的 skill 列表通过 spawn_agent /
  spawn_named_agent 传给 Task.active_skills，SubAgent 构建自己的 Agent 时
  按名称重新激活；ToolResultCache 可在多个 SubAgent 间共享并发安全；
  SubAgent 触发的规则型 lesson 通过 TaskManager 在任务终态时触发主 agent
  memory backend reload()，而不是只留在 TaskRecord.log_lines 里。

风格延续 tests/test_orchestrator.py：unittest.TestCase + 共享 make_task/make_cfg
工厂，_build_agent 直接调用（纯本地构造，不发起真实 LLM 请求）来验证装配逻辑，
run_turn 仍然按既有测试的 mock 方式打桩，不在本文件重复造轮子。
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa: F401（确保内置工具已注册）
from mini_agent.orchestrator.task import Task, TaskRecord, TaskStatus
from mini_agent.orchestrator.task_manager import TaskManager
from mini_agent.orchestrator.sub_agent import SubAgent


def make_task(prompt="Do something", **kwargs) -> Task:
    return Task(prompt=prompt, **kwargs)


def make_cfg(project_root: Path = None, **overrides):
    from mini_agent.config import load_config
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def write_skill(skills_root: Path, name: str, description: str = "test skill") -> Path:
    """在 <skills_root>/<name>/SKILL.md 写入一个最小可被 SkillLoader 解析的 skill。"""
    d = skills_root / name
    d.mkdir(parents=True, exist_ok=True)
    content = f"---\nname: {name}\ndescription: {description}\n---\n\nbody for {name}.\n"
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


# ══════════════════════════════════════════════════════════════════════════════
# Task.active_skills 字段
# ══════════════════════════════════════════════════════════════════════════════

class TestTaskActiveSkillsField(unittest.TestCase):

    def test_default_is_empty_list(self):
        task = make_task()
        self.assertEqual(task.active_skills, [])

    def test_explicit_active_skills(self):
        task = make_task(active_skills=["foo", "bar"])
        self.assertEqual(task.active_skills, ["foo", "bar"])

    def test_each_task_gets_independent_list(self):
        """default_factory=list 防止多个 Task 实例共享同一个可变默认值。"""
        t1 = make_task()
        t2 = make_task()
        t1.active_skills.append("x")
        self.assertEqual(t2.active_skills, [])


# ══════════════════════════════════════════════════════════════════════════════
# spawn_agent / spawn_named_agent / spawn_agents 传递 active_skills
# ══════════════════════════════════════════════════════════════════════════════

class TestSpawnToolsPropagateActiveSkills(unittest.TestCase):

    def setUp(self):
        from mini_agent.tools import orchestration as orch
        self._orch = orch
        # 每个测试独立，避免 thread-local provider 状态串到其他测试文件
        orch.set_active_skills_provider(None)

        self.cfg = make_cfg()
        self.mgr = TaskManager(self.cfg, max_workers=2)
        orch._task_manager = self.mgr

    def tearDown(self):
        self.mgr.stop()
        self._orch._task_manager = None
        self._orch.set_active_skills_provider(None)

    def test_no_provider_registered_yields_empty_active_skills(self):
        from mini_agent.tools.orchestration import spawn_agent
        import json
        result = json.loads(spawn_agent(prompt="hello"))
        rec = self.mgr.get(result["task_id"])
        self.assertEqual(rec.task.active_skills, [])

    def test_spawn_agent_inherits_active_skills(self):
        from mini_agent.tools.orchestration import spawn_agent, set_active_skills_provider
        import json
        set_active_skills_provider(lambda: ["bash-rm-safety", "code-review"])
        result = json.loads(spawn_agent(prompt="hello"))
        rec = self.mgr.get(result["task_id"])
        self.assertEqual(rec.task.active_skills, ["bash-rm-safety", "code-review"])

    def test_spawn_agents_batch_inherits_active_skills(self):
        from mini_agent.tools.orchestration import spawn_agents, set_active_skills_provider
        import json
        set_active_skills_provider(lambda: ["foo"])
        result = json.loads(spawn_agents(tasks=[
            {"prompt": "task A"},
            {"prompt": "task B"},
        ]))
        self.assertEqual(result["spawned"], 2)
        for entry in result["tasks"]:
            rec = self.mgr.get(entry["task_id"])
            self.assertEqual(rec.task.active_skills, ["foo"])

    def test_provider_exception_yields_empty_list_not_crash(self):
        from mini_agent.tools.orchestration import spawn_agent, set_active_skills_provider
        import json

        def boom():
            raise RuntimeError("skill_loader exploded")

        set_active_skills_provider(boom)
        result = json.loads(spawn_agent(prompt="hello"))  # 不应抛异常
        rec = self.mgr.get(result["task_id"])
        self.assertEqual(rec.task.active_skills, [])

    def test_provider_is_thread_local(self):
        """主线程注册的 provider，不应该泄漏给另一个线程（模拟并发 SubAgent 场景）。"""
        from mini_agent.tools.orchestration import (
            set_active_skills_provider, _get_active_skills,
        )
        set_active_skills_provider(lambda: ["main-thread-skill"])

        other_thread_result = []

        def worker():
            # 另一个线程没有注册 provider，应该看到空列表，而不是主线程的值
            other_thread_result.append(_get_active_skills())

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        self.assertEqual(other_thread_result, [[]])
        # 主线程自己的 provider 不受影响
        self.assertEqual(_get_active_skills(), ["main-thread-skill"])


# ══════════════════════════════════════════════════════════════════════════════
# SubAgent._build_agent 按 active_skills 激活 skill
# ══════════════════════════════════════════════════════════════════════════════

class TestSubAgentSkillActivation(unittest.TestCase):
    """
    注意：register_skill_tools() 把 skill_list/skill_activate/... 注册到
    Agent.registry（未显式传入时默认是进程级单例 get_default_registry()）。
    生产环境一个进程同一时刻通常只有一个"持有 skill_loader 的 Agent"在用
    这个全局 registry；但本测试类在同一个测试进程里连续构造多个带
    skill_loader 的 Agent（验证装配逻辑本身），会触发 ToolRegistry.register()
    的"重复注册"保护。这是测试本身的隔离问题，不是被测代码的缺陷——
    用 setUp/tearDown 快照并恢复全局 registry 的 _tools 字典来规避。
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.skills_root = self.project_root / ".claude" / "skills"
        self.skills_root.mkdir(parents=True)
        write_skill(self.skills_root, "bash-rm-safety")
        write_skill(self.skills_root, "code-review")

        from mini_agent.tools import get_default_registry
        self._registry_snapshot = dict(get_default_registry()._tools)

    def tearDown(self):
        from mini_agent.tools import get_default_registry
        get_default_registry()._tools = dict(self._registry_snapshot)
        self._tmpdir.cleanup()

    def test_no_active_skills_means_no_skill_loader(self):
        task = make_task(active_skills=[])
        rec = TaskRecord(task=task)
        cfg = make_cfg(project_root=self.project_root)
        sub = SubAgent(rec, cfg)
        agent = sub._build_agent(task)
        self.assertIsNone(agent.skill_loader)

    def test_active_skills_creates_loader_and_activates(self):
        task = make_task(active_skills=["bash-rm-safety"])
        rec = TaskRecord(task=task)
        cfg = make_cfg(project_root=self.project_root)
        sub = SubAgent(rec, cfg)
        agent = sub._build_agent(task)
        self.assertIsNotNone(agent.skill_loader)
        self.assertIn("bash-rm-safety", agent.skill_loader.active)

    def test_multiple_active_skills_all_activated(self):
        task = make_task(active_skills=["bash-rm-safety", "code-review"])
        rec = TaskRecord(task=task)
        cfg = make_cfg(project_root=self.project_root)
        sub = SubAgent(rec, cfg)
        agent = sub._build_agent(task)
        self.assertEqual(set(agent.skill_loader.active), {"bash-rm-safety", "code-review"})

    def test_unknown_skill_name_does_not_crash(self):
        """SkillLoader.activate() 对未知名称静默返回 False，不应该让 SubAgent 构造失败。"""
        task = make_task(active_skills=["does-not-exist"])
        rec = TaskRecord(task=task)
        cfg = make_cfg(project_root=self.project_root)
        sub = SubAgent(rec, cfg)
        agent = sub._build_agent(task)  # 不应抛异常
        self.assertIsNotNone(agent.skill_loader)
        self.assertNotIn("does-not-exist", agent.skill_loader.active)

    def test_built_subagent_is_marked_is_subagent(self):
        task = make_task()
        rec = TaskRecord(task=task)
        cfg = make_cfg(project_root=self.project_root)
        sub = SubAgent(rec, cfg)
        agent = sub._build_agent(task)
        self.assertTrue(agent._is_subagent)


# ══════════════════════════════════════════════════════════════════════════════
# ToolResultCache 跨 SubAgent 共享 + 线程安全
# ══════════════════════════════════════════════════════════════════════════════

class TestSharedToolResultCache(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_task_manager_creates_shared_cache_when_enabled(self):
        from mini_agent.config.models import PerceptionConfig
        cfg = make_cfg(project_root=self.project_root)
        cfg.perception.tool_cache_enabled = True
        cfg.perception.tool_cache_max_entries = 99
        mgr = TaskManager(cfg, max_workers=2)
        self.assertIsNotNone(mgr._shared_tool_cache)
        self.assertEqual(mgr._shared_tool_cache._max_entries, 99)

    def test_task_manager_no_shared_cache_when_disabled(self):
        cfg = make_cfg(project_root=self.project_root)
        cfg.perception.tool_cache_enabled = False
        mgr = TaskManager(cfg, max_workers=2)
        self.assertIsNone(mgr._shared_tool_cache)

    def test_subagent_receives_shared_cache_instance(self):
        from mini_agent.perception.tool_cache import ToolResultCache
        cfg = make_cfg(project_root=self.project_root)
        shared = ToolResultCache(max_entries=10)

        task = make_task()
        rec = TaskRecord(task=task)
        sub = SubAgent(rec, cfg, shared_tool_cache=shared)
        agent = sub._build_agent(task)

        self.assertIs(agent._tool_cache, shared)

    def test_two_subagents_share_same_cache_object(self):
        from mini_agent.perception.tool_cache import ToolResultCache
        cfg = make_cfg(project_root=self.project_root)
        shared = ToolResultCache(max_entries=10)

        agents = []
        for _ in range(2):
            task = make_task()
            rec = TaskRecord(task=task)
            sub = SubAgent(rec, cfg, shared_tool_cache=shared)
            agents.append(sub._build_agent(task))

        self.assertIs(agents[0]._tool_cache, agents[1]._tool_cache)

    def test_private_cache_when_no_shared_cache_but_enabled(self):
        """没有传 shared_tool_cache，但 cfg 自身开启了 tool_cache：退化为各自私有缓存。"""
        cfg = make_cfg(project_root=self.project_root)
        cfg.perception.tool_cache_enabled = True

        task = make_task()
        rec = TaskRecord(task=task)
        sub = SubAgent(rec, cfg)  # shared_tool_cache=None（默认）
        agent = sub._build_agent(task)
        self.assertIsNotNone(agent._tool_cache)

    def test_cache_get_put_concurrent_no_corruption(self):
        """并发 get/put/invalidate_file 不应抛异常或破坏内部结构（线程安全冒烟测试）。"""
        from mini_agent.perception.tool_cache import ToolResultCache
        cache = ToolResultCache(max_entries=50)
        errors = []

        def writer(i):
            try:
                for j in range(200):
                    cache.put("read_file", {"path": f"/tmp/file_{i}.txt"}, f"content-{i}-{j}")
            except Exception as e:
                errors.append(e)

        def reader(i):
            try:
                for _ in range(200):
                    cache.get("read_file", {"path": f"/tmp/file_{i}.txt"})
            except Exception as e:
                errors.append(e)

        def invalidator(i):
            try:
                for _ in range(50):
                    cache.invalidate_file(f"/tmp/file_{i}.txt")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))
            threads.append(threading.Thread(target=invalidator, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        # store 容量上限始终被遵守，说明 LRU 淘汰逻辑在并发下没有失控
        self.assertLessEqual(len(cache._store), cache._max_entries)

    def test_stats_summary_does_not_raise_under_concurrency(self):
        from mini_agent.perception.tool_cache import ToolResultCache
        cache = ToolResultCache(max_entries=20)
        stop = threading.Event()

        def hammer():
            i = 0
            while not stop.is_set():
                cache.put("read_file", {"path": f"/tmp/f{i % 30}.txt"}, "x")
                cache.get("read_file", {"path": f"/tmp/f{i % 30}.txt"})
                i += 1

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for _ in range(20):
            cache.stats_summary()
            cache.hit_rate
        stop.set()
        for t in threads:
            t.join(timeout=5)


# ══════════════════════════════════════════════════════════════════════════════
# SubAgent lesson 汇总回主 agent memory（TaskManager.set_memory_sinks + reload）
# ══════════════════════════════════════════════════════════════════════════════

class TestMemorySinkReloadOnTaskCompletion(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = make_cfg(project_root=self.project_root)
        self.mgr = TaskManager(self.cfg, max_workers=2)

    def tearDown(self):
        self.mgr.stop()
        self._tmpdir.cleanup()

    def test_set_memory_sinks_stores_references(self):
        mem = MagicMock()
        gmem = MagicMock()
        self.mgr.set_memory_sinks(memory=mem, global_memory=gmem)
        self.assertIs(self.mgr._main_memory, mem)
        self.assertIs(self.mgr._main_global_memory, gmem)

    def test_handle_terminal_reloads_main_memory(self):
        mem = MagicMock()
        gmem = MagicMock()
        self.mgr.set_memory_sinks(memory=mem, global_memory=gmem)

        task = make_task()
        rec = TaskRecord(task=task)
        with self.mgr._lock:
            self.mgr._records[rec.task_id] = rec

        self.mgr._handle_terminal(rec.task_id, TaskStatus.RUNNING, TaskStatus.DONE)

        mem.reload.assert_called_once()
        gmem.reload.assert_called_once()

    def test_handle_terminal_no_reload_on_non_terminal_status(self):
        mem = MagicMock()
        self.mgr.set_memory_sinks(memory=mem, global_memory=None)
        task = make_task()
        rec = TaskRecord(task=task)
        with self.mgr._lock:
            self.mgr._records[rec.task_id] = rec

        self.mgr._handle_terminal(rec.task_id, TaskStatus.PENDING, TaskStatus.RUNNING)
        mem.reload.assert_not_called()

    def test_handle_terminal_reloads_on_failed_and_cancelled_too(self):
        mem = MagicMock()
        self.mgr.set_memory_sinks(memory=mem, global_memory=None)
        task = make_task()
        rec = TaskRecord(task=task)
        with self.mgr._lock:
            self.mgr._records[rec.task_id] = rec

        self.mgr._handle_terminal(rec.task_id, TaskStatus.RUNNING, TaskStatus.FAILED)
        self.mgr._handle_terminal(rec.task_id, TaskStatus.RUNNING, TaskStatus.CANCELLED)
        self.assertEqual(mem.reload.call_count, 2)

    def test_reload_exception_does_not_propagate(self):
        mem = MagicMock()
        mem.reload.side_effect = RuntimeError("disk error")
        self.mgr.set_memory_sinks(memory=mem, global_memory=None)
        task = make_task()
        rec = TaskRecord(task=task)
        with self.mgr._lock:
            self.mgr._records[rec.task_id] = rec

        # 不应抛异常
        self.mgr._handle_terminal(rec.task_id, TaskStatus.RUNNING, TaskStatus.DONE)

    def test_no_sinks_registered_is_noop(self):
        # set_memory_sinks 从未被调用，_main_memory/_main_global_memory 仍是 None
        task = make_task()
        rec = TaskRecord(task=task)
        with self.mgr._lock:
            self.mgr._records[rec.task_id] = rec
        self.mgr._handle_terminal(rec.task_id, TaskStatus.RUNNING, TaskStatus.DONE)  # 不应抛异常


# ══════════════════════════════════════════════════════════════════════════════
# 主 agent 构造时正确登记自己（而非被后构造的 SubAgent 覆盖）
# ══════════════════════════════════════════════════════════════════════════════

class TestMainAgentRegistersMemorySink(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)

    def tearDown(self):
        from mini_agent.tools import orchestration as orch
        orch._task_manager = None
        self._tmpdir.cleanup()

    def test_main_agent_init_registers_with_existing_task_manager(self):
        from mini_agent.tools import orchestration as orch
        from mini_agent.agent import Agent

        cfg = make_cfg(project_root=self.project_root)
        cfg.memory.enabled = True
        mgr = TaskManager(cfg, max_workers=2)
        orch._task_manager = mgr

        agent = Agent(cfg=cfg)
        self.assertIs(mgr._main_memory, agent._memory)
        mgr.stop()

    def test_subagent_does_not_override_main_registration(self):
        """is_subagent=True 构造的 Agent 不应覆盖 TaskManager 上已登记的主 agent 引用。"""
        from mini_agent.tools import orchestration as orch
        from mini_agent.agent import Agent

        cfg = make_cfg(project_root=self.project_root)
        cfg.memory.enabled = True
        mgr = TaskManager(cfg, max_workers=2)
        orch._task_manager = mgr

        main_agent = Agent(cfg=cfg)
        self.assertIs(mgr._main_memory, main_agent._memory)

        # 模拟一个在主 agent 之后才构造完成的 SubAgent（后台线程异步构造）
        sub_cfg = make_cfg(project_root=self.project_root)
        sub_cfg.memory.enabled = True
        sub_agent = Agent(cfg=sub_cfg, is_subagent=True)

        # TaskManager 登记的仍然是主 agent 的 memory，没有被 SubAgent 覆盖
        self.assertIs(mgr._main_memory, main_agent._memory)
        self.assertIsNot(mgr._main_memory, sub_agent._memory)
        mgr.stop()


if __name__ == "__main__":
    unittest.main()
