import json
import tempfile
import unittest
from pathlib import Path

import yaml

from adapters.base import FetchContext, SourceSpec
from adapters.script import ScriptAdapter, static_check
from core.explore import explore_html, explore_json, explore_response
from core.fingerprint import find_similar, fingerprint_of
from core.llm import resolve_provider
from core.settings import load_settings
from core.source_generator import SYSTEM_PROMPT, parse_llm_json
from core.source_store import SourceError
from tests.helpers import make_project


class FingerprintTests(unittest.TestCase):
    def test_bare_and_www_domain_match(self):
        a = fingerprint_of({"request": {"url": "https://arxiv.org/abs/1"}, "adapter": "html_list", "parse": {"list_selector": ".x"}})
        b = fingerprint_of({"request": {"url": "https://www.arxiv.org/abs/2"}, "adapter": "html_list", "parse": {"list_selector": ".x"}})
        self.assertTrue(a.same_as(b))

    def test_different_path_and_selector_not_duplicate(self):
        a = fingerprint_of({"request": {"url": "https://h.example/hot"}, "adapter": "html_list", "parse": {"list_selector": ".a"}})
        b = fingerprint_of({"request": {"url": "https://h.example/new"}, "adapter": "html_list", "parse": {"list_selector": ".b"}})
        self.assertFalse(a.same_as(b))

    def test_same_list_selector_on_same_domain_is_duplicate(self):
        a = fingerprint_of({"request": {"url": "https://h.example/hot"}, "adapter": "html_list", "parse": {"list_selector": ".item"}})
        b = fingerprint_of({"request": {"url": "https://h.example/other"}, "adapter": "html_list", "parse": {"list_selector": ".item"}})
        self.assertTrue(a.same_as(b))

    def test_find_similar_uses_existing_specs(self):
        from pathlib import Path as P
        spec = SourceSpec(id="old", name="o", category="news", kind="hot_list", adapter="html_list", enabled=True,
                          request={"url": "https://www.h.example/hot"}, parse={"list_selector": ".item"},
                          policy={}, schedule={}, browser={}, tags=[], dir=P("."))
        hits = find_similar({"old": spec}, {"request": {"url": "https://h.example/hot"}, "adapter": "html_list",
                                             "parse": {"list_selector": ".item"}})
        self.assertEqual(hits, ["old"])


class ExploreTests(unittest.TestCase):
    HTML = ('<html><head><title>热榜页</title></head><body>'
            + "".join(f'<div class="hot-item"><span class="t">条目{i}</span><a href="/q/{i}">链接</a></div>' for i in range(4))
            + '<script id="__NEXT_DATA__" type="application/json">{}</script></body></html>')

    def test_html_candidates_and_hints(self):
        s = explore_html(self.HTML)
        self.assertEqual(s["page_type"], "html")
        self.assertEqual(s["title"], "热榜页")
        sel = [c["selector"] for c in s["candidates"]]
        self.assertIn("div.hot-item", sel)
        cand = next(c for c in s["candidates"] if c["selector"] == "div.hot-item")
        self.assertEqual(cand["count"], 4)
        self.assertTrue(cand["sample_has_link"])
        self.assertTrue(any("__NEXT_DATA__" in h for h in s["embedded_data_hints"]))

    def test_json_shape(self):
        s = explore_json('{"data": {"list": [{"title": "a", "url": "u"}, {"title": "b"}]}}')
        self.assertEqual(s["page_type"], "json")
        self.assertEqual(s["shape"]["data"]["list"]["list_len"], 2)

    def test_response_detects_json_by_content(self):
        self.assertEqual(explore_response(b'[{"a":1}]')["page_type"], "json")
        self.assertEqual(explore_response(b"<html></html>")["page_type"], "html")


