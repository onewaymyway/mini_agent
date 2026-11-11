"""源恢复后，调度状态不再显示旧错误；失败期间才显示最近错误。"""
import unittest

from core import health
from core.settings import load_settings
from server import api
from tests.helpers import make_project
from tests.test_digest_and_tick import HOT


class StatusRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"hot_a": HOT})
        self.settings = load_settings(self.root)

    def _row(self):
        return next(r for r in api.tick_status(self.settings)["sources"] if r["id"] == "hot_a")

    def test_error_shown_while_failing(self):
        health.record_result(self.settings, "hot_a", False, "解析到 0 条")
        r = self._row()
        self.assertEqual(r["failures"], 1)
        self.assertEqual(r["last_error"], "解析到 0 条")

    def test_stale_error_cleared_after_recovery(self):
        health.record_result(self.settings, "hot_a", False, "解析到 0 条")
        health.record_result(self.settings, "hot_a", True)
        r = self._row()
        self.assertEqual(r["failures"], 0)
        self.assertIsNone(r["last_error"])

    def test_error_not_leaked_when_failure_count_zero(self):
        # 即使状态文件里残留 last_error，只要没有连续失败，接口也不返回它
        import json
        path = self.settings.paths.state_dir / "_health.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hot_a": {"failures": 0, "last_error": "旧错误"}}, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(self._row()["last_error"])


if __name__ == "__main__":
    unittest.main()
