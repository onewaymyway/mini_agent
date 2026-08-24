"""
tests/test_hybrid_exec_playbook_runner.py

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节：
PlaybookRunner —— SKILL 档"参照 playbook 执行"的执行器。

mock 掉 `_agent.run_agent_prompt`（同 test_hybrid_exec_p2.py 的既有模式），
不发起真实 Agent/LLM 调用，验证 prompt 拼装、max_turns/session_label 透传、
以及"PLAYBOOK_INVALID:"前缀识别为 PlaybookInvalidError。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mini_agent.hybrid_exec.playbook_runner import PlaybookInvalidError, PlaybookRunner
from mini_agent.hybrid_exec.runner import RunnerAppConfig
from mini_agent.hybrid_exec.spec import TaskSpec


def _app_cfg() -> RunnerAppConfig:
    return RunnerAppConfig(project_root="/tmp/fake_project")


def _task(**kw) -> TaskSpec:
    kw.setdefault("task_id", "t1")
    kw.setdefault("description", "demo task")
    kw.setdefault("input_data", {"query": "自主进化Agent"})
    return TaskSpec(**kw)


class TestPlaybookRunner(unittest.TestCase):
    def test_requires_explicit_max_turns(self):
        """max_turns 没有默认值——用户已确认暂不预设该数值，构造函数必须
        显式传入，不能靠一个隐藏默认值蒙混过去。"""
        with self.assertRaises(TypeError):
            PlaybookRunner(_app_cfg())  # noqa: 缺少必填的 max_turns

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_run_returns_raw_text_on_success(self, mock_run):
        mock_run.return_value = '{"results": [{"title": "t", "url": "https://x.example/"}]}'
        runner = PlaybookRunner(_app_cfg(), max_turns=12)
        output = runner.run(_task(), "# 步骤说明\n1. 打开搜索页\n2. 提取结果列表\n")
        self.assertIn("results", output)
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("session_label"), "skill_playbook")
        self.assertEqual(kwargs.get("max_turns"), 12)

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_prompt_includes_playbook_and_task_description(self, mock_run):
        mock_run.return_value = "ok"
        runner = PlaybookRunner(_app_cfg(), max_turns=10)
        runner.run(_task(description="抓取知乎搜索结果"), "playbook 正文标记 XYZ")

        args, kwargs = mock_run.call_args
        prompt_arg = args[2]  # run_agent_prompt(app_cfg, task, prompt, ...)
        self.assertIn("抓取知乎搜索结果", prompt_arg)
        self.assertIn("playbook 正文标记 XYZ", prompt_arg)
        self.assertIn("自主进化Agent", prompt_arg)  # input_data 被序列化进了 prompt

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_playbook_invalid_prefix_raises(self, mock_run):
        mock_run.return_value = "PLAYBOOK_INVALID: 页面结构已彻底改版，原步骤全部失效"
        runner = PlaybookRunner(_app_cfg(), max_turns=10)
        with self.assertRaises(PlaybookInvalidError) as cm:
            runner.run(_task(), "旧的步骤说明")
        self.assertIn("页面结构已彻底改版", str(cm.exception))

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_playbook_invalid_prefix_without_reason_uses_fallback_message(self, mock_run):
        mock_run.return_value = "PLAYBOOK_INVALID:"
        runner = PlaybookRunner(_app_cfg(), max_turns=10)
        with self.assertRaises(PlaybookInvalidError) as cm:
            runner.run(_task(), "旧的步骤说明")
        self.assertIn("未说明", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
