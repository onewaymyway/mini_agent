"""接收服务管理（端口、启停、状态）与专用浏览器状态/停止，以及对应 API 的输入校验。"""
import os
import socket
import unittest
from pathlib import Path
from unittest import mock

from core import dedicated_browser as db
from core import services
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PortTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_default_port_is_18765(self):
        self.assertEqual(services.effective_port(self.settings), 18765)

    def test_set_port_persists_and_overrides(self):
        services.set_port(self.settings, 19001)
        self.assertEqual(services.effective_port(self.settings), 19001)
        self.assertEqual(db.load_config(self.root)["receiver_port"], 19001)

    def test_invalid_ports_rejected(self):
        for bad in ("abc", 80, 70000, None):
            with self.subTest(bad=bad), self.assertRaises(services.ServiceError):
                services.set_port(self.settings, bad)

    def test_port_free_detects_occupied(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            self.assertFalse(services.port_free(s.getsockname()[1]))
        self.assertTrue(services.port_free(_free_port()))

    def test_status_when_stopped(self):
        services.set_port(self.settings, _free_port())
        st = services.status(self.settings)
        self.assertEqual(st["mode"], "stopped")
        self.assertFalse(st["running"])
        self.assertTrue(st["port_free"])

    def test_start_refuses_occupied_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            with self.assertRaises(services.ServiceError) as cm:
                services.start(self.settings, s.getsockname()[1])
            self.assertIn("已被其他程序占用", str(cm.exception))

    def test_stop_without_running_service(self):
        with self.assertRaises(services.ServiceError):
            services.stop(self.settings)


class ReceiverLifecycleTests(unittest.TestCase):
    """真实启动一个接收服务子进程，验证看板可以启动、查询、停止它。"""

    def test_start_status_stop(self):
        root = make_project({})
        settings = load_settings(root)
        port = _free_port()
        try:
            st = services.start(settings, port)
            self.assertEqual(st["mode"], "managed")
            self.assertTrue(st["running"])
            self.assertTrue(st["pid"])
            self.assertTrue((root / "data" / "state" / "receiver_token.txt").exists())
            with self.assertRaises(services.ServiceError):  # 重复启动
                services.start(settings, port)
            after = services.stop(settings)
            self.assertEqual(after["mode"], "stopped")
        finally:
            try:
                services.stop(settings)
            except services.ServiceError:
                pass


class BrowserStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.cfg = {"executable": "", "profile_dir": "browser/dedicated_profile"}
        self.profile = db.profile_path(self.root, self.cfg["profile_dir"])
        self.profile.mkdir(parents=True)

    def test_no_process_is_stopped(self):
        with mock.patch.object(db, "find_executable", return_value=None):
            st = db.browser_status(self.root, self.cfg)
        self.assertIn(st["state"], ("stopped", "unknown"))
        self.assertTrue(st["profile_exists"])

    def test_singleton_lock_detects_live_process(self):
        if os.name != "posix":
            self.skipTest("SingletonLock 仅用于 POSIX")
        os.symlink(f"testhost-{os.getpid()}", self.profile / "SingletonLock")
        with mock.patch.dict("sys.modules", {"psutil": None}):
            self.assertEqual(db.browser_pids(self.profile), [os.getpid()])

    def test_singleton_lock_with_dead_process_is_stopped(self):
        if os.name != "posix":
            self.skipTest("SingletonLock 仅用于 POSIX")
        os.symlink("testhost-999999", self.profile / "SingletonLock")
        with mock.patch.dict("sys.modules", {"psutil": None}):
            self.assertEqual(db.browser_pids(self.profile), [])

    def test_stop_reports_unknown_when_undetectable(self):
        with mock.patch.object(db, "browser_pids", return_value=None):
            with self.assertRaises(RuntimeError):
                db.stop_browser(self.root, self.cfg)


class ApiServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_receiver_start_rejects_non_numeric_port(self):
        with self.assertRaises(ApiError) as cm:
            api.receiver_start(self.settings, {"port": "abc"})
        self.assertEqual(cm.exception.status, 400)

    def test_receiver_set_port_validation(self):
        with self.assertRaises(ApiError) as cm:
            api.receiver_set_port(self.settings, {"port": 80})
        self.assertEqual(cm.exception.status, 400)
        out = api.receiver_set_port(self.settings, {"port": 19002})
        self.assertEqual(out["port"], 19002)

    def test_receiver_stop_when_not_running_is_409(self):
        with self.assertRaises(ApiError) as cm:
            api.receiver_stop(self.settings)
        self.assertEqual(cm.exception.status, 409)

    def test_browser_set_executable_rejects_missing_path(self):
        with self.assertRaises(ApiError) as cm:
            api.browser_set_executable(self.settings, {"executable": "/no/such/chrome"})
        self.assertEqual(cm.exception.status, 400)

    def test_browser_start_refused_when_running(self):
        with mock.patch.object(db, "browser_status", return_value={"state": "running"}):
            with self.assertRaises(ApiError) as cm:
                api.browser_start(self.settings)
        self.assertEqual(cm.exception.status, 409)

    def test_browser_overview_shape(self):
        out = api.browser_overview(self.settings)
        for key in ("state", "pids", "executable", "profile"):
            self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()
