"""阶段 P19-3：模块级常量迁入注册表（不访问网络）。"""
import unittest
from pathlib import Path

from core import autoexplore, discovery, explore_params
from core.explore_json_api import MAX_DEPTH as JSON_MAX_DEPTH, MIN_ITEMS as JSON_MIN_ITEMS, observe_json
from core.explore_render import HeadedRenderer
from core.frontier import MAX_DEPTH, Frontier, enqueue_links
from core.settings import load_settings
from server import api
from tests.helpers import make_project

ENDPOINT = "https://example.com/api/list"


def payload(n):
    return {"data": {"list": [{"title": f"标题{i}", "url": f"https://example.com/a/{i}"} for i in range(n)]}}


class RegistryTests(unittest.TestCase):
    def test_new_registry_defaults_equal_old_constants(self):
        self.assertEqual(explore_params.defaults("autoexplore")["max_depth"], MAX_DEPTH)
        self.assertEqual(explore_params.defaults("autoexplore")["json_min_items"], JSON_MIN_ITEMS)
        self.assertEqual(explore_params.defaults("autoexplore")["json_max_depth"], JSON_MAX_DEPTH)
        self.assertEqual(explore_params.defaults("discovery_limits")["daily"], discovery.DEFAULT_DAILY_CAP)
        self.assertEqual(explore_params.defaults("discovery_limits")["pending"], discovery.DEFAULT_PENDING_CAP)

    def test_autoexplore_defaults_still_match_registry(self):
        reg = explore_params.defaults("autoexplore")
        for key, default in autoexplore.DEFAULTS.items():
            self.assertEqual(reg[key], default, key)

    def test_api_exposes_all_four_sections(self):
        root = make_project({})
        ov = api.explore_params_overview(load_settings(root))
        self.assertEqual([s["section"] for s in ov["sections"]],
                         ["autoexplore", "review", "discovery_limits", "discovery_adoption"])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = load_settings(self.root).paths.data_dir

    def test_yaml_limits_and_adoption_still_read_as_before(self):
        (self.root / "config" / "discovery.yaml").write_text(
            "limits:\n  daily: 7\n  pending: 4\nadoption:\n  min_decisions: 9\n  min_rate: 0.5\n", encoding="utf-8")
        cfg = discovery.load_config(self.root)
        self.assertEqual((cfg["daily_cap"], cfg["pending_cap"]), (7, 4))
        self.assertEqual((cfg["min_decisions"], cfg["min_rate"]), (9, 0.5))

    def test_dashboard_override_changes_discovery_limits(self):
        explore_params.save(self.root, "discovery_limits", {"daily": 12})
        explore_params.save(self.root, "discovery_adoption", {"min_rate": 0.0})
        cfg = discovery.load_config(self.root)
        self.assertEqual(cfg["daily_cap"], 12)
        self.assertEqual(cfg["min_rate"], 0.0)          # 0 表示关闭自动暂停

    def test_dashboard_override_changes_max_depth_of_queue(self):
        explore_params.save(self.root, "autoexplore", {"max_depth": 1})
        cfg = autoexplore.load_config(self.root)
        f = Frontier(self.data)
        f.max_depth = cfg["max_depth"]
        self.assertEqual(f.max_depth, 1)
        self.assertTrue(f.enqueue("https://example.com/a", 1, "p", 1.0, "t"))
        self.assertFalse(f.enqueue("https://example.com/b", 2, "p", 1.0, "t"))

    def test_default_frontier_depth_unchanged(self):
        self.assertEqual(Frontier(self.data).max_depth, MAX_DEPTH)

    def test_enqueue_links_respects_frontier_depth(self):
        f = Frontier(self.data)
        f.max_depth = 1
        links = [("https://example.com/news/x", "新闻链接")]
        self.assertEqual(enqueue_links(f, links, parent="https://example.com/", parent_depth=1,
                                       denylist=set(), cross_site=False, trusted=True, now_iso="t"), 0)


class InterfaceAndRenderTests(unittest.TestCase):
    def test_observe_json_threshold_from_argument(self):
        self.assertIsNone(observe_json(ENDPOINT, payload(2)))                 # 默认 3 条，2 条不够
        self.assertIsNotNone(observe_json(ENDPOINT, payload(2), min_items=2))  # 看板降到 2 后可识别

    def test_observe_json_depth_from_argument(self):
        deep = {"a": {"b": {"d": payload(4)["data"]["list"]}}}   # 列表位于第 3 层
        self.assertIsNotNone(observe_json(ENDPOINT, deep))                    # 默认深度 3 可找到
        self.assertIsNone(observe_json(ENDPOINT, deep, max_depth=2))          # 深度限制为 2 找不到

    def test_make_renderer_uses_config(self):
        root = make_project({})
        cfg = {"render_timeout_s": 12.5, "render_max_responses": 7, "json_min_items": 5, "json_max_depth": 2}
        r = autoexplore.make_renderer(load_settings(root).paths.data_dir, cfg)
        self.assertIsInstance(r, HeadedRenderer)
        self.assertEqual((r.timeout_ms, r.max_responses, r.min_items, r.max_depth), (12500, 7, 5, 2))

    def test_renderer_stops_after_max_responses(self):
        class Resp:
            status = 200
            headers = {"content-type": "application/json"}
            url = ENDPOINT
            def json(self):
                return payload(4)
        r = HeadedRenderer(Path("/tmp/unused"), max_responses=0)
        r._on_response(Resp())
        self.assertEqual(r.last_observations, [])
        r2 = HeadedRenderer(Path("/tmp/unused"), max_responses=5, min_items=4)
        r2._on_response(Resp())
        self.assertEqual(len(r2.last_observations), 1)

    def test_dashboard_override_flows_into_renderer(self):
        root = make_project({})
        explore_params.save(root, "autoexplore", {"render_max_responses": 3, "render_timeout_s": 45})
        cfg = autoexplore.load_config(root)
        r = autoexplore.make_renderer(load_settings(root).paths.data_dir, cfg)
        self.assertEqual((r.max_responses, r.timeout_ms), (3, 45000))


if __name__ == "__main__":
    unittest.main()
