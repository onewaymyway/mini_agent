import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from adapters.base import FetchContext
from core import store
from core.digest import build_digest, render_markdown, write_report
from core.settings import load_settings
from core.tick import tick
from tests.helpers import make_project

TZ = datetime.now().astimezone().tzinfo
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=TZ)

HOT = {"id": "hot_a", "name": "热榜A", "category": "news", "kind": "hot_list", "adapter": "rss",
       "request": {"url": "https://x/feed"}, "schedule": {"every": "1h"}}
FEED = {"id": "feed_b", "name": "订阅B", "category": "news", "kind": "feed", "adapter": "rss",
        "request": {"url": "https://x/b"}, "schedule": {"every": "2h"}}

RSS_A1 = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>A1</title><link>https://a/1</link></item><item><title>A2</title><link>https://a/2</link></item>
</channel></rss>"""
RSS_A2 = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>A2</title><link>https://a/2</link></item><item><title>A1</title><link>https://a/1</link></item>
<item><title>A3</title><link>https://a/3</link></item>
</channel></rss>"""


def write_fixture(root: Path, when: datetime, source_id: str, title: str, url: str, kind="feed"):
    """直接写入一条带固定时间戳的条目，用于窗口测试。"""
    ts = when.isoformat(timespec="seconds")
    day = when.strftime("%Y-%m-%d")
    item = {"id": f"{source_id}:{title}", "source_id": source_id, "category": "news", "title": title,
            "url": url, "summary": "", "rank": 1, "heat": None, "published_at": None,
            "fetched_at": ts, "extra": {}}
    store.append_jsonl(root / "data" / "items" / source_id / f"{day}.jsonl", item)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"hot_a": HOT, "feed_b": FEED})
        self.settings = load_settings(self.root)
        self.plan = {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}

    def test_window_filters_old_items(self):
        write_fixture(self.root, NOW - timedelta(hours=1), "feed_b", "新条目", "https://b/1")
        write_fixture(self.root, NOW - timedelta(hours=10), "feed_b", "过期条目", "https://b/2")
        d = build_digest(self.settings, self.plan, NOW)
        titles = [i["title"] for sec in d["sections"] for s in sec["sources"] for i in s["items"]]
        self.assertEqual(titles, ["新条目"])
        self.assertEqual(d["summary"]["new_items"], 1)

    def test_errors_in_window_are_listed(self):
        log = self.root / "data" / "runs" / f"{NOW:%Y-%m-%d}.log.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        recent = (NOW - timedelta(hours=1)).strftime("%H:%M:%S")
        old = (NOW - timedelta(hours=8)).strftime("%H:%M:%S")
        log.write_text(
            f"# 运行记录\n\n- {old} `hot_a` 失败 | 错误：很久以前 | 耗时 0.01s\n"
            f"- {recent} `hot_a` 失败 | 错误：超时 | 耗时 0.02s\n",
            encoding="utf-8")
        d = build_digest(self.settings, self.plan, NOW)
        self.assertEqual([e["error"] for e in d["errors"]], ["超时"])
        self.assertIn("异常", render_markdown(d, "t"))

    def test_hot_list_changes_come_from_latest_snapshot(self):
        snap = {"source_id": "hot_a", "fetched_at": (NOW - timedelta(minutes=5)).isoformat(timespec="seconds"),
                "item_count": 1, "new_count": 0,
                "changes": {"first_snapshot": False, "new": [], "rising": [{"id": "x", "title": "上升的", "from": 3, "to": 1}],
                            "falling": [], "dropped": []},
                "meta": {}, "items": []}
        store.atomic_write_json(self.root / "data" / "raw" / "hot_a" / f"{NOW:%Y-%m-%d}" / "x.json", snap)
        d = build_digest(self.settings, self.plan, NOW)
        hot = [s["hot_changes"] for sec in d["sections"] for s in sec["sources"] if s["source_id"] == "hot_a"][0]
        self.assertEqual(hot["rising"][0]["title"], "上升的")
        md = render_markdown(d, "t")
        self.assertIn("3→1", md)

    def test_write_report_creates_md_json_and_latest_pointer(self):
        write_fixture(self.root, NOW - timedelta(minutes=30), "feed_b", "报告条目", "https://b/9")
        paths = write_report(self.settings, self.plan, NOW)
        md = (self.root / paths["markdown"]).read_text(encoding="utf-8")
        self.assertIn("报告条目", md)
        self.assertTrue(paths["markdown"].endswith(".md"))
        data = json.loads((self.root / paths["json"]).read_text(encoding="utf-8"))
        self.assertEqual(data["plan_id"], "rolling")
        latest = store.read_json(self.root / "reports" / "latest.json")
        self.assertEqual(latest["markdown"], paths["markdown"])


class RunLogFormatTests(unittest.TestCase):
    def test_error_line_round_trips_without_stats_leaking_into_message(self):
        from core.digest import _ERROR_LINE_RE
        from core.runner import RunResult, format_run_line
        res = RunResult("hot_a", False, 0, 0.5, error="URLError: refused")
        line = format_run_line(NOW, res).rstrip("\n")
        m = _ERROR_LINE_RE.match(line)
        self.assertIsNotNone(m, line)
        self.assertEqual(m.group(3), "URLError: refused")

    def test_success_line_is_not_an_error(self):
        from core.digest import _ERROR_LINE_RE
        from core.runner import RunResult, format_run_line
        res = RunResult("hot_a", True, 3, 0.1, new_count=2)
        self.assertIsNone(_ERROR_LINE_RE.match(format_run_line(NOW, res).rstrip("\n")))


class TickTests(unittest.TestCase):
    def test_tick_runs_due_sources_and_reports_once(self):
        root = make_project({"hot_a": HOT, "feed_b": FEED})
        # 报告计划：每 3 小时一份
        settings_path = root / "config" / "settings.yaml"
        import yaml
        conf = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        conf["reports"] = [{"id": "rolling", "every": "3h", "window": "3h", "categories": "all"}]
        settings_path.write_text(yaml.safe_dump(conf, allow_unicode=True), encoding="utf-8")

        settings = load_settings(root)
        now = datetime.now().astimezone()
        ctx = FetchContext(http_get=lambda url, **kw: RSS_A1 if "feed" in url else RSS_A1)
        first = tick(settings, now=now, ctx=ctx)
        self.assertEqual(sorted(first.ran_sources), ["feed_b", "hot_a"])
        self.assertEqual(first.generated_reports, ["rolling"])

        # 同一时刻再次 tick：没有到期任务
        second = tick(settings, now=now + timedelta(minutes=5), ctx=ctx)
        self.assertEqual(second.ran_sources, [])
        self.assertEqual(second.generated_reports, [])

        # 1 小时后：hot_a 到期（间隔 1h），feed_b（2h）未到期，报告（3h）未到期
        third = tick(settings, now=now + timedelta(hours=1, minutes=1), ctx=ctx)
        self.assertEqual(third.ran_sources, ["hot_a"])
        self.assertEqual(third.generated_reports, [])

    def test_tick_skips_disabled_source(self):
        disabled = dict(FEED, id="off", enabled=False)
        root = make_project({"off": disabled})
        settings = load_settings(root)
        rep = tick(settings, now=datetime.now().astimezone(), ctx=FetchContext(http_get=lambda u, **k: RSS_A1))
        self.assertEqual(rep.ran_sources, [])


if __name__ == "__main__":
    unittest.main()
