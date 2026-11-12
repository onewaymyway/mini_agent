"""推荐信息流 P3：学习权重的训练、留出集验证门槛、回落、用户开启、与 build_feed 的集成。

对应 next_doc/info_hub_recommendation_feed_design.md 第 6 节 P3。P1 / P2 的测试见 test_feed*.py。
"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core import feed, feed_learn
from core.feedback import item_key, record_feedback
from core.settings import load_settings
from server import api
from tests.helpers import make_project
from tests.test_feed import _src, _sources_of


def _synthetic_world(data_dir, now, *, batches=30, per_batch=10, seed=11):
    """构造有真实信号的展示记录：正例的 interest 分量高，且手工顺序（展示位置）与之无关。

    每个展示条目之后都有一条反馈（正例 👍，负例 👎），因此全部带标签。
    返回 url 列表与对应分量，便于测试断言。
    """
    import random
    rng = random.Random(seed)
    for b in range(batches):
        when = now - timedelta(hours=b + 1)
        items = []
        labels = []
        for pos in range(per_batch):
            positive = rng.random() < 0.3
            url = f"https://a.example/b{b}p{pos}"
            title = f"合成条目{b}号{pos}号内容{rng.randint(0, 10**6)}"
            comps = {
                "interest": round(rng.uniform(0.6, 1.0) if positive else rng.uniform(0.0, 0.4), 4),
                "source_affinity": round(rng.uniform(0.3, 0.7), 4),
                "freshness": round(rng.uniform(0.2, 1.0), 4),
                "importance": round(rng.uniform(0.0, 1.0), 4),
                "persistence_penalty": 0.0,
                "fatigue_penalty": 0.0,
            }
            items.append({"item_key": item_key(url, title), "position": pos,
                          "first_seen": (when - timedelta(hours=rng.randint(1, 50))).isoformat(timespec="seconds"),
                          "components": comps, "section": "others", "score": 0.0})
            labels.append((url, title, positive))
        # 手工顺序与信号无关：打乱位置
        rng.shuffle(items)
        for pos, it in enumerate(items):
            it["position"] = pos
        feed.record_impressions(data_dir, window_hours=24, impressions=items, now=when)
        for url, title, positive in labels:
            record_feedback(data_dir, {"action": "interested" if positive else "not_interested",
                                       "source_id": "a", "title": title, "url": url})


class TrainLogRegTests(unittest.TestCase):
    def test_learns_to_weight_the_predictive_feature(self):
        import random
        rng = random.Random(1)
        X, y = [], []
        for _ in range(300):
            signal = rng.random()
            noise = rng.random()
            x = [signal, noise, 0.0, 0.0, 0.0, 0.0]
            X.append(x)
            y.append(1 if signal > 0.5 else 0)
        w, b = feed_learn.train_logreg(X, y, epochs=300, lr=0.5, l2=0.001)
        self.assertGreater(w[0], 0.5)
        self.assertGreater(w[0], abs(w[1]) * 3)

    def test_empty_input_returns_zero_weights(self):
        w, b = feed_learn.train_logreg([], [], epochs=10, lr=0.1, l2=0.0)
        self.assertEqual(w, [0.0] * len(feed_learn.FEATURES))
        self.assertEqual(b, 0.0)


class LearnGateTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A")})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self.now = datetime.now().astimezone().replace(microsecond=0)

    def test_insufficient_samples_is_not_validated(self):
        rep = feed_learn.train_and_validate(self.data, now=self.now, raw_config={})
        self.assertFalse(rep["validated"])
        self.assertIn("样本不足", rep["reason"])
        self.assertIsNone(feed_learn.active_weights(self.data, enabled=True))

    def test_validated_model_is_used_only_when_user_enables(self):
        _synthetic_world(self.data, self.now)
        rep = feed_learn.train_and_validate(self.data, now=self.now,
                                            raw_config={"feed": {"learning": {"min_samples": 100}}})
        self.assertTrue(rep["validated"], rep)
        self.assertGreater(rep["holdout"]["gain"], 0.02)
        self.assertGreater(rep["weights"]["interest"], 0)
        self.assertIsNone(feed_learn.active_weights(self.data, enabled=False), "用户未开启时不得生效")
        self.assertIsNotNone(feed_learn.active_weights(self.data, enabled=True))

    def test_model_not_validated_never_activates(self):
        feed_learn._save(self.data, {"validated": False, "weights": {n: 1.0 for n in feed_learn.FEATURES}})
        self.assertIsNone(feed_learn.active_weights(self.data, enabled=True))

    def test_insufficient_gain_keeps_hand_weights(self):
        _synthetic_world(self.data, self.now)
        rep = feed_learn.train_and_validate(self.data, now=self.now,
                                            raw_config={"feed": {"learning": {"min_samples": 100,
                                                                              "min_gain": 0.99}}})
        self.assertFalse(rep["validated"])
        self.assertIn("提升不足", rep["reason"])
        self.assertIsNone(feed_learn.active_weights(self.data, enabled=True))

    def test_learned_score_is_linear_in_components(self):
        w = {n: 0.0 for n in feed_learn.FEATURES}
        w["interest"] = 2.0
        self.assertEqual(feed.learned_score({"interest": 0.5}, w), 1.0)
        self.assertEqual(feed.learned_score({}, w), 0.0)


class LearnIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A")})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self.now = datetime.now().astimezone().replace(microsecond=0)

    def test_prefs_default_off_and_toggle_persists(self):
        self.assertFalse(feed.load_prefs(self.data)["learning_enabled"])
        api.feed_set_learning_api(self.settings, {"enabled": True})
        self.assertTrue(feed.load_prefs(self.data)["learning_enabled"])
        # 切换学习开关不得丢失隐藏来源
        feed.set_source_hidden(self.data, "a", True)
        api.feed_set_learning_api(self.settings, {"enabled": False})
        prefs = feed.load_prefs(self.data)
        self.assertEqual(prefs["hidden_sources"], ["a"])
        self.assertFalse(prefs["learning_enabled"])

    def test_status_and_train_api_round_trip(self):
        st = api.feed_learning_status_api(self.settings)
        self.assertFalse(st["enabled"])
        self.assertFalse(st["active"])
        _synthetic_world(self.data, self.now)
        rep = api.feed_train_api(self.settings)
        self.assertIn("validated", rep)
        self.assertEqual(api.feed_learning_status_api(self.settings)["model"]["samples"], rep["samples"])

    def test_feed_view_uses_learned_scoring_only_when_active(self):
        _synthetic_world(self.data, self.now)
        cfg_raw = {"feed": {"learning": {"min_samples": 100, "min_gain": 0.0}}}
        self.settings = load_settings(self.root)
        self.settings.raw.setdefault("feed", {}).update(cfg_raw["feed"])
        rep = feed_learn.train_and_validate(self.data, now=self.now, raw_config=cfg_raw)
        self.assertTrue(rep["validated"], rep)
        from core.store import atomic_write_json  # noqa: F401
        res_off = api.feed_view(self.settings, hours=24, now=self.now)
        self.assertEqual(res_off["scoring"], "hand")
        api.feed_set_learning_api(self.settings, {"enabled": True})
        res_on = api.feed_view(self.settings, hours=24, now=self.now)
        self.assertEqual(res_on["scoring"], "learned")


if __name__ == "__main__":
    unittest.main()
