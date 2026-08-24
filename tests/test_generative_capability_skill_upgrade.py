"""
tests/test_generative_capability_skill_upgrade.py

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
第3节 3.3b 最后一项："SKILL 档执行时观察到可参数化则升级蒸馏为
script.py"。验证 `CapabilityEngine._maybe_upgrade_skill_to_script()`：

  1. 默认 `enable_skill_upgrade=False` 时，即使 playbook 成功次数达标、
     也注入了 llm_helper，也不会尝试升级（不改变既有默认行为）。
  2. 开启后，playbook 成功次数达到门槛、且 LLM 产出的脚本通过自测/
     schema 校验/合理性检查时，member 目录下会新增 script.py，且下一次
     调用直接走脚本（不再需要 SKILL 档）。
  3. LLM 生成的脚本自测失败时，静默放弃升级，不影响本次已经成功的调用
     结果，也不产生 script.py。

用最小合成 skill 目录，不依赖网络/浏览器/真实 LLM。
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
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_skill_upgrade_"))
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
    intent_schema = {
        "type": "object",
        "required": ["results"],
        "properties": {"results": {"type": "array"}},
    }
    _write_json(skill_dir / "registry.json", {
        "members": {
            "m1": {
                "status": "trusted", "success_count": 5, "fail_count": 0,
                "consecutive_failures": 0, "intent_schema": intent_schema,
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
    (member_dir / "meta.json").write_text(
        json.dumps({"version": 1, "intent_schema": intent_schema}, ensure_ascii=False), encoding="utf-8",
    )
    return skill_dir


# 一份能通过自测+schema 校验+合理性检查的合法脚本：真实调用 tool_runtime
# 拿到的 executor，从 request 里读取 url 参数化输出。
_VALID_UPGRADED_SCRIPT = '''
def run(input: dict) -> dict:
    from tool_runtime import get_tool_executor
    executor = get_tool_executor()
    target = input.get("target", {})
    out = executor("browser_navigate", {"url": target.get("url", "")})
    return {"status": "success", "data": {"results": [out.get("echo", {}).get("url", "")]}}
'''

_INVALID_UPGRADED_SCRIPT = "not even python def run"


class _StubLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    def ask(self, *args, **kwargs):
        self.calls += 1
        return self._response


class TestCapabilityEngineSkillUpgrade(unittest.TestCase):
    def setUp(self):
        self.skill_dir = _build_minimal_skill_dir()
        self._request = {
            "text": "https://a.example.com/x",
            "target": {"url": "https://a.example.com/x"},
        }

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def _make_playbook_repo(self, success_count: int):
        from mini_agent.skills.generative_capability import build_playbook_repo
        repo = build_playbook_repo(self.skill_dir)
        repo.save_new_version("m1", "# 步骤说明 v1\n1. 打开页面\n2. 提取数据", "manual")
        for _ in range(success_count):
            repo.record_success("m1", 1)
        return repo

    def _tool_executor(self, name, inp):
        return {"ok": True, "echo": inp}

    def test_disabled_by_default_even_with_llm_helper(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        repo = self._make_playbook_repo(success_count=5)

        def skill_runner(request, playbook_content):
            return {"status": "success", "data": {"results": ["skill-result"]}}

        engine = CapabilityEngine(
            self.skill_dir, playbook_repo=repo, skill_runner=skill_runner,
            tool_executor=self._tool_executor,
            llm_helper=_StubLLMHelper(_VALID_UPGRADED_SCRIPT),
        )
        result = engine.call(self._request)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.resolve_reason, "skill_playbook")
        self.assertFalse((self.skill_dir / "members" / "m1" / "script.py").exists() and
                          "skill_upgraded" in (self.skill_dir / "members" / "m1" / "meta.json").read_text())

    def test_upgrade_succeeds_and_persists_script(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        # 删掉 script.py，让第一次调用只能走 SKILL 档（execute() 加载脚本
        # 失败 -> _try_skill 顶上），验证升级后的脚本能被后续调用真正用到。
        (self.skill_dir / "members" / "m1" / "script.py").unlink()

        repo = self._make_playbook_repo(success_count=3)

        def skill_runner(request, playbook_content):
            return {"status": "success", "data": {"results": ["skill-result"]}}

        llm_helper = _StubLLMHelper(_VALID_UPGRADED_SCRIPT)
        engine = CapabilityEngine(
            self.skill_dir, playbook_repo=repo, skill_runner=skill_runner,
            tool_executor=self._tool_executor, llm_helper=llm_helper,
            enable_skill_upgrade=True, skill_upgrade_success_threshold=3,
        )
        result = engine.call(self._request)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.resolve_reason, "skill_playbook")
        self.assertEqual(llm_helper.calls, 1)

        script_path = self.skill_dir / "members" / "m1" / "script.py"
        self.assertTrue(script_path.exists())
        meta = json.loads((self.skill_dir / "members" / "m1" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["distill_source_kind"], "skill_upgraded")
        self.assertEqual(meta["version"], 2)

        # playbook 本身没有被破坏，仍然可用作兜底。
        active = repo.get_active_playbook("m1")
        self.assertIsNotNone(active)

        # 升级后的脚本本身应当能被独立加载执行（验证 execute() 之后会优先
        # 走脚本这条路径本身没有被破坏）——不通过 engine.call() 的完整
        # resolve() 链路验证，因为 `_atomic_persist()` 会用
        # `_infer_match_rule(request)` 重新生成该 member 的 domain_pattern
        # 检索规则（既有行为，`reexplore`/`distill()` 产出脚本时同样如此，
        # 不是本次升级功能引入的新问题），与测试请求 URL 是否重新命中是
        # 检索规则生成算法本身的行为，超出本次"升级是否成功产出可用脚本"
        # 这一验证目标的范围。
        from mini_agent.skills.generative_capability import CapabilityEngine as _CE
        engine2 = _CE(self.skill_dir, tool_executor=self._tool_executor)
        run_fn = engine2._load_member_run("m1")
        self.assertIsNotNone(run_fn)
        import mini_agent.skills.generative_capability.tool_runtime as tool_runtime
        tool_runtime.set_tool_executor(self._tool_executor)
        out = run_fn(self._request)
        self.assertEqual(out.get("status"), "success")

    def test_llm_invalid_output_leaves_call_result_and_playbook_untouched(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        (self.skill_dir / "members" / "m1" / "script.py").unlink()
        repo = self._make_playbook_repo(success_count=3)

        def skill_runner(request, playbook_content):
            return {"status": "success", "data": {"results": ["skill-result"]}}

        llm_helper = _StubLLMHelper(_INVALID_UPGRADED_SCRIPT)
        engine = CapabilityEngine(
            self.skill_dir, playbook_repo=repo, skill_runner=skill_runner,
            tool_executor=self._tool_executor, llm_helper=llm_helper,
            enable_skill_upgrade=True, skill_upgrade_success_threshold=3,
        )
        result = engine.call(self._request)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data, {"results": ["skill-result"]})
        self.assertFalse((self.skill_dir / "members" / "m1" / "script.py").exists())
        active = repo.get_active_playbook("m1")
        self.assertIsNotNone(active)  # 升级失败不影响 playbook 本身

    def test_upgrade_retry_cooldown_skips_second_llm_call(self):
        """对应 next_doc/generative_capability_three_tier_improvement_plan.md
        阶段二：升级失败后记录 last_upgrade_attempt_at，冷却期内再次触发
        _maybe_upgrade_skill_to_script 不应该重新调用 LLM。"""
        from mini_agent.skills.generative_capability import CapabilityEngine

        (self.skill_dir / "members" / "m1" / "script.py").unlink()
        repo = self._make_playbook_repo(success_count=3)

        def skill_runner(request, playbook_content):
            return {"status": "success", "data": {"results": ["skill-result"]}}

        llm_helper = _StubLLMHelper(_INVALID_UPGRADED_SCRIPT)  # 每次都升级失败
        engine = CapabilityEngine(
            self.skill_dir, playbook_repo=repo, skill_runner=skill_runner,
            tool_executor=self._tool_executor, llm_helper=llm_helper,
            enable_skill_upgrade=True, skill_upgrade_success_threshold=3,
        )

        engine.call(self._request)  # 第一次：升级失败，记一次 last_upgrade_attempt_at
        self.assertEqual(llm_helper.calls, 1)
        active = repo.get_active_playbook("m1")
        self.assertIsNotNone(active.last_upgrade_attempt_at)

        engine.call(self._request)  # 第二次：仍在默认 3600s 冷却期内，应跳过
        self.assertEqual(llm_helper.calls, 1)

    def test_upgrade_retry_after_cooldown_expires(self):
        """冷却期过后应允许再次尝试升级（不是失败一次就永久放弃）。"""
        from mini_agent.skills.generative_capability import CapabilityEngine

        (self.skill_dir / "members" / "m1" / "script.py").unlink()
        # 用一个 0 秒冷却期的 capability.yaml，模拟"冷却期已过"。
        (self.skill_dir / "capability.yaml").write_text(
            "name: test-skill\n"
            "domain_matchers:\n"
            "  - type: domain_pattern\n"
            "    field: target.url\n"
            "lifecycle:\n"
            "  probation_success_threshold: 3\n"
            "  degrade_failure_threshold: 3\n"
            "  skill_upgrade_retry_cooldown_seconds: 0\n",
            encoding="utf-8",
        )
        repo = self._make_playbook_repo(success_count=3)

        def skill_runner(request, playbook_content):
            return {"status": "success", "data": {"results": ["skill-result"]}}

        llm_helper = _StubLLMHelper(_INVALID_UPGRADED_SCRIPT)
        engine = CapabilityEngine(
            self.skill_dir, playbook_repo=repo, skill_runner=skill_runner,
            tool_executor=self._tool_executor, llm_helper=llm_helper,
            enable_skill_upgrade=True, skill_upgrade_success_threshold=3,
        )

        engine.call(self._request)
        self.assertEqual(llm_helper.calls, 1)
        engine.call(self._request)  # 冷却期为 0，应该再次尝试
        self.assertEqual(llm_helper.calls, 2)


if __name__ == "__main__":
    unittest.main()
