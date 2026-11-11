import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta

import yaml

import cli
from adapters.base import FetchContext
from core import health
from core.digest import build_digest, render_markdown
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from core.speech_text import digest_to_segments
from core.tick import tick
from server import api
from tests.helpers import make_project

NOW = datetime(2026, 11, 11, 12, 0).astimezone()
SRC = {"id": "flaky", "name": "不稳定源", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://x/feed"}, "schedule": {"every": "10m"}}
GOOD = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
        b'<item><title>ok</title><link>https://x/1</link></item></channel></rss>')


def make(enabled=False, **extra):
    root = make_project({"flaky": SRC})
    p = root / "config" / "settings.yaml"
    conf = yaml.safe_load(p.read_text(encoding="utf-8"))
    conf["health"] = {"enabled": enabled, "degrade_after": 3, "pause_after": 6,
                      "backoff_factor": 2, "max_interval": "1h", **extra}
    p.write_text(yaml.safe_dump(conf, allow_unicode=True), encoding="utf-8")
    return root, load_settings(root)


class Net:
    """可切换成功/失败的假网络。"""
    def __init__(self):
        self.fail = True
        self.calls = 0

    def ctx(self):
        def get(url, **kw):
            self.calls += 1
            if self.fail:
                raise OSError("连接失败")
            return GOOD
        return FetchContext(http_get=get)


def runs_log(settings):
    return "".join(p.read_text(encoding="utf-8") for p in settings.paths.runs_dir.glob("*.log.md"))


class TrackingAlwaysOnTests(unittest.TestCase):
    def test_default_only_flags_never_throttles_or_pauses(self):
        root, s = make(enabled=False)
        net = Net()
        spec = load_sources(s).specs["flaky"]
        for _ in range(20):
            run_source(spec, s, net.ctx())
        e = health.read_all(s)["flaky"]
        cfg = health.load_health_config(s)
        self.assertEqual(e["failures"], 20)
        self.assertFalse(e["paused"])
        self.assertEqual(health.status_of(e, cfg), "degraded")          # 标红
        self.assertEqual(health.effective_interval("10m", e, cfg), timedelta(minutes=10))  # 频率不变
        self.assertIn("告警", runs_log(s))
        self.assertIn("仅标红提示", runs_log(s))
        # tick 仍照常运行
        net.calls = 0
        tick(s, now=NOW, ctx=net.ctx())
        self.assertEqual(net.calls, 1)

    def test_success_resets_and_logs_recovery(self):
        root, s = make()
        net = Net()
        spec = load_sources(s).specs["flaky"]
        for _ in range(3):
            run_source(spec, s, net.ctx())
        net.fail = False
        run_source(spec, s, net.ctx())
        e = health.read_all(s)["flaky"]
        self.assertEqual(e["failures"], 0)
        self.assertIn("恢复", runs_log(s))

    def test_health_failure_does_not_break_run(self):
        root, s = make()
        net = Net(); net.fail = False
        orig = health.record_result
        health.record_result = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk"))
        try:
            r = run_source(load_sources(s).specs["flaky"], s, net.ctx())
        finally:
            health.record_result = orig
        self.assertTrue(r.ok)


