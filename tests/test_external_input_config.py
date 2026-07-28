"""tests/test_external_input_config.py — sources.yaml 加载（P2）测试

覆盖：
  1. 文件不存在 → 空列表
  2. 正常加载多条 source，含默认值填充（enabled/interval_seconds）
  3. 顶层结构不对 → SourcesConfigError
  4. 单条记录缺 id/type → 跳过该条，其余正常加载
  5. interval_seconds 非法值 → 回退默认值
  6. get_source_config 按 id 查找
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.config import (
    DEFAULT_INTERVAL_SECONDS,
    SourcesConfigError,
    get_source_config,
    load_sources_config,
)
from mini_agent.storage.paths import AgentPaths


class TestSourcesConfig(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, content: str) -> None:
        p = self.paths.external_input_sources_config
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_sources_config(self.paths), [])

    def test_load_multiple_sources_with_defaults(self):
        self._write(
            "sources:\n"
            "  - id: my_watch\n"
            "    type: watch\n"
            "    interval_seconds: 60\n"
            "    params:\n"
            "      url: https://example.com/feed\n"
            "  - id: my_webhook\n"
            "    type: webhook\n"
        )
        configs = load_sources_config(self.paths)
        self.assertEqual(len(configs), 2)

        watch_cfg = next(c for c in configs if c.id == "my_watch")
        self.assertEqual(watch_cfg.type, "watch")
        self.assertEqual(watch_cfg.interval_seconds, 60)
        self.assertTrue(watch_cfg.enabled)
        self.assertEqual(watch_cfg.params["url"], "https://example.com/feed")

        webhook_cfg = next(c for c in configs if c.id == "my_webhook")
        self.assertEqual(webhook_cfg.interval_seconds, DEFAULT_INTERVAL_SECONDS)
        self.assertTrue(webhook_cfg.enabled)

    def test_invalid_top_level_structure_raises(self):
        self._write("not_sources_key:\n  - id: a\n")
        with self.assertRaises(SourcesConfigError):
            load_sources_config(self.paths)

    def test_entry_missing_required_field_is_skipped(self):
        self._write(
            "sources:\n"
            "  - id: valid_one\n"
            "    type: watch\n"
            "  - type: no_id_here\n"
            "  - id: no_type_here\n"
        )
        configs = load_sources_config(self.paths)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].id, "valid_one")

    def test_invalid_interval_falls_back_to_default(self):
        self._write(
            "sources:\n"
            "  - id: bad_interval\n"
            "    type: watch\n"
            "    interval_seconds: not_a_number\n"
        )
        configs = load_sources_config(self.paths)
        self.assertEqual(configs[0].interval_seconds, DEFAULT_INTERVAL_SECONDS)

    def test_get_source_config_by_id(self):
        self._write(
            "sources:\n"
            "  - id: a\n    type: watch\n"
            "  - id: b\n    type: webhook\n"
        )
        cfg = get_source_config(self.paths, "b")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.type, "webhook")
        self.assertIsNone(get_source_config(self.paths, "missing"))


if __name__ == "__main__":
    unittest.main()
