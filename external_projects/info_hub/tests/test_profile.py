"""第二阶段：画像学习、衰减、显式偏好、排序、LLM 归纳（假 LLM）与 API 约束。"""
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock

from core import feedback, profile as prof
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)


def ev(action, title, days_ago=0, source="news_a", reasons=(), url=None):
    t = NOW - timedelta(days=days_ago)
    return {"action": action, "title": title, "source_id": source, "reasons": list(reasons),
            "time": t.isoformat(timespec="seconds"),
            "item_key": feedback.item_key(url or f"https://n/{abs(hash(title))}", title)}


class TokenizeTests(unittest.TestCase):
    def test_english_and_chinese(self):
        toks = prof.tokenize("Python 异步编程入门 与 AI Agent")
        self.assertIn("python", toks)
        self.assertIn("agent", toks)
        self.assertIn("异步编程", toks)
        self.assertIn("编程", toks)  # 子串候选
        self.assertNotIn("与", toks)

    def test_stopwords_removed(self):
        self.assertNotIn("我们", prof.tokenize("我们这个最新"))


class LearnTests(unittest.TestCase):
    def test_interested_raises_and_not_interested_lowers_keywords(self):
        events = [ev("interested", "大模型训练技巧"), ev("not_interested", "体育赛事回顾")]
        learned = prof.learn_from_events(events, NOW)
        self.assertGreater(learned["keywords"]["大模型"]["score"], 0)
        self.assertLess(learned["keywords"]["体育"]["score"], 0)

    def test_off_topic_amplifies_negative(self):
        plain = prof.learn_from_events([ev("not_interested", "娱乐八卦")], NOW)
        off = prof.learn_from_events([ev("not_interested", "娱乐八卦", reasons=["off_topic"])], NOW)
        self.assertLess(off["keywords"]["娱乐"]["score"], plain["keywords"]["娱乐"]["score"])

    def test_source_dislike_penalizes_source(self):
        out = prof.learn_from_events([ev("not_interested", "随便一条", reasons=["source_dislike"])], NOW)
        self.assertLess(out["sources"]["news_a"]["score"], -1.0)

    def test_time_decay_makes_old_feedback_weaker(self):
        recent = prof.learn_from_events([ev("interested", "量子计算", days_ago=0)], NOW)
        old = prof.learn_from_events([ev("interested", "量子计算", days_ago=30)], NOW)
        self.assertGreater(recent["keywords"]["量子"]["score"], old["keywords"]["量子"]["score"] * 1.8)

    def test_later_is_weaker_than_interested(self):
        a = prof.learn_from_events([ev("interested", "电动汽车")], NOW)["keywords"]["电动"]["score"]
        b = prof.learn_from_events([ev("later", "电动汽车")], NOW)["keywords"]["电动"]["score"]
        self.assertGreater(a, b)
        self.assertGreater(b, 0)

    def test_weak_noise_pruned(self):
        out = prof.learn_from_events([ev("later", "偶然一条稀有词汇X", days_ago=365)], NOW)
        self.assertEqual(out["keywords"], {})


class RankTests(unittest.TestCase):
    def setUp(self):
        self.profile = prof.empty_profile()
        self.profile["learned"]["keywords"] = {"大模型": {"score": 2.0, "evidence": 3},
                                                "体育": {"score": -2.0, "evidence": 2}}

    def test_matching_interest_ranks_higher(self):
        items = [{"title": "体育新闻速递", "url": "https://n/a"},
                 {"title": "大模型推理优化", "url": "https://n/b"}]
        ranked = prof.rank_items(self.profile, items, "news_a", {})
        self.assertEqual(ranked[0]["title"], "大模型推理优化")
        self.assertIn("大模型", ranked[0]["matches"])

    def test_explicit_topic_boosts_and_exclude_demotes(self):
        self.profile["explicit"] = {"topics": ["机器人"], "exclude": ["广告"]}
        s_topic, m = prof.score_item(self.profile, "机器人新品发布")
        s_ex, _ = prof.score_item(self.profile, "广告合集")
        self.assertGreater(s_topic, 0)
        self.assertIn("机器人", m)
        self.assertLess(s_ex, 0)

    def test_not_interested_last_action_sinks_to_bottom(self):
        items = [{"title": "大模型新闻", "url": "https://n/x"}, {"title": "其他普通新闻", "url": "https://n/y"}]
        key = feedback.item_key("https://n/x", "大模型新闻")
        ranked = prof.rank_items(self.profile, items, "news_a", {key: "not_interested"})
        self.assertEqual(ranked[-1]["title"], "大模型新闻")
        self.assertEqual(ranked[-1]["feedback"], "not_interested")

    def test_rank_does_not_mutate_input(self):
        items = [{"title": "大模型", "url": "https://n/z"}]
        prof.rank_items(self.profile, items, "news_a", {})
        self.assertNotIn("score", items[0])

    def test_last_actions_uses_latest(self):
        events = [ev("interested", "甲", days_ago=2, url="https://n/1"),
                  ev("not_interested", "甲", days_ago=0, url="https://n/1")]
        key = feedback.item_key("https://n/1", "甲")
        self.assertEqual(prof.last_actions(events)[key], "not_interested")


class ExplicitAndLlmTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = self.root / "data"

    def test_explicit_is_normalized_and_survives_rebuild(self):
        prof.save_explicit(self.data, "机器人，AI、  机器人 ", "广告")
        p = prof.load_profile(self.data)
        self.assertEqual(p["explicit"]["topics"], ["机器人", "AI"])
        self.assertEqual(p["explicit"]["exclude"], ["广告"])
        prof.rebuild_learned(self.data, [ev("interested", "量子计算")], NOW)
        self.assertEqual(prof.load_profile(self.data)["explicit"]["topics"], ["机器人", "AI"])

    def test_llm_summarize_parses_and_stores(self):
        prof.rebuild_learned(self.data, [ev("interested", "大模型推理")], NOW)
        reply = '```json\n{"interest_keywords": ["大模型", "推理"], "avoid_keywords": ["八卦"], ' \
                '"summary": "关注AI工程"}\n```'
        seen = {}

        def fake_ask(prompt, system=""):
            seen["prompt"] = prompt
            return reply

        out = prof.llm_summarize(self.data, fake_ask, [ev("interested", "大模型推理")], "测试模型")
        self.assertEqual(out["llm"]["keywords"], ["大模型", "推理"])
        self.assertEqual(out["llm"]["avoid"], ["八卦"])
        self.assertEqual(out["llm"]["summary"], "关注AI工程")
        self.assertIn("大模型推理", seen["prompt"])
        self.assertEqual(prof.load_profile(self.data)["llm"]["provider"], "测试模型")

    def test_llm_bad_output_raises_profile_error(self):
        with self.assertRaises(prof.ProfileError):
            prof.llm_summarize(self.data, lambda p, system="": "不是json", [ev("interested", "甲")])

    def test_llm_needs_feedback(self):
        with self.assertRaises(prof.ProfileError):
            prof.llm_summarize(self.data, lambda p, system="": "{}", [])

    def test_llm_terms_used_in_scoring(self):
        p = prof.empty_profile()
        p["llm"] = {"keywords": ["芯片封装"], "avoid": ["彩票"], "summary": ""}
        pos, _ = prof.score_item(p, "芯片封装新工艺")
        neg, _ = prof.score_item(p, "彩票开奖结果")
        self.assertGreater(pos, 0)
        self.assertLess(neg, 0)

    def test_clear_learned_keeps_explicit(self):
        prof.save_explicit(self.data, "机器人", "")
        prof.rebuild_learned(self.data, [ev("interested", "量子计算")], NOW)
        cleared = prof.clear_learned(self.data)
        self.assertEqual(cleared["learned"]["keywords"], {})
        self.assertEqual(cleared["explicit"]["topics"], ["机器人"])


class ApiProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                             "adapter": "rss", "request": {"url": "https://n/feed"}}})
        self.settings = load_settings(self.root)

    def test_get_profile_auto_refreshes_after_feedback(self):
        before = api.get_profile(self.settings)
        self.assertEqual(before["event_count"], 0)
        api.record_feedback(self.settings, {"action": "interested", "source_id": "news_a",
                                            "url": "https://n/1", "title": "量子计算突破"})
        after = api.get_profile(self.settings)
        self.assertEqual(after["event_count"], 1)
        self.assertIn("量子", after["profile"]["learned"]["keywords"])

    def test_save_explicit_api_and_bad_type(self):
        out = api.save_explicit_profile(self.settings, {"topics": "机器人", "exclude": ""})
        self.assertEqual(out["profile"]["explicit"]["topics"], ["机器人"])

    def test_rank_snapshot_api(self):
        api.record_feedback(self.settings, {"action": "interested", "source_id": "news_a",
                                            "url": "https://n/1", "title": "量子计算突破"})
        items = [{"title": "普通新闻", "url": "https://n/9"}, {"title": "量子计算新进展", "url": "https://n/8"}]
        out = api.rank_snapshot(self.settings, "news_a", items)
        self.assertEqual(out["items"][0]["title"], "量子计算新进展")
        self.assertIn("score", out["items"][0])

    def test_llm_summary_blocked_when_disabled(self):
        (self.root / "config" / "settings.yaml").write_text(
            "paths:\n  sources_dir: sources\n  data_dir: data\n  reports_dir: reports\n"
            "  browser_profile_dir: browser/profile\nprofile:\n  llm_enabled: false\n", encoding="utf-8")
        settings = load_settings(self.root)
        with self.assertRaises(ApiError) as cm:
            api.summarize_profile_with_llm(settings)
        self.assertEqual(cm.exception.status, 403)

    def test_llm_summary_without_provider_is_503(self):
        from core.llm import LLMUnavailable
        with mock.patch("core.llm.build_llm", side_effect=LLMUnavailable("未配置")):
            with self.assertRaises(ApiError) as cm:
                api.summarize_profile_with_llm(self.settings)
        self.assertEqual(cm.exception.status, 503)

    def test_llm_summary_via_api_with_fake_model(self):
        api.record_feedback(self.settings, {"action": "interested", "source_id": "news_a",
                                            "url": "https://n/1", "title": "量子计算突破"})
        fake_llm = mock.Mock()
        fake_llm.ask.return_value = json.dumps({"interest_keywords": ["量子"], "avoid_keywords": [],
                                                "summary": "关注前沿科技"}, ensure_ascii=False)
        with mock.patch("core.llm.build_llm", return_value=fake_llm), \
                mock.patch("core.llm.resolve_provider", return_value=mock.Mock(label="假模型")):
            out = api.summarize_profile_with_llm(self.settings)
        self.assertEqual(out["profile"]["llm"]["summary"], "关注前沿科技")
        self.assertEqual(out["profile"]["llm"]["provider"], "假模型")


if __name__ == "__main__":
    unittest.main()
