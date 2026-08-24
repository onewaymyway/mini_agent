"""
tests/test_generative_capability_available_tiers.py

对应 next_doc/generative_capability_three_tier_improvement_plan.md 阶段一
"available_tiers 信息性字段"——验证 CapabilityEngine 在 script/skill 档
执行后正确刷新 registry.json 里每个 member 的 available_tiers 字段。

这是一个纯信息性字段：只验证它被正确计算/写入，不验证它影响任何决策
逻辑（因为按设计它确实不影响）。
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


def _build_minimal_skill_dir(*, with_script: bool) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_available_tiers_"))
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
    if with_script:
        (member_dir / "script.py").write_text(
            "def run(request):\n"
            "    return {\"status\": \"success\", \"data\": {}}\n",
            encoding="utf-8",
        )
    return skill_dir


class TestAvailableTiers(unittest.TestCase):
    def _registry_of(self, skill_dir: Path) -> dict:
        return json.loads((skill_dir / "registry.json").read_text(encoding="utf-8"))

    def test_script_only(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        skill_dir = _build_minimal_skill_dir(with_script=True)
        try:
            engine = CapabilityEngine(skill_dir)
            result = engine.call({"text": "https://a.example.com/x", "target": {"url": "https://a.example.com/x"}})
            self.assertEqual(result.status, "success")
            registry = self._registry_of(skill_dir)
            self.assertEqual(registry["members"]["m1"]["available_tiers"], ["script"])
        finally:
            shutil.rmtree(skill_dir.parent, ignore_errors=True)

    def test_skill_only(self):
        from mini_agent.skills.generative_capability import CapabilityEngine, build_playbook_repo

        skill_dir = _build_minimal_skill_dir(with_script=False)
        try:
            repo = build_playbook_repo(skill_dir)
            repo.save_new_version("m1", "# 步骤说明 v1", "manual")

            def skill_runner(request, playbook_content):
                return {"status": "success", "data": {}}

            engine = CapabilityEngine(skill_dir, playbook_repo=repo, skill_runner=skill_runner)
            result = engine.call({"text": "https://a.example.com/x", "target": {"url": "https://a.example.com/x"}})
            # 没有 script.py，execute() 判"脚本加载失败" -> _try_skill() 顶上
            # 并成功，最终 call() 整体返回 success。
            self.assertEqual(result.status, "success")
            self.assertEqual(result.resolve_reason, "skill_playbook")
            registry = self._registry_of(skill_dir)
            self.assertEqual(registry["members"]["m1"]["available_tiers"], ["skill"])
        finally:
            shutil.rmtree(skill_dir.parent, ignore_errors=True)

    def test_script_and_skill(self):
        from mini_agent.skills.generative_capability import CapabilityEngine, build_playbook_repo

        skill_dir = _build_minimal_skill_dir(with_script=True)
        try:
            repo = build_playbook_repo(skill_dir)
            repo.save_new_version("m1", "# 步骤说明 v1", "manual")

            def skill_runner(request, playbook_content):
                raise AssertionError("script 成功时不应该调用 skill_runner")

            engine = CapabilityEngine(skill_dir, playbook_repo=repo, skill_runner=skill_runner)
            result = engine.call({"text": "https://a.example.com/x", "target": {"url": "https://a.example.com/x"}})
            self.assertEqual(result.status, "success")
            registry = self._registry_of(skill_dir)
            # script 执行成功时 available_tiers 只反映"当前实际具备"的手段，
            # 与是否被用到无关——两者都存在就都列出来。
            self.assertEqual(sorted(registry["members"]["m1"]["available_tiers"]), ["script", "skill"])
        finally:
            shutil.rmtree(skill_dir.parent, ignore_errors=True)

    def test_neither(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        skill_dir = _build_minimal_skill_dir(with_script=False)
        try:
            engine = CapabilityEngine(skill_dir)
            engine.call({"text": "https://a.example.com/x", "target": {"url": "https://a.example.com/x"}})
            registry = self._registry_of(skill_dir)
            # 既无 script 也无 playbook_repo：_try_skill 静默跳过（返回
            # None），从未触碰 registry，available_tiers 字段根本不会被
            # 写入（不是空列表，是压根没有这个 key）——这本身也是合理的
            # 诊断信息："这个 member 从来没有被任何一档手段成功计算过"。
            self.assertNotIn("available_tiers", registry["members"]["m1"])
        finally:
            shutil.rmtree(skill_dir.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
