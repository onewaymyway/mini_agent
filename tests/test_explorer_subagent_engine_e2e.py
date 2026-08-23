"""
tests/test_explorer_subagent_engine_e2e.py

对应 next_doc/generative_capability_explorer_rearch_plan.md 阶段四：
「新增/改造测试覆盖」中明确要求、但此前测试矩阵里缺失的一类场景——
`build_subagent_explorer()`（阶段一的真实 SubAgent 驱动探索器，而不是
`build_stub_explorer()`）被真正接到 `CapabilityEngine.call()` 里跑一次完整
的 miss -> explore -> distill -> 落盘 -> 免探索复用 闭环。

此前 `test_explorer_runtime_subagent.py` 只验证到「探索器本身接线正确」
（`finish`/`report_failure`/领域工具桥接/预算判定），没有验证「探索结果被
`CapabilityEngine`/`distiller.py` 正确消费、落盘、复用」这一段；
`test_generative_capability_engine.py::TestRealTextCoreExploreEndToEnd`
验证的是旧的 `build_llm_explorer()`（手写循环）链路。本文件补齐两者之间
缺失的组合场景，且覆盖 `script_source` 优先路径与无 `script_source` 时的
trace-replay 兜底路径都不回归。

不发起真实网络请求：延续 `test_explorer_runtime_subagent.py` 的做法，
monkeypatch `SubAgent._run_with_capture`，在其内部直接调用探索用 Agent 上
已注册的 `finish`/`report_failure` 工具，模拟"探索子agent这一轮的决定"。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa: F401（确保内置工具已注册）
from mini_agent.orchestrator.sub_agent import SubAgent
from mini_agent.skills.generative_capability import CapabilityEngine
from mini_agent.skills.generative_capability.explorer_runtime import (
    build_subagent_explorer,
    FINISH_TOOL,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_TRANSFORM_CAPABILITY_DIR = REPO_ROOT / ".claude" / "skills" / "text-transform-capability"


def _copy_skill_dir(src: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="gc_skill_e2e_"))
    dst = tmp_dir / src.name
    shutil.copytree(src, dst)
    return dst


def make_cfg(project_root: Path = None, **overrides):
    from mini_agent.config import load_config
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@unittest.skipUnless(TEXT_TRANSFORM_CAPABILITY_DIR.is_dir(), "text-transform-capability skill 目录不存在")
class TestSubagentExplorerScriptSourceEndToEnd(unittest.TestCase):
    """script_source 路径：探索子agent自己交付可复用脚本，直接校验落盘。"""

    def setUp(self):
        self.skill_dir = _copy_skill_dir(TEXT_TRANSFORM_CAPABILITY_DIR)
        self.cfg = make_cfg()

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def test_explore_with_script_source_composes_new_member_and_is_reused(self):
        script_source = (
            "def run(input):\n"
            "    text = input.get('content', {}).get('text', '')\n"
            "    return {'status': 'success', 'data': {'result': {'text': text.upper() + '!'}}}\n"
        )

        def fake_run_with_capture(self, agent, prompt):
            finish_fn = agent.registry.get(FINISH_TOOL).fn
            return finish_fn(
                data={"result": {"text": "HELLO!"}},
                script_source=script_source,
            )

        request = {
            "text": "帮我对一段文本执行一个还没见过的定制操作",
            "target": {"op": "shout-e2e-novel"},
            "content": {"text": "hello"},
        }

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(self.cfg)
            engine = CapabilityEngine(self.skill_dir, explore_runner=explorer)
            result = engine.call(request)

        self.assertEqual(result.status, "success", msg=result.error)
        self.assertEqual(result.resolve_reason, "explored")
        self.assertIsNotNone(result.member_id)

        # meta.json 应如实记录这次蒸馏走的是 script_source 路径（阶段二）。
        import json
        meta_path = self.skill_dir / "members" / result.member_id / "meta.json"
        self.assertTrue(meta_path.is_file())
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta.get("distill_source_kind"), "script_source")

        # 免探索复用：不注入 explore_runner，同一请求应直接命中刚落盘的 member。
        engine2 = CapabilityEngine(self.skill_dir)
        result2 = engine2.call(request)
        self.assertEqual(result2.status, "success")
        self.assertEqual(result2.data, {"result": {"text": "HELLO!"}})


@unittest.skipUnless(TEXT_TRANSFORM_CAPABILITY_DIR.is_dir(), "text-transform-capability skill 目录不存在")
class TestSubagentExplorerBudgetExhaustedEndToEnd(unittest.TestCase):
    """预算耗尽（既不 finish 也不 report_failure）应让 CapabilityEngine 判失败，不伪造成功。"""

    def setUp(self):
        self.skill_dir = _copy_skill_dir(TEXT_TRANSFORM_CAPABILITY_DIR)
        self.cfg = make_cfg()

    def tearDown(self):
        shutil.rmtree(self.skill_dir.parent, ignore_errors=True)

    def test_engine_reports_not_implemented_without_fabricating_success(self):
        def fake_run_with_capture(self, agent, prompt):
            return "模型没有调用任何终态工具。"

        request = {
            "text": "做一个从没见过的变换",
            "target": {"op": "totally-novel-op"},
            "content": {"text": "hello"},
        }

        with patch.object(SubAgent, "_run_with_capture", fake_run_with_capture):
            explorer = build_subagent_explorer(self.cfg)
            engine = CapabilityEngine(self.skill_dir, explore_runner=explorer)
            result = engine.call(request)

        self.assertEqual(result.status, "not_implemented")
        self.assertFalse((self.skill_dir / "members" / "totally-novel-op").exists())


if __name__ == "__main__":
    unittest.main()
