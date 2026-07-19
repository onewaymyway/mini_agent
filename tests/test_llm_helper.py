"""
tests/test_llm_helper.py

针对 next_doc/llm_helper_unification_plan.md 落地步骤第 5 步"待办"的补充单测：

  - LLMHelper 默认路径（走 client_pool.call_with_pool）
  - LLMHelper override 路径（override_model / override_provider / override_temperature
    任一被传入时，一次性构造独立 client，套用同一个 RetryPolicy，不经过 fallback chain）
  - ask() 对 LLMResponse.text 的取值 + strip 行为
  - 重试触发路径：override 分支下异常重试直到 max_retries 预算内成功
  - P0 bug 修复后的目标拆解链路：_default_llm_decompose / GoalBacklog._llm_decompose
    在真实（mock）LLMHelper 驱动下能产出多步骤，而不是历史 bug 里的静默 []/None
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.llm.base import LLMResponse, LLMUsage, LLMConfig
from mini_agent.llm.retry import RetryPolicy, FixedBackoff
from mini_agent.llm.service import LLMHelper


def make_response(text="ok") -> LLMResponse:
    return LLMResponse(
        text=text, tool_calls=[],
        usage=LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        stop_reason="end_turn",
    )


def make_app_cfg(provider="anthropic", model="claude-opus-4-5"):
    cfg = MagicMock()
    cfg.provider = provider
    cfg.model = model
    return cfg


NO_WAIT_POLICY = RetryPolicy(max_retries=3, backoff=FixedBackoff(0.0), retry_on_exception=True)


class TestLLMHelperDefaultPath(unittest.TestCase):
    """无 override_* 时应完全走 client_pool.call_with_pool，不构造独立 client。"""

    def test_chat_delegates_to_pool(self):
        pool = MagicMock()
        pool.call_with_pool.return_value = make_response("from pool")
        helper = LLMHelper(pool, make_app_cfg())

        resp = helper.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(resp.text, "from pool")
        pool.call_with_pool.assert_called_once()
        _, kwargs = pool.call_with_pool.call_args
        self.assertIn("call_fn", kwargs)
        self.assertIn("retry_policy", kwargs)

    def test_ask_strips_and_returns_text(self):
        pool = MagicMock()
        pool.call_with_pool.return_value = make_response("  hello world  \n")
        helper = LLMHelper(pool, make_app_cfg())

        result = helper.ask("prompt")

        self.assertEqual(result, "hello world")

    def test_default_path_follows_current_pool_model(self):
        """不 copy 配置：helper 每次调用都通过 pool 转发，而不是缓存了某一次的结果。"""
        pool = MagicMock()
        pool.call_with_pool.side_effect = [make_response("v1"), make_response("v2")]
        helper = LLMHelper(pool, make_app_cfg())

        r1 = helper.ask("p1")
        r2 = helper.ask("p2")

        self.assertEqual((r1, r2), ("v1", "v2"))
        self.assertEqual(pool.call_with_pool.call_count, 2)


class TestLLMHelperOverridePath(unittest.TestCase):
    """override_model / override_provider / override_temperature 任一被传入时，
    应走独立 client 构造分支，不经过 pool（不触发 fallback chain）。"""

    def test_override_model_bypasses_pool(self):
        pool = MagicMock()
        helper = LLMHelper(pool, make_app_cfg(model="claude-opus-4-5"))

        fake_client = MagicMock()
        fake_client.chat.return_value = make_response("judge says yes")

        with unittest.mock.patch(
            "mini_agent.llm.factory.create_client", return_value=fake_client
        ) as mock_create:
            resp = helper.chat(
                [{"role": "user", "content": "judge this"}],
                override_model="claude-haiku-4-5",
            )

        self.assertEqual(resp.text, "judge says yes")
        pool.call_with_pool.assert_not_called()
        mock_create.assert_called_once()
        used_cfg: LLMConfig = mock_create.call_args[0][0]
        self.assertEqual(used_cfg.model, "claude-haiku-4-5")

    def test_override_temperature_only_still_bypasses_pool(self):
        pool = MagicMock()
        helper = LLMHelper(pool, make_app_cfg())
        fake_client = MagicMock()
        fake_client.chat.return_value = make_response("cold take")

        with unittest.mock.patch(
            "mini_agent.llm.factory.create_client", return_value=fake_client
        ) as mock_create:
            helper.chat([{"role": "user", "content": "x"}], override_temperature=0.0)

        pool.call_with_pool.assert_not_called()
        used_cfg: LLMConfig = mock_create.call_args[0][0]
        self.assertEqual(used_cfg.temperature, 0.0)

    def test_override_provider_takes_precedence_over_base_cfg(self):
        pool = MagicMock()
        helper = LLMHelper(pool, make_app_cfg(provider="anthropic"))
        fake_client = MagicMock()
        fake_client.chat.return_value = make_response("ok")

        with unittest.mock.patch(
            "mini_agent.llm.factory.create_client", return_value=fake_client
        ) as mock_create:
            helper.chat([{"role": "user", "content": "x"}], override_provider="openai")

        used_cfg: LLMConfig = mock_create.call_args[0][0]
        self.assertEqual(used_cfg.provider, "openai")


class TestLLMHelperRetry(unittest.TestCase):
    """重试预算：default 路径下 retry_policy 正确构造并传给 pool；
    override 路径下真正触发重试直到成功。"""

    def test_default_path_builds_retry_policy_with_max_retries(self):
        pool = MagicMock()
        pool.call_with_pool.return_value = make_response("done")
        helper = LLMHelper(pool, make_app_cfg())

        helper.ask("prompt", max_retries=5)

        _, kwargs = pool.call_with_pool.call_args
        policy = kwargs["retry_policy"]
        self.assertEqual(policy.max_retries, 5)

    def test_override_path_retries_via_explicit_policy(self):
        pool = MagicMock()
        helper = LLMHelper(pool, make_app_cfg())

        fake_client = MagicMock()
        # 前两次抛异常，第三次成功 —— 验证 override 分支确实套用了 RetryPolicy
        fake_client.chat.side_effect = [
            RuntimeError("boom"), RuntimeError("boom"), make_response("recovered"),
        ]

        with unittest.mock.patch(
            "mini_agent.llm.factory.create_client", return_value=fake_client
        ):
            resp = helper.chat(
                [{"role": "user", "content": "x"}],
                override_model="m2",
                max_retries=3,
                retry_policy=NO_WAIT_POLICY,
            )

        self.assertEqual(resp.text, "recovered")
        self.assertEqual(fake_client.chat.call_count, 3)


class TestObjectiveDecomposeMultiStep(unittest.TestCase):
    """P0 bug 修复回归测试：_default_llm_decompose 应能借助 LLMHelper.ask()
    正常产出多步骤列表，而不是历史 bug 里因 TypeError 被吞掉后静默返回 []。"""

    def test_produces_multiple_steps_from_numbered_list(self):
        from mini_agent.evolution.objective_executor import _default_llm_decompose

        fake_helper = MagicMock()
        fake_helper.ask.return_value = (
            "1. 调研现有实现\n"
            "2. 设计接口\n"
            "3. 编写单元测试\n"
            "4. 提交并跑全量测试\n"
        )

        objective = MagicMock()
        objective.title = "统一 LLM 调用入口"
        objective.progress_notes = ""

        steps = _default_llm_decompose(fake_helper, objective)

        self.assertEqual(
            steps,
            ["调研现有实现", "设计接口", "编写单元测试", "提交并跑全量测试"],
        )
        fake_helper.ask.assert_called_once()

    def test_returns_empty_list_when_helper_raises(self):
        """确认降级语义保留：helper.ask 抛异常时返回 []，而不是向上传播。"""
        from mini_agent.evolution.objective_executor import _default_llm_decompose

        fake_helper = MagicMock()
        fake_helper.ask.side_effect = RuntimeError("network down")

        objective = MagicMock()
        objective.title = "目标"
        objective.progress_notes = ""

        steps = _default_llm_decompose(fake_helper, objective)
        self.assertEqual(steps, [])


class TestGoalBacklogDecompose(unittest.TestCase):
    """goal_backlog._llm_decompose 的同类回归：不再传 max_tokens=，
    改走 helper.ask(prompt)，能正常拿到文本结果。"""

    def test_llm_decompose_returns_stripped_text(self):
        from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = MagicMock()
            paths.workdir_dir = Path(tmp)
            backlog = GoalBacklog(paths)
            fake_helper = MagicMock()
            fake_helper.ask.return_value = "  实现具体的 Task 描述  "

            obj = GoalNode(
                id="obj-1", level="objective", title="示例目标",
                source="agent_derived", status="active",
            )

            result = backlog._llm_decompose(fake_helper, obj, "下一步建议")

            self.assertEqual(result, "实现具体的 Task 描述")
            fake_helper.ask.assert_called_once()
            # 确认调用方式是 ask(prompt) 而不是历史 bug 里的
            # chat(messages=..., max_tokens=...)
            call_args, call_kwargs = fake_helper.ask.call_args
            self.assertNotIn("max_tokens", call_kwargs)

    def test_llm_decompose_returns_none_when_helper_raises(self):
        from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = MagicMock()
            paths.workdir_dir = Path(tmp)
            backlog = GoalBacklog(paths)
            fake_helper = MagicMock()
            fake_helper.ask.side_effect = RuntimeError("boom")

            obj = GoalNode(
                id="obj-2", level="objective", title="示例目标",
                source="agent_derived", status="active",
            )

            result = backlog._llm_decompose(fake_helper, obj, "下一步建议")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
