"""
tests/test_generative_capability_engine.py

对应文档: next_doc/generative-capability-skill-plan.md 阶段七。

阶段一到阶段六的"验证结果"一直只靠手动跑 `capability_engine.py` 的 CLI
自测入口完成，没有进 `tests/` 目录的正常 pytest 回归覆盖——这是阶段七
明确要修的问题之一。本文件把此前实施记录里手工验证过的关键场景固化为
真正的自动化测试：

  1. `mini_agent.skills.generative_capability` 能被正常 import（不再需要
     `sys.path` hack）。
  2. `CapabilityEngine.resolve()` 的确定性匹配 + `execute()` 命中已有
     trusted member 并成功执行的路径。
  3. `resolve()` 未命中、且未注入 `explore_runner` 时明确返回
     `not_implemented`，不会伪造成功。
  4. 完整 explore -> distill -> 落盘 -> 免探索复用 闭环（用桩探索器/桩工具
     执行器，不依赖真实网络/API key，可在任意 CI 环境跑）。
  5. `src.mini_agent.skills.SkillLoader` 能正确解析 `skill_type:
     generative-capability` 与 `category_summary` frontmatter 字段，并且
     `build_context()` 对这类 skill 只注入一行摘要，不整段注入正文。
  6. `tools.capability_call.register_capability_tools` 注册出的工具函数，
     对普通静态 skill 正确拒绝、对不存在的 skill 名正确报错。

除第 4 类测试外均不依赖网络；第 4 类用的是 `build_stub_explorer`，同样不
发起真实网络调用。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSER_SITE_SCRAPER_DIR = REPO_ROOT / ".claude" / "skills" / "browser-site-scraper"
DOC_TEMPLATE_GENERATION_DIR = REPO_ROOT / ".claude" / "skills" / "doc-template-generation"


def _copy_skill_dir(src: Path) -> Path:
    """复制一份 skill 目录到临时目录，避免测试写操作(蒸馏落盘)污染仓库里的真实数据。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_skill_"))
    dst = tmp_dir / src.name
    shutil.copytree(src, dst)
    return dst


class TestPackageImport(unittest.TestCase):
    def test_import_without_sys_path_hack(self):
        from mini_agent.skills.generative_capability import (
            CapabilityEngine,
            ResolveResult,
            ExecuteResult,
            CapabilityCallResult,
            build_llm_resolver,
            build_stub_resolver,
            build_llm_explorer,
            build_stub_explorer,
            distill,
            validate_schema,
            run_patrol,
        )
        self.assertTrue(callable(CapabilityEngine))
        self.assertTrue(callable(build_stub_resolver))
        self.assertTrue(callable(build_stub_explorer))
        self.assertTrue(callable(distill))
        self.assertTrue(callable(validate_schema))
        self.assertTrue(callable(run_patrol))
        # 仅确认存在，不实际调用（这两个是真实/占位构造器）
        self.assertTrue(callable(build_llm_resolver))
        self.assertTrue(callable(build_llm_explorer))
        self.assertTrue(ResolveResult and ExecuteResult and CapabilityCallResult)


