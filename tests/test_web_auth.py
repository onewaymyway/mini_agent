"""
tests/test_web_auth.py —— 通用 Streamlit 登录（mini_agent.web_auth，next_doc/shared_web_login_plan.md）。

  1. 共享账户目录：环境变量优先，默认 ~/.agent/web_auth。
  2. 看板旧账户迁移：只在共享文件不存在时复制，旧文件保留。
  3. 门禁（AppTest）：未登录不渲染内容；错误密码；正确密码放行并保持；会话撤销后重新要求登录；
     账户目录为空时给出提示且不放行。
  4. 账户命令行：list / set-admin / remove 等。
  5. 看板 auth.py 转发模块仍可导入（保持旧导入路径）。
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mini_agent.web_auth import gate
from mini_agent.web_auth.accounts import SessionStore, UserStore
from mini_agent.web_auth.__main__ import main as cli_main


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.auth_dir = self.tmp / "web_auth"
        self._env = mock.patch.dict(os.environ, {gate.ENV_AUTH_DIR: str(self.auth_dir)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class SharedDirTests(_Tmp):
    def test_env_overrides_default(self):
        self.assertEqual(gate.shared_auth_dir(), self.auth_dir)

    def test_default_is_home_agent_web_auth(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(gate.ENV_AUTH_DIR, None)
            with mock.patch.object(Path, "home", return_value=self.tmp / "home"):
                self.assertEqual(gate.shared_auth_dir(), self.tmp / "home" / ".agent" / "web_auth")


class MigrationTests(_Tmp):
    def test_legacy_kanban_users_copied_once(self):
        root = self.tmp / "kanban_project"
        legacy = root / ".agent" / "kanban_users.json"
        legacy.parent.mkdir(parents=True)
        UserStore(legacy).add_user("legacy_admin", "secret123", is_admin=True)

        self.assertTrue(gate.migrate_legacy_kanban_users(root))
        self.assertTrue(UserStore(gate.auth_paths().users).verify("legacy_admin", "secret123"))
        self.assertTrue(legacy.exists(), "旧文件应保留")

    def test_existing_shared_file_is_not_overwritten(self):
        root = self.tmp / "kanban_project"
        legacy = root / ".agent" / "kanban_users.json"
        legacy.parent.mkdir(parents=True)
        UserStore(legacy).add_user("old", "secret123")
        UserStore(gate.auth_paths().users).add_user("shared", "secret123")

        self.assertFalse(gate.migrate_legacy_kanban_users(root))
        self.assertEqual(UserStore(gate.auth_paths().users).list_users(), ["shared"])


class AccountsEmptyTests(_Tmp):
    def test_empty_until_first_user(self):
        self.assertTrue(gate.accounts_empty())
        UserStore(gate.auth_paths().users).add_user("u", "secret123")
        self.assertFalse(gate.accounts_empty())


class GateAppTest(_Tmp):
    """真实 Streamlit AppTest 驱动门禁：这是验证"未登录看不到内容"的核心测试。"""

    SCRIPT = (
        "import streamlit as st\n"
        "from mini_agent.web_auth import require_login\n"
        "require_login('测试应用')\n"
        "st.write('SECRET-CONTENT')\n"
    )

    def _app(self):
        from streamlit.testing.v1 import AppTest
        script = self.tmp / "gated_app.py"
        script.write_text(self.SCRIPT, encoding="utf-8")
        return AppTest.from_file(str(script), default_timeout=30)

    def _visible(self, at) -> bool:
        return any("SECRET-CONTENT" in m.value for m in at.markdown)

    def test_unauthenticated_sees_only_login_page(self):
        UserStore(gate.auth_paths().users).add_user("alice", "secret123")
        at = self._app().run()
        self.assertEqual(at.exception, [])
        self.assertFalse(self._visible(at))
        self.assertEqual([b.label for b in at.button], ["登录"])

    def test_wrong_password_is_rejected(self):
        UserStore(gate.auth_paths().users).add_user("alice", "secret123")
        at = self._app().run()
        at.text_input[0].input("alice")
        at.text_input[1].input("wrong-password")
        at.button[0].click().run()
        self.assertTrue(at.error)
        self.assertFalse(self._visible(at))

    def test_correct_login_releases_content_and_persists(self):
        UserStore(gate.auth_paths().users).add_user("alice", "secret123")
        at = self._app().run()
        at.text_input[0].input("alice")
        at.text_input[1].input("secret123")
        at.button[0].click().run()
        self.assertEqual(at.exception, [])
        self.assertTrue(self._visible(at))
        self.assertEqual(len(at.text_input), 0, "登录成功后登录表单应被清掉")
        at.run()
        self.assertTrue(self._visible(at), "刷新/重跑后应保持登录")

    def test_revoked_session_requires_login_again(self):
        UserStore(gate.auth_paths().users).add_user("alice", "secret123")
        at = self._app().run()
        at.text_input[0].input("alice")
        at.text_input[1].input("secret123")
        at.button[0].click().run()
        self.assertTrue(self._visible(at))

        sid = at.session_state["session_id"]
        SessionStore(gate.auth_paths().sessions).revoke(sid)
        at.run()
        self.assertFalse(self._visible(at))

    def test_empty_account_store_blocks_and_explains(self):
        at = self._app().run()
        self.assertFalse(self._visible(at))
        self.assertIn("python -m mini_agent.web_auth add", "\n".join(w.value for w in at.warning))


class CliTests(_Tmp):
    def _run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main(["--auth-dir", str(self.auth_dir), *argv])
        return code, buf.getvalue()

    def test_add_list_set_admin_remove(self):
        with mock.patch("mini_agent.web_auth.__main__.getpass.getpass", side_effect=["secret123", "secret123"]):
            code, _ = self._run("add", "bob", "--admin")
        self.assertEqual(code, 0)
        _, out = self._run("list")
        self.assertIn("bob [admin]", out)

        code, _ = self._run("unset-admin", "bob")
        self.assertEqual(code, 1, "不能取消最后一个管理员")

        code, _ = self._run("remove", "bob")
        self.assertEqual(code, 1, "不能删除最后一个管理员")

        UserStore(gate.auth_paths().users).add_user("carol", "secret123")
        code, _ = self._run("remove", "carol")
        self.assertEqual(code, 0)

    def test_revoke_all_reports_count(self):
        UserStore(gate.auth_paths().users).add_user("dave", "secret123")
        SessionStore(gate.auth_paths().sessions).create("dave", "")
        _, out = self._run("revoke-all", "--username", "dave")
        self.assertIn("已撤销 1 个会话", out)


class KanbanForwarderTests(unittest.TestCase):
    def test_kanban_auth_module_still_importable(self):
        apps_dir = Path(__file__).resolve().parents[1] / "apps" / "mini_agent_kanban"
        sys.path.insert(0, str(apps_dir))
        try:
            import importlib
            sys.modules.pop("auth", None)
            auth = importlib.import_module("auth")
            self.assertTrue(hasattr(auth, "UserStore"))
            self.assertTrue(hasattr(auth, "SessionStore"))
            self.assertTrue(hasattr(auth, "make_token"))
        finally:
            sys.path.remove(str(apps_dir))
            sys.modules.pop("auth", None)


if __name__ == "__main__":
    unittest.main()
