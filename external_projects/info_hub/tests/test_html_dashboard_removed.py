"""回归：旧 HTML 看板已移除，Streamlit 是唯一看板；后台调度循环迁至 core.tick。"""
import threading
import unittest
from pathlib import Path

import yaml

import cli
from core import tick as tick_mod
from core.settings import load_settings
from tests.helpers import make_project

ROOT = Path(__file__).resolve().parents[1]


class HtmlDashboardRemovedTests(unittest.TestCase):
    def test_legacy_files_are_gone(self):
        self.assertFalse((ROOT / "ui").exists())
        self.assertFalse((ROOT / "server" / "app.py").exists())

    def test_cli_has_no_serve_command(self):
        self.assertFalse(hasattr(cli, "cmd_serve"))
        with self.assertRaises(SystemExit):
            cli.main(["serve"], root=make_project({}))

    def test_project_yaml_has_only_streamlit_dashboard(self):
        conf = yaml.safe_load((ROOT / "project.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("serve", conf["entrypoints"])
        self.assertNotIn("dashboard", conf["entrypoints"])
        self.assertEqual(conf["web_app"]["entry"], "app.py")  # 看板只通过 web_app 声明（Streamlit）

    def test_settings_no_longer_have_html_server_block(self):
        settings = load_settings(make_project({}))
        self.assertNotIn("server", settings.raw)
        self.assertFalse(hasattr(settings, "server_port"))

    def test_start_tick_loop_runs_tick_and_stops(self):
        settings = load_settings(make_project({}))
        calls, ran = [], threading.Event()
        orig = tick_mod.tick
        tick_mod.tick = lambda s, *a, **k: (calls.append(1), ran.set())
        stop = threading.Event()
        try:
            th = tick_mod.start_tick_loop(settings, stop, 3600)
            self.assertTrue(ran.wait(5))
        finally:
            stop.set()
            tick_mod.tick = orig
        th.join(5)
        self.assertFalse(th.is_alive())
        self.assertEqual(len(calls), 1)

    def test_start_tick_loop_survives_tick_failure(self):
        settings = load_settings(make_project({}))
        n, twice = [], threading.Event()

        def boom(*a, **k):
            n.append(1)
            if len(n) >= 2:
                twice.set()
            raise RuntimeError("x")
        orig = tick_mod.tick
        tick_mod.tick = boom
        stop = threading.Event()
        try:
            tick_mod.start_tick_loop(settings, stop, 0.05)
            self.assertTrue(twice.wait(5))
        finally:
            stop.set()
            tick_mod.tick = orig


if __name__ == "__main__":
    unittest.main()