@unittest.skipUnless(BROWSER_SITE_SCRAPER_DIR.is_dir(), "browser-site-scraper skill 目录不存在")
class TestCapabilityEngineResolveExecute(unittest.TestCase):
    def setUp(self):
        self.skill_dir = _copy_skill_dir(BROWSER_SITE_SCRAPER_DIR)

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def test_domain_match_hit_but_execute_fails_without_real_browser(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        engine = CapabilityEngine(self.skill_dir)
        result = engine.call({
            "text": "https://www.baidu.com/s?wd=test",
            "target": {"url": "https://www.baidu.com/s?wd=test"},
            "query": "test",
        })
        # 沙盒/CI 环境没有可用浏览器，命中 baidu 后 execute() 必然失败，
        # 因为没有注入 explore_runner，会明确落到 not_implemented（不伪造成功）。
        self.assertEqual(result.resolve_reason, "domain_pattern_match")
        self.assertEqual(result.status, "not_implemented")

    def test_no_match_without_explore_runner_returns_not_implemented(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        engine = CapabilityEngine(self.skill_dir)
        result = engine.call({
            "text": "https://www.totally-unknown-site.example/x",
            "target": {"url": "https://www.totally-unknown-site.example/x"},
            "query": "",
        })
        self.assertEqual(result.resolve_reason, "no_match")
        self.assertEqual(result.status, "not_implemented")
        self.assertIsNotNone(result.error)

    def test_llm_resolver_hit_via_stub(self):
        from mini_agent.skills.generative_capability import CapabilityEngine, build_stub_resolver

        resolver = build_stub_resolver(["baidu"])
        engine = CapabilityEngine(self.skill_dir, llm_resolver=resolver)
        result = engine.call({
            "text": "帮我查一下这个论坛的内容",
            "target": {"url": "https://random-forum.example/x"},
            "query": "",
        })
        self.assertEqual(result.resolve_reason, "llm_match")

    def test_full_explore_distill_reuse_cycle(self):
        from mini_agent.skills.generative_capability import (
            CapabilityEngine, ExploreStep, build_stub_explorer,
        )

        target_url = "https://www.some-new-ci-site.example/x"
        steps = [ExploreStep(tool="browser_navigate", input={"url": target_url}, output={"ok": True})]
        explorer = build_stub_explorer(
            steps=steps, final_data={"results": [{"title": "桩数据", "url": target_url}]},
        )
        tool_executor = lambda name, inp: {"ok": True, "echo": inp}  # noqa: E731

        engine = CapabilityEngine(self.skill_dir, explore_runner=explorer, tool_executor=tool_executor)
        request = {"text": target_url, "target": {"url": target_url}, "query": ""}
        result = engine.call(request)

        # trust_trace_data 默认 false，重放最后一步是 browser_navigate（无 data），
        # 因此蒸馏自测预期会在这里失败——这是阶段五/阶段六实施记录里明确记录过的
        # 已知行为，不是本测试的 bug。用一个真正会在最后一步返回 data 的桩步骤
        # 来验证成功路径。
        self.assertEqual(result.status, "not_implemented")

        steps_with_data = [
            ExploreStep(
                tool="browser_extract_content",
                input={"url": target_url},
                output={"data": {"results": [{"title": "桩数据", "url": target_url}]}},
            )
        ]
        explorer2 = build_stub_explorer(
            steps=steps_with_data, final_data={"results": [{"title": "桩数据", "url": target_url}]},
        )
        tool_executor2 = lambda name, inp: {  # noqa: E731
            "ok": True, "data": {"results": [{"title": "桩数据", "url": target_url}]},
        }
        engine2 = CapabilityEngine(self.skill_dir, explore_runner=explorer2, tool_executor=tool_executor2)
        result2 = engine2.call(request)
        self.assertEqual(result2.status, "success")
        self.assertIsNotNone(result2.member_id)
        self.assertEqual(result2.resolve_reason, "explored")

        # 免探索复用：不注入 explore_runner，只注入 tool_executor，同一请求
        # 应该通过 domain_pattern_match 直接命中刚落盘的 member。
        engine3 = CapabilityEngine(self.skill_dir, tool_executor=tool_executor2)
        result3 = engine3.call(request)
        self.assertEqual(result3.status, "success")
        self.assertEqual(result3.resolve_reason, "domain_pattern_match")


@unittest.skipUnless(DOC_TEMPLATE_GENERATION_DIR.is_dir(), "doc-template-generation skill 目录不存在")
class TestSecondDomainReuse(unittest.TestCase):
    """验证第二个 generative-capability skill 复用同一套引擎（阶段五泛化性验证的回归覆盖）。"""

    def setUp(self):
        self.skill_dir = _copy_skill_dir(DOC_TEMPLATE_GENERATION_DIR)

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def test_standard_report_member_hit(self):
        from mini_agent.skills.generative_capability import CapabilityEngine

        engine = CapabilityEngine(self.skill_dir)
        result = engine.call({
            "text": "standard_report weekly",
            "target": {"template_name": "standard_report"},
            "content": {"title": "T", "body_sections": [{"heading": "H", "text": "x"}]},
        })
        self.assertEqual(result.status, "success")
        self.assertEqual(result.member_id, "standard_report")


class TestSkillLoaderGenerativeCapabilityAwareness(unittest.TestCase):
    """SkillLoader 对 skill_type: generative-capability 的特殊处理（阶段七新增）。"""

    def _write_skill(self, root: Path, skill_type: str = "generative-capability") -> Path:
        skill_dir = root / "fake-gc-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: fake-gc-skill\n"
            f"skill_type: {skill_type}\n"
            "category_summary: 一个用于测试的假领域能力包\n"
            "description: 测试用\n"
            "---\n\n"
            "# fake-gc-skill\n\n"
            "这一大段正文不应该出现在 build_context() 的输出里。"
            "member 清单、探索细节等本不该泄漏进主 context 的内容也写在这里。\n",
            encoding="utf-8",
        )
        return skill_dir

    def test_skill_type_and_category_summary_parsed(self):
        from mini_agent.skills import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root)
            loader = SkillLoader([root])
            skill = loader.get("fake-gc-skill")
            self.assertIsNotNone(skill)
            self.assertTrue(skill.is_generative_capability)
            self.assertEqual(skill.category_summary, "一个用于测试的假领域能力包")

    def test_build_context_only_injects_summary_line(self):
        from mini_agent.skills import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root)
            loader = SkillLoader([root])
            loader.activate("fake-gc-skill")
            ctx = loader.build_context()
            self.assertIn("一个用于测试的假领域能力包", ctx)
            self.assertIn("capability_call", ctx)
            # 正文里刻意写的"不应该出现"标记不应该被注入
            self.assertNotIn("这一大段正文不应该出现", ctx)

    def test_static_skill_unaffected(self):
        from mini_agent.skills import SkillLoader

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root, skill_type="")
            loader = SkillLoader([root])
            skill = loader.get("fake-gc-skill")
            self.assertFalse(skill.is_generative_capability)
            loader.activate("fake-gc-skill")
            ctx = loader.build_context()
            # 普通 skill 仍然整段注入正文
            self.assertIn("这一大段正文不应该出现", ctx)


