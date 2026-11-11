import http.client
import json
import threading
import unittest
from datetime import datetime
from pathlib import Path
from urllib import request as urlreq
from urllib.error import HTTPError

from core import store
from core.digest import write_report
from core.settings import load_settings
from server import api
from server.app import make_server
from tests.helpers import make_project

TZ = datetime.now().astimezone().tzinfo
RSS = ('<?xml version="1.0"?><rss version="2.0"><channel>'
       '<item><title>新闻一</title><link>https://n/1</link><description>摘要</description></item>'
       '</channel></rss>').encode("utf-8")
SRC = {"id": "news_a", "name": "新闻A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}


def build_project():
    root = make_project({"news_a": SRC})
    import yaml
    conf = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
    conf["reports"] = [{"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}]
    (root / "config" / "settings.yaml").write_text(yaml.safe_dump(conf, allow_unicode=True), encoding="utf-8")
    return root


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.root = build_project()
        self.settings = load_settings(self.root)

    def test_list_sources_and_toggle(self):
        rows = api.list_sources(self.settings)["sources"]
        self.assertEqual([r["id"] for r in rows], ["news_a"])
        self.assertTrue(rows[0]["enabled"])
        api.set_source_enabled(self.settings, "news_a", False)
        self.assertFalse(api.list_sources(self.settings)["sources"][0]["enabled"])
        index = store.read_json(self.settings.paths.source_index)
        self.assertFalse(index["sources"][0]["enabled"])

    def test_toggle_unknown_source_is_404(self):
        with self.assertRaises(api.ApiError) as cm:
            api.set_source_enabled(self.settings, "nope", True)
        self.assertEqual(cm.exception.status, 404)

    def test_run_now_and_report_and_read_back(self):
        from adapters.base import FetchContext
        import core.runner as runner
        orig = runner.default_context
        runner.default_context = lambda s: FetchContext(http_get=lambda u, **k: RSS)
        try:
            res = api.run_source_now(self.settings, "news_a")
        finally:
            runner.default_context = orig
        self.assertTrue(res["ok"])
        self.assertEqual(res["new_count"], 1)

        gen = api.generate_report_now(self.settings, "rolling")
        self.assertEqual(gen["plan_id"], "rolling")
        listing = api.list_reports(self.settings)
        self.assertEqual(listing["plans"], ["rolling"])
        self.assertEqual(len(listing["reports"]), 1)
        md = api.read_report(self.settings, listing["reports"][0]["markdown"])["markdown"]
        self.assertIn("新闻一", md)

        speech = api.report_speech(self.settings, listing["reports"][0]["json"])["segments"]
        self.assertTrue(any("新闻一" in s["text"] for s in speech))

        item_id = api.read_report(self.settings, listing["reports"][0]["json"])["json"]["sections"][0]["sources"][0]["items"][0]["id"]
        one = api.item_speech(self.settings, "news_a", item_id)
        self.assertEqual(one["item"]["title"], "新闻一")

    def test_report_path_traversal_rejected(self):
        for bad in ("../config/settings.yaml", "reports/../../config/settings.yaml", "config/categories.yaml"):
            with self.assertRaises(api.ApiError) as cm:
                api.read_report(self.settings, bad)
            self.assertEqual(cm.exception.status, 400, bad)

    def test_list_runs_parses_status_and_detail(self):
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        log = self.root / "data" / "runs" / f"{day}.log.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("# 运行记录\n\n- 10:00:00 `news_a` 成功 | 抓取 2 条 | 新增 1 条 | 耗时 0.1s\n"
                       "- 10:05:00 `news_a` 失败 | 错误：超时 | 耗时 0.2s\n", encoding="utf-8")
        rows = api.list_runs(self.settings, day)["runs"]
        self.assertEqual(rows[0]["status"], "失败")  # 倒序，最新在前
        self.assertIn("超时", rows[0]["detail"])
        with self.assertRaises(api.ApiError):
            api.list_runs(self.settings, "../../etc")

    def test_login_status_lists_configured_sites(self):
        import yaml
        (self.root / "config" / "sites.yaml").write_text(
            yaml.safe_dump({"sites": {"zh": {"name": "知乎", "login_url": "https://z/login"}}}, allow_unicode=True),
            encoding="utf-8")
        s = api.login_status(load_settings(self.root))["sites"]
        self.assertEqual(s[0]["status"], "unknown")
        self.assertEqual(s[0]["login_command"], "python cli.py login zh")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = build_project()
        cls.settings = load_settings(cls.root)
        cls.httpd = make_server(cls.settings, port=0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host} if host else {}
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def post(self, path, payload, custom=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if custom:
            headers["X-Info-Hub"] = "1"
        conn.request("POST", path, body=json.dumps(payload), headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, json.loads(body or b"{}")

    def test_index_and_static(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"info_hub", body)
        status, _ = self.get("/app.js")
        self.assertEqual(status, 200)
        status, _ = self.get("/../config/settings.yaml")
        self.assertIn(status, (403, 404))

    def test_api_sources_json(self):
        status, body = self.get("/api/sources")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sources"][0]["id"], "news_a")

    def test_post_without_custom_header_is_forbidden(self):
        status, payload = self.post("/api/tick", {}, custom=False)
        self.assertEqual(status, 403)

    def test_post_with_header_toggles_source(self):
        status, payload = self.post("/api/sources/news_a/enabled", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        self.post("/api/sources/news_a/enabled", {"enabled": True})

    def test_non_local_host_rejected(self):
        status, body = self.get("/api/sources", host="evil.example.com")
        self.assertEqual(status, 403)

    def test_unknown_api_is_404(self):
        status, _ = self.get("/api/does-not-exist")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
