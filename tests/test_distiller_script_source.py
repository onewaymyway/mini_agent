"""
tests/test_distiller_script_source.py

对应 next_doc/generative_capability_explorer_rearch_plan.md 阶段二:
验证 distiller.py 的 script_source 优先路径（探索子agent在 finish 时自己
提交可复用脚本源码），与既有 trace-replay 兜底路径并存、互不干扰。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSER_SITE_SCRAPER_DIR = REPO_ROOT / ".claude" / "skills" / "browser-site-scraper"


def _copy_skill_dir(src: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_skill_"))
    dst = tmp_dir / src.name
    shutil.copytree(src, dst)
    return dst


VALID_SCRIPT_SOURCE = '''
def run(input: dict) -> dict:
    target = input.get("target", {})
    return {"status": "success", "data": {"results": [{"title": "来自 script_source", "url": target.get("url", "")}]}}
'''

INVALID_SCRIPT_SOURCE_NO_RUN = '''
def not_run(input: dict) -> dict:
    return {"status": "success", "data": {}}
'''

SCRIPT_SOURCE_RETURNS_BAD_SHAPE = '''
def run(input: dict) -> dict:
    return {"result": "not the expected {status,data} shape"}
'''


@unittest.skipUnless(BROWSER_SITE_SCRAPER_DIR.is_dir(), "browser-site-scraper skill 目录不存在")
class TestDistillScriptSourcePath(unittest.TestCase):
    def setUp(self):
        self.skill_dir = _copy_skill_dir(BROWSER_SITE_SCRAPER_DIR)
        # intent_schema 从 capability.yaml 里的既有 request_formats/intent 定义已经
        # 存在，这里直接构造一个宽松 schema 供本文件独立测试，不依赖 capability.yaml
        # 的具体字段（distill() 只要求 data 通过 schema 校验）。
        self.intent_schema = {
            "type": "object",
            "required": ["results"],
            "properties": {"results": {"type": "array"}},
        }
        self.capability = {"name": "browser-site-scraper"}

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def _make_trace(self, script_source):
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreTrace
        return ExploreTrace(
            success=True,
            data={"results": [{"title": "探索阶段拿到的数据", "url": "https://x.example/"}]},
            steps=[],
            stop_reason="finished",
            script_source=script_source,
        )

    def test_script_source_path_used_when_present(self):
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace(VALID_SCRIPT_SOURCE)
        request = {"text": "t", "target": {"url": "https://x.example/"}, "query": ""}

        def self_test_executor(name, tool_input):  # 本用例脚本不调用任何工具
            raise AssertionError("script_source 路径的脚本不应该调用 tool_executor")

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=self_test_executor,
        )

        self.assertTrue(result.success, msg=result.error)
        self.assertIsNotNone(result.member_id)
        self.assertEqual(
            result.data,
            {"results": [{"title": "来自 script_source", "url": "https://x.example/"}]},
        )

        script_path = self.skill_dir / "members" / result.member_id / "script.py"
        self.assertTrue(script_path.exists())
        self.assertIn("def run(input: dict) -> dict", script_path.read_text(encoding="utf-8"))

        meta = json.loads((self.skill_dir / "members" / result.member_id / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["distill_source_kind"], "script_source")
        self.assertFalse(meta["distill_used_trace_data_fallback"])

    def test_script_source_missing_run_fails_self_test_not_falls_back_silently(self):
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace(INVALID_SCRIPT_SOURCE_NO_RUN)
        request = {"text": "t", "target": {"url": "https://x.example/"}, "query": ""}

        members_dir = self.skill_dir / "members"
        before = set(p.name for p in members_dir.iterdir()) if members_dir.exists() else set()

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: {"ok": True},
        )

        self.assertFalse(result.success)
        self.assertIn("run()", result.error)
        # 自测失败不应该产生任何新的落盘残留（不检查 members/ 是否为空，因为
        # browser-site-scraper 自带的已有 member 会被 _copy_skill_dir 一并
        # 拷进来，members/ 目录本来就存在且非空）。
        after = set(p.name for p in members_dir.iterdir()) if members_dir.exists() else set()
        self.assertEqual(before, after)

    def test_script_source_bad_return_shape_fails_self_test(self):
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace(SCRIPT_SOURCE_RETURNS_BAD_SHAPE)
        request = {"text": "t", "target": {"url": "https://x.example/"}, "query": ""}

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: {"ok": True},
        )

        self.assertFalse(result.success)

    def test_no_script_source_falls_back_to_trace_replay(self):
        """未提交 script_source 时，distill_source_kind 应为 trace_replay（既有行为不变）。"""
        from mini_agent.skills.generative_capability.distiller import distill
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreStep, ExploreTrace

        target_url = "https://x.example/"
        steps = [ExploreStep(
            tool="browser_extract_content", input={"url": target_url},
            output={"data": {"results": [{"title": "t", "url": target_url}]}},
        )]
        trace = ExploreTrace(
            success=True, data={"results": [{"title": "t", "url": target_url}]},
            steps=steps, stop_reason="finished", script_source=None,
        )
        request = {"text": target_url, "target": {"url": target_url}, "query": ""}

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: {
                "ok": True, "data": {"results": [{"title": "t", "url": target_url}]},
            },
        )

        self.assertTrue(result.success, msg=result.error)
        meta = json.loads((self.skill_dir / "members" / result.member_id / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["distill_source_kind"], "trace_replay")

    def test_trace_replay_member_gets_conservative_probation_override(self):
        """[本次新增，阶段 C] trace-replay 产出的 member 在 registry.json 里
        应该带有更保守的 probation_success_threshold_override（默认领域门槛
        的两倍），script_source 产出的 member 不应该有这个字段。"""
        from mini_agent.skills.generative_capability.distiller import distill
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreStep, ExploreTrace

        target_url = "https://y.example/"
        steps = [ExploreStep(
            tool="browser_extract_content", input={"url": target_url},
            output={"data": {"results": [{"title": "t", "url": target_url}]}},
        )]
        trace = ExploreTrace(
            success=True, data={"results": [{"title": "t", "url": target_url}]},
            steps=steps, stop_reason="finished", script_source=None,
        )
        request = {"text": target_url, "target": {"url": target_url}, "query": ""}
        capability_with_default = {"name": "browser-site-scraper", "lifecycle": {"probation_success_threshold": 3}}

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, capability_with_default,
            self_test_executor=lambda name, inp: {
                "ok": True, "data": {"results": [{"title": "t", "url": target_url}]},
            },
        )

        self.assertTrue(result.success, msg=result.error)
        registry = json.loads((self.skill_dir / "registry.json").read_text(encoding="utf-8"))
        entry = registry["members"][result.member_id]
        self.assertEqual(entry.get("probation_success_threshold_override"), 6)

        # 对照组：script_source 产出的 member 不应该有这个覆盖字段。
        trace2 = self._make_trace(VALID_SCRIPT_SOURCE)
        request2 = {"text": "t2", "target": {"url": "https://z.example/"}, "query": ""}
        result2 = distill(
            trace2, request2, self.intent_schema, self.skill_dir, capability_with_default,
            self_test_executor=lambda name, inp: (_ for _ in ()).throw(AssertionError("不应调用")),
        )
        self.assertTrue(result2.success, msg=result2.error)
        registry2 = json.loads((self.skill_dir / "registry.json").read_text(encoding="utf-8"))
        entry2 = registry2["members"][result2.member_id]
        self.assertNotIn("probation_success_threshold_override", entry2)


# --------------------------------------------------------------------------- #
# 合理性检查（假数据脚本检测）
#
# 对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第2节。
# --------------------------------------------------------------------------- #

FAKE_DATA_SCRIPT_SOURCE = '''
def run(input: dict) -> dict:
    # 忽略 input，永远返回同一份写死的数据（对应用户报告里知乎抓取脚本的问题）
    return {"status": "success", "data": {"results": [
        {"title": "自进化（Self-evolving/RSI），一篇就够了", "url": "https://zhuanlan.zhihu.com/p/2065227313973825752"},
        {"title": "谷歌的推荐系统agent自进化", "url": "https://zhuanlan.zhihu.com/p/2062662963899716138"},
    ]}}
'''


@unittest.skipUnless(BROWSER_SITE_SCRAPER_DIR.is_dir(), "browser-site-scraper skill 目录不存在")
class TestDistillPlausibilityCheck(unittest.TestCase):
    def setUp(self):
        self.skill_dir = _copy_skill_dir(BROWSER_SITE_SCRAPER_DIR)
        self.intent_schema = {
            "type": "object",
            "required": ["results"],
            "properties": {"results": {"type": "array"}},
        }
        self.capability = {"name": "browser-site-scraper"}

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def _make_trace(self, script_source):
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreTrace
        return ExploreTrace(
            success=True,
            data={"results": [{"title": "探索阶段拿到的数据", "url": "https://x.example/"}]},
            steps=[],
            stop_reason="finished",
            script_source=script_source,
        )

    def test_fake_data_script_rejected_without_llm_helper(self):
        """规则预检发现"换了 query 但输出逐字节不变"，未注入 llm_helper 时
        保守拒绝落盘，不放行假数据脚本（对应用户报告的知乎抓取脚本场景）。"""
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace(FAKE_DATA_SCRIPT_SOURCE)
        request = {"text": "t", "target": {"url": "https://zhihu.com/search"}, "query": "自主进化Agent"}

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: {"ok": True},
            llm_helper=None,
        )

        self.assertFalse(result.success)
        self.assertIn("假数据", result.error)
        # 拒绝的产物不应该落盘
        members_before = set(p.name for p in (self.skill_dir / "members").iterdir())
        self.assertNotIn("zhihu_search", members_before)  # 不应新增以此请求命名的 member

    def test_fake_data_script_rejected_when_llm_confirms_hardcoded(self):
        """规则预检可疑 + LLM 复核确认硬编码 -> 拒绝。"""
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace(FAKE_DATA_SCRIPT_SOURCE)
        request = {"text": "t", "target": {"url": "https://zhihu.com/search"}, "query": "自主进化Agent"}

        class _FakeLLMHelper:
            def ask(self, prompt, system=""):
                return '{"hardcoded": true, "reason": "脚本忽略 input，返回固定数据"}'

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: {"ok": True},
            llm_helper=_FakeLLMHelper(),
        )

        self.assertFalse(result.success)
        self.assertIn("假数据", result.error)

    def test_real_parametrized_script_accepted_when_llm_confirms_plausible(self):
        """脚本本身真实使用 input 参数（VALID_SCRIPT_SOURCE），规则预检应该
        直接因"输出随参数变化"而通过，不需要触发 LLM 复核。"""
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace(VALID_SCRIPT_SOURCE)
        request = {"text": "t", "target": {"url": "https://x.example/"}, "query": ""}

        class _NeverCalledLLMHelper:
            def ask(self, prompt, system=""):
                raise AssertionError("规则预检应已通过，不应触发 LLM 复核")

        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: (_ for _ in ()).throw(AssertionError("不应调用")),
            llm_helper=_NeverCalledLLMHelper(),
        )

        self.assertTrue(result.success, msg=result.error)

    def test_trace_replay_path_not_subject_to_plausibility_check(self):
        """trace_replay 路径的自测执行器在测试环境里天然不随输入变化，
        这项检查不应该误伤该路径（只对 script_source/llm_synthesized 生效）。"""
        from mini_agent.skills.generative_capability.distiller import distill
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreStep, ExploreTrace

        target_url = "https://trace-replay-plausibility.example/"
        steps = [ExploreStep(
            tool="browser_extract_content", input={"url": target_url},
            output={"data": {"results": [{"title": "t", "url": target_url}]}},
        )]
        trace = ExploreTrace(
            success=True, data={"results": [{"title": "t", "url": target_url}]},
            steps=steps, stop_reason="finished", script_source=None,
        )
        request = {"text": target_url, "target": {"url": target_url}, "query": ""}

        # 自测执行器完全不随 input 变化（典型的桩执行器写法）
        result = distill(
            trace, request, self.intent_schema, self.skill_dir, self.capability,
            self_test_executor=lambda name, inp: {
                "ok": True, "data": {"results": [{"title": "t", "url": target_url}]},
            },
            llm_helper=None,
        )

        self.assertTrue(result.success, msg=result.error)


if __name__ == "__main__":
    unittest.main()