class TestCapabilityCallTool(unittest.TestCase):
    """tools/capability_call.py 的边界条件（不发真实请求）。"""

    def _make_loader_and_registry(self, skill_type: str):
        from mini_agent.skills import SkillLoader
        from mini_agent.tools import ToolRegistry
        from mini_agent.tools.capability_call import register_capability_tools

        tmp = Path(tempfile.mkdtemp(prefix="gc_tool_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        skill_dir = tmp / "some-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: some-skill\n"
            + (f"skill_type: {skill_type}\n" if skill_type else "")
            + "description: 测试\n---\n\n正文\n",
            encoding="utf-8",
        )
        loader = SkillLoader([tmp])
        registry = ToolRegistry()
        register_capability_tools(registry, loader)
        return registry

    def test_rejects_static_skill(self):
        registry = self._make_loader_and_registry(skill_type="")
        tool = registry.get("capability_call")
        result = json.loads(tool.fn(skill_name="some-skill", request={}))
        self.assertEqual(result["status"], "error")
        self.assertIn("普通静态 skill", result["error"])

    def test_reports_unknown_skill(self):
        registry = self._make_loader_and_registry(skill_type="generative-capability")
        tool = registry.get("capability_call")
        result = json.loads(tool.fn(skill_name="does-not-exist", request={}))
        self.assertEqual(result["status"], "error")
        self.assertIn("available_generative_capability_skills", result)
        self.assertIn("some-skill", result["available_generative_capability_skills"])


if __name__ == "__main__":
    unittest.main()
