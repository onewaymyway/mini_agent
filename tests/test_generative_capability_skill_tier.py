"""
tests/test_generative_capability_skill_tier.py

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节
"三档 member 执行机制"——本文件验证 `CapabilityEngine` 试点接入 SKILL 档
（`skill_tier.py`）后的行为：

  1. 未注入 playbook_repo/skill_runner 时，行为与接入前完全一致（不额外
     调用任何 SKILL 相关逻辑）。
  2. 命中的 member 执行失败、且存在 active playbook 时，SKILL 档顶上并
     成功——返回 status=success，resolve_reason="skill_playbook"，不进入
     explore。
  3. SKILL 档也失败（skill_runner 返回 fail）：错误信息与 member 执行失败
     的原因合并，继续走 explore（未注入 explore_runner 时按既有行为返回
     not_implemented），且 PlaybookRepository 记一次失败。
  4. skill_runner 返回 `PLAYBOOK_INVALID:` 前缀错误时，playbook 直接
     retire（不走 consecutive_fail 计数）。
  5. SKILL 档输出未通过 intent_schema 校验时按失败处理。

用最小的合成 skill 目录（而非真实 browser-site-scraper），不依赖网络/浏览器。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_minimal_skill_dir() -> Path:
    """构造一个最小的 generative-capability skill 目录：一个 member `m1`，
    通过 domain_pattern 匹配 `*.example.com`，脚本执行必定失败（用于触发
    SKILL 档兜底路径）。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_skill_tier_"))
    skill_dir = tmp_dir / "test-skill"
    skill_dir.mkdir(parents=True)

    (skill_dir / "capability.yaml").write_text(
        "name: test-skill\n"
        "domain_matchers:\n"
        "  - type: domain_pattern\n"
        "    field: target.url\n"
        "lifecycle:\n"
        "  probation_success_threshold: 3\n"
        "  degrade_failure_threshold: 3\n",
        encoding="utf-8",
    )
    _write_json(skill_dir / "_index.json", {
        "members": [
            {"member_id": "m1", "match": {"domain_pattern": "*.example.com*"}, "description": "test member"},
        ],
    })
    _write_json(skill_dir / "registry.json", {
        "members": {
            "m1": {
                "status": "trusted", "success_count": 5, "fail_count": 0,
                "consecutive_failures": 0, "intent_schema": None,
            },
        },
    })
    member_dir = skill_dir / "members" / "m1"
    member_dir.mkdir(parents=True)
    (member_dir / "script.py").write_text(
        "def run(request):\n"
        "    return {\"status\": \"fail\", \"error\": \"script 执行必定失败（测试用）\"}\n",
        encoding="utf-8",
    )
    return skill_dir


class TestCapabilityEngineSkillTier(unittest.TestCase):
    def setUp(self):
        self.skill_dir = _build_minimal_skill_dir()
        self._request = {
            "text": "https://a.example.com/x",
            "target": {"url": "https://a.example.com/x"},
        }

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def _make_playbook_repo(self):
        from mini_agent.skills.generative_capability import build_playbook_repo
        repo = build_playbook_repo(self.skill_dir)
        repo.save_new_version("m1", "# 步骤说明 v1\n1. 打开页面\n2. 提取数据", "manual")
        return repo

    def test_no_skill_wiring_behaves_like_before(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        engine = CapabilityEngine(self.skill_dir)
        result = engine.call(self._request)

        self.assertEqual(result.status, "not_implemented")
        self.assertEqual(result.resolve_reason, "domain_pattern_match")

    def test_active_playbook_missing_even_with_wiring_falls_through(self):
        """配置了 playbook_repo/skill_runner，但该 member 没有 active
        playbook（从未探索/落盘过）时，_try_skill 应静默跳过。"""
        from mini_agent.skills.generative_capability import CapabilityEngine, build_playbook_repo

        repo = build_playbook_repo(self.skill_dir)  # 空仓库，不写任何版本

        def skill_runner(request, playbook_content):
            raise AssertionError("不应该被调用：没有 active playbook")

        engine = CapabilityEngine(self.skill_dir, playbook_repo=repo, skill_runner=skill_runner)
        result = engine.call(self._request)

        self.assertEqual(result.status, "not_implemented")

    def test_skill_tier_succeeds_after_script_fails(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        repo = self._make_playbook_repo()

        def skill_runner(request, playbook_content):
            self.assertIn("步骤说明", playbook_content)
            return {"status": "success", "data": {"results": ["skill-result"]}}

        engine = CapabilityEngine(self.skill_dir, playbook_repo=repo, skill_runner=skill_runner)
        result = engine.call(self._request)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.resolve_reason, "skill_playbook")
        self.assertEqual(result.member_id, "m1")
        self.assertEqual(result.data, {"results": ["skill-result"]})

        active = repo.get_active_playbook("m1")
        self.assertEqual(active.success_count, 1)

    def test_skill_tier_fails_falls_through_to_explore_and_combines_errors(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        repo = self._make_playbook_repo()

        def skill_runner(request, playbook_content):
            return {"status": "fail", "error": "playbook 执行时页面结构对不上"}

        engine = CapabilityEngine(self.skill_dir, playbook_repo=repo, skill_runner=skill_runner)
        result = engine.call(self._request)

        self.assertEqual(result.status, "not_implemented")
        self.assertIn("script 执行必定失败", result.error)
        self.assertIn("playbook 执行时页面结构对不上", result.error)

        active = repo.get_active_playbook("m1")
        self.assertEqual(active.fail_count, 1)

    def test_playbook_invalid_prefix_retires_immediately(self):
        from mini_agent.skills.generative_capability import (
            CapabilityEngine, SKILL_RETIRE_ERROR_PREFIX,
        )

        repo = self._make_playbook_repo()

        def skill_runner(request, playbook_content):
            return {"status": "fail", "error": f"{SKILL_RETIRE_ERROR_PREFIX}页面结构完全变了"}

        engine = CapabilityEngine(self.skill_dir, playbook_repo=repo, skill_runner=skill_runner)
        result = engine.call(self._request)

        self.assertEqual(result.status, "not_implemented")
        self.assertIsNone(repo.get_active_playbook("m1"))
        versions = repo.list_versions("m1")
        self.assertEqual(versions[0].status, "retired")
        # retire 不经过 consecutive_fail 计数路径，fail_count 不应该被累加。
        self.assertEqual(versions[0].fail_count, 0)

    def test_skill_tier_output_fails_schema_validation(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        repo = self._make_playbook_repo()
        # 给这个 member 声明一个必须有 "results" 字段的 schema
        engine_registry_path = self.skill_dir / "registry.json"
        registry = json.loads(engine_registry_path.read_text(encoding="utf-8"))
        registry["members"]["m1"]["intent_schema"] = {
            "type": "object",
            "properties": {"results": {"type": "array"}},
            "required": ["results"],
        }
        engine_registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        def skill_runner(request, playbook_content):
            return {"status": "success", "data": {"wrong_field": 1}}

        from mini_agent.skills.generative_capability import CapabilityEngine as CE
        engine = CE(self.skill_dir, playbook_repo=repo, skill_runner=skill_runner)
        result = engine.call(self._request)

        self.assertEqual(result.status, "not_implemented")
        active = repo.get_active_playbook("m1")
        self.assertEqual(active.fail_count, 1)
        self.assertIn("intent_schema", result.error)


if __name__ == "__main__":
    unittest.main()
