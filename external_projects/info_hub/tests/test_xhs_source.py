"""小红书源配置：加载正常、默认停用、浏览器登录站点已声明。"""
import unittest
from pathlib import Path

from core.settings import load_settings, load_sites
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent


class XhsSourceTests(unittest.TestCase):
    def test_source_loads_and_is_configured(self):
        res = load_sources(load_settings(ROOT))
        self.assertNotIn("xiaohongshu_hot", res.errors)
        spec = res.specs["xiaohongshu_hot"]
        self.assertTrue(spec.enabled)
        self.assertNotIn("TODO", str(spec.parse))
        self.assertEqual(spec.browser.get("site"), "xiaohongshu")

    def test_site_declared(self):
        sites = load_sites(ROOT)
        self.assertIn("xiaohongshu", sites)
        self.assertIn("expired_pattern", sites["xiaohongshu"])


if __name__ == "__main__":
    unittest.main()
