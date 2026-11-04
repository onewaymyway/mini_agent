"""
tests/test_llm_call_stats_sources.py

覆盖"调用计数按来源 / 按项目拆分"的改动：
  - 旧数据（没有 source / origin 字段、已压缩的每日汇总行）读取不报错，归入 legacy；
  - 调用计数统一由 LLMClientPool.call_with_pool() 记录，fallback 不会重复计数；
  - 来源可以通过 call_source() 标记（skill / helper / background …）；
  - 未配置计数的 pool（测试、嵌入场景）行为与之前完全一致，不写文件。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_llm_call_stats_sources.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.llm import call_stats
from mini_agent.llm.base import LLMConfig, LLMPermanentError, LLMResponse, LLMUsage
from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry
from mini_agent.llm.retry import RetryPolicy
from mini_agent.storage.paths import AgentPaths


class _OkClient:
    def __init__(self, text: str = "ok"):
        self.text = text
        self.calls = 0

    def chat(self, messages, system, tools=None):
        self.calls += 1
        return LLMResponse(
            text=self.text, tool_calls=[], usage=LLMUsage(input_tokens=10, output_tokens=4),
            stop_reason="end_turn",
        )


class _FailClient:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, system, tools=None):
        self.calls += 1
        raise LLMPermanentError("OpenAI API error: 403 unusual activity")


def _entry(provider: str, model: str, client, key: str = "k1") -> ProviderEntry:
    return ProviderEntry(
        config=LLMConfig(provider=provider, model=model, api_key=key),
        client=client,
        key_pool=None,
    )


def _policy() -> RetryPolicy:
    return RetryPolicy(max_retries=0, retry_on_exception=False)


class TestLegacyCompatibility(unittest.TestCase):
    """旧数据必须能被新代码读取，不报错、不丢统计。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rows):
        call_stats._write_jsonl(self.paths.llm_call_stats_path, rows)

    def test_old_rows_without_source_or_origin_go_to_legacy(self):
        now = time.time()
        self._write([
            {"ts": now, "provider": "a", "model": "m", "input_tokens": 3, "output_tokens": 2,
             "duration_ms": 100, "outcome": "success"},
            {"ts": now, "provider": "a", "model": "m", "input_tokens": 0, "output_tokens": 0,
             "duration_ms": 50, "outcome": "error"},
        ])
        bd = call_stats.call_stats_breakdown(self.paths, days=7)
        self.assertEqual(set(bd["by_source"].keys()), {call_stats.LEGACY_BUCKET})
        self.assertEqual(set(bd["by_origin"].keys()), {call_stats.LEGACY_BUCKET})
        legacy = bd["by_source"][call_stats.LEGACY_BUCKET]
        self.assertEqual(legacy["call_count"], 2)
        self.assertEqual(legacy["error_count"], 1)
        self.assertEqual(legacy["total_input_tokens"], 3)
        # 原有 series 口径不变
        series = call_stats.call_stats_series(self.paths, days=7)
        self.assertEqual(series[0]["call_count"], 2)

    def test_compacted_daily_aggregates_are_counted_under_legacy(self):
        now = time.time()
        self._write([
            {"ts": now - 30 * 86400, "day": "old", "is_daily_aggregate": True,
             "call_count": 5, "success_count": 4, "error_count": 1,
             "key_switch_count": 0, "config_switch_count": 0,
             "total_input_tokens": 100, "total_output_tokens": 50, "total_duration_ms": 500},
            {"ts": now, "provider": "a", "model": "m", "outcome": "success",
             "source": "agent", "origin": "main", "duration_ms": 10},
        ])
        # 7 天窗口内只有 raw 行；汇总行（30 天前）不参与
        bd = call_stats.call_stats_breakdown(self.paths, days=7)
        self.assertEqual(bd["by_source"]["agent"]["call_count"], 1)
        self.assertNotIn(call_stats.LEGACY_BUCKET, bd["by_source"])
        # 窗口拉大到 60 天：汇总行计入 legacy
        bd60 = call_stats.call_stats_breakdown(self.paths, days=60)
        self.assertEqual(bd60["by_source"][call_stats.LEGACY_BUCKET]["call_count"], 5)
        self.assertEqual(bd60["by_source"][call_stats.LEGACY_BUCKET]["total_input_tokens"], 100)

    def test_breakdown_empty_when_no_file(self):
        self.assertEqual(
            call_stats.call_stats_breakdown(self.paths, days=7),
            {"by_source": {}, "by_origin": {}},
        )

    def test_breakdown_skips_corrupt_lines(self):
        self.paths.llm_call_stats_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.llm_call_stats_path.write_text(
            '{"ts": %f, "outcome": "success", "source": "agent", "origin": "main"}\n'
            'not-json\n' % time.time(),
            encoding="utf-8",
        )
        bd = call_stats.call_stats_breakdown(self.paths, days=7)
        self.assertEqual(bd["by_source"]["agent"]["call_count"], 1)


