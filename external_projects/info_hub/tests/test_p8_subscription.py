import json
import unittest
from datetime import datetime

from adapters.base import FetchContext
from adapters.registry import get_adapter, list_adapters
from checkers.registry import get_checker, list_checkers
from core.digest import build_digest, render_markdown
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from core.speech_text import digest_to_segments
from core.store import read_json, read_jsonl
from server import api
from tests.helpers import make_project


def feed(*titles):
    items = "".join(f"<item><title>{t}</title><link>https://n/{t}</link></item>" for t in titles)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'.encode("utf-8")


def sub_source(**sub):
    return {"id": "novel_a", "name": "某小说", "category": "news", "kind": "subscription",
            "adapter": "subscription", "request": {"url": "https://n/feed"},
            "subscription": {"checker": "feed_latest", **sub}}


class Env:
    def __init__(self, src):
        self.root = make_project({src["id"]: src})
        self.settings = load_settings(self.root)
        self.body = b""
        self.ctx = FetchContext(http_get=lambda url, **kw: self.body)
        self.sid = src["id"]

    def run(self, body=None):
        if body is not None:
            self.body = body
        spec = load_sources(self.settings).specs[self.sid]
        return run_source(spec, self.settings, self.ctx)

    def items(self):
        out = []
        for p in sorted((self.settings.paths.items_dir / self.sid).glob("*.jsonl")):
            out += read_jsonl(p)
        return out

    def state(self):
        return read_json(self.settings.paths.state_dir / f"{self.sid}.json")


class RegistryAndValidationTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn("subscription", list_adapters())
        self.assertEqual({"feed_latest", "json_latest", "html_latest"}, set(list_checkers()))
        self.assertIsNotNone(get_checker("feed_latest"))

    def errs(self, src):
        root = make_project({src["id"]: src})
        return load_sources(load_settings(root)).errors.get(src["id"], [])

    def test_valid_source_has_no_errors(self):
        self.assertEqual(self.errs(sub_source()), [])

    def test_missing_or_unknown_checker(self):
        s = sub_source(); del s["subscription"]["checker"]
        self.assertTrue(any("subscription.checker" in e for e in self.errs(s)))
        self.assertTrue(any("未找到检查器" in e for e in self.errs(sub_source(checker="nope"))))

    def test_kind_must_be_subscription_and_bad_order(self):
        s = sub_source(); s["kind"] = "feed"
        self.assertTrue(any("kind: subscription" in e for e in self.errs(s)))
        self.assertTrue(any("order" in e for e in self.errs(sub_source(order="sideways"))))
        self.assertTrue(any("include_keywords" in e for e in self.errs(sub_source(include_keywords="x"))))

    def test_inner_adapter_config_is_validated(self):
        s = sub_source(); s["request"] = {}
        self.assertTrue(any("request.url" in e for e in self.errs(s)))


class CursorFlowTests(unittest.TestCase):
    def test_baseline_then_update_then_unchanged(self):
        env = Env(sub_source())
        r = env.run(feed("第1章", "第0章"))
        self.assertTrue(r.ok); self.assertEqual(r.new_count, 0)           # 首次只记基线
        self.assertEqual(env.items(), [])
        self.assertEqual(env.state()["cursor"]["title"], "第1章")

        r = env.run(feed("第2章", "第1章", "第0章"))
        self.assertEqual(r.new_count, 1)
        it = env.items()[0]
        self.assertEqual(it["title"], "第2章")
        self.assertEqual(it["extra"]["previous_title"], "第1章")
        self.assertEqual(env.state()["cursor"]["title"], "第2章")

        r = env.run()                                                      # 没变化
        self.assertEqual(r.new_count, 0)
        self.assertEqual(len(env.items()), 1)

    def test_cursor_going_back_still_counts_as_change(self):
        env = Env(sub_source())
        env.run(feed("第2章")); env.run(feed("第3章"))
        r = env.run(feed("第2章"))   # 目录回退（章节被撤）再回来也算变化，不被“已见去重”吞掉
        self.assertEqual(r.new_count, 1)

    def test_notify_on_first_opt_in(self):
        env = Env(sub_source(notify_on_first=True))
        self.assertEqual(env.run(feed("第1章")).new_count, 1)

    def test_filters_pick_latest_matching(self):
        env = Env(sub_source(exclude_keywords=["番外"]))
        env.run(feed("番外一", "第1章"))
        self.assertEqual(env.state()["cursor"]["title"], "第1章")
        env2 = Env(sub_source(include_keywords=["正文"]))
        env2.run(feed("广告", "正文第5章", "正文第4章"))
        self.assertEqual(env2.state()["cursor"]["title"], "正文第5章")

    def test_no_candidate_keeps_cursor(self):
        env = Env(sub_source(include_keywords=["正文"]))
        env.run(feed("正文第1章"))
        r = env.run(feed("广告"))
        self.assertTrue(r.ok); self.assertEqual(r.new_count, 0)
        self.assertEqual(env.state()["cursor"]["title"], "正文第1章")

    def test_fetch_error_keeps_state(self):
        env = Env(sub_source())
        env.run(feed("第1章"))
        env.ctx = FetchContext(http_get=lambda url, **kw: (_ for _ in ()).throw(OSError("down")))
        r = env.run()
        self.assertFalse(r.ok)
        self.assertEqual(env.state()["cursor"]["title"], "第1章")


