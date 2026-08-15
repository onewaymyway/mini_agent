"""
tests/test_flat_nested_config_compat.py

`next_doc/flat_nested_config_unification_migration_plan.md` Stage 1/2 的专项
回归测试：

  1. `param_registry.load_nested_block_with_flat_compat()` 本身的优先级
     行为（nested > 旧 flat key > dataclass 默认值），以及"脏配置不中断
     加载流程"的容错行为。
  2. `loader.py` 里迁移的 11 个 block（memory/compress/tool_trim/skill/
     perception/session/profile/debug/http/retry/ensemble）端到端跑
     `load_config()`，验证：
       a) 只写 flat key 仍然生效（向后兼容存量 agent_config.json）
       b) 只写 nested 写法也生效（此前部分 block 完全不支持，是本次
          迁移要补齐的能力）
       c) 同时写两种写法时 nested 优先
       d) 有 CLI/函数参数覆盖需求的字段，CLI 优先级最高
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from mini_agent.config.loader import load_config
from mini_agent.config.param_registry import load_nested_block_with_flat_compat


@dataclasses.dataclass
class _Dummy:
    a: int = 1
    b: str = "x"
    c: bool = False


class TestLoadNestedBlockWithFlatCompat:
    def test_nested_only(self):
        cfg = {"dummy": {"a": 5}}
        d = load_nested_block_with_flat_compat(cfg, "dummy", _Dummy, flat_key_map={"a": "dummy_a"})
        assert d.a == 5 and d.b == "x" and d.c is False

    def test_flat_only(self):
        cfg = {"dummy_a": 7}
        d = load_nested_block_with_flat_compat(cfg, "dummy", _Dummy, flat_key_map={"a": "dummy_a"})
        assert d.a == 7

    def test_nested_wins_over_flat(self):
        cfg = {"dummy": {"a": 5}, "dummy_a": 7}
        d = load_nested_block_with_flat_compat(cfg, "dummy", _Dummy, flat_key_map={"a": "dummy_a"})
        assert d.a == 5

    def test_missing_flat_key_map_entry_is_pure_nested(self):
        # b 没有登记 flat_key_map，只能通过 nested 写法设置
        cfg = {"dummy_b": "should-not-apply"}
        d = load_nested_block_with_flat_compat(cfg, "dummy", _Dummy, flat_key_map={"a": "dummy_a"})
        assert d.b == "x"

    def test_dirty_flat_value_falls_back_to_default(self):
        cfg = {"dummy_a": "not-an-int"}
        d = load_nested_block_with_flat_compat(cfg, "dummy", _Dummy, flat_key_map={"a": "dummy_a"})
        assert d.a == 1  # 转换失败，回退默认值，不抛异常

    def test_explicit_null_in_nested_overrides_to_none(self):
        @dataclasses.dataclass
        class _Opt:
            x: "int | None" = 3
        cfg = {"opt": {"x": None}}
        d = load_nested_block_with_flat_compat(cfg, "opt", _Opt, flat_key_map={"x": "opt_x"})
        assert d.x is None

    def test_missing_block_uses_all_defaults(self):
        d = load_nested_block_with_flat_compat({}, "dummy", _Dummy, flat_key_map={"a": "dummy_a"})
        assert d == _Dummy()


def _write_cfg(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "agent_config.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


class TestElevenBlocksEndToEnd:
    """对 11 个迁移 block 各挑一个代表性字段，跑通 flat/nested/CLI 三层。"""

    @pytest.mark.parametrize(
        "block,flat_key,flat_val,nested_field,nested_val,getter",
        [
            ("memory", "memory_top_k", 9, "top_k", 3, lambda c: c.memory.top_k),
            ("compress", "compact_max_turns", 15, "max_turns_before_compact", 30, lambda c: c.compress.max_turns_before_compact),
            ("tool_trim", "tool_trim_grep_max_lines", 10, "grep_max_lines", 99, lambda c: c.tool_trim.grep_max_lines),
            ("skill", "skill_compact_budget", 1000, "compact_budget", 2000, lambda c: c.skill.compact_budget),
            ("perception", "token_warn_threshold", 0.5, "token_warn_threshold", 0.9, lambda c: c.perception.token_warn_threshold),
            ("session", "session_summary_min_turns", 2, "summary_min_turns", 6, lambda c: c.session.summary_min_turns),
            ("profile", "profile_min_entries", 2, "min_entries", 5, lambda c: c.profile.min_entries),
            ("http", "http_ring_maxlen", 100, "ring_maxlen", 3000, lambda c: c.http.ring_maxlen),
            ("retry", "llm_retry_max", 2, "max_retries", 20, lambda c: c.retry.max_retries),
            ("ensemble", "ensemble_n", 2, "n", 5, lambda c: c.ensemble.n),
        ],
    )
    def test_flat_then_nested_then_both(self, tmp_path, block, flat_key, flat_val, nested_field, nested_val, getter):
        # 只写 flat key
        _write_cfg(tmp_path, {flat_key: flat_val})
        cfg = load_config(project_root=tmp_path)
        assert getter(cfg) == flat_val

        # 只写 nested
        _write_cfg(tmp_path, {block: {nested_field: nested_val}})
        cfg = load_config(project_root=tmp_path)
        assert getter(cfg) == nested_val

        # 两者都写：nested 优先
        _write_cfg(tmp_path, {block: {nested_field: nested_val}, flat_key: flat_val})
        cfg = load_config(project_root=tmp_path)
        assert getter(cfg) == nested_val

    def test_debug_block_flat_and_nested(self, tmp_path):
        _write_cfg(tmp_path, {"debug_llm": True})
        cfg = load_config(project_root=tmp_path)
        assert cfg.debug.llm_enabled is True

        _write_cfg(tmp_path, {"debug": {"llm_enabled": True}})
        cfg = load_config(project_root=tmp_path)
        assert cfg.debug.llm_enabled is True

    def test_memory_cli_override_wins_over_nested_and_flat(self, tmp_path):
        _write_cfg(tmp_path, {"memory": {"top_k": 3}, "memory_top_k": 9})
        cfg = load_config(project_root=tmp_path, memory_top_k=42)
        assert cfg.memory.top_k == 42

    def test_skill_cli_override_wins_over_nested_and_flat(self, tmp_path):
        _write_cfg(tmp_path, {"skill": {"semantic_enabled": False}, "skill_semantic_enabled": False})
        cfg = load_config(project_root=tmp_path, skill_semantic_enabled=True)
        assert cfg.skill.semantic_enabled is True

    def test_compress_previously_nested_only_field_still_works(self, tmp_path):
        """extraction_trigger_*/selective_* 这批字段迁移前后都只支持 nested
        写法，确认迁移没有破坏这批本来就能工作的字段。"""
        _write_cfg(tmp_path, {"compress": {"selective_min_user_turns": 7}})
        cfg = load_config(project_root=tmp_path)
        assert cfg.compress.selective_min_user_turns == 7

    def test_http_no_cli_plumbing_pure_flat_nested(self, tmp_path):
        _write_cfg(tmp_path, {"http": {"port": 1234}})
        cfg = load_config(project_root=tmp_path)
        assert cfg.http.port == 1234

    def test_defaults_match_dataclass_when_unconfigured(self, tmp_path):
        _write_cfg(tmp_path, {})
        cfg = load_config(project_root=tmp_path)
        from mini_agent.config.models import (
            MemoryConfig, CompressConfig, ToolTrimConfig, SkillConfig,
            PerceptionConfig, SessionConfig, ProfileConfig, DebugConfig,
            HttpConfig, RetryConfig, EnsembleConfig,
        )
        assert cfg.tool_trim.raw_store_max_entries == ToolTrimConfig().raw_store_max_entries
        assert cfg.skill.auto_unload_idle_seconds == SkillConfig().auto_unload_idle_seconds
        assert cfg.perception.tool_cache_max_entries == PerceptionConfig().tool_cache_max_entries
        assert cfg.session.backend == SessionConfig().backend
        assert cfg.profile.stale_after_days == ProfileConfig().stale_after_days
        assert cfg.http.allowed_ips == HttpConfig().allowed_ips
        assert cfg.retry.network_aware == RetryConfig().network_aware
        assert cfg.ensemble.judge_strategy == EnsembleConfig().judge_strategy
        assert cfg.debug.llm_enabled == DebugConfig().llm_enabled
