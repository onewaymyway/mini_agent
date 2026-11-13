"""Hugging Face 两个新信息源：每日论文、热门模型。用仿真响应验证字段映射（沙箱无法访问 huggingface.co）。"""
import json
import unittest
from pathlib import Path

from adapters.base import FetchContext
from adapters.registry import get_adapter
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent
DAILY = [
    {"paper": {"id": "2611.00001", "title": "论文标题", "summary": "摘要\n内容", "upvotes": 42,
               "publishedAt": "2026-11-12T00:00:00.000Z",
               "authors": [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}, {"name": "丁"}]},
     "title": "论文标题", "publishedAt": "2026-11-12T00:00:00.000Z"},
    {"paper": {"id": "2611.00002", "title": "第二篇", "upvotes": 7}, "title": "第二篇"},
    {"paper": {"id": "x"}, "title": ""},
]
MODELS = [
    {"id": "org/model-a", "pipeline_tag": "text-generation", "likes": 1200, "downloads": 98765,
     "trendingScore": 88.5, "lastModified": "2026-11-10T08:00:00.000Z"},
    {"id": "org/model-b", "likes": 3, "trendingScore": 1.2},
    {"likes": 1},
]


def _ctx(payload):
    def http_get(url, headers=None, timeout=20.0):
        return json.dumps(payload).encode("utf-8")
    return FetchContext(http_get=http_get)


class HfSourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = load_sources(load_settings(ROOT)).specs

    def test_configs_load(self):
        for sid in ("hf_daily_papers", "hf_trending_models"):
            self.assertIn(sid, self.specs)
            self.assertTrue(self.specs[sid].enabled)

    def test_daily_papers_mapping(self):
        res = get_adapter("script").fetch(self.specs["hf_daily_papers"], _ctx(DAILY))
        self.assertIsNone(res.error)
        self.assertEqual([i.title for i in res.items], ["论文标题", "第二篇"])
        first = res.items[0]
        self.assertEqual(first.url, "https://huggingface.co/papers/2611.00001")
        self.assertEqual(first.heat, "42")
        self.assertIn("甲, 乙, 丙 等", first.summary)
        self.assertNotIn("\n", first.summary)
        self.assertEqual(first.published_at, "2026-11-12T00:00:00.000Z")

    def test_trending_models_mapping(self):
        res = get_adapter("script").fetch(self.specs["hf_trending_models"], _ctx(MODELS))
        self.assertIsNone(res.error)
        self.assertEqual([i.title for i in res.items], ["org/model-a", "org/model-b"])
        first = res.items[0]
        self.assertEqual(first.url, "https://huggingface.co/org/model-a")
        self.assertEqual(first.heat, "88.5")
        self.assertIn("text-generation", first.summary)
        self.assertIn("♥ 1200", first.summary)
        self.assertEqual(first.rank, 1)


if __name__ == "__main__":
    unittest.main()