class ThrottleAndPauseTests(unittest.TestCase):
    def test_effective_interval_escalates_and_is_capped(self):
        cfg = health.HealthConfig(enabled=True, degrade_after=3, pause_after=99, backoff_factor=2, max_interval="1h")
        iv = lambda n: health.effective_interval("10m", {"failures": n}, cfg)
        self.assertEqual(iv(2), timedelta(minutes=10))
        self.assertEqual(iv(3), timedelta(minutes=20))
        self.assertEqual(iv(4), timedelta(minutes=40))
        self.assertEqual(iv(5), timedelta(hours=1))
        self.assertEqual(iv(50), timedelta(hours=1))
        # 源自身间隔已大于上限时，不会被“降频”缩短
        self.assertEqual(health.effective_interval("1d", {"failures": 9}, cfg), timedelta(days=1))

    def test_acceptance_consecutive_failures_throttle_then_pause_then_report(self):
        root, s = make(enabled=True)
        net = Net()
        now = NOW
        ran = []
        for _ in range(40):
            before = net.calls
            rep = tick(s, now=now, ctx=net.ctx())
            ran.append((now, net.calls - before, list(rep.paused_sources)))
            now += timedelta(minutes=10)
        e = health.read_all(s)["flaky"]
        self.assertTrue(e["paused"])
        self.assertEqual(e["failures"], 6)                         # 到 pause_after 即停，不再继续尝试
        self.assertEqual(net.calls, 6)
        # 间隔确实在拉长：第 4、5 次失败之后的相邻运行间隔 >10 分钟
        times = [t for t, c, _ in ran if c]
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
        self.assertEqual(gaps[:2], [10, 10])        # 前 3 次失败前保持原间隔
        self.assertGreater(gaps[3], gaps[2])
        self.assertIn("flaky", ran[-1][2])
        log = runs_log(s)
        self.assertIn("降频", log); self.assertIn("暂停", log)

        d = build_digest(s, {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}, now)
        self.assertEqual(d["health"][0]["kind"], "paused")
        md = render_markdown(d, "t")
        self.assertIn("## 异常", md)
        self.assertIn("`flaky` 已自动暂停，需手动恢复（连续失败 6 次）", md)
        self.assertIn("已暂停", " ".join(x["text"] for x in digest_to_segments(d)))

    def test_resume_then_runs_again_and_manual_success_also_recovers(self):
        root, s = make(enabled=True)
        net = Net()
        spec = load_sources(s).specs["flaky"]
        for _ in range(6):
            run_source(spec, s, net.ctx())
        self.assertTrue(health.is_paused(s, "flaky"))
        net.calls = 0
        tick(s, now=NOW, ctx=net.ctx())
        self.assertEqual(net.calls, 0)                              # 暂停期间 tick 不运行
        self.assertTrue(health.resume(s, "flaky"))
        self.assertFalse(health.is_paused(s, "flaky"))
        self.assertFalse(health.resume(s, "flaky"))                 # 没有需要恢复的状态
        tick(s, now=NOW + timedelta(hours=2), ctx=net.ctx())
        self.assertEqual(net.calls, 1)
        # 另一条路径：再次暂停后，手动抓取成功即恢复
        for _ in range(6):
            run_source(spec, s, net.ctx())
        self.assertTrue(health.is_paused(s, "flaky"))
        net.fail = False
        run_source(spec, s, net.ctx())
        self.assertFalse(health.is_paused(s, "flaky"))

    def test_bad_health_config_falls_back_to_disabled(self):
        root, s = make(enabled=True, max_interval="soon")
        self.assertFalse(health.load_health_config(s).enabled)


class SurfaceTests(unittest.TestCase):
    def test_config_error_source_appears_in_report_problems(self):
        root = make_project({"bad": {"id": "bad", "name": "坏源", "category": "nope", "kind": "feed",
                                     "adapter": "rss", "request": {"url": "https://x"}}})
        s = load_settings(root)
        d = build_digest(s, {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}, NOW)
        self.assertEqual(d["health"][0]["kind"], "config_error")
        self.assertIn("配置错误", render_markdown(d, "t"))

    def test_report_without_problems_has_no_exception_section(self):
        root, s = make()
        d = build_digest(s, {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}, NOW)
        self.assertEqual(d["health"], [])
        self.assertNotIn("## 异常", render_markdown(d, "t"))

    def test_api_and_run_list_include_health(self):
        root, s = make(enabled=True)
        net = Net()
        spec = load_sources(s).specs["flaky"]
        for _ in range(6):
            run_source(spec, s, net.ctx())
        row = api.list_sources(s)["sources"][0]
        self.assertEqual((row["health"], row["failures"]), ("paused", 6))
        self.assertIn("连接失败", row["last_error"])
        kinds = [r["status"] for r in api.list_runs(s)["runs"]]
        self.assertIn("暂停", kinds); self.assertIn("降频", kinds)
        self.assertTrue(api.resume_source(s, "flaky")["resumed"])
        self.assertEqual(api.list_sources(s)["sources"][0]["health"], "ok")
        with self.assertRaises(api.ApiError):
            api.resume_source(s, "nope")

    def test_cli_health_and_resume(self):
        root, s = make(enabled=True)
        net = Net()
        spec = load_sources(s).specs["flaky"]
        for _ in range(6):
            run_source(spec, s, net.ctx())
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["health"], root=root), 0)
        out = buf.getvalue()
        self.assertIn("已暂停", out); self.assertIn("flaky", out)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["resume", "flaky"], root=root), 0)
            self.assertEqual(cli.main(["resume", "flaky"], root=root), 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["health"], root=root)
        self.assertIn("状态正常", buf.getvalue())

    def test_streamlit_shows_sick_source_and_resume_button(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        root, s = make(enabled=True)
        net = Net()
        spec = load_sources(s).specs["flaky"]
        for _ in range(6):
            run_source(spec, s, net.ctx())
        os.environ["INFO_HUB_ROOT"] = str(root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        try:
            at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60)
            at.run()
            self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
            self.assertTrue(any("已自动暂停" in e.value for e in at.error))
            btn = [b for b in at.button if b.key == "resume_flaky"][0]
            btn.click(); at.run()
            self.assertFalse(health.is_paused(s, "flaky"))
        finally:
            os.environ.pop("INFO_HUB_ROOT", None)


if __name__ == "__main__":
    unittest.main()