class TestCallSourceContext(unittest.TestCase):
    def test_default_when_unset(self):
        self.assertEqual(call_stats.current_source("agent"), "agent")

    def test_call_source_overrides_and_restores(self):
        with call_stats.call_source("skill"):
            self.assertEqual(call_stats.current_source("agent"), "skill")
        self.assertEqual(call_stats.current_source("agent"), "agent")

    def test_if_unset_keeps_outer_value(self):
        with call_stats.call_source("skill"):
            with call_stats.call_source_if_unset("helper"):
                self.assertEqual(call_stats.current_source("x"), "skill")
        with call_stats.call_source_if_unset("helper"):
            self.assertEqual(call_stats.current_source("x"), "helper")


class TestPoolRecording(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AgentPaths(project_root=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _rows(self):
        call_stats.flush_now(self.paths)
        return call_stats._read_jsonl(self.paths.llm_call_stats_path)

    def test_success_recorded_once_with_default_kind(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent", origin="main")
        pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "success")
        self.assertEqual(rows[0]["source"], "agent")
        self.assertEqual(rows[0]["origin"], "main")
        self.assertEqual(rows[0]["input_tokens"], 10)
        self.assertEqual(rows[0]["provider"], "p")

    def test_call_source_marks_skill(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent")
        with call_stats.call_source("skill"):
            pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        self.assertEqual(self._rows()[0]["source"], "skill")

    def test_fallback_counts_switch_and_final_success_without_double_count(self):
        pool = LLMClientPool(
            entries=[_entry("bad", "m", _FailClient()), _entry("good", "m2", _OkClient())],
            max_rounds=1, round_wait=0,
        )
        pool.configure_stats(str(self.root), kind="agent")
        pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        rows = self._rows()
        outcomes = [r["outcome"] for r in rows]
        # 一次成功调用 = 一条 config_switch（切换事件）+ 一条 success（最终结果）
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("config_switch"), 1)
        self.assertNotIn("error", outcomes)
        success = [r for r in rows if r["outcome"] == "success"][0]
        self.assertEqual(success["provider"], "good")

    def test_final_failure_recorded_as_error_and_still_raises(self):
        pool = LLMClientPool(entries=[_entry("bad", "m", _FailClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent")
        with self.assertRaises(LLMPermanentError):
            pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        outcomes = [r["outcome"] for r in self._rows()]
        self.assertEqual(outcomes, ["error"])

    def test_chat_with_retry_is_background_by_default(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent")
        pool.chat_with_retry([{"role": "user", "content": "hi"}], "sys", [], max_retries=0)
        self.assertEqual(self._rows()[0]["source"], "background")

    def test_chat_with_retry_respects_outer_source(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent")
        with call_stats.call_source("skill"):
            pool.chat_with_retry([{"role": "user", "content": "hi"}], "sys", [], max_retries=0)
        self.assertEqual(self._rows()[0]["source"], "skill")

    def test_unconfigured_pool_writes_nothing(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        self.assertFalse(self.paths.llm_call_stats_path.exists())

    def test_broken_root_never_breaks_call(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        # 根目录指向一个文件，写入必然失败——调用本身必须成功
        bogus = self.root / "not_a_dir"
        bogus.write_text("x", encoding="utf-8")
        pool.configure_stats(str(bogus), kind="agent")
        resp = pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        self.assertEqual(resp.text, "ok")


class TestSourceTagging(unittest.TestCase):
    def test_capability_call_wrapper_preserves_name_and_marks_skill(self):
        import inspect
        from mini_agent.tools.capability_call import _with_skill_source

        def capability_call(skill_name: str, request: dict) -> str:
            """doc"""
            return call_stats.current_source("agent")

        wrapped = _with_skill_source(capability_call)
        self.assertEqual(wrapped.__name__, "capability_call")
        self.assertEqual(wrapped.__doc__, "doc")
        self.assertEqual(list(inspect.signature(wrapped).parameters), ["skill_name", "request"])
        self.assertEqual(wrapped("x", {}), "skill")


class _Cfg:
    def __init__(self, project_root):
        self.project_root = str(project_root)


class TestHelperAndExternalScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AgentPaths(project_root=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_llm_helper_chat_recorded_as_helper_and_skill_override(self):
        from mini_agent.llm.service import LLMHelper

        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent")
        helper = LLMHelper(pool, _Cfg(self.root))
        helper.ask("hi")
        with call_stats.call_source("skill"):
            helper.ask("hi again")
        call_stats.flush_now(self.paths)
        rows = call_stats._read_jsonl(self.paths.llm_call_stats_path)
        self.assertEqual([r["source"] for r in rows], ["helper", "skill"])
        self.assertTrue(all(r["outcome"] == "success" for r in rows))

    def test_external_project_scope_points_to_main_root(self):
        import os
        from mini_agent.llm.client_pool import _resolve_stats_scope

        main = self.root / "main"
        ext = self.root / "novel_writer"
        main.mkdir()
        ext.mkdir()
        (ext / "project.yaml").write_text("name: novel_writer\n", encoding="utf-8")
        old = os.environ.get("MINI_AGENT_MAIN_PROJECT_ROOT")
        os.environ["MINI_AGENT_MAIN_PROJECT_ROOT"] = str(main)
        try:
            root, origin = _resolve_stats_scope(_Cfg(ext))
        finally:
            if old is None:
                os.environ.pop("MINI_AGENT_MAIN_PROJECT_ROOT", None)
            else:
                os.environ["MINI_AGENT_MAIN_PROJECT_ROOT"] = old
        self.assertEqual(Path(root).resolve(), main.resolve())
        self.assertEqual(origin, "external:novel_writer")

    def test_main_project_scope_is_main(self):
        from mini_agent.llm.client_pool import _resolve_stats_scope

        root, origin = _resolve_stats_scope(_Cfg(self.root))
        self.assertEqual(Path(root).resolve(), self.root.resolve())
        self.assertEqual(origin, "main")

    def test_external_origin_written_into_main_breakdown(self):
        pool = LLMClientPool(entries=[_entry("p", "m", _OkClient())], max_rounds=1, round_wait=0)
        pool.configure_stats(str(self.root), kind="agent", origin="external:novel_writer")
        pool.call_with_pool(call_fn=lambda c: c.chat([], "", []), retry_policy=_policy())
        call_stats.flush_now(self.paths)
        bd = call_stats.call_stats_breakdown(self.paths, days=7)
        self.assertIn("external:novel_writer", bd["by_origin"])
        self.assertEqual(bd["by_origin"]["external:novel_writer"]["call_count"], 1)


if __name__ == "__main__":
    unittest.main()
