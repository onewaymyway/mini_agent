"""抓取失败时的调试信息：原文、解析错误位置、落盘文件，以及 RSS 对常见非法字符的修复。"""
import json
import unittest
from datetime import datetime
from unittest import mock

from adapters.base import FetchContext, failure_debug
from adapters.registry import get_adapter
from core.runner import debug_dir, run_source
from core.settings import load_settings
from core.sources import load_sources
from server import api
from tests.helpers import make_project

GOOD = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>标题一</title><link>https://a.example/1</link><description>摘要</description></item>
</channel></rss>""".encode("utf-8")
BAD_AMP = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>A &amp; B 与 C & D</title><link>https://a.example/2</link></item>
<item><title>x < y 的比较</title><link>https://a.example/3</link></item>
</channel></rss>""".encode("utf-8")
BAD_CTRL = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>ctrl\x01char</title><link>https://a.example/4</link></item></channel></rss>"""
BROKEN = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>ok</title><link>https://a.example/5</link></item>
<item><title>坏的 "引号 </title><link>https://a.example/6</link>
</channel></rss>""".encode("utf-8")
RSS_CFG = {"id": "kr", "name": "36氪", "category": "news", "kind": "feed", "adapter": "rss",
           "enabled": True, "request": {"url": "https://kr.example/feed"}}


def _ctx(payload):
    def http_get(url, headers=None, timeout=20.0):
        if isinstance(payload, Exception):
            raise payload
        return payload
    return FetchContext(http_get=http_get)


class RssRepairTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_sources(load_settings(make_project({"kr": RSS_CFG}))).specs["kr"]

    def test_good_feed_has_no_note(self):
        res = get_adapter("rss").fetch(self.spec, _ctx(GOOD))
        self.assertIsNone(res.error)
        self.assertNotIn("xml_sanitized", res.meta)

    def test_bare_ampersand_and_less_than_are_repaired(self):
        res = get_adapter("rss").fetch(self.spec, _ctx(BAD_AMP))
        self.assertIsNone(res.error)
        self.assertEqual(len(res.items), 2)
        self.assertTrue(res.meta.get("xml_sanitized"))
        self.assertIn("&", res.items[0].title)

    def test_control_character_is_removed(self):
        res = get_adapter("rss").fetch(self.spec, _ctx(BAD_CTRL))
        self.assertIsNone(res.error)
        self.assertEqual(res.items[0].title, "ctrl" + "char")

    def test_unrepairable_feed_returns_debug_with_position(self):
        res = get_adapter("rss").fetch(self.spec, _ctx(BROKEN))
        self.assertIsNotNone(res.error)
        self.assertIn("ParseError", res.error)
        dbg = res.meta["debug"]
        self.assertEqual(dbg["error_type"], "ParseError")
        self.assertEqual(dbg["parse_error_at"]["line"], 4)
        self.assertIn("[此处]", dbg["context"])
        self.assertIn("坏的", dbg["raw_text"])
        self.assertTrue(any(line.startswith("4:") for line in dbg["context_lines"]))

    def test_network_error_has_debug_without_raw(self):
        res = get_adapter("rss").fetch(self.spec, _ctx(OSError("boom")))
        self.assertIn("OSError", res.error)
        self.assertEqual(res.meta["debug"]["raw_length"], 0)
        self.assertEqual(res.meta["debug"]["error_type"], "OSError")

    def test_failure_debug_truncates_large_raw(self):
        big = b"x" * 150_000
        dbg = failure_debug(big, None)
        self.assertTrue(dbg["raw_truncated"])
        self.assertEqual(len(dbg["raw_text"]), 100_000)


WAF_PAGE = b"""<!DOCTYPE html><html><head><script type="application/json" id="__waf_verification_challenge">{"v":1}</script></head><body>wait</body></html>"""


class BlockPageTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_sources(load_settings(make_project({"kr": RSS_CFG}))).specs["kr"]

    def test_waf_challenge_is_reported_not_parse_error(self):
        from adapters.base import detect_block
        self.assertEqual(detect_block(WAF_PAGE), "waf_challenge")
        self.assertIsNone(detect_block(GOOD))
        res = get_adapter("rss").fetch(self.spec, _ctx(WAF_PAGE))
        self.assertIsNotNone(res.error)
        self.assertIn("安全验证", res.error)
        self.assertNotIn("ParseError", res.error)
        self.assertEqual(res.meta["blocked"], "waf_challenge")
        self.assertIn("__waf_verification_challenge", res.meta["debug"]["raw_text"])


class RunnerDebugTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"kr": RSS_CFG})
        self.settings = load_settings(self.root)
        self.spec = load_sources(self.settings).specs["kr"]

    def test_failed_run_writes_debug_file_and_returns_summary(self):
        res = run_source(self.spec, self.settings, _ctx(BROKEN))
        self.assertFalse(res.ok)
        self.assertIn("debug", res.extra)
        self.assertIn("debug_path", res.extra)
        files = sorted(debug_dir(self.settings, "kr").glob("*.json"))
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["source_id"], "kr")
        self.assertIn("坏的", saved["debug"]["raw_text"])

    def test_debug_files_are_pruned(self):
        from core import runner
        with mock.patch.object(runner, "DEBUG_KEEP", 2):
            for i in range(4):
                when = datetime(2026, 11, 13, 10, i)
                runner._failure_extra(self.settings, "kr", when, "e",
                                      {"debug": {"raw_text": str(i)}})
        self.assertEqual(len(list(debug_dir(self.settings, "kr").glob("*.json"))), 2)

    def test_run_now_api_returns_debug_for_failure(self):
        with mock.patch("core.runner.default_context", return_value=_ctx(BROKEN)):
            out = api.run_source_now(self.settings, "kr")
        self.assertFalse(out["ok"])
        self.assertIn("坏的", out["debug"]["raw_text"])
        self.assertIn("parse_error_at", out["debug"])
        self.assertTrue(out["debug_path"].endswith(".json"))

    def test_run_now_api_success_has_stats(self):
        with mock.patch("core.runner.default_context", return_value=_ctx(BAD_AMP)):
            out = api.run_source_now(self.settings, "kr")
        self.assertTrue(out["ok"])
        self.assertEqual(out["item_count"], 2)
        self.assertTrue(out["meta"]["xml_sanitized"])
        self.assertIsNone(out["debug"])


class RunOncePanelTests(unittest.TestCase):
    def test_panel_shows_error_context_and_raw_for_failure(self):
        import os
        from pathlib import Path as _P
        from streamlit.testing.v1 import AppTest
        root = make_project({"kr": RSS_CFG})
        os.environ["INFO_HUB_ROOT"] = str(root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        settings = load_settings(root)
        with mock.patch("core.runner.default_context", return_value=_ctx(BROKEN)):
            out = api.run_source_now(settings, "kr")
        at = AppTest.from_file(str(_P(__file__).resolve().parents[1] / "app.py"), default_timeout=60)
        at.session_state["run_once_result"] = out
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any("失败" in e.value for e in at.error))
        codes = " ".join(c.value for c in at.code)
        self.assertIn("[此处]", codes)
        self.assertIn("坏的", codes)


if __name__ == "__main__":
    unittest.main()
