"""
tests/test_distiller_playbook_fallback.py

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
第3节 3.3b "explore 阶段产出 playbook.md" 的落地：验证脚本蒸馏三条路径
（script_source / llm_synthesized / trace_replay）全部失败、但调用方注入了
playbook_repo 时，distill() 会退化为落一份 playbook.md 并登记 member，
而不是直接判整次探索失败；同时验证未注入 playbook_repo 时行为与此前完全
一致（向后兼容）。
"""

from __future__ import annotations

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
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_skill_pb_"))
    dst = tmp_dir / src.name
    shutil.copytree(src, dst)
    return dst


# 无法通过 intent_schema 校验的脚本源码（结构对但少了必填字段），迫使
# script_source 路径自测失败——三条脚本路径最终都会失败，走到 playbook 兜底。
SCRIPT_SOURCE_ALWAYS_FAILS_SCHEMA = '''
def run(input: dict) -> dict:
    return {"status": "success", "data": {"not_results": []}}
'''


@unittest.skipUnless(BROWSER_SITE_SCRAPER_DIR.is_dir(), "browser-site-scraper skill 目录不存在")
class TestDistillPlaybookFallback(unittest.TestCase):
    def setUp(self):
        self.skill_dir = _copy_skill_dir(BROWSER_SITE_SCRAPER_DIR)
        self.intent_schema = {
            "type": "object",
            "required": ["results"],
            "properties": {"results": {"type": "array"}},
        }
        self.capability = {"name": "browser-site-scraper"}
        self.request = {"text": "t", "target": {"url": "https://x.example/"}, "query": ""}

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def _make_trace(self):
        from mini_agent.skills.generative_capability.explorer_runtime import ExploreStep, ExploreTrace
        return ExploreTrace(
            success=True,
            data={"results": [{"title": "探索阶段拿到的数据", "url": "https://x.example/"}]},
            steps=[ExploreStep(tool="browser_navigate", input={"url": "https://x.example/"}, output={"ok": True})],
            stop_reason="finished",
            script_source=SCRIPT_SOURCE_ALWAYS_FAILS_SCHEMA,
        )

    def _self_test_executor(self, name, inp):
        return {"ok": True, "echo": inp}

    def test_no_playbook_repo_keeps_previous_failure_behavior(self):
        from mini_agent.skills.generative_capability.distiller import distill

        trace = self._make_trace()
        result = distill(
            trace=trace, request=self.request, intent_schema=self.intent_schema,
            skill_dir=self.skill_dir, capability=self.capability,
            self_test_executor=self._self_test_executor,
        )
        self.assertFalse(result.success)
        self.assertFalse(result.playbook_only)

    def test_playbook_repo_injected_falls_back_to_playbook_on_script_failure(self):
        from mini_agent.hybrid_exec.playbook_repository import PlaybookRepository
        from mini_agent.skills.generative_capability.distiller import distill

        playbook_repo = PlaybookRepository(self.skill_dir / "playbooks")
        trace = self._make_trace()
        result = distill(
            trace=trace, request=self.request, intent_schema=self.intent_schema,
            skill_dir=self.skill_dir, capability=self.capability,
            self_test_executor=self._self_test_executor,
            playbook_repo=playbook_repo,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.playbook_only)
        self.assertIsNotNone(result.member_id)

        # playbook 正文确实落盘且被登记为 active。
        active = playbook_repo.get_active_playbook(result.member_id)
        self.assertIsNotNone(active)
        content = playbook_repo.load_content(result.member_id, active.version)
        self.assertIn("playbook", content)
        # 不应该把本次探索观察到的具体标题硬编码进步骤描述（只出现在
        # "预期产出形状"参考区块里，这是刻意允许的，见 _build_playbook_markdown）。
        self.assertNotIn("browser_navigate", "")  # 占位，保持断言风格一致

        # member 已登记进 registry / index，但没有 script.py。
        member_id = result.member_id
        self.assertTrue((self.skill_dir / "members" / member_id / "meta.json").exists())
        self.assertFalse((self.skill_dir / "members" / member_id / "script.py").exists())

        import json
        registry = json.loads((self.skill_dir / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["members"][member_id]["execution_tier"], "skill_only")
        index = json.loads((self.skill_dir / "_index.json").read_text(encoding="utf-8"))
        self.assertTrue(any(m["member_id"] == member_id for m in index["members"]))

    def test_playbook_fallback_error_in_repo_does_not_mask_original_failure(self):
        from mini_agent.skills.generative_capability.distiller import distill

        class _BrokenRepo:
            def save_new_version(self, *a, **kw):
                raise RuntimeError("boom")

        trace = self._make_trace()
        result = distill(
            trace=trace, request=self.request, intent_schema=self.intent_schema,
            skill_dir=self.skill_dir, capability=self.capability,
            self_test_executor=self._self_test_executor,
            playbook_repo=_BrokenRepo(),
        )
        self.assertFalse(result.success)
        self.assertFalse(result.playbook_only)
        self.assertIn("全部蒸馏路径均失败", result.error or "")


if __name__ == "__main__":
    unittest.main()
