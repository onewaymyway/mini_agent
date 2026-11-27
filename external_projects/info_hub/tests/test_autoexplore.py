"""阶段 P12：自动站点探索（伪造站点、robots、LLM，不访问网络）。"""
import json
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from core import autoexplore, discovery
from core.settings import load_settings
from server import api
from tests.helpers import make_project

NOW = datetime(2026, 11, 27, 12, 0).astimezone()
SITE = "https://good.example/"
PAGE = "".join(
    f'<div class="hot-item"><span class="t">热点标题第{i}条</span><a href="https://good.example/p/{i}">x</a></div>'
    for i in range(1, 6)).join(["<html><body>", "</body></html>"])


class FakeCtx:
    def __init__(self, routes):
        self.routes = routes
        self.fetched = []

    def http_get(self, url, headers=None, timeout=20.0):
        self.fetched.append(url)
        v = self.routes.get(url)
        if v is None:
            raise urllib.error.HTTPError(url, 404, "not found", None, None)
        if isinstance(v, BaseException):
            raise v
        return v


class FakeLLM:
    def __init__(self, url, adapter="html_list"):
        self.url, self.adapter, self.prompts = url, adapter, []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return json.dumps({
            "adapter": self.adapter, "explanation": "重复列表结构",
            "source": {"id": "x", "name": "示例", "category": "news", "kind": "hot_list", "tags": [],
                       "enabled": True, "request": {"url": self.url},
                       "parse": {"list_selector": "div.hot-item", "fields": {"title": "span.t", "url": "a@href"}},
                       "policy": {"max_items": 20, "dedup_by": "url"}},
        }, ensure_ascii=False)


class AutoExploreTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "seed_sites.yaml").write_text(
            f"sites:\n  - url: {SITE}\n    name: 示例站\n", encoding="utf-8")
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def _run(self, routes, llm=None, now=NOW):
        ctx = FakeCtx(routes)
        llm = llm or FakeLLM(SITE)
        return autoexplore.run(self.settings, llm, ctx=ctx, now=now, provider_label="fake"), ctx

    def test_good_site_becomes_disabled_candidate(self):
        out, ctx = self._run({SITE: PAGE.encode()})
        self.assertEqual(len(out["added"]), 1, out)
        cand = out["added"][0]
        self.assertEqual(cand["origin"], "auto_explore")
        self.assertEqual(cand["status"], "pending")
        self.assertFalse(cand["source"]["enabled"])  # 默认停用
        self.assertTrue(any(r["step"] == "candidate" for r in
                            [json.loads(x) for x in autoexplore.log_path(self.data).read_text().splitlines()]))
        self.assertTrue(any(c["id"] == "auto:good.example" for c in discovery.list_candidates(self.data)))

    def test_robots_disallow_blocks_site(self):
        robots = b"User-agent: *\nDisallow: /\n"
        out, ctx = self._run({SITE: PAGE.encode(), f"{SITE}robots.txt": robots})
        self.assertEqual(out["added"], [])
        self.assertEqual(out["skipped"].get("robots_disallow"), 1)
        self.assertNotIn(SITE, ctx.fetched)  # robots 不允许时不读取页面

    def test_robots_unavailable_is_conservative(self):
        out, ctx = self._run({SITE: PAGE.encode(), f"{SITE}robots.txt": urllib.error.URLError("down")})
        self.assertEqual(out["skipped"].get("robots_unavailable"), 1)
        self.assertEqual(out["added"], [])

    def test_verification_page_is_abandoned(self):
        blocked = b"<html>cf-chl-bypass challenge</html>"
        out, _ = self._run({SITE: blocked})
        self.assertEqual(out["skipped"].get("blocked_or_login"), 1)
        self.assertEqual(out["added"], [])

    def test_no_llm_adds_no_model_candidates(self):
        ctx = FakeCtx({SITE: PAGE.encode()})
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        self.assertEqual(out["added"], [])
        self.assertIn("no_llm", out["skipped"])

    def test_site_not_explored_twice(self):
        out1, _ = self._run({SITE: PAGE.encode()})
        self.assertEqual(len(out1["added"]), 1)
        out2, _ = self._run({SITE: PAGE.encode()}, now=NOW + timedelta(days=1))
        self.assertEqual(out2["added"], [])
        self.assertEqual(out2["skipped"].get("already_known"), 1)

    def test_rejected_candidate_is_not_regenerated(self):
        self._run({SITE: PAGE.encode()})
        discovery.decide(self.data, "auto:good.example", "rejected")
        out, _ = self._run({SITE: PAGE.encode()}, now=NOW + timedelta(days=2))
        self.assertEqual(out["added"], [])
        self.assertEqual(out["skipped"].get("already_known"), 1)

    def test_daily_cap_zero_stops_before_fetching(self):
        (self.root / "config" / "discovery.yaml").write_text("autoexplore:\n  daily: 0\n", encoding="utf-8")
        out, ctx = self._run({SITE: PAGE.encode()})
        self.assertEqual(out["skipped"].get("daily_cap"), 1)
        self.assertEqual(ctx.fetched, [])

    def test_min_interval_skips_recently_explored_host(self):
        state = {"hosts": {"good.example": NOW.isoformat(timespec="seconds")}, "days": {}}
        autoexplore.state_path(self.data).parent.mkdir(parents=True, exist_ok=True)
        autoexplore.state_path(self.data).write_text(json.dumps(state), encoding="utf-8")
        out, ctx = self._run({SITE: PAGE.encode()}, now=NOW + timedelta(minutes=10))
        self.assertEqual(out["skipped"].get("min_interval"), 1)
        self.assertEqual(out["added"], [])

    def test_script_drafts_are_never_auto_candidates(self):
        out, _ = self._run({SITE: PAGE.encode()}, llm=FakeLLM(SITE, adapter="script"))
        self.assertEqual(out["added"], [])
        self.assertEqual(out["skipped"].get("script_not_auto"), 1)

    def test_seeds_must_be_https(self):
        (self.root / "config" / "seed_sites.yaml").write_text(
            "sites:\n  - url: http://plain.example/\n  - url: https://ok.example/\n", encoding="utf-8")
        self.assertEqual([s["url"] for s in autoexplore.load_seeds(self.root)], ["https://ok.example/"])

    def test_harvest_uses_recent_item_links_only(self):
        items = self.data / "items" / "news_a"
        items.mkdir(parents=True)
        today = NOW.date().isoformat()
        (items / f"{today}.jsonl").write_text(
            json.dumps({"url": "https://fresh.example/a/1"}) + "\n" + json.dumps({"url": "http://x.example/"}) + "\n",
            encoding="utf-8")
        hosts = autoexplore.harvest_hosts(self.data, set(), 10, NOW)
        self.assertEqual([h["url"] for h in hosts], ["https://fresh.example/"])


class AutoExploreApiTests(unittest.TestCase):
    def test_api_without_model_reports_no_llm(self):
        root = make_project({})
        settings = load_settings(root)
        (root / "config" / "seed_sites.yaml").write_text(f"sites:\n  - url: {SITE}\n", encoding="utf-8")
        out = api.autoexplore_run_api(settings)
        self.assertIn("skipped", out)
        self.assertIn("added", out)


if __name__ == "__main__":
    unittest.main()
