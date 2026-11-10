"""
tests/test_web_app_bind.py

外部项目 Streamlit 的监听地址（看板启动时选择，默认 0.0.0.0）：
  - manifest：bind 为启动时的"建议值"；未声明时建议 0.0.0.0；非法 IP 报错；bind 不改变 is_remote。
  - 启动命令：--server.address 使用启动时选择的地址。
  - 探测：固定探测 127.0.0.1；监听具体网卡 IP 时备选探测该 IP（只探测一次）。
  - 局域网地址：仅非回环监听时给出；0.0.0.0 时探测本机局域网 IP。
  - start/restart：非法地址不启动；restart 未指定时沿用上次的监听地址。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.external_projects import web_app as wa
from mini_agent.external_projects.manifest import ProjectManifestError, WebAppSpec, parse_manifest

BASE = "name: demo\nentrypoints:\n  a:\n    cmd: echo hi\n"


class TestManifestBind(unittest.TestCase):
    def test_default_suggestion_is_all_interfaces(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n").web_app
        self.assertIsNone(spec.bind)
        self.assertEqual(spec.bind_address, "0.0.0.0")

    def test_declared_bind_is_the_suggestion(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n  bind: 127.0.0.1\n").web_app
        self.assertEqual(spec.bind_address, "127.0.0.1")

    def test_invalid_bind_rejected(self):
        for bad in ("not-an-ip", "999.1.1.1", "0.0.0.0:8503"):
            with self.assertRaises(ProjectManifestError):
                parse_manifest(BASE + f"web_app:\n  port: 8503\n  bind: '{bad}'\n")

    def test_bind_does_not_make_it_remote(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n  bind: 0.0.0.0\n").web_app
        self.assertFalse(spec.is_remote)

    def test_host_still_means_remote(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n  host: 10.0.0.5\n").web_app
        self.assertTrue(spec.is_remote)


class TestNormalizeBind(unittest.TestCase):
    def test_default_when_empty(self):
        self.assertEqual(wa.normalize_bind(None), "0.0.0.0")
        self.assertEqual(wa.normalize_bind("  "), "0.0.0.0")

    def test_localhost_normalized(self):
        self.assertEqual(wa.normalize_bind("localhost"), "127.0.0.1")

    def test_valid_ip_kept(self):
        self.assertEqual(wa.normalize_bind("192.168.1.20"), "192.168.1.20")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            wa.normalize_bind("not-an-ip")


class TestBuildCommandAndProbe(unittest.TestCase):
    def _cmd(self, bind=None, spec=None):
        spec = spec or WebAppSpec(port=8503, entry="app.py")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(wa, "_resolve_python", return_value="/usr/bin/python3"):
                return wa.build_command(Path(d), spec, bind=bind)

    def _address(self, argv):
        return argv[argv.index("--server.address") + 1]

    def test_command_uses_chosen_bind(self):
        self.assertEqual(self._address(self._cmd("192.168.1.20")), "192.168.1.20")

    def test_command_default_is_all_interfaces(self):
        self.assertEqual(self._address(self._cmd(None)), "0.0.0.0")

    def test_command_falls_back_to_manifest_suggestion(self):
        spec = WebAppSpec(port=8503, entry="app.py", bind="127.0.0.1")
        self.assertEqual(self._address(self._cmd(None, spec)), "127.0.0.1")

    def test_probe_is_always_loopback_for_local(self):
        self.assertEqual(wa.probe_host(WebAppSpec(port=8503, bind="192.168.1.20")), "127.0.0.1")
        self.assertEqual(wa.probe_host(WebAppSpec(port=8503, bind="0.0.0.0")), "127.0.0.1")

    def test_probe_remote_host_unchanged(self):
        self.assertEqual(wa.probe_host(WebAppSpec(port=1, host="10.0.0.5")), "10.0.0.5")

    def test_find_listening_falls_back_to_recorded_specific_ip(self):
        spec = WebAppSpec(port=8503)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wa._write_pid_record(root, {"pid": 1, "port": 8503, "bind": "192.168.1.20"})
            with mock.patch.object(wa, "port_listening",
                                   side_effect=lambda h, p, timeout=0.3: h == "192.168.1.20"):
                self.assertEqual(wa._find_listening(spec, root), ("192.168.1.20", True))
            with mock.patch.object(wa, "port_listening", return_value=False):
                self.assertEqual(wa._find_listening(spec, root), ("127.0.0.1", False))

    def test_find_listening_probes_loopback_once(self):
        spec = WebAppSpec(port=8503)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(wa, "port_listening", return_value=True) as probe:
                wa._find_listening(spec, Path(d))
            self.assertEqual(probe.call_count, 1)


class TestLanUrl(unittest.TestCase):
    def test_loopback_has_no_lan_url(self):
        self.assertIsNone(wa.lan_url(WebAppSpec(port=8503), "127.0.0.1"))

    def test_specific_ip_lan_url(self):
        self.assertEqual(wa.lan_url(WebAppSpec(port=8503), "192.168.1.20"),
                         "http://192.168.1.20:8503")

    def test_wildcard_detects_lan_ip(self):
        with mock.patch.object(wa, "detect_lan_ip", return_value="192.168.1.77"):
            self.assertEqual(wa.lan_url(WebAppSpec(port=8503), "0.0.0.0"),
                             "http://192.168.1.77:8503")

    def test_wildcard_without_detectable_lan_ip_returns_none(self):
        with mock.patch.object(wa, "detect_lan_ip", return_value=None):
            self.assertIsNone(wa.lan_url(WebAppSpec(port=8503), "0.0.0.0"))


class TestStartRestartBind(unittest.TestCase):
    def test_start_rejects_invalid_bind_without_launching(self):
        spec = WebAppSpec(port=8503, entry="app.py")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(wa.subprocess, "Popen") as popen:
                ok, msg = wa.start("demo", Path(d), spec, bind="bad ip")
            self.assertFalse(ok)
            self.assertIn("监听地址不合法", msg)
            popen.assert_not_called()

    def test_start_records_chosen_bind(self):
        spec = WebAppSpec(port=8503, entry="app.py")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app.py").write_text("x", encoding="utf-8")
            fake = mock.Mock(pid=4242)
            with mock.patch.object(wa.subprocess, "Popen", return_value=fake) as popen, \
                 mock.patch.object(wa, "_resolve_python", return_value="/usr/bin/python3"), \
                 mock.patch.object(wa, "get_status", return_value=mock.Mock(state=wa.STATE_STOPPED)):
                ok, _ = wa.start("demo", root, spec, bind="0.0.0.0")
            self.assertTrue(ok)
            argv = popen.call_args[0][0]
            self.assertEqual(argv[argv.index("--server.address") + 1], "0.0.0.0")
            self.assertEqual(wa.read_pid_record(root)["bind"], "0.0.0.0")

    def test_restart_without_bind_reuses_previous(self):
        spec = WebAppSpec(port=8503, entry="app.py")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wa._write_pid_record(root, {"pid": 1, "port": 8503, "bind": "192.168.1.20"})
            with mock.patch.object(wa, "get_status", return_value=mock.Mock(state=wa.STATE_STOPPED)), \
                 mock.patch.object(wa, "_find_listening", return_value=("127.0.0.1", False)), \
                 mock.patch.object(wa, "start", return_value=(True, "ok")) as start:
                wa.restart("demo", root, spec)
            self.assertEqual(start.call_args.kwargs["bind"], "192.168.1.20")


if __name__ == "__main__":
    unittest.main()
