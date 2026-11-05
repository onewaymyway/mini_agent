"""
tests/test_web_app.py

外部项目 Streamlit 界面支持（next_doc/external_projects_web_app_plan.md）：
  - project.yaml 的 web_app 块解析与校验
  - 启动命令构造（缺省 / 自定义 command / 远程 host）
  - 同端口冲突检测
  - 状态探测（停止 / 端口被占用 / 外部 Streamlit）
  - 真实 start/stop（仅当本机装了 streamlit 时运行）
"""

from __future__ import annotations

import http.server
import importlib.util
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.external_projects import web_app as wa
from mini_agent.external_projects.manifest import ProjectManifestError, WebAppSpec, parse_manifest

BASE_YAML = "name: demo\nentrypoints:\n  a:\n    cmd: echo hi\n"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestWebAppManifest(unittest.TestCase):
    def test_minimal_block_gets_defaults(self):
        m = parse_manifest(BASE_YAML + "web_app:\n  port: 8503\n")
        self.assertEqual(m.web_app, WebAppSpec(port=8503))
        self.assertFalse(m.web_app.is_remote)

    def test_absent_block_is_none(self):
        self.assertIsNone(parse_manifest(BASE_YAML).web_app)

    def test_full_block(self):
        m = parse_manifest(BASE_YAML + (
            "web_app:\n  port: 8510\n  entry: ui/main.py\n  python: .venv/bin/python\n"
            "  command: '{python} -m streamlit run {entry} --server.port {port}'\n"
            "  health_path: /_stcore/health\n"
        ))
        self.assertEqual(m.web_app.entry, "ui/main.py")
        self.assertEqual(m.web_app.python, ".venv/bin/python")
        self.assertIn("{python}", m.web_app.command)

    def test_localhost_host_is_local(self):
        m = parse_manifest(BASE_YAML + "web_app:\n  port: 8503\n  host: localhost\n")
        self.assertFalse(m.web_app.is_remote)

    def test_remote_host(self):
        m = parse_manifest(BASE_YAML + "web_app:\n  port: 8503\n  host: 192.168.1.20\n")
        self.assertTrue(m.web_app.is_remote)

    def test_invalid_ports_rejected(self):
        for bad in ("80", '"8501"', "true", "70000"):
            with self.subTest(port=bad), self.assertRaises(ProjectManifestError):
                parse_manifest(BASE_YAML + f"web_app:\n  port: {bad}\n")

    def test_empty_string_field_rejected(self):
        with self.assertRaises(ProjectManifestError):
            parse_manifest(BASE_YAML + "web_app:\n  port: 8503\n  host: ''\n")


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_default_command_uses_current_interpreter(self):
        argv = wa.build_command(self.root, WebAppSpec(port=8503))
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:4], ["-m", "streamlit", "run"])
        self.assertIn("app.py", argv)
        self.assertEqual(argv[argv.index("--server.port") + 1], "8503")
        self.assertEqual(argv[argv.index("--server.address") + 1], "127.0.0.1")

    def test_relative_python_resolves_to_project_root(self):
        argv = wa.build_command(self.root, WebAppSpec(port=8503, python=".venv/bin/python"))
        self.assertEqual(argv[0], str((self.root / ".venv/bin/python").resolve()))

    def test_custom_command_placeholders(self):
        spec = WebAppSpec(port=8600, entry="ui.py",
                          command="{python} -m streamlit run {entry} --server.port {port}")
        argv = wa.build_command(self.root, spec)
        self.assertEqual(argv[1:3], ["-m", "streamlit"])
        self.assertEqual(argv[-3:], ["ui.py", "--server.port", "8600"])


