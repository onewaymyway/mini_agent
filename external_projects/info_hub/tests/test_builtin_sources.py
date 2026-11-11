"""内置信息源：配置全部合法，并用仿真接口响应验证字段映射。"""
import json
import unittest
from pathlib import Path

from adapters.base import FetchContext
from adapters.registry import get_adapter
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent

RESP = {
    "weibo_hot": {"data": {"realtime": [{"word": "某 话题", "num": 123456, "label_name": "热"}]}},
    "baidu_hot": {"data": {"cards": [{"component": "hotList", "content": [{"content": [
        {"word": "百度词", "hotScore": 999, "url": "https://www.baidu.com/s?wd=x", "desc": "说明"}]}]}]}},
    "bilibili_popular": {"data": {"list": [{"title": "视频A", "bvid": "BV1xx", "stat": {"view": 5000}, "desc": "d"}]}},
    "v2ex_hot": [{"title": "主题", "url": "https://www.v2ex.com/t/1", "replies": 33, "content": "c"}],
}
EXPECT = {
    "weibo_hot": ("某 话题", "https://s.weibo.com/weibo?q=%23%E6%9F%90%20%E8%AF%9D%E9%A2%98%23", "123456"),
    "baidu_hot": ("百度词", "https://www.baidu.com/s?wd=x", "999"),
    "bilibili_popular": ("视频A", "https://www.bilibili.com/video/BV1xx", "5000"),
    "v2ex_hot": ("主题", "https://www.v2ex.com/t/1", "33"),
}


class BuiltinSourcesTests(unittest.TestCase):
    def test_all_configs_valid(self):
        res = load_sources(load_settings(ROOT))
        self.assertEqual(res.errors, {})
        for sid in RESP:
            self.assertIn(sid, res.specs)

    def test_field_mapping(self):
        specs = load_sources(load_settings(ROOT)).specs
        for sid, payload in RESP.items():
            seen = {}

            def http_get(url, headers=None, _p=payload, _s=seen):
                _s["headers"] = headers
                return json.dumps(_p).encode("utf-8")

            r = get_adapter(specs[sid].adapter).fetch(specs[sid], FetchContext(http_get=http_get))
            self.assertTrue(r.ok, (sid, r.error))
            title, url, heat = EXPECT[sid]
            it = r.items[0]
            self.assertEqual((it.title, it.url, it.heat), (title, url, heat), sid)
            self.assertIn("User-Agent", seen["headers"])

    def test_zero_items_reports_structure_outline(self):
        specs = load_sources(load_settings(ROOT)).specs
        payload = {"data": {"cards": [{"content": [{"content": [{"query": "x"}]}]}]}}
        r = get_adapter("json_api").fetch(specs["baidu_hot"], FetchContext(
            http_get=lambda u, headers=None: json.dumps(payload).encode("utf-8")))
        self.assertFalse(r.ok)
        self.assertIn("解析到 0 条", r.error)
        self.assertIn("cards", r.error)

    def test_baidu_flat_shape_still_supported(self):
        specs = load_sources(load_settings(ROOT)).specs
        flat = {"data": {"cards": [{"content": [
            {"word": "扁平词", "hotScore": 1, "url": "https://www.baidu.com/s?wd=y", "desc": ""}]}]}}
        r = get_adapter("json_api").fetch(specs["baidu_hot"], FetchContext(
            http_get=lambda u, headers=None: json.dumps(flat).encode("utf-8")))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.items[0].title, "扁平词")


if __name__ == "__main__":
    unittest.main()
