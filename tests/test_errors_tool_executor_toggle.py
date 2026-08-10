"""tests/test_errors_tool_executor_toggle.py — 覆盖
next_doc/errors_tool_executor_log_toggle_plan.md：

  1. `configure_tool_executor_log_saving(False)` 后，`where` 前缀命中
     "mini_agent.tool_executor" 的记录不再写入全局错误日志文件，其它
     `where` 的记录不受影响。
  2. `reraise=True` 时无论开关状态如何，原异常都会被重新抛出。
  3. `config/loader.py::load_config()` 能把 agent_config.json 里的
     `save_tool_executor_error_logs` 字段同步到 errors.py 的开关。
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mini_agent.errors as errors_mod
from mini_agent.errors import configure_tool_executor_log_saving, log_exception


class TestToolExecutorLogToggle(unittest.TestCase):
    def setUp(self):
        # 每个用例独立的临时日志文件，避免污染真实 ~/.agent/logs/error.jsonl，
        # 也避免用例之间通过模块级单例 `_FILE_LOGGER` 互相影响。
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._log_path = Path(self._tmp_dir.name) / "error.jsonl"
        self._patcher = mock.patch.object(
            errors_mod, "_error_log_path", return_value=self._log_path
        )
        self._patcher.start()
        errors_mod._FILE_LOGGER = None
        # `_get_file_logger()` 内部用 `logging.getLogger("mini_agent._errors_sink")`
        # 拿到的是进程级单例 logger，仅重置 `_FILE_LOGGER` 缓存不够——上一个
        # 用例挂的 handler（指向已被清理的临时目录）还留在 logger 上，
        # `if not logger.handlers` 的判断会让本用例复用那个失效 handler。
        # 这里一并清空，保证每个用例都拿到指向自己临时目录的新 handler。
        sink_logger = logging.getLogger("mini_agent._errors_sink")
        sink_logger.handlers.clear()

    def tearDown(self):
        self._patcher.stop()
        errors_mod._FILE_LOGGER = None
        self._tmp_dir.cleanup()
        # 恢复默认值，不让本文件的用例影响其它测试文件的默认行为。
        configure_tool_executor_log_saving(True)

    def _read_lines(self) -> list[dict]:
        if not self._log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self._log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_default_enabled_writes_tool_executor_records(self):
        log_exception(ValueError("boom"), where="mini_agent.tool_executor")
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["where"], "mini_agent.tool_executor")

    def test_disabled_skips_tool_executor_records(self):
        configure_tool_executor_log_saving(False)
        log_exception(ValueError("boom"), where="mini_agent.tool_executor")
        self.assertEqual(self._read_lines(), [])

    def test_disabled_still_writes_other_where_records(self):
        configure_tool_executor_log_saving(False)
        log_exception(ValueError("boom"), where="mini_agent.some_other_module")
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["where"], "mini_agent.some_other_module")

    def test_disabled_still_reraises(self):
        configure_tool_executor_log_saving(False)
        exc = ValueError("boom")
        with self.assertRaises(ValueError):
            log_exception(exc, where="mini_agent.tool_executor", reraise=True)
        self.assertEqual(self._read_lines(), [])

    def test_prefix_match_covers_submodule_where(self):
        # where 是 "mini_agent.tool_executor" 的前缀匹配（比如未来加了
        # "mini_agent.tool_executor.helper" 这种细分 where），也应该被
        # 同一个开关覆盖。
        configure_tool_executor_log_saving(False)
        log_exception(ValueError("boom"), where="mini_agent.tool_executor.helper")
        self.assertEqual(self._read_lines(), [])


class TestLoadConfigBridgesToggle(unittest.TestCase):
    def tearDown(self):
        configure_tool_executor_log_saving(True)

    def test_load_config_syncs_flag_to_errors_module(self):
        from mini_agent.config.loader import load_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent_config.json").write_text(
                json.dumps({"save_tool_executor_error_logs": False}),
                encoding="utf-8",
            )
            load_config(project_root=root)
            self.assertFalse(errors_mod._SAVE_TOOL_EXECUTOR_ERROR_LOGS)

            (root / "agent_config.json").write_text(
                json.dumps({"save_tool_executor_error_logs": True}),
                encoding="utf-8",
            )
            load_config(project_root=root)
            self.assertTrue(errors_mod._SAVE_TOOL_EXECUTOR_ERROR_LOGS)


if __name__ == "__main__":
    unittest.main()
