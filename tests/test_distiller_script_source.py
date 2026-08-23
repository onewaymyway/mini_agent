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


if __name__ == "__main__":
    unittest.main()