class OtherCheckersTests(unittest.TestCase):
    def test_json_latest_oldest_first_not_truncated_by_max_items(self):
        src = sub_source(checker="json_latest", order="oldest_first")
        src["parse"] = {"items_path": "list", "fields": {"title": "t", "url": "u"}}
        src["policy"] = {"max_items": 3}
        env = Env(src)
        payload = lambda n: json.dumps({"list": [{"t": f"第{i}章", "u": f"https://n/{i}"} for i in range(1, n + 1)]},
                                       ensure_ascii=False).encode()
        env.run(payload(80))
        self.assertEqual(env.state()["cursor"]["title"], "第80章")
        self.assertEqual(env.run(payload(81)).new_count, 1)

    def test_html_latest_without_browser(self):
        src = sub_source(checker="html_latest")
        src["parse"] = {"list_selector": ".c li", "fields": {"title": "a", "url": "a@href"}}
        env = Env(src)
        page = lambda *n: ("<ul class='c'>" + "".join(f"<li><a href='/c/{x}'>第{x}章</a></li>" for x in n) + "</ul>").encode()
        env.run(page(2, 1))
        r = env.run(page(3, 2, 1))
        self.assertEqual(r.new_count, 1)
        self.assertEqual(env.items()[0]["title"], "第3章")

    def test_cursor_from_title(self):
        env = Env(sub_source(cursor_from="title"))
        env.run(feed("第1章"))
        self.assertEqual(env.state()["cursor"]["value"], "第1章")


class DigestAndApiTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(sub_source())
        self.env.run(feed("第1章")); self.env.run(feed("第2章", "第1章"))
        self.plan = {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}
        self.digest = build_digest(self.env.settings, self.plan, datetime.now().astimezone())

    def test_subscription_section_pinned_and_not_in_categories(self):
        d = self.digest
        self.assertEqual(d["summary"]["subscription_updates"], 1)
        self.assertEqual(d["subscriptions"][0]["previous_title"], "第1章")
        self.assertEqual(d["sections"], [])                    # 不重复出现在分类小节
        md = render_markdown(d, "2026-11-11 12:00")
        self.assertIn("## 订阅更新", md)
        self.assertIn("《某小说》更新至 [第2章](https://n/第2章)（上次：第1章）", md)
        self.assertNotIn("没有可展示的内容", md)

    def test_speech_mentions_subscription_first(self):
        segs = digest_to_segments(self.digest)
        self.assertEqual(segs[1]["kind"], "subscription")
        self.assertIn("某小说更新至第2章", segs[1]["text"])

    def test_report_without_subscription_unchanged(self):
        env = Env({"id": "n", "name": "N", "category": "news", "kind": "feed", "adapter": "rss",
                   "request": {"url": "https://n/feed"}})
        env.run(feed("a"))
        d = build_digest(env.settings, self.plan, datetime.now().astimezone())
        self.assertEqual(d["subscriptions"], [])
        self.assertNotIn("订阅更新", render_markdown(d, "t"))

    def test_api_list_subscriptions(self):
        rows = api.list_subscriptions(self.env.settings)["subscriptions"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["id"], r["checker"], r["current_title"]), ("novel_a", "feed_latest", "第2章"))
        self.assertEqual(r["recent_updates"][0]["previous_title"], "第1章")

    def test_normal_sources_excluded_from_subscription_list(self):
        env = Env({"id": "n", "name": "N", "category": "news", "kind": "feed", "adapter": "rss",
                   "request": {"url": "https://n/feed"}})
        self.assertEqual(api.list_subscriptions(env.settings)["subscriptions"], [])


if __name__ == "__main__":
    unittest.main()
