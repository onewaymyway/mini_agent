"""
tests/test_web_app_bind.py

外部项目 Streamlit 的监听地址（web_app.bind）：
  - 解析：缺省 127.0.0.1；合法 IP；非法值报错；bind 不改变 is_remote（启停归属）。
  - 启动命令：--server.address 使用 bind（不再写死 127.0.0.1）。
  - 探测：监听具体网卡 IP 时按该 IP 探测（否则误判为未运行）。
  - 局域网地址：仅非回环监听时给出；0.0.0.0 时探测本机局域网 IP。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.external_projects import web_app as wa
from mini_agent.external_projects.manifest import ProjectManifestError, WebAppSpec, parse_manifest

BASE = "name: demo\nentrypoints:\n  a:\n    cmd: echo hi\n"


class TestBindParsing(unittest.TestCase):
    def test_default_is_loopback(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n").web_app
        self.assertIsNone(spec.bind)
        self.assertEqual(spec.bind_address, "127.0.0.1")
        self.assertFalse(spec.listens_beyond_loopback)

    def test_all_interfaces_and_specific_ip_accepted(self):
        for ip in ("0.0.0.0", "192.168.1.20"):
            spec = parse_manifest(BASE + f"web_app:\n  port: 8503\n  bind: {ip}\n").web_app
            self.assertEqual(spec.bind_address, ip)
            self.assertTrue(spec.listens_beyond_loopback)

    def test_invalid_bind_rejected(self):
        for bad in ("not-an-ip", "999.1.1.1", "0.0.0.0:8503"):
            with self.assertRaises(ProjectManifestError):
                parse_manifest(BASE + f"web_app:\n  port: 8503\n  bind: '{bad}'\n")

    def test_bind_does_not_make_it_remote(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n  bind: 0.0.0.0\n").web_app
        self.assertFalse(spec.is_remote)  # 仍由看板启停

    def test_host_still_means_remote(self):
        spec = parse_manifest(BASE + "web_app:\n  port: 8503\n  host: 10.0.0.5\n").web_app
        self.assertTrue(spec.is_remote)


class TestBindCommandAndProbe(unittest.TestCase):
    def _cmd(self, bind):
        spec = WebAppSpec(port=8503, bind=bind)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(wa, "_resolve_python", return_value="/usr/bin/python3"):
                return wa.build_command(Path(d), spec)

    def test_command_uses_bind_address(self):
        argv = self._cmd("0.0.0.0")
        self.assertEqual(argv[argv.index("--server.address") + 1], "0.0.0.0")

    def test_command_default_is_loopback(self):
        argv = self._cmd(None)
        self.assertEqual(argv[argv.index("--server.address") + 1], "127.0.0.1")

    def test_probe_uses_specific_bind_ip(self):
        self.assertEqual(wa.probe_host(WebAppSpec(port=1, bind="192.168.1.20")), "192.168.1.20")

    def test_probe_wildcard_uses_loopback(self):
        self.assertEqual(wa.probe_host(WebAppSpec(port=1, bind="0.0.0.0")), "127.0.0.1")

    def test_probe_remote_host_unchanged(self):
        self.assertEqual(wa.probe_host(WebAppSpec(port=1, host="10.0.0.5")), "10.0.0.5")


class TestLanUrl(unittest.TestCase):
    def test_loopback_has_no_lan_url(self):
        self.assertIsNone(wa.lan_url(WebAppSpec(port=8503)))

    def test_specific_ip_lan_url(self):
        self.assertEqual(wa.lan_url(WebAppSpec(port=8503, bind="192.168.1.20")),
                         "http://192.168.1.20:8503")

    def test_wildcard_detects_lan_ip(self):
        with mock.patch.object(wa, "detect_lan_ip", return_value="192.168.1.77"):
            self.assertEqual(wa.lan_url(WebAppSpec(port=8503, bind="0.0.0.0")),
                             "http://192.168.1.77:8503")

    def test_wildcard_without_detectable_lan_ip_returns_none(self):
        with mock.patch.object(wa, "detect_lan_ip", return_value=None):
            self.assertIsNone(wa.lan_url(WebAppSpec(port=8503, bind="0.0.0.0")))


if __name__ == "__main__":
    unittest.main()
