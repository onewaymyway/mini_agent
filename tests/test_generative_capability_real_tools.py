"""
tests/test_generative_capability_real_tools.py

对应 next_doc/generative-capability-skill-plan.md 阶段十二。

覆盖 `mini_agent.skills.generative_capability.real_tools`：
  1. `text_transform_apply` 各 op 的纯逻辑正确性 + 参数校验分支。
  2. `build_default_tool_executor` 的分发行为：命中的工具真的执行，未命中
     的工具如实报错（不抛异常、不伪造成功）。
"""

from __future__ import annotations

import unittest
from pathlib import Path


class TestTextTransformApply(unittest.TestCase):
    def _run(self, **kwargs):
        from mini_agent.skills.generative_capability.real_tools import text_transform_apply
        return text_transform_apply(kwargs)

    def test_no_arg_ops(self):
        self.assertEqual(self._run(text="Hi", op="upper"), {"result": "HI"})
        self.assertEqual(self._run(text="Hi", op="lower"), {"result": "hi"})
        self.assertEqual(self._run(text="Hi", op="reverse"), {"result": "iH"})
        self.assertEqual(self._run(text="  hi  ", op="strip"), {"result": "hi"})
        self.assertEqual(self._run(text="hi there", op="title"), {"result": "Hi There"})
        self.assertEqual(self._run(text="hi", op="capitalize"), {"result": "Hi"})
        self.assertEqual(self._run(text="Hi", op="swapcase"), {"result": "hI"})

    def test_append_prepend_replace(self):
        self.assertEqual(
            self._run(text="hi", op="append", args={"suffix": "!"}), {"result": "hi!"}
        )
        self.assertEqual(
            self._run(text="hi", op="prepend", args={"prefix": ">> "}), {"result": ">> hi"}
        )
        self.assertEqual(
            self._run(text="hello world", op="replace", args={"old": "world", "new": "there"}),
            {"result": "hello there"},
        )

    def test_missing_text_is_error(self):
        result = self._run(op="upper")
        self.assertIn("error", result)

    def test_unknown_op_is_error(self):
        result = self._run(text="hi", op="shout")
        self.assertIn("error", result)
        self.assertIn("不支持的 op", result["error"])

    def test_append_missing_suffix_is_error(self):
        result = self._run(text="hi", op="append", args={})
        self.assertIn("error", result)

    def test_bad_args_type_is_error(self):
        from mini_agent.skills.generative_capability.real_tools import text_transform_apply
        result = text_transform_apply({"text": "hi", "op": "upper", "args": "not a dict"})
        self.assertIn("error", result)


class TestBuildDefaultToolExecutor(unittest.TestCase):
    def test_known_tool_executes_for_real(self):
        from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

        executor = build_default_tool_executor()
        result = executor("text_transform_apply", {"text": "hi", "op": "upper"})
        self.assertEqual(result, {"result": "HI"})

    def test_unknown_tool_returns_honest_error(self):
        from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

        executor = build_default_tool_executor()
        result = executor("browser_navigate", {"url": "https://example.com"})
        self.assertIn("error", result)
        self.assertIn("占位声明", result["error"])

    def test_executor_never_raises_on_internal_exception(self):
        from mini_agent.skills.generative_capability import real_tools

        def _boom(_input):
            raise RuntimeError("boom")

        original = real_tools.REAL_TOOL_IMPLEMENTATIONS.get("text_transform_apply")
        real_tools.REAL_TOOL_IMPLEMENTATIONS["text_transform_apply"] = _boom
        try:
            executor = real_tools.build_default_tool_executor()
            result = executor("text_transform_apply", {"text": "hi", "op": "upper"})
            self.assertIn("error", result)
            self.assertIn("boom", result["error"])
        finally:
            real_tools.REAL_TOOL_IMPLEMENTATIONS["text_transform_apply"] = original


class TestSkillLocalToolImplementationLoading(unittest.TestCase):
    """阶段十四：build_default_tool_executor(skill_dir=...) 动态加载各
    generative-capability skill 通过 explorer.base_tools 声明的静态 skill
    自带实现（约定路径 <skills_root>/<base_tool>/impl/tools_impl.py）。"""

    def test_browser_site_scraper_picks_up_browser_core_impl(self):
        from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

        repo_root = Path(__file__).resolve().parents[1]
        skill_dir = repo_root / ".claude" / "skills" / "browser-site-scraper"
        executor = build_default_tool_executor(skill_dir=skill_dir)

        # browser-core 的真实实现应当被加载到，命中后不再是"占位声明"提示，
        # 而是浏览器层面的诚实失败（沙盒环境不一定有可用浏览器/调试端口）。
        result = executor(
            "browser_navigate",
            {"url": "https://example.com", "session": {"mode": "attach", "port": 19222}},
        )
        self.assertIn("ok", result)
        self.assertFalse(result["ok"])
        self.assertNotIn("占位声明", result.get("error", ""))
        self.assertIn("remote-debugging-port", result["error"])

        # 项目内置的 text_transform_apply 不应因为叠加了 browser-core 而失效。
        text_result = executor("text_transform_apply", {"text": "hi", "op": "upper"})
        self.assertEqual(text_result, {"result": "HI"})

    def test_without_skill_dir_browser_tools_stay_placeholder(self):
        from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

        executor = build_default_tool_executor()  # 不传 skill_dir，行为应与阶段十二一致
        result = executor("browser_navigate", {"url": "https://example.com"})
        self.assertIn("占位声明", result.get("error", ""))

    def test_skill_without_base_tools_impl_falls_back_gracefully(self):
        from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

        repo_root = Path(__file__).resolve().parents[1]
        skill_dir = repo_root / ".claude" / "skills" / "doc-template-generation"
        executor = build_default_tool_executor(skill_dir=skill_dir)
        # doc-core 仍未提供 impl/tools_impl.py，应当安静跳过，不抛异常、
        # 不影响其余分发表条目。
        result = executor("doc_parse_sample", {})
        self.assertIn("占位声明", result.get("error", ""))
        text_result = executor("text_transform_apply", {"text": "hi", "op": "upper"})
        self.assertEqual(text_result, {"result": "HI"})


if __name__ == "__main__":
    unittest.main()
