"""
tests/test_config_catalog_list_seed_merge.py

覆盖 next_doc/growth_advisor_improvement_plan_v4.md 方向二 2.2 节
（N3：关键词表 → tech_radar 种子同步）新增的两个 config_catalog 函数：
  - apply_list_seed_merge()  — list 字段幂等合并
  - write_config_file()      — 原子写入 agent_config.json

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_config_catalog_list_seed_merge.py -q
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.config import config_catalog as cc


class TestApplyListSeedMerge(unittest.TestCase):
    def test_merges_new_items_into_empty_block(self):
        new_cfg, added = cc.apply_list_seed_merge({}, "tech_radar", "keywords", ["Rust", "异步运行时"])
        self.assertEqual(added, 2)
        self.assertEqual(new_cfg["tech_radar"]["keywords"], ["Rust", "异步运行时"])

    def test_idempotent_case_insensitive_dedup(self):
        raw = {"tech_radar": {"keywords": ["Rust"]}}
        new_cfg, added = cc.apply_list_seed_merge(raw, "tech_radar", "keywords", ["rust", "Go"])
        self.assertEqual(added, 1)
        self.assertEqual(new_cfg["tech_radar"]["keywords"], ["Rust", "Go"])

    def test_does_not_mutate_input_dict(self):
        raw = {"tech_radar": {"keywords": ["Rust"]}}
        cc.apply_list_seed_merge(raw, "tech_radar", "keywords", ["Go"])
        self.assertEqual(raw["tech_radar"]["keywords"], ["Rust"])  # 原始 dict 未被修改

    def test_preserves_other_fields_in_same_block(self):
        raw = {"tech_radar": {"keywords": ["Rust"], "daily_seed_limit": 5}}
        new_cfg, _ = cc.apply_list_seed_merge(raw, "tech_radar", "keywords", ["Go"])
        self.assertEqual(new_cfg["tech_radar"]["daily_seed_limit"], 5)

    def test_empty_and_blank_items_are_skipped(self):
        new_cfg, added = cc.apply_list_seed_merge({}, "tech_radar", "keywords", ["", "   ", "Go"])
        self.assertEqual(added, 1)
        self.assertEqual(new_cfg["tech_radar"]["keywords"], ["Go"])

    def test_no_new_items_returns_zero_added(self):
        raw = {"tech_radar": {"keywords": ["Rust", "Go"]}}
        new_cfg, added = cc.apply_list_seed_merge(raw, "tech_radar", "keywords", ["rust", "go"])
        self.assertEqual(added, 0)
        self.assertEqual(new_cfg["tech_radar"]["keywords"], ["Rust", "Go"])


class TestWriteConfigFile(unittest.TestCase):
    def test_writes_and_is_readable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent_config.json"
            cc.write_config_file(config_path, {"tech_radar": {"keywords": ["Rust"]}})
            self.assertTrue(config_path.exists())
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["tech_radar"]["keywords"], ["Rust"])

    def test_overwrites_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent_config.json"
            config_path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            cc.write_config_file(config_path, {"a": 2})
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["a"], 2)
            # 临时文件不应该残留
            self.assertFalse((Path(tmp) / "agent_config.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
