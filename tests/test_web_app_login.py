"""
tests/test_web_app_login.py —— 外部项目 Streamlit 的登录门禁接入（next_doc/shared_web_login_plan.md）。

  1. manifest：web_app.require_login 解析（默认 false，非布尔报错）。
  2. 包装入口：生成的 .run/auth_entry.py 先门禁、后 runpy 执行项目入口。
  3. 启动命令：启用登录时入口换成包装入口；自定义 command 无法启用登录（ValueError）。
  4. 子进程环境：启用登录时注入 PYTHONPATH 与账户目录。
  5. start：账户为空 / 自定义 command 时拒绝且不启动进程；启用时透传 env；restart 沿用登录设置。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.external_projects import web_app as wa
from mini_agent.external_projects.manifest import ProjectManifestError, WebAppSpec, parse_manifest
from mini_agent.web_auth import gate
from mini_agent.web_auth.accounts import UserStore

BASE = "name: demo\nentrypoints:\n  a:\n    cmd: echo hi\n"


class ManifestLoginTests(unittest.TestCase):
    def test_default_is_no_login(self):
        self.assertFalse(parse_manifest(BASE + "web_app:\n  port: 8503\n").web_app.require_login)

    def test_true_parsed(self):
        self.assertTrue(parse_manifest(BASE + "web_app:\n  port: 8503\n  require_login: true\n").web_app.require_login)

    def test_non_bool_rejected(self):
        with self.assertRaises(ProjectManifestError):
            parse_manifest(BASE + "web_app:\n  port: 8503\n  require_login: 'yes'\n")


class _Root(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        (self.root / "app.py").write_text("print('hi')\n", encoding="utf-8")
        self.auth_dir = Path(self._tmp.name) / "web_auth"
        self._env = mock.patch.dict(os.environ, {gate.ENV_AUTH_DIR: str(self.auth_dir)})
        self._env.start()
        self._py = mock.patch.object(wa, "_resolve_python", return_value="/usr/bin/python3")
        self._py.start()

    def tearDown(self):
        self._py.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _accounts(self):
        UserStore(gate.auth_paths().users).add_user("alice", "secret123", is_admin=True)


class WrapperTests(_Root):
    def test_wrapper_gates_before_running_entry(self):
        path = wa.write_auth_entry(self.root, WebAppSpec(port=8503, entry="app.py", require_login=True))
        text = path.read_text(encoding="utf-8")
        self.assertEqual(path, self.root / ".run" / "auth_entry.py")
        gate_pos = text.index("require_login(APP_TITLE)")
        run_pos = text.index("runpy.run_path")
        self.assertLess(gate_pos, run_pos, "门禁必须在执行项目入口之前")
        self.assertIn("'app.py'", text)
        self.assertIn("set_page_config = lambda", text, "项目自己的 set_page_config 应被屏蔽")

    def test_wrapper_is_valid_python(self):
        path = wa.write_auth_entry(self.root, WebAppSpec(port=8503, entry="app.py", require_login=True))
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


class BuildCommandLoginTests(_Root):
    def test_login_uses_wrapper_entry(self):
        spec = WebAppSpec(port=8503, entry="app.py")
        argv = wa.build_command(self.root, spec, require_login=True)
        self.assertEqual(argv[argv.index("run") + 1], str(wa.auth_entry_path(self.root)))

    def test_no_login_keeps_original_entry(self):
        spec = WebAppSpec(port=8503, entry="app.py")
        argv = wa.build_command(self.root, spec, require_login=False)
        self.assertEqual(argv[argv.index("run") + 1], "app.py")

    def test_default_follows_manifest(self):
        spec = WebAppSpec(port=8503, entry="app.py", require_login=True)
        argv = wa.build_command(self.root, spec)
        self.assertIn(".run", argv[argv.index("run") + 1])

    def test_custom_command_cannot_enable_login(self):
        spec = WebAppSpec(port=8503, command="{python} -m streamlit run app.py --server.port {port}")
        with self.assertRaises(ValueError):
            wa.build_command(self.root, spec, require_login=True)


class ChildEnvTests(_Root):
    def test_login_env_injects_pythonpath_and_auth_dir(self):
        env = wa.child_env(self.root, True)
        self.assertEqual(env[gate.ENV_AUTH_DIR], str(self.auth_dir))
        self.assertIn(str(wa.mini_agent_src_dir()), env["PYTHONPATH"].split(os.pathsep))

    def test_no_login_env_does_not_add_auth_dir(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(gate.ENV_AUTH_DIR, None)
            self.assertNotIn(gate.ENV_AUTH_DIR, wa.child_env(self.root, False))


class StartLoginTests(_Root):
    def _start(self, spec, **kw):
        fake = mock.Mock(pid=5151)
        with mock.patch.object(wa.subprocess, "Popen", return_value=fake) as popen, \
             mock.patch.object(wa, "get_status", return_value=mock.Mock(state=wa.STATE_STOPPED)):
            ok, msg = wa.start("demo", self.root, spec, **kw)
        return ok, msg, popen

    def test_refused_when_no_accounts(self):
        ok, msg, popen = self._start(WebAppSpec(port=8503, entry="app.py"), require_login=True)
        self.assertFalse(ok)
        self.assertIn("尚未创建任何账户", msg)
        popen.assert_not_called()

    def test_refused_for_custom_command(self):
        self._accounts()
        spec = WebAppSpec(port=8503, command="{python} -m streamlit run app.py --server.port {port}")
        ok, msg, popen = self._start(spec, require_login=True)
        self.assertFalse(ok)
        self.assertIn("自定义了 command", msg)
        popen.assert_not_called()

    def test_start_with_login_passes_wrapper_and_env(self):
        self._accounts()
        ok, _, popen = self._start(WebAppSpec(port=8503, entry="app.py"), require_login=True)
        self.assertTrue(ok)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[argv.index("run") + 1], str(wa.auth_entry_path(self.root)))
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env[gate.ENV_AUTH_DIR], str(self.auth_dir))

    def test_record_keeps_login_flag(self):
        self._accounts()
        self._start(WebAppSpec(port=8503, entry="app.py"), require_login=True)
        self.assertTrue(wa.read_pid_record(self.root)["auth"])

    def test_restart_without_choice_keeps_previous_login(self):
        spec = WebAppSpec(port=8503, entry="app.py")
        wa._write_pid_record(self.root, {"pid": 1, "port": 8503, "bind": "0.0.0.0", "auth": True})
        with mock.patch.object(wa, "get_status", return_value=mock.Mock(state=wa.STATE_STOPPED)), \
             mock.patch.object(wa, "_find_listening", return_value=("127.0.0.1", False)), \
             mock.patch.object(wa, "start", return_value=(True, "ok")) as start:
            wa.restart("demo", self.root, spec)
        self.assertTrue(start.call_args.kwargs["require_login"])


if __name__ == "__main__":
    unittest.main()
