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


if __name__ == "__main__":
    unittest.main()
