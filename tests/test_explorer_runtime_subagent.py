"""
tests/test_explorer_runtime_subagent.py

对应 next_doc/generative_capability_explorer_rearch_plan.md 阶段一/阶段四：
验证 `explorer_runtime.build_subagent_explorer()` —— 探索器从"手写决策循环"
切换为"构造真实 SubAgent 驱动"之后的接线逻辑。

风格延续 tests/test_subagent_inheritance.py：不发起真实 LLM 请求，改为
monkeypatch `SubAgent._run_with_capture`，在其内部直接调用探索用 Agent 上
已注册的 `finish`/`report_failure`/领域桥接工具（这些工具是本文件要验证的
真正对象），模拟"探索子agent这一轮决定怎么收尾"，而不是重新实现一个假 LLM。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa: F401（确保内置工具已注册）
from mini_agent.orchestrator.sub_agent import SubAgent
from mini_agent.skills.generative_capability.explorer_runtime import (
    build_subagent_explorer,
    FINISH_TOOL,
    REPORT_FAILURE_TOOL,
)


def make_cfg(project_root: Path = None, **overrides):
    from mini_agent.config import load_config
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class TestBuildSubagentExplorerFinish(unittest.TestCase):
    """finish 工具被调用 → success=True，且 script_source 透传。"""

    def test_finish_success_with_script_source(self):
        cfg = make_cfg()
        captured = {}

        def fake_run_with_capture(self, agent, prompt):
            captured["agent"] = agent
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={"result": {"text": "HELLO"}}, script_source="def run(input): ...")

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            trace = explorer(
                {"text": "把 hello 转大写", "content": {"text": "hello"}},
                {"type": "object", "required": ["result"]},
                {"max_turns": 5},
            )

        self.assertTrue(trace.success)
        self.assertEqual(trace.data, {"result": {"text": "HELLO"}})
        self.assertEqual(trace.script_source, "def run(input): ...")
        self.assertEqual(trace.stop_reason, "finished")
        # finish/report_failure 都应该已注册为真实工具，而不是手写分支
        self.assertIn(FINISH_TOOL, captured["agent"].registry.names)
        self.assertIn(REPORT_FAILURE_TOOL, captured["agent"].registry.names)

    def test_finish_without_script_source_defaults_to_none(self):
        cfg = make_cfg()

        def fake_run_with_capture(self, agent, prompt):
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={"result": {"text": "X"}})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            trace = explorer({"text": "t"}, {}, {})

        self.assertTrue(trace.success)
        self.assertIsNone(trace.script_source)


class TestBuildSubagentExplorerReportFailure(unittest.TestCase):
    """report_failure 工具被调用 → success=False，如实携带 reason。"""

    def test_report_failure(self):
        cfg = make_cfg()

        def fake_run_with_capture(self, agent, prompt):
            rf_fn = agent.registry.get(REPORT_FAILURE_TOOL).fn
            return rf_fn(reason="遇到登录墙，无法继续")

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            trace = explorer({"text": "t"}, {}, {})

        self.assertFalse(trace.success)
        self.assertEqual(trace.error, "遇到登录墙，无法继续")
        self.assertEqual(trace.stop_reason, "reported_failure")


class TestBuildSubagentExplorerBudgetExhausted(unittest.TestCase):
    """既不调用 finish 也不调用 report_failure → 判定为步数预算耗尽，不伪造成功。"""

    def test_neither_finish_nor_report_failure(self):
        cfg = make_cfg()

        def fake_run_with_capture(self, agent, prompt):
            return "模型只是随便说了点什么，没有调用任何终态工具。"

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            trace = explorer({"text": "t"}, {}, {"max_turns": 7})

        self.assertFalse(trace.success)
        self.assertEqual(trace.stop_reason, "step_budget")
        self.assertIn("max_turns=7", trace.error)


class TestBuildSubagentExplorerDomainToolBridge(unittest.TestCase):
    """领域声明的底层原语（如 browser_navigate）应被桥接为真实工具，转发给 tool_executor。"""

    def test_domain_tool_bridged_and_forwarded(self):
        cfg = make_cfg()
        calls = []

        def fake_tool_executor(name, tool_input):
            calls.append((name, tool_input))
            return {"ok": True, "echo": tool_input}

        bridge_results = {}

        def fake_run_with_capture(self, agent, prompt):
            bridge_fn = agent.registry.get("my_domain_tool").fn
            bridge_results["result"] = bridge_fn(foo="bar")
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg, tool_executor=fake_tool_executor)
            trace = explorer(
                {"text": "t"}, {},
                {"allowed_tools": ["my_domain_tool"], "max_turns": 5},
            )

        self.assertTrue(trace.success, msg=trace.error)
        self.assertEqual(bridge_results["result"], {"ok": True, "echo": {"foo": "bar"}})
        self.assertEqual(calls, [("my_domain_tool", {"foo": "bar"})])

    def test_no_tool_executor_means_no_bridge(self):
        cfg = make_cfg()

        bridge_absent = {}

        def fake_run_with_capture(self, agent, prompt):
            bridge_absent["absent"] = "my_domain_tool" not in agent.registry.names
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg, tool_executor=None)
            trace = explorer({"text": "t"}, {}, {"allowed_tools": ["my_domain_tool"], "max_turns": 5})

        self.assertTrue(trace.success, msg=trace.error)
        self.assertTrue(bridge_absent["absent"])


class TestResolveDomainToolNames(unittest.TestCase):
    """_resolve_domain_tool_names() 兼容三种历史写法（见函数 docstring）。"""

    def test_inline_allowed_tools(self):
        from mini_agent.skills.generative_capability.explorer_runtime import _resolve_domain_tool_names
        names = _resolve_domain_tool_names({"allowed_tools": ["a", "b"]})
        self.assertEqual(names, ["a", "b"])

    def test_json_file_allowed_tools(self, ):
        import json
        import tempfile
        from mini_agent.skills.generative_capability.explorer_runtime import _resolve_domain_tool_names
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tool_allowlist.json"
            p.write_text(json.dumps({"allowed_tools": ["browser_navigate", "browser_click"]}), encoding="utf-8")
            names = _resolve_domain_tool_names({"_resolved_tool_allowlist_path": str(p)})
        self.assertEqual(names, ["browser_navigate", "browser_click"])

    def test_json_file_tools_list_shape(self):
        import json
        import tempfile
        from mini_agent.skills.generative_capability.explorer_runtime import _resolve_domain_tool_names
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tool_allowlist.json"
            p.write_text(json.dumps({"tools": [{"name": "text_transform_apply"}]}), encoding="utf-8")
            names = _resolve_domain_tool_names({"_resolved_tool_allowlist_path": str(p)})
        self.assertEqual(names, ["text_transform_apply"])

    def test_fallback_to_base_tools(self):
        from mini_agent.skills.generative_capability.explorer_runtime import _resolve_domain_tool_names
        names = _resolve_domain_tool_names({"base_tools": ["doc-core"]})
        self.assertEqual(names, ["doc-core"])


class TestBuildSubagentExplorerToolExecutorRegistrySync(unittest.TestCase):
    """
    [BUGFIX 回归测试] 探索用 Agent 的 `_tool_executor.registry` 必须与
    `agent.registry` 是同一个对象，工具才能在真实派发路径
    （`ToolExecutor.execute_all()` -> `self.registry.call(...)`）里被找到。

    此前的 bug：`build_subagent_explorer()` 在 `_build_agent()` 返回*之后*
    重新赋值了 `agent.registry`（换成私有 filtered 副本），但
    `agent._tool_executor.registry` 在 `Agent.__init__` 期间已经捕获了
    重新赋值*之前*的那个（全局默认）registry 对象引用，此后两者不再是同一
    对象。`finish`/`report_failure`/领域桥接工具都注册在了新对象上，
    但真实工具派发（`agent.run_turn()` 内部走的是
    `self._tool_executor.execute_all()` -> `self.registry.call(name, ...)`）
    用的是旧对象——导致模型一旦真的尝试调用 `browser_navigate` 之类的
    工具，会得到 `Unknown tool: 'browser_navigate'`，即使
    `agent.registry.names` 里明明有这个名字。

    这个 bug 是 `test_finish_success_with_script_source` 等既有用例测不出来
    的：那些用例直接调用 `agent.registry.get(FINISH_TOOL).fn(...)`，绕过了
    `ToolExecutor.execute_all()` 这条真实派发路径，天然掩盖了两个 registry
    对象分裂的问题。本测试改为通过 `agent._tool_executor.registry`（真实派发
    时实际会用到的那个引用）去查找/调用工具，才能真正复现并锁定这个修复。
    """

    def test_tool_executor_registry_is_same_object_as_agent_registry(self):
        cfg = make_cfg()
        captured = {}

        def fake_run_with_capture(self, agent, prompt):
            captured["agent"] = agent
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            explorer({"text": "t"}, {}, {})

        agent = captured["agent"]
        self.assertIsNotNone(getattr(agent, "_tool_executor", None))
        self.assertIs(
            agent._tool_executor.registry, agent.registry,
            msg="agent._tool_executor.registry 必须与 agent.registry 是同一个"
                "对象，否则真实工具派发路径（ToolExecutor.execute_all）会用"
                "过期的 registry，看不到 finish/report_failure/领域桥接工具。",
        )

    def test_finish_tool_reachable_via_tool_executor_registry(self):
        """更直接的回归：真实派发时会用到的引用（_tool_executor.registry）
        必须能查到并成功调用 finish/领域桥接工具，而不只是 agent.registry。
        """
        cfg = make_cfg()
        calls = []

        def fake_tool_executor(name, tool_input):
            calls.append((name, tool_input))
            return {"ok": True}

        captured = {}

        def fake_run_with_capture(self, agent, prompt):
            captured["agent"] = agent
            # 故意不走 agent.registry，而是走真实派发时会用到的
            # agent._tool_executor.registry，模拟 ToolExecutor.execute_all()
            # 内部 self.registry.call(name, tool_input) 的真实查找路径。
            exec_registry = agent._tool_executor.registry
            bridge_result = exec_registry.call("browser_navigate", {"url": "https://example.com"})
            finish_fn = exec_registry.get(FINISH_TOOL).fn
            return finish_fn(data={"bridged": bridge_result})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg, tool_executor=fake_tool_executor)
            trace = explorer(
                {"text": "t"}, {},
                {"allowed_tools": ["browser_navigate"], "max_turns": 5},
            )

        self.assertTrue(trace.success, msg=trace.error)
        self.assertEqual(calls, [("browser_navigate", {"url": "https://example.com"})])


class TestExplorerExcludedTools(unittest.TestCase):
    """[阶段二十二] 回归防止"嵌套探索"：探索子agent的 registry 里不应该出现
    capability_call/skill_list/spawn_agent 等元编排类工具——否则探索子agent
    能在探索过程中再触发一次 capability_call，递归构造出下一层探索子agent，
    深度没有上限。"""

    def test_capability_call_and_meta_tools_stripped_from_explorer_registry(self):
        import mini_agent.tools.builtin  # noqa: F401
        from mini_agent.tools import get_default_registry
        from mini_agent.skills.generative_capability.explorer_runtime import (
            _EXPLORER_EXCLUDED_TOOLS,
        )

        # 手工在全局默认 registry 里注册几个"元编排类"占位工具，模拟真实系统
        # 里 capability_call/skill_list/spawn_agent 等工具已经注册好的情形
        # （测试环境里 mini_agent.tools.builtin 不一定真的注册了 capability_call
        # 这种上层工具，所以显式注册占位版本，只关心"是否被过滤掉"这件事）。
        registry = get_default_registry()
        for name in ("capability_call", "skill_list", "spawn_agent"):
            if name not in registry.names:
                registry.register_fn(
                    fn=lambda **kw: {"ok": True},
                    name=name,
                    description="占位工具，仅用于验证探索子agent黑名单过滤",
                    input_schema={"type": "object", "properties": {}},
                )

        cfg = make_cfg()
        captured = {}

        def fake_run_with_capture(self, agent, prompt):
            captured["agent"] = agent
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={"result": {"text": "ok"}})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            trace = explorer({"text": "t"}, {"type": "object", "required": ["result"]}, {"max_turns": 5})

        self.assertTrue(trace.success, msg=trace.error)
        explorer_names = set(captured["agent"].registry.names)
        for excluded_name in ("capability_call", "skill_list", "spawn_agent"):
            self.assertNotIn(
                excluded_name, explorer_names,
                msg=f"{excluded_name} 不应该出现在探索子agent的工具集里（会导致嵌套探索/失控）",
            )
        # finish/report_failure 必须还在，不能被黑名单误伤
        self.assertIn(FINISH_TOOL, explorer_names)
        self.assertIn(REPORT_FAILURE_TOOL, explorer_names)
        # 黑名单本身覆盖的这几个名字要跟常量定义对得上，防止以后有人改了黑名单
        # 常量却忘了同步这条断言
        self.assertTrue({"capability_call", "skill_list", "spawn_agent"} <= _EXPLORER_EXCLUDED_TOOLS)


class TestExplorerConsoleVerbose(unittest.TestCase):
    """[阶段二十三] 探索子agent自己的控制台输出应该带上完整 tool_input——
    不依赖顶层主agent是否开了 --verbose（SubAgent._build_agent() 默认
    verbose=False，是刷屏保护，跟"探索子agent排查工具调用参数"是两个不同的
    需求，需要单独覆盖）。"""

    def test_explorer_agent_cfg_verbose_forced_true(self):
        cfg = make_cfg(verbose=False)
        self.assertFalse(cfg.verbose, msg="前置条件：base_cfg 本身不是 verbose，才能验证是探索子agent自己覆盖的")
        captured = {}

        def fake_run_with_capture(self, agent, prompt):
            captured["agent"] = agent
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(data={"result": {"text": "ok"}})

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(cfg)
            trace = explorer({"text": "t"}, {"type": "object", "required": ["result"]}, {"max_turns": 5})

        self.assertTrue(trace.success, msg=trace.error)
        self.assertTrue(
            captured["agent"].cfg.verbose,
            msg="探索子agent自己的 cfg.verbose 应该被强制打开，控制台才能打印工具调用的完整参数",
        )


if __name__ == "__main__":
    unittest.main()