class ScriptTests(unittest.TestCase):
    def test_static_check_flags_forbidden_and_missing_fetch(self):
        errs = static_check("import os\ndef other(): pass\n")
        self.assertTrue(any("os" in e for e in errs))
        self.assertTrue(any("fetch" in e for e in errs))

    def test_static_check_flags_exec_and_open(self):
        errs = static_check("def fetch(ctx):\n    open('x')\n    eval('1')\n")
        self.assertTrue(any("open" in e for e in errs))
        self.assertTrue(any("eval" in e for e in errs))

    def test_good_script_passes_and_runs(self):
        d = Path(tempfile.mkdtemp())
        (d / "script.py").write_text(
            "def fetch(ctx):\n"
            "    body = ctx.http_get('https://x/y').decode('utf-8')\n"
            "    return [{'title': t, 'url': 'https://x/' + t} for t in body.split(',') if t]\n",
            encoding="utf-8")
        spec = SourceSpec(id="sc", name="s", category="news", kind="custom", adapter="script", enabled=True,
                          request={}, parse={}, policy={"max_items": 5}, schedule={}, browser={}, tags=[], dir=d)
        adapter = ScriptAdapter()
        self.assertEqual(adapter.validate(spec), [])
        res = adapter.fetch(spec, FetchContext(http_get=lambda u, **k: "甲,乙,".encode("utf-8")))
        self.assertTrue(res.ok, res.error)
        self.assertEqual([i.title for i in res.items], ["甲", "乙"])

    def test_script_runtime_error_is_result_not_exception(self):
        d = Path(tempfile.mkdtemp())
        (d / "script.py").write_text("def fetch(ctx):\n    return 1/0\n", encoding="utf-8")
        spec = SourceSpec(id="sc", name="s", category="news", kind="custom", adapter="script", enabled=True,
                          request={}, parse={}, policy={}, schedule={}, browser={}, tags=[], dir=d)
        res = ScriptAdapter().fetch(spec, FetchContext(http_get=lambda u, **k: b""))
        self.assertFalse(res.ok)
        self.assertIn("ZeroDivisionError", res.error)


class ParseLlmTests(unittest.TestCase):
    OK = {"adapter": "rss", "explanation": "x", "source": {"request": {"url": "https://a"}}, "script": ""}

    def test_plain_json(self):
        self.assertEqual(parse_llm_json(json.dumps(self.OK))["adapter"], "rss")

    def test_fenced_and_prefixed(self):
        text = "好的，结果如下：\n```json\n" + json.dumps(self.OK, ensure_ascii=False) + "\n```\n希望有用"
        self.assertEqual(parse_llm_json(text)["adapter"], "rss")

    def test_missing_source_raises(self):
        with self.assertRaises(SourceError):
            parse_llm_json('{"adapter": "rss"}')

    def test_not_json_raises(self):
        with self.assertRaises(SourceError):
            parse_llm_json("没有结构化内容")

    def test_system_prompt_forbids_script_side_effects(self):
        self.assertIn("禁止导入", SYSTEM_PROMPT)


class ProviderResolutionTests(unittest.TestCase):
    def test_explicit_file_wins(self):
        root = make_project({})
        (root / "custom_providers.json").write_text("{}", encoding="utf-8")
        (root / "providers.json").write_text("{}", encoding="utf-8")
        (root / "config" / "llm.yaml").write_text(yaml.safe_dump({"providers_file": "custom_providers.json"}), encoding="utf-8")
        choice = resolve_provider(load_settings(root))
        self.assertEqual(choice.source, "explicit")
        self.assertTrue(choice.path.endswith("custom_providers.json"))

    def test_project_file_used_when_no_explicit(self):
        root = make_project({})
        (root / "providers.json").write_text("{}", encoding="utf-8")
        self.assertEqual(resolve_provider(load_settings(root)).source, "project")

    def test_none_when_nothing_configured(self):
        root = make_project({})
        choice = resolve_provider(load_settings(root))
        self.assertEqual(choice.source, "none")

    def test_missing_explicit_file_is_error(self):
        from core.llm import LLMUnavailable
        root = make_project({})
        (root / "config" / "llm.yaml").write_text(yaml.safe_dump({"providers_file": "nope.json"}), encoding="utf-8")
        with self.assertRaises(LLMUnavailable):
            resolve_provider(load_settings(root))


if __name__ == "__main__":
    unittest.main()
