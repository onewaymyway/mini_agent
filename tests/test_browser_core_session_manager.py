"""
tests/test_browser_core_session_manager.py

对应 next_doc/generative-capability-skill-plan.md 阶段十五。

覆盖 `.claude/skills/browser-core/impl/session_manager.py` 的一条关键行为：
`launch_headed` 模式在未显式传 `user_data_dir` 时，应该默认使用一个固定的、
跨调用持久化的用户数据目录（而不是每次都是全新 profile），这样手动登录过
一次之后，后续再用 `launch_headed` 打开能自动带着登录态；`launch_headless`
则不应该被这条默认值影响（保持原来的按端口区分的临时目录）。

不依赖真实 Chrome：通过 monkeypatch `session_manager` 内部引用的
`spawn_browser`/`is_debug_port_alive`/`wait_port_alive`/`list_tabs`/
`connect_tab`，只验证"传给 spawn_browser 的 user_data_dir 参数是否符合
预期"这一条逻辑，不涉及任何真实浏览器进程/网络调用。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


def _load_session_manager():
    """
    像 `real_tools.py::load_skill_local_tool_implementations` 一样，把
    browser-core/impl 目录塞进 sys.path 后按路径动态加载 session_manager，
    与 tools_impl.py 在生产环境里被加载的方式保持一致（这些文件本来就是
    独立于本仓库 Python 包之外、按约定路径动态加载的）。
    """
    repo_root = Path(__file__).resolve().parents[1]
    impl_dir = repo_root / ".claude" / "skills" / "browser-core" / "impl"
    impl_dir_str = str(impl_dir.resolve())
    if impl_dir_str not in sys.path:
        sys.path.insert(0, impl_dir_str)

    # 每次都用一个新的 module 名重新加载，避免测试之间共享
    # `session_manager._sessions` 这个模块级字典产生串扰。
    import uuid

    module_name = f"browser_core_session_manager_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, impl_dir / "session_manager.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class TestPersistentProfileDefault(unittest.TestCase):
    def setUp(self):
        self.session_manager = _load_session_manager()

    def _patch_common(self, is_alive_side_effect):
        """打好 is_debug_port_alive/wait_port_alive/list_tabs/connect_tab 的桩，
        让 get_or_create_session 走到 spawn_browser 这一步就能正常"成功"退出，
        不需要真实浏览器/网络。"""
        sm = self.session_manager
        patches = [
            mock.patch.object(sm, "is_debug_port_alive", side_effect=is_alive_side_effect),
            mock.patch.object(sm, "wait_port_alive", return_value=(True, None)),
            mock.patch.object(sm, "list_tabs", return_value=[]),
            mock.patch.object(sm, "new_tab", return_value={"webSocketDebuggerUrl": "ws://fake"}),
            mock.patch.object(sm, "connect_tab", return_value=mock.Mock(name="fake_session")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_launch_headed_defaults_to_persistent_profile_dir(self):
        sm = self.session_manager
        # 端口从未监听过 -> 走 spawn_browser 分支
        self._patch_common(is_alive_side_effect=lambda *a, **k: False)
        spawn_mock = mock.Mock(return_value=mock.Mock(name="fake_proc"))
        with mock.patch.object(sm, "spawn_browser", spawn_mock):
            sm.get_or_create_session({"mode": "launch_headed", "port": 39222})

        spawn_mock.assert_called_once()
        _, kwargs = spawn_mock.call_args
        self.assertEqual(kwargs["headless"], False)
        self.assertEqual(kwargs["user_data_dir"], str(sm.DEFAULT_PERSISTENT_PROFILE_DIR))

    def test_launch_headless_does_not_use_persistent_profile_dir(self):
        sm = self.session_manager
        self._patch_common(is_alive_side_effect=lambda *a, **k: False)
        spawn_mock = mock.Mock(return_value=mock.Mock(name="fake_proc"))
        with mock.patch.object(sm, "spawn_browser", spawn_mock):
            sm.get_or_create_session({"mode": "launch_headless", "port": 39223})

        spawn_mock.assert_called_once()
        _, kwargs = spawn_mock.call_args
        self.assertEqual(kwargs["headless"], True)
        self.assertIsNone(kwargs["user_data_dir"])  # 沿用 browser_launch.spawn_browser 自己的按端口临时目录默认值

    def test_launch_headed_respects_explicit_user_data_dir_override(self):
        sm = self.session_manager
        self._patch_common(is_alive_side_effect=lambda *a, **k: False)
        spawn_mock = mock.Mock(return_value=mock.Mock(name="fake_proc"))
        with mock.patch.object(sm, "spawn_browser", spawn_mock):
            sm.get_or_create_session(
                {"mode": "launch_headed", "port": 39224, "user_data_dir": "/tmp/custom-profile"}
            )

        _, kwargs = spawn_mock.call_args
        self.assertEqual(kwargs["user_data_dir"], "/tmp/custom-profile")

    def test_attach_mode_with_no_listening_port_raises_actionable_error(self):
        sm = self.session_manager
        self._patch_common(is_alive_side_effect=lambda *a, **k: False)
        with self.assertRaises(RuntimeError) as ctx:
            sm.get_or_create_session({"mode": "attach", "port": 39225})
        self.assertIn("remote-debugging-port", str(ctx.exception))

    def test_auto_mode_falls_back_to_headed_not_headless(self):
        """阶段十六：auto 模式 attach 不到时，应退化为有界面浏览器
        （launch_headed 语义：headless=False + 复用持久化 profile 默认值），
        而不是阶段十五及之前的 launch_headless，方便调试/登录场景。"""
        sm = self.session_manager
        self._patch_common(is_alive_side_effect=lambda *a, **k: False)
        spawn_mock = mock.Mock(return_value=mock.Mock(name="fake_proc"))
        with mock.patch.object(sm, "spawn_browser", spawn_mock):
            sm.get_or_create_session({"mode": "auto", "port": 39226})

        spawn_mock.assert_called_once()
        _, kwargs = spawn_mock.call_args
        self.assertEqual(kwargs["headless"], False)
        self.assertEqual(kwargs["user_data_dir"], str(sm.DEFAULT_PERSISTENT_PROFILE_DIR))


if __name__ == "__main__":
    unittest.main()
