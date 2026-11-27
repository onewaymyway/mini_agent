"""阶段 P13：探索位升级——新发现且已启用的来源可占用少量探索名额，不挤占多源热点资格。"""
import unittest

from core import discovery, feed
from tests.helpers import make_project


def card(key, src, *, n_sources=1, matches=None, freshness=0.9, importance=0.8, score=1.0):
    return {"key": key, "title": f"标题{key}", "url": f"https://x/{key}", "source_id": src,
            "sources": [{"source_id": src}] * n_sources if n_sources else [{"source_id": src}],
            "n_sources": n_sources, "matches": matches or [], "score": score,
            "components": {"importance": importance, "freshness": freshness}, "section": ""}


class ExploreSlotTests(unittest.TestCase):
    def cfg(self, **kw):
        base = feed.load_config(None)  # 完整默认配置
        base.update({"featured_size": 0, "explore_size": 4, "explore_discovered_size": 2})
        base.update(kw)
        return base

    def test_discovered_source_gets_reserved_explore_slots(self):
        cards = [card("old1", "news_old", freshness=0.99), card("old2", "news_old", freshness=0.98),
                 card("old3", "news_old", freshness=0.97), card("new1", "news_new", freshness=0.2),
                 card("new2", "news_new", freshness=0.1)]
        out = feed._assign_sections(cards, self.cfg(), new_sources=frozenset({"news_new"}))
        explore_keys = [c["key"] for c in out["explore"]]
        self.assertIn("new1", explore_keys)
        self.assertIn("new2", explore_keys)
        self.assertTrue(all(c.get("discovered") for c in out["explore"] if c["key"].startswith("new")))
        self.assertEqual(len(out["explore"]), 4)  # 总数仍受 explore_size 限制

    def test_no_new_sources_behaves_as_before(self):
        cards = [card("a", "news_old"), card("b", "news_new")]
        out = feed._assign_sections(cards, self.cfg(), new_sources=frozenset())
        self.assertFalse(any(c.get("discovered") for c in out["explore"]))

    def test_discovered_cards_never_take_hot_eligibility(self):
        cards = [card(f"h{i}", "news_new", n_sources=3, freshness=0.9) for i in range(3)]
        out = feed._assign_sections(cards, self.cfg(), new_sources=frozenset({"news_new"}))
        self.assertEqual(out["explore"], [])
        self.assertEqual(len(out["hot"]), 3)

    def test_topic_matched_items_stay_out_of_explore(self):
        cards = [card("t1", "news_new", matches=["大模型"])]
        out = feed._assign_sections(cards, self.cfg(), new_sources=frozenset({"news_new"}))
        self.assertEqual(out["explore"], [])

    def test_quota_zero_disables_reservation(self):
        cards = [card("old1", "news_old", freshness=0.99), card("new1", "news_new", freshness=0.1)]
        out = feed._assign_sections(cards, self.cfg(explore_discovered_size=0),
                                    new_sources=frozenset({"news_new"}))
        self.assertFalse(any(c.get("discovered") for c in out["explore"]))

    def test_reason_marks_discovered_source(self):
        c = card("n", "news_new")
        c["discovered"] = True
        c["age"] = ""
        self.assertTrue(feed.build_reason(c).startswith("新发现来源"))


class AcceptedAutoSourcesTests(unittest.TestCase):
    def test_only_accepted_auto_candidates_count(self):
        root = make_project({})
        data = root / "data"
        base = {"template_id": "t", "template_name": "t", "query": "q", "reason": "r", "url": "u",
                "created_at": "2026-11-27T10:00:00+08:00", "decided_at": None}
        discovery.add_candidate(data, dict(base, id="auto:a.example", origin="auto_explore",
                                           status="accepted", source={"id": "auto_a", "enabled": True}))
        discovery.add_candidate(data, dict(base, id="auto:b.example", origin="auto_explore",
                                           status="pending", source={"id": "auto_b", "enabled": False}))
        discovery.add_candidate(data, dict(base, id="tpl:c", origin="", status="accepted",
                                           source={"id": "tpl_c", "enabled": True}))
        self.assertEqual(discovery.accepted_auto_sources(data), {"auto_a"})


if __name__ == "__main__":
    unittest.main()
