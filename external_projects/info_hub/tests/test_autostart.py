"""看板启动后自动恢复：默认关闭、只启动未运行的服务、两项互不影响、异常不外抛、接口校验。"""
import unittest
from unittest import mock

from core import autostart
from core import dedicated_browser as db
from core import services
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

RUNNING_RECEIVER = {"running": True, "mode": "managed", "port": 18765}
STOPPED_RECEIVER = {"running": False, "mode": "stopped", "port": 18765}
BROWSER_RUNNING = {"state": "running", "pids": [1]}
BROWSER_STOPPED = {"state": "stopped", "pids": []}


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_default_is_disabled_and_run_does_nothing(self):
        self.assertFalse(autostart.is_enabled(self.settings))
        with mock.patch.object(services, "start") as start, mock.patch.object(db, "open_url") as open_url:
            report = autostart.run(self.settings)
        self.assertEqual(report, {"enabled": False})
        start.assert_not_called()
        open_url.assert_not_called()
        self.assertIsNone(autostart.last_report(self.settings))

    def test_yaml_default_is_used_when_not_saved(self):
        self.settings.raw["service_autostart"] = {"enabled": True}
        self.assertTrue(autostart.is_enabled(self.settings))

    def test_saved_value_overrides_yaml_default(self):
        self.settings.raw["service_autostart"] = {"enabled": True}
        autostart.set_enabled(self.settings, False)
        self.assertFalse(autostart.is_enabled(self.settings))

    def test_set_enabled_rejects_non_bool(self):
        with self.assertRaises(ValueError):
            autostart.set_enabled(self.settings, "yes")

    def test_starts_both_when_both_stopped(self):
        autostart.set_enabled(self.settings, True)
        with mock.patch.object(services, "status", return_value=STOPPED_RECEIVER), \
             mock.patch.object(services, "start", return_value={"port": 18765, "pid": 42}) as start, \
             mock.patch.object(db, "browser_status", return_value=BROWSER_STOPPED), \
             mock.patch.object(db, "open_url", return_value={"pid": 7}) as open_url:
            report = autostart.run(self.settings)
        start.assert_called_once()
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[2], "about:blank")
        self.assertEqual(report["receiver"]["action"], "started")
        self.assertEqual(report["browser"]["action"], "started")
        self.assertEqual(autostart.last_report(self.settings)["browser"]["action"], "started")

    def test_does_not_restart_what_is_already_running(self):
        autostart.set_enabled(self.settings, True)
        with mock.patch.object(services, "status", return_value=RUNNING_RECEIVER), \
             mock.patch.object(services, "start") as start, \
             mock.patch.object(db, "browser_status", return_value=BROWSER_RUNNING), \
             mock.patch.object(db, "open_url") as open_url:
            report = autostart.run(self.settings)
        start.assert_not_called()
        open_url.assert_not_called()
        self.assertEqual(report["receiver"]["action"], "already_running")
        self.assertEqual(report["browser"]["action"], "already_running")

    def test_receiver_failure_does_not_block_browser(self):
        autostart.set_enabled(self.settings, True)
        with mock.patch.object(services, "status", return_value=STOPPED_RECEIVER), \
             mock.patch.object(services, "start",
                               side_effect=services.ServiceError("端口 18765 已被其他程序占用\n更多")), \
             mock.patch.object(db, "browser_status", return_value=BROWSER_STOPPED), \
             mock.patch.object(db, "open_url", return_value={"pid": 7}) as open_url:
            report = autostart.run(self.settings)
        open_url.assert_called_once()
        self.assertEqual(report["receiver"]["action"], "failed")
        self.assertEqual(report["receiver"]["message"], "端口 18765 已被其他程序占用")
        self.assertEqual(report["browser"]["action"], "started")

    def test_browser_not_found_is_reported_not_raised(self):
        autostart.set_enabled(self.settings, True)
        with mock.patch.object(services, "status", return_value=RUNNING_RECEIVER), \
             mock.patch.object(db, "browser_status", return_value=BROWSER_STOPPED), \
             mock.patch.object(db, "open_url", side_effect=FileNotFoundError("未找到 Chrome")):
            report = autostart.run(self.settings)
        self.assertEqual(report["browser"], {"action": "failed", "message": "未找到 Chrome"})

    def test_unknown_browser_state_is_skipped_to_avoid_double_launch(self):
        autostart.set_enabled(self.settings, True)
        with mock.patch.object(services, "status", return_value=RUNNING_RECEIVER), \
             mock.patch.object(db, "browser_status", return_value={"state": "unknown", "pids": []}), \
             mock.patch.object(db, "open_url") as open_url:
            report = autostart.run(self.settings)
        open_url.assert_not_called()
        self.assertEqual(report["browser"]["action"], "skipped")

    def test_unexpected_exception_is_recorded_not_raised(self):
        autostart.set_enabled(self.settings, True)
        with mock.patch.object(services, "status", side_effect=RuntimeError("boom")), \
             mock.patch.object(db, "browser_status", return_value=BROWSER_RUNNING):
            report = autostart.run(self.settings)
        self.assertEqual(report["receiver"]["action"], "failed")
        self.assertIn("RuntimeError", report["receiver"]["message"])
        self.assertTrue(autostart.last_report(self.settings))

    def test_describe_lines(self):
        self.assertEqual(autostart.describe(None), [])
        self.assertEqual(autostart.describe({"enabled": False}), [])
        lines = autostart.describe({
            "enabled": True,
            "receiver": {"action": "already_running"},
            "browser": {"action": "failed", "message": "未找到 Chrome"},
        })
        self.assertEqual(lines, ["接收服务已在运行，未重复启动", "专用浏览器启动失败：未找到 Chrome"])


class AutostartApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_overview_reports_default(self):
        out = api.autostart_overview(self.settings)
        self.assertFalse(out["enabled"])
        self.assertIsNone(out["last"])
        self.assertEqual(out["summary"], [])

    def test_set_persists_and_returns_overview(self):
        out = api.autostart_set(self.settings, {"enabled": True})
        self.assertTrue(out["enabled"])
        self.assertTrue(api.autostart_overview(self.settings)["enabled"])

    def test_set_rejects_non_bool(self):
        for bad in ("true", 1, None, "false"):
            with self.subTest(bad=bad), self.assertRaises(ApiError) as cm:
                api.autostart_set(self.settings, {"enabled": bad})
            self.assertEqual(cm.exception.status, 400)
        self.assertFalse(api.autostart_overview(self.settings)["enabled"])


if __name__ == "__main__":
    unittest.main()


class DashboardAutostartPanelTests(unittest.TestCase):
    """看板“服务管理”里的自动恢复设置：AppTest 渲染、开关状态、上次结论显示。"""

    def setUp(self):
        import os
        from core.store import atomic_write_json
        self.root = make_project({})
        self.settings = load_settings(self.root)
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        self._atomic_write_json = atomic_write_json

    def _app(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        app = str(Path(__file__).resolve().parents[1] / "app.py")
        at = AppTest.from_file(app, default_timeout=60)
        # 看板启动时的后台恢复不在测试中真正执行（避免拉起真实进程）
        with mock.patch.object(autostart, "run", return_value={"enabled": False}):
            at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def _checkbox(self, at):
        return [c for c in at.checkbox if c.key == "autostart_check"]

    def test_panel_shows_disabled_by_default(self):
        at = self._app()
        boxes = self._checkbox(at)
        self.assertEqual(len(boxes), 1)
        self.assertFalse(boxes[0].value)

    def test_panel_reflects_saved_setting_and_last_result(self):
        autostart.set_enabled(self.settings, True)
        self._atomic_write_json(self.settings.paths.state_dir / "autostart_last.json", {
            "enabled": True, "ran_at": "2026-11-12T09:00:00",
            "receiver": {"action": "started", "port": 18765},
            "browser": {"action": "already_running"},
        })
        at = self._app()
        self.assertTrue(self._checkbox(at)[0].value)
        captions = " ".join(c.value for c in at.caption)
        self.assertIn("接收服务已自动启动", captions)
        self.assertIn("专用浏览器已在运行", captions)