class TestPortConflicts(unittest.TestCase):
    def _entry(self, name, port, host=None):
        return wa.WebAppEntry(name=name, root=Path("/x"), spec=WebAppSpec(port=port, host=host))

    def test_same_local_port_marked(self):
        entries = wa._mark_port_conflicts([self._entry("a", 8503), self._entry("b", 8503)])
        self.assertTrue(all(e.error and "冲突" in e.error for e in entries))

    def test_different_ports_ok(self):
        entries = wa._mark_port_conflicts([self._entry("a", 8502), self._entry("b", 8503)])
        self.assertTrue(all(e.error is None for e in entries))

    def test_remote_and_local_same_port_not_conflict(self):
        entries = wa._mark_port_conflicts([self._entry("a", 8503), self._entry("b", 8503, host="10.0.0.5")])
        self.assertTrue(all(e.error is None for e in entries))


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestStatusProbe(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_stopped_when_nothing_listens(self):
        spec = WebAppSpec(port=_free_port())
        st = wa.get_status("demo", self.root, spec)
        self.assertEqual(st.state, wa.STATE_STOPPED)
        self.assertTrue(st.can_start)

    def test_port_busy_by_non_streamlit(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        try:
            spec = WebAppSpec(port=sock.getsockname()[1])
            st = wa.get_status("demo", self.root, spec)
            self.assertEqual(st.state, wa.STATE_PORT_BUSY)
            self.assertFalse(st.can_start)
        finally:
            sock.close()

    def test_external_streamlit_is_running_but_not_stoppable(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            spec = WebAppSpec(port=server.server_address[1])
            st = wa.get_status("demo", self.root, spec)
            self.assertEqual(st.state, wa.STATE_RUNNING_EXTERNAL)
            self.assertTrue(st.is_up)
            self.assertFalse(st.can_stop)
            self.assertEqual(st.url, f"http://localhost:{spec.port}")
        finally:
            server.shutdown()
            server.server_close()

    def test_remote_spec_never_starts(self):
        spec = WebAppSpec(port=8503, host="10.0.0.5")
        ok, msg = wa.start("demo", self.root, spec)
        self.assertFalse(ok)
        self.assertIn("远程", msg)
        ok, _ = wa.stop("demo", self.root, spec)
        self.assertFalse(ok)

    def test_stale_pid_record_is_cleaned(self):
        spec = WebAppSpec(port=_free_port())
        wa._write_pid_record(self.root, {"name": "demo", "pid": 2_000_000_000, "port": spec.port})
        st = wa.get_status("demo", self.root, spec)
        self.assertEqual(st.state, wa.STATE_STOPPED)
        self.assertIsNone(wa.read_pid_record(self.root))

    def test_stop_refuses_external_instance(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            spec = WebAppSpec(port=server.server_address[1])
            ok, msg = wa.stop("demo", self.root, spec)
            self.assertFalse(ok)
            self.assertIn("不是由看板启动", msg)
        finally:
            server.shutdown()
            server.server_close()


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "需要本机安装 streamlit")
class TestRealStartStop(unittest.TestCase):
    def test_start_wait_stop_cycle(self):
        root = Path(tempfile.mkdtemp())
        (root / "app.py").write_text("import streamlit as st\nst.write('hi')\n", encoding="utf-8")
        spec = WebAppSpec(port=_free_port())
        try:
            ok, msg = wa.start("demo", root, spec)
            self.assertTrue(ok, msg)
            st = wa.wait_until_ready("demo", root, spec, timeout=60)
            self.assertEqual(st.state, wa.STATE_RUNNING, st.message)
            self.assertFalse(wa.start("demo", root, spec)[0])  # 已运行，拒绝重复启动

            ok, msg = wa.stop("demo", root, spec)
            self.assertTrue(ok, msg)
            self.assertEqual(wa.get_status("demo", root, spec).state, wa.STATE_STOPPED)
        finally:
            wa.stop("demo", root, spec)


if __name__ == "__main__":
    unittest.main()


# ── 停止 / 重启 ─────────────────────────────────────────────────────────

import os as _os  # noqa: E402


@unittest.skipUnless(_os.path.isdir("/proc"), "依赖 Linux /proc")
class TestFindListeningPid(unittest.TestCase):
    def test_finds_the_pid_listening_on_the_port(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        try:
            self.assertEqual(wa.find_listening_pid(sock.getsockname()[1]), _os.getpid())
        finally:
            sock.close()

    def test_returns_none_when_nothing_listens(self):
        self.assertIsNone(wa.find_listening_pid(_free_port()))


class TestStopExternalSafety(unittest.TestCase):
    def test_refuses_to_kill_a_non_streamlit_process(self):
        """端口上的进程不是 Streamlit（这里是测试进程本身）：必须拒绝，且不能结束它。"""
        server = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            spec = WebAppSpec(port=server.server_address[1])
            ok, msg = wa.stop_external("demo", Path(tempfile.mkdtemp()), spec)
            self.assertFalse(ok)
            self.assertIn("不是 Streamlit", msg)
            self.assertTrue(wa.port_listening("127.0.0.1", spec.port))   # 没有被结束
        finally:
            server.shutdown()
            server.server_close()

    def test_no_listener_means_nothing_to_stop(self):
        ok, msg = wa.stop_external("demo", Path(tempfile.mkdtemp()), WebAppSpec(port=_free_port()))
        self.assertFalse(ok)

    def test_remote_is_refused(self):
        self.assertFalse(wa.restart("demo", Path(tempfile.mkdtemp()), WebAppSpec(port=8503, host="10.0.0.5"))[0])


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "需要本机安装 streamlit")
class TestRestart(unittest.TestCase):
    def test_restart_from_stopped_starts_it(self):
        root = Path(tempfile.mkdtemp())
        (root / "app.py").write_text("import streamlit as st\nst.write('hi')\n", encoding="utf-8")
        spec = WebAppSpec(port=_free_port())
        try:
            ok, msg = wa.restart("demo", root, spec)
            self.assertTrue(ok, msg)
            self.assertEqual(wa.get_status("demo", root, spec).state, wa.STATE_RUNNING)
        finally:
            wa.stop("demo", root, spec)

    def test_restart_running_instance_changes_pid(self):
        root = Path(tempfile.mkdtemp())
        (root / "app.py").write_text("import streamlit as st\nst.write('hi')\n", encoding="utf-8")
        spec = WebAppSpec(port=_free_port())
        try:
            self.assertTrue(wa.start("demo", root, spec)[0])
            self.assertEqual(wa.wait_until_ready("demo", root, spec, timeout=60).state, wa.STATE_RUNNING)
            old_pid = wa.read_pid_record(root)["pid"]
            ok, msg = wa.restart("demo", root, spec)
            self.assertTrue(ok, msg)
            new = wa.get_status("demo", root, spec)
            self.assertEqual(new.state, wa.STATE_RUNNING, new.message)
            self.assertNotEqual(wa.read_pid_record(root)["pid"], old_pid)
        finally:
            wa.stop("demo", root, spec)
