"""分类合并 + 时间范围：core/merged.py 的合并规则、api 校验，以及看板“合并显示”页面。"""
import os
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core import merged
from core.settings import load_settings
from core.store import atomic_write_json
from server import api
from server.api import ApiError
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _src(sid, name, category="news", kind="feed"):
    return {"id": sid, "name": name, "category": category, "kind": kind, "adapter": "rss",
            "request": {"url": f"https://{sid}/feed"}}


def _item(sid, key, title, rank=1, url=None, **kw):
    return {"id": f"{sid}:{key}", "source_id": sid, "title": title, "url": url or f"https://{sid}/{key}",
            "summary": kw.get("summary", ""), "rank": rank, "heat": kw.get("heat"), "published_at": None}


def _snap(settings, sid, when, items, new_ids=()):
    path = settings.paths.raw_dir / sid / f"{when:%Y-%m-%d}" / f"{when:%H%M%S}.json"
    atomic_write_json(path, {"source_id": sid, "fetched_at": when.isoformat(timespec="seconds"),
                             "item_count": len(items), "new_count": len(new_ids),
                             "changes": {"new": [{"id": i} for i in new_ids]}, "items": items})


class MergeCoreTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B")})
        self.settings = load_settings(self.root)
        self.now = datetime.now().astimezone()

    def _merge(self, ids, hours):
        return merged.merge_sources(self.settings.paths.raw_dir, ids, hours, now=self.now)

    def test_window_excludes_old_snapshots(self):
        _snap(self.settings, "a", self.now - timedelta(hours=30), [_item("a", "old", "旧闻")])
        _snap(self.settings, "a", self.now - timedelta(hours=2), [_item("a", "new", "新闻")])
        res = self._merge(["a"], 24)
        self.assertEqual([i["title"] for i in res["items"]], ["新闻"])
        self.assertEqual(res["sources"]["a"]["snapshots"], 1)
        self.assertEqual(sorted(i["title"] for i in self._merge(["a"], 72)["items"]), ["新闻", "旧闻"])

    def test_dedup_keeps_first_seen_and_latest_values(self):
        t1, t2, t3 = (self.now - timedelta(hours=h) for h in (5, 3, 1))
        _snap(self.settings, "a", t1, [_item("a", "x", "热点", rank=9, heat="100")], new_ids=["a:x"])
        _snap(self.settings, "a", t2, [_item("a", "x", "热点", rank=4, heat="300")])
        _snap(self.settings, "a", t3, [_item("a", "x", "热点", rank=2, heat="900"), _item("a", "y", "另一条")])
        items = {i["title"]: i for i in self._merge(["a"], 24)["items"]}
        x = items["热点"]
        self.assertEqual((x["rank"], x["heat"], x["seen_count"]), (2, "900", 3))
        self.assertEqual(x["first_seen"], t1.isoformat(timespec="seconds"))
        self.assertEqual(x["last_seen"], t3.isoformat(timespec="seconds"))
        self.assertTrue(x["is_new"])
        self.assertFalse(items["另一条"]["is_new"])  # 老条目：窗口内没有被标记为新增

    def test_same_id_in_different_sources_is_not_merged(self):
        _snap(self.settings, "a", self.now - timedelta(hours=1), [_item("a", "k", "同名", url="https://s/1")])
        _snap(self.settings, "b", self.now - timedelta(hours=1), [_item("b", "k", "同名", url="https://s/1")])
        res = self._merge(["a", "b"], 24)
        self.assertEqual(sorted(i["source_id"] for i in res["items"]), ["a", "b"])

    def test_source_without_data_and_corrupt_file(self):
        _snap(self.settings, "a", self.now - timedelta(hours=1), [_item("a", "k", "有数据")])
        bad = self.settings.paths.raw_dir / "a" / f"{self.now:%Y-%m-%d}" / "000001.json"
        bad.write_text("{broken", encoding="utf-8")
        res = self._merge(["a", "b"], 24)
        self.assertEqual([i["title"] for i in res["items"]], ["有数据"])
        self.assertEqual(res["sources"]["b"]["snapshots"], 0)

    def test_truncation_flag(self):
        old = merged.MAX_SNAPSHOTS_PER_SOURCE
        merged.MAX_SNAPSHOTS_PER_SOURCE = 2
        try:
            for m in (50, 40, 30, 20):
                _snap(self.settings, "a", self.now - timedelta(minutes=m), [_item("a", f"k{m}", f"条{m}")])
            res = self._merge(["a"], 24)
        finally:
            merged.MAX_SNAPSHOTS_PER_SOURCE = old
        self.assertTrue(res["sources"]["a"]["truncated"])
        self.assertEqual(sorted(i["title"] for i in res["items"]), ["条20", "条30"])  # 只保留最新两次

    def test_api_validation(self):
        for bad in ({"source_ids": ["../x"], "hours": 24}, {"source_ids": "a", "hours": 24},
                    {"source_ids": ["a"], "hours": "abc"}, {"source_ids": ["a"], "hours": 0},
                    {"source_ids": ["a"], "hours": 24 * 91}):
            with self.assertRaises(ApiError):
                api.merged_results(self.settings, **bad)
        self.assertEqual(api.merged_results(self.settings, ["a"], 24)["items"], [])


class MergedViewTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B"),
                                  "c": _src("c", "源C", category="hot_community", kind="hot_list")})
        self.settings = load_settings(self.root)
        now = datetime.now().astimezone()
        _snap(self.settings, "a", now - timedelta(hours=40), [_item("a", "old", "A旧闻")], new_ids=["a:old"])
        _snap(self.settings, "a", now - timedelta(minutes=30),
              [_item("a", "n1", "A新闻一"), _item("a", "n2", "A新闻二", rank=2)], new_ids=["a:n1"])
        _snap(self.settings, "b", now - timedelta(hours=5), [_item("b", "b1", "B财经")], new_ids=["b:b1"])
        _snap(self.settings, "c", now - timedelta(hours=1), [_item("c", "c1", "C热搜")])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        api_cache = __import__("streamlit").cache_data
        api_cache.clear()

    def _app(self, **selects):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.radio(key="res_mode").set_value("merged").run()
        for key, value in selects.items():
            at.selectbox(key=key).set_value(value).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def _titles(self, at):
        # 只取“抓取结果”标签页内的条目标题（测试数据的标题都以 A/B/C 开头）。
        # 限定到该标签页：“推荐”标签页也会展示同样的条目，不能混进来；页面其他面板里的粗体小标题也排除在外。
        tab = next(t for t in at.tabs if t.label == "抓取结果")
        return [m.value for m in tab.markdown if re.fullmatch(r"\*\*[ABC][^*]*\*\*", m.value)]

    def _buttons(self, at):
        return [b for b in at.button if b.key and b.key.startswith("op_mg_")]

    def test_category_merge_shows_all_sources_in_window(self):
        at = self._app(res_cat="news")
        titles = self._titles(at)
        self.assertEqual(sorted(titles), ["**A新闻一**", "**A新闻二**", "**B财经**"])  # 默认 24 小时：A旧闻不在内，C 不属于该分类
        self.assertEqual(len(self._buttons(at)), 3)

    def test_latest_first_order_and_window_change(self):
        at = self._app(res_cat="news")
        self.assertEqual(self._titles(at)[-1], "**B财经**")  # 5 小时前首次出现，排在 30 分钟前的 A 之后
        at.selectbox(key="mg_hours").set_value(1).run()
        self.assertEqual(sorted(self._titles(at)), ["**A新闻一**", "**A新闻二**"])
        at.selectbox(key="mg_hours").set_value(72).run()
        self.assertIn("**A旧闻**", self._titles(at))

    def test_only_new_and_keyword(self):
        at = self._app(res_cat="news")
        at.toggle(key="mg_new").set_value(True).run()
        self.assertEqual(sorted(self._titles(at)), ["**A新闻一**", "**B财经**"])  # A新闻二 不是窗口内新增
        at.text_input(key="mg_kw").set_value("财经").run()
        self.assertEqual(self._titles(at), ["**B财经**"])

    def test_no_category_merges_everything_and_kind_narrows(self):
        at = self._app()
        self.assertEqual(len(self._buttons(at)), 4)
        at.selectbox(key="res_kind").set_value("hot_list").run()
        self.assertEqual(self._titles(at), ["**C热搜**"])

    def test_sorts_do_not_crash_and_group_order(self):
        at = self._app(res_cat="news", mg_sort="按信息源分组")
        self.assertEqual(self._titles(at), ["**A新闻一**", "**A新闻二**", "**B财经**"])
        at.selectbox(key="mg_sort").set_value("按我的兴趣排序").run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(len(self._buttons(at)), 3)

    def test_empty_window_message_and_missing_sources_note(self):
        at = self._app(res_cat="news")
        at.text_input(key="mg_kw").set_value("不存在的词").run()
        self.assertTrue(any("没有符合条件" in i.value for i in at.info))

    def test_feedback_from_merged_row_uses_item_own_source(self):
        from core import feedback
        at = self._app(res_cat="news")
        [b for b in at.button if b.key and b.key.startswith("fb_interested_mg_")][-1].click().run()
        events = feedback.read_feedback(self.root / "data")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["title"], events[0]["source_id"], events[0]["view"]), ("B财经", "b", "plain"))

    def test_default_is_single_source_view(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertEqual(at.radio(key="res_mode").value, "single")
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("op_mg_")])


if __name__ == "__main__":
    unittest.main()
