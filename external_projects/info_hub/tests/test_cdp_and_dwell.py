"""抓取附着到专用浏览器（CDP）、停留时长、报告按画像排序的测试。

CDP 流程用假的 Playwright 对象验证调用顺序与异常处理；真实浏览器需在本机手动验证。
"""
import contextlib
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from core import dedicated_browser as db
from core import evaluation, feedback, profile as prof
from core.browser_session import BrowserNotRunning, BrowserSession, RiskControlBlocked, cdp_available
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

NOW = datetime(2026, 11, 12, 12, 0).astimezone()


class FakePage:
    def __init__(self, url="about:blank", html="<html>正文</html>"):
        self.url = url
        self._html = html
        self.closed = False

    def goto(self, url, **kw):
        self.url = self._landing

    def wait_for_selector(self, *a, **kw):
        return None

    def content(self):
        return self._html

    def close(self):
        self.closed = True


class _Info:
    value = None


class FakeContext:
    def __init__(self, landing, expect_fails=False):
        self.pages = []
        self._landing = landing
        self._expect_fails = expect_fails

    def new_page(self):
        page = FakePage()
        page._landing = self._landing
        self.pages.append(page)
        return page

    @contextlib.contextmanager
    def expect_page(self, timeout=None):
        """模拟 Playwright：等待块内触发的新页面事件；失败时抛出超时。"""
        if self._expect_fails:
            raise TimeoutError("no page")
        info = _Info()
        yield info
        info.value = self.new_page()


class FakeCDP:
    def __init__(self, ctx, landing):
        self.ctx, self.landing = ctx, landing

    def send(self, method, params=None):
        return {}  # 新页面由 expect_page 的事件模拟产生


class FakeBrowser:
    def __init__(self, ctx, landing):
        self.contexts = [ctx]
        self._cdp = FakeCDP(ctx, landing)
        self.closed = False

    def new_browser_cdp_session(self):
        return self._cdp

    def close(self):
        self.closed = True


def _fake_playwright(landing, html="<html>正文</html>", expect_fails=False):
    ctx = FakeContext(landing, expect_fails=expect_fails)
    browser = FakeBrowser(ctx, landing)
    chromium = mock.Mock()
    chromium.connect_over_cdp.return_value = browser
    pw = mock.Mock()
    pw.chromium = chromium
    sync_api = mock.Mock()
    sync_api.sync_playwright.return_value.start.return_value = pw
    return sync_api, pw, browser, ctx


class CdpFetchTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.session = BrowserSession(self.root / "p", self.root / "login.json", mode="cdp", cdp_port=9999)

    def test_not_running_raises_clear_error(self):
        with mock.patch("core.browser_session.cdp_available", return_value=False):
            with self.assertRaises(BrowserNotRunning) as cm:
                self.session.fetch_html("https://x/")
        self.assertIn("服务管理", str(cm.exception))

    def test_background_tab_fetched_closed_and_connection_dropped(self):
        sync_api, pw, browser, ctx = _fake_playwright("https://x/explore/1")
        with mock.patch("core.browser_session.cdp_available", return_value=True), \
                mock.patch.dict(sys.modules, {"playwright": mock.Mock(), "playwright.sync_api": sync_api}):
            html = self.session.fetch_html("https://x/explore/1", site="xhs", expired_pattern="/login")
        self.assertEqual(html, "<html>正文</html>")
        self.assertTrue(all(p.closed for p in ctx.pages))   # 抓取标签已关闭
        self.assertTrue(browser.closed)                     # 断开 CDP 连接
        pw.stop.assert_called_once()

    def test_risk_page_detected_in_cdp_mode(self):
        risk = ("https://www.xiaohongshu.com/website-login/error?error_code=300012"
                "&error_msg=risk&rejectUrl=edith/api/v1/login/qrcode")
        sync_api, _, _, _ = _fake_playwright(risk)
        with mock.patch("core.browser_session.cdp_available", return_value=True), \
                mock.patch.dict(sys.modules, {"playwright": mock.Mock(), "playwright.sync_api": sync_api}):
            with self.assertRaises(RiskControlBlocked):
                self.session.fetch_html("https://www.xiaohongshu.com/explore", site="xiaohongshu",
                                        expired_pattern="/login")

    def test_falls_back_to_normal_tab_when_background_unavailable(self):
        sync_api, pw, browser, ctx = _fake_playwright("https://x/explore/1", expect_fails=True)
        with mock.patch("core.browser_session.cdp_available", return_value=True), \
                mock.patch.dict(sys.modules, {"playwright": mock.Mock(), "playwright.sync_api": sync_api}):
            html = self.session.fetch_html("https://x/explore/1")
        self.assertEqual(html, "<html>正文</html>")
        self.assertTrue(all(p.closed for p in ctx.pages))

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            BrowserSession(self.root / "p", self.root / "login.json", mode="weird")

    def test_cdp_available_false_for_closed_port(self):
        self.assertFalse(cdp_available(1, timeout=0.2))


class DedicatedConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                             "adapter": "rss", "request": {"url": "https://n/feed"}}})
        self.settings = load_settings(self.root)

    def test_launch_command_includes_debug_port_bound_to_loopback(self):
        cmd = db.launch_command("/bin/chrome", Path("/tmp/p"), "about:blank", debug_port=9223)
        self.assertIn("--remote-debugging-port=9223", cmd)
        self.assertIn("--remote-debugging-address=127.0.0.1", cmd)
        self.assertNotIn("0.0.0.0", " ".join(cmd))

    def test_scrape_mode_default_and_switch(self):
        self.assertEqual(db.load_config(self.root)["scrape_mode"], "launch")
        api.browser_set_scrape_mode(self.settings, {"scrape_mode": "cdp"})
        self.assertEqual(db.load_config(self.root)["scrape_mode"], "cdp")
        session = db.make_browser_session(self.settings)
        self.assertEqual(session.mode, "cdp")
        with self.assertRaises(ApiError):
            api.browser_set_scrape_mode(self.settings, {"scrape_mode": "magic"})

    def test_overview_reports_debug_fields(self):
        out = api.browser_overview(self.settings)
        for key in ("debug_port", "debug_ready", "scrape_mode"):
            self.assertIn(key, out)


class DwellTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = self.root / "data"

    def test_record_dwell_validates_seconds(self):
        for bad in (0, -5, 99999, "x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evaluation.record_dwell(self.data, source_id="s", item_key="0" * 16, view="plain",
                                        seconds=bad, now=NOW)

    def test_average_dwell_per_view(self):
        k1, k2 = "a" * 16, "b" * 16
        evaluation.record_dwell(self.data, source_id="s", item_key=k1, view="ranked", seconds=60, now=NOW)
        evaluation.record_dwell(self.data, source_id="s", item_key=k1, view="ranked", seconds=30, now=NOW)
        evaluation.record_dwell(self.data, source_id="s", item_key=k2, view="plain", seconds=10, now=NOW)
        m = evaluation.compute_metrics(self.data, [], now=NOW)
        self.assertEqual(m["views"]["ranked"]["avg_dwell_seconds"], 90.0)   # 同一条目累加后取平均
        self.assertEqual(m["views"]["plain"]["avg_dwell_seconds"], 10.0)

    def test_receiver_dwell_api_validation(self):
        settings = load_settings(self.root)
        api.record_dwell_api(settings, {"source_id": "s", "item_key": "c" * 16, "view": "plain", "seconds": 5})
        with self.assertRaises(ApiError):
            api.record_dwell_api(settings, {"source_id": "s", "item_key": "bad", "seconds": 5})
        with self.assertRaises(ApiError):
            api.record_dwell_api(settings, {"source_id": "s", "item_key": "c" * 16, "seconds": "many"})

    def test_dwell_strengthens_keywords_of_long_read_items(self):
        key = feedback.item_key("https://n/1", "量子计算突破")
        learned = prof.learn_from_events([], NOW, {key: 240}, {key: "量子计算突破"})
        self.assertGreater(learned["keywords"]["量子"]["score"], 0)
        short = prof.learn_from_events([], NOW, {key: 10}, {key: "量子计算突破"})
        self.assertEqual(short["keywords"], {})   # 不足阈值不计入

    def test_profile_refreshes_when_dwell_changes(self):
        feedback.record_feedback(self.data, {"action": "later", "source_id": "s", "url": "https://n/1",
                                             "title": "量子计算突破"})
        prof.ensure_fresh(self.data)
        evaluation.record_dwell(self.data, source_id="s", item_key=feedback.item_key("https://n/1", "量子计算突破"),
                                view="plain", seconds=300)
        fresh = prof.ensure_fresh(self.data)
        self.assertEqual(fresh["learned"]["computed_from_dwell"], 1)


class ReportRankingTests(unittest.TestCase):
    def test_ranker_disabled_by_config(self):
        from core import digest
        root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                        "adapter": "rss", "request": {"url": "https://n/feed"}}})
        (root / "config" / "settings.yaml").write_text(
            "paths:\n  sources_dir: sources\n  data_dir: data\n  reports_dir: reports\n"
            "  browser_profile_dir: browser/profile\nprofile:\n  rank_reports: false\n", encoding="utf-8")
        self.assertIsNone(digest._build_ranker(load_settings(root)))

    def test_ranker_orders_items_by_interest(self):
        from core import digest
        root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                        "adapter": "rss", "request": {"url": "https://n/feed"}}})
        settings = load_settings(root)
        for _ in range(2):
            feedback.record_feedback(settings.paths.data_dir, {"action": "interested", "source_id": "news_a",
                                                               "url": "https://n/1", "title": "量子计算突破"})
        ranker = digest._build_ranker(settings)
        self.assertIsNotNone(ranker)
        ordered = ranker("news_a", [{"title": "普通新闻", "url": "https://n/x"},
                                    {"title": "量子计算新进展", "url": "https://n/y"}])
        self.assertEqual(ordered[0]["title"], "量子计算新进展")


if __name__ == "__main__":
    unittest.main()
