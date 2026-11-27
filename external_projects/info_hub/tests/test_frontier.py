"""阶段 P16：Frontier（持久化队列、深度限制、同站/跨站规则、禁止名单、跨运行续跑）。不访问网络。"""
import unittest
from datetime import datetime, timedelta

from core import autoexplore
from core.frontier import Frontier, MAX_DEPTH, enqueue_links, is_denied, normalize, score_link
from core.settings import load_settings
from tests.helpers import make_project
from tests.test_autoexplore import FakeCtx, FakeLLM, PAGE, SITE

NOW = datetime(2026, 11, 27, 12, 0).astimezone()
ISO = NOW.isoformat(timespec="seconds")
CATS = {"科技": "tech", "新闻": "news", "体育": "sports", "游戏": "game", "视频": "video", "财经": "finance"}


def home_html():
    return ("<html><body>" + "".join(f'<a href="/{p}/">{t}</a>' for t, p in CATS.items())
            + "</body></html>").encode("utf-8")


def listing_html(p):
    return f'<html><body><a href="/{p}/item">标题页</a><a href="/login">登录</a></body></html>'.encode("utf-8")


class RulesTests(unittest.TestCase):
    def test_normalize_strips_tracking_and_requires_https(self):
        self.assertEqual(normalize("HTTPS://Good.Example/news/?utm_source=x&id=3#top"),
                         "https://good.example/news/?id=3")
        self.assertIsNone(normalize("http://a.example/x"))
        self.assertIsNone(normalize("mailto:a@b.example"))

    def test_score_rejects_login_download_and_files(self):
        self.assertIsNone(score_link("https://good.example/login", "登录"))
        self.assertIsNone(score_link("https://good.example/download/app", "下载"))
        self.assertIsNone(score_link("https://good.example/a/report.pdf", "报告"))
        self.assertIsNone(score_link("https://good.example/a/b/c/d", "深层"))

    def test_single_segment_category_outranks_article_path(self):
        self.assertGreater(score_link("https://good.example/tech/", "科技"),
                           score_link("https://good.example/x/y", "标题"))

    def test_denylist_matches_subdomains_not_lookalikes(self):
        self.assertTrue(is_denied("m.bad.example", {"bad.example"}))
        self.assertFalse(is_denied("notbad.example", {"bad.example"}))


class FrontierStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = load_settings(self.root).paths.data_dir

    def test_persists_across_instances_and_resumes(self):
        f1 = Frontier(self.data)
        self.assertTrue(f1.enqueue("https://good.example/tech/", 1, SITE, 2.0, ISO))
        f2 = Frontier(self.data)                         # 模拟重启
        self.assertEqual(f2.queued_count(), 1)
        self.assertEqual([r["url"] for r in f2.next_batch(5, lambda h: True)], ["https://good.example/tech/"])

    def test_status_change_removes_from_queue(self):
        f = Frontier(self.data)
        f.enqueue("https://good.example/tech/", 1, SITE, 2.0, ISO)
        f.set_status("https://good.example/tech/", "done", ISO)
        self.assertEqual(f.queued_count(), 0)
        self.assertFalse(f.enqueue("https://good.example/tech/", 1, SITE, 2.0, ISO))  # 已处理不再入队

    def test_depth_limit(self):
        f = Frontier(self.data)
        self.assertFalse(f.enqueue("https://good.example/deep/", MAX_DEPTH + 1, SITE, 1.0, ISO))

    def test_queue_capacity(self):
        f = Frontier(self.data, max_queued=2)
        self.assertTrue(f.enqueue("https://good.example/a/", 1, SITE, 1.0, ISO))
        self.assertTrue(f.enqueue("https://good.example/b/", 1, SITE, 1.0, ISO))
        self.assertFalse(f.enqueue("https://good.example/c/", 1, SITE, 1.0, ISO))

    def test_next_batch_prefers_priority_then_shallow(self):
        f = Frontier(self.data)
        f.enqueue("https://good.example/low/", 1, SITE, 1.0, ISO)
        f.enqueue("https://good.example/high/", 2, SITE, 3.0, ISO)
        self.assertEqual(f.next_batch(1, lambda h: True)[0]["url"], "https://good.example/high/")


class EnqueueRulesTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = load_settings(self.root).paths.data_dir
        self.f = Frontier(self.data)
        self.links = [("https://good.example/tech/", "科技"), ("https://other.example/x/", "外站页面"),
                      ("https://good.example/login", "登录")]

    def test_same_site_only_by_default(self):
        n = enqueue_links(self.f, self.links, parent=SITE, parent_depth=0, denylist=set(),
                          cross_site=False, trusted=True, now_iso=ISO)
        self.assertEqual(n, 1)
        self.assertEqual(self.f.rows()[0]["url"], "https://good.example/tech/")

    def test_cross_site_requires_flag_and_trusted_parent(self):
        n = enqueue_links(self.f, self.links, parent=SITE, parent_depth=0, denylist=set(),
                          cross_site=True, trusted=False, now_iso=ISO)
        self.assertEqual(n, 1)                          # 不可信来源：不入跨站链接
        n = enqueue_links(self.f, self.links, parent=SITE, parent_depth=0, denylist=set(),
                          cross_site=True, trusted=True, now_iso=ISO)
        self.assertEqual(n, 1)                          # 只新增了跨站的 other.example
        self.assertTrue(any(r["host"] == "other.example" for r in self.f.rows()))

    def test_denylisted_host_never_enqueued(self):
        n = enqueue_links(self.f, self.links, parent=SITE, parent_depth=0, denylist={"good.example"},
                          cross_site=False, trusted=True, now_iso=ISO)
        self.assertEqual(n, 0)

    def test_per_page_cap(self):
        many = [(f"https://good.example/c{i}/", "分类") for i in range(10)]
        n = enqueue_links(self.f, many, parent=SITE, parent_depth=0, denylist=set(), cross_site=False,
                          trusted=True, now_iso=ISO, per_page_cap=3)
        self.assertEqual(n, 3)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "seed_sites.yaml").write_text(f"sites:\n  - url: {SITE}\n", encoding="utf-8")
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def _cfg(self, **kw):
        base = {"max_listing_pages": 2, "frontier_batch": 10, "host_daily_pages": 30, "min_interval_hours": 1}
        base.update(kw)
        (self.root / "config" / "discovery.yaml").write_text(
            "autoexplore:\n" + "".join(f"  {k}: {v}\n" for k, v in base.items()), encoding="utf-8")

    def _routes(self):
        routes = {SITE: home_html(), f"{SITE}robots.txt": b"", }
        for p in CATS.values():
            routes[f"https://good.example/{p}/"] = listing_html(p)
            routes[f"https://good.example/{p}/item"] = PAGE.encode("utf-8")
        return routes

    def test_listings_beyond_cap_are_queued_then_processed_next_run(self):
        self._cfg()
        ctx1 = FakeCtx(self._routes())
        out1 = autoexplore.run(self.settings, None, ctx=ctx1, now=NOW)
        fetched1 = set(ctx1.fetched)
        processed_listings = [u for u in fetched1 if u.endswith("/") and u != SITE and "robots" not in u]
        self.assertEqual(len(processed_listings), 2)            # 本轮内最多 2 个栏目
        queued1 = Frontier(self.data).queued_count()
        self.assertGreaterEqual(queued1, 4)                       # 其余栏目已入队
        later = NOW + timedelta(hours=2)
        ctx2 = FakeCtx(self._routes())
        autoexplore.run(self.settings, None, ctx=ctx2, now=later)
        self.assertTrue(any(u.endswith("/sports/") or u.endswith("/game/") for u in ctx2.fetched))
        self.assertLess(Frontier(self.data).queued_count(), queued1)

    def test_depth_two_links_are_queued_not_deeper(self):
        self._cfg(max_listing_pages=1)
        autoexplore.run(self.settings, None, ctx=FakeCtx(self._routes()), now=NOW)
        depths = {r["depth"] for r in Frontier(self.data).rows()}
        self.assertIn(2, depths)
        self.assertNotIn(3, depths)

    def test_host_daily_page_budget(self):
        self._cfg(max_listing_pages=5, host_daily_pages=2)
        out = autoexplore.run(self.settings, None, ctx=FakeCtx(self._routes()), now=NOW)
        self.assertEqual(out["skipped"].get("host_budget"), 1)

    def test_denylisted_site_not_visited(self):
        (self.root / "config" / "denylist.yaml").write_text("hosts:\n  - good.example\n", encoding="utf-8")
        self._cfg()
        ctx = FakeCtx(self._routes())
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        self.assertEqual(ctx.fetched, [])
        self.assertEqual(out["skipped"].get("denylisted"), 1)

    def test_robots_disallow_marks_queued_item(self):
        self._cfg(max_listing_pages=1)
        routes = self._routes()
        autoexplore.run(self.settings, None, ctx=FakeCtx(routes), now=NOW)
        routes[f"{SITE}robots.txt"] = b"User-agent: *\nDisallow: /sports/\n"
        autoexplore.run(self.settings, None, ctx=FakeCtx(routes), now=NOW + timedelta(hours=2))
        sports = [r for r in Frontier(self.data).rows() if r["url"].endswith("/sports/")]
        self.assertTrue(sports)
        self.assertNotEqual(sports[0]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
