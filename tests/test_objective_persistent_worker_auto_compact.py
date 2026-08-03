"""
tests/test_objective_persistent_worker_auto_compact.py

覆盖 next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
§7.3（"持久 Worker 的会话历史没有上限，理论上可能撑爆 context window"）的修复：

  - build_objective_agent(persistent=True) 在项目全局没有开启
    cfg.compress.enabled 时，会强制打开 token 阈值 compact 触发器
    （复用现有的 compact_with_skills 实现）。
  - 项目全局已经显式配置过 cfg.compress.enabled（无论 True/False）时，
    尊重用户配置，不覆盖。
  - autonomy.objective_persistent_worker_auto_compact_enabled=False 时，
    完全不介入，行为与改造前一致。
  - persistent=False（隔离 runner 路径）不受影响——每个 step 都是一次性
    Agent，没有历史累积问题，不需要这个兜底。

测试直接调用 build_objective_agent()，用 monkeypatch 替身 load_config /
LLMConfig / PermissionGuard / create_client / Agent，避免真实网络/LLM 依赖
（与 tests/test_objective_persistent_runner.py 里已有的同类测试同一套模式）。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mini_agent.evolution import objective_agent_bridge as mod


class _CompressCfg:
    def __init__(self, enabled=False, threshold=0.7):
        self.enabled = enabled
        self.threshold = threshold


class _FakeAppConfigReal:
    project_root = "."
    sandbox = None
    model = "m"
    llm_provider = "anthropic"
    llm_base_url = None
    use_system_tool_call = False
    debug_llm = False
    tool_cache_enabled = False
    api_key = "x"
    system_extra = ""

    def __init__(self, autonomy=None):
        self.autonomy = autonomy


class _FakeAutonomyConfig:
    def __init__(self, auto_compact_enabled=True, auto_compact_threshold=0.75):
        self.objective_persistent_worker_auto_compact_enabled = auto_compact_enabled
        self.objective_persistent_worker_auto_compact_threshold = auto_compact_threshold


def _patch_build_deps(loaded_cfg):
    class _FakeGuard:
        def __init__(self, **kwargs):
            pass

    class _FakeLLMConfig:
        @staticmethod
        def from_app_config(cfg):
            return object()

    def fake_load_config(**kwargs):
        return loaded_cfg

    return patch.multiple(
        mod,
        load_config=fake_load_config,
        LLMConfig=_FakeLLMConfig,
        PermissionGuard=_FakeGuard,
        create_client=lambda *a, **kw: object(),
        Agent=lambda **kwargs: kwargs,
    )


class TestPersistentWorkerAutoCompactFloor(unittest.TestCase):
    def test_forces_compress_enabled_when_project_left_it_off(self):
        """项目全局 compress.enabled=False（默认）时，persistent=True 应该
        强制打开，且阈值取 autonomy 里配置的兜底阈值。"""
        loaded_cfg = type("_Cfg", (), {
            "api_key": "x", "system_extra": "",
            "compress": _CompressCfg(enabled=False, threshold=0.7),
        })()
        base_cfg = _FakeAppConfigReal(autonomy=_FakeAutonomyConfig(
            auto_compact_enabled=True, auto_compact_threshold=0.6,
        ))
        with _patch_build_deps(loaded_cfg):
            mod.build_objective_agent(base_cfg, "obj", "exec_1", persistent=True)

        self.assertTrue(loaded_cfg.compress.enabled)
        self.assertEqual(loaded_cfg.compress.threshold, 0.6)

    def test_respects_project_global_compress_enabled_true(self):
        """项目全局已经显式打开了 compress.enabled（自定义阈值），应该
        原样保留，不被兜底逻辑覆盖成默认阈值。"""
        loaded_cfg = type("_Cfg", (), {
            "api_key": "x", "system_extra": "",
            "compress": _CompressCfg(enabled=True, threshold=0.42),
        })()
        base_cfg = _FakeAppConfigReal(autonomy=_FakeAutonomyConfig())
        with _patch_build_deps(loaded_cfg):
            mod.build_objective_agent(base_cfg, "obj", "exec_1", persistent=True)

        self.assertTrue(loaded_cfg.compress.enabled)
        self.assertEqual(loaded_cfg.compress.threshold, 0.42)

    def test_respects_project_global_compress_enabled_false_explicit_opt_out(self):
        """项目显式关闭兜底开关（autonomy 配置）时，即使全局
        compress.enabled=False，也不应该被强制打开。"""
        loaded_cfg = type("_Cfg", (), {
            "api_key": "x", "system_extra": "",
            "compress": _CompressCfg(enabled=False, threshold=0.7),
        })()
        base_cfg = _FakeAppConfigReal(autonomy=_FakeAutonomyConfig(auto_compact_enabled=False))
        with _patch_build_deps(loaded_cfg):
            mod.build_objective_agent(base_cfg, "obj", "exec_1", persistent=True)

        self.assertFalse(loaded_cfg.compress.enabled)
        self.assertEqual(loaded_cfg.compress.threshold, 0.7)

    def test_no_autonomy_config_falls_back_to_default_floor(self):
        """base_cfg.autonomy 为 None（比如测试/精简场景）时，兜底默认仍然
        生效（getattr 默认值 True / 0.75），不应该抛异常。"""
        loaded_cfg = type("_Cfg", (), {
            "api_key": "x", "system_extra": "",
            "compress": _CompressCfg(enabled=False, threshold=0.7),
        })()
        base_cfg = _FakeAppConfigReal(autonomy=None)
        with _patch_build_deps(loaded_cfg):
            mod.build_objective_agent(base_cfg, "obj", "exec_1", persistent=True)

        self.assertTrue(loaded_cfg.compress.enabled)
        self.assertEqual(loaded_cfg.compress.threshold, 0.75)

    def test_isolated_path_not_affected(self):
        """persistent=False（隔离 runner）不应该触碰 compress 配置——每个
        step 都是一次性 Agent，没有历史累积问题。"""
        loaded_cfg = type("_Cfg", (), {
            "api_key": "x", "system_extra": "",
            "compress": _CompressCfg(enabled=False, threshold=0.7),
        })()
        base_cfg = _FakeAppConfigReal(autonomy=_FakeAutonomyConfig())
        with _patch_build_deps(loaded_cfg):
            mod.build_objective_agent(base_cfg, "obj", "exec_1", persistent=False)

        self.assertFalse(loaded_cfg.compress.enabled)
        self.assertEqual(loaded_cfg.compress.threshold, 0.7)


if __name__ == "__main__":
    unittest.main()
