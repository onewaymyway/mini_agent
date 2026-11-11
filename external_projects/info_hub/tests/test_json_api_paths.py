"""json_api 适配器：通配符路径、候选路径回退、跳过条目诊断。"""
import json
import unittest

from adapters.base import FetchContext, SourceSpec
from adapters.json_api import JsonApiAdapter
from pathlib import Path


def _spec(items_path, fields=None):
    return SourceSpec(
        id="t", name="t", category="c", kind="hot_list", adapter="json_api", enabled=True,
        request={"url": "https://x.test/api"}, parse={"items_path": items_path,
        "fields": fields or {"title": "title"}}, policy={}, schedule={}, browser={},
        tags=[], dir=Path("."), raw={})


def _run(spec, payload):
    return JsonApiAdapter().fetch(spec, FetchContext(
        http_get=lambda u, headers=None: json.dumps(payload).encode("utf-8")))


class JsonApiPathTests(unittest.TestCase):
    def test_wildcard_flattens_nested_groups(self):
        payload = {"a": [{"content": [{"title": "1"}, {"title": "2"}]}, {"content": [{"title": "3"}]}]}
        r = _run(_spec("a.*.content"), payload)
        self.assertEqual([i.title for i in r.items], ["1", "2", "3"])

    def test_candidate_list_uses_first_non_empty(self):
        payload = {"x": {"list": []}, "y": [{"title": "ok"}]}
        r = _run(_spec(["x.list", "y"]), payload)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.items[0].title, "ok")

    def test_non_list_path_reports_tried_paths(self):
        r = _run(_spec(["nope.x"]), {"a": 1})
        self.assertFalse(r.ok)
        self.assertIn("nope.x", r.error)

    def test_skipped_entries_are_recorded_in_meta(self):
        payload = {"l": [{"title": "good"}, {"ads": [1]}, {"title": ""}]}
        r = _run(_spec("l"), payload)
        self.assertTrue(r.ok)
        self.assertEqual(r.meta["skipped"], 2)
        self.assertIn('"ads"', r.meta["debug"]["skipped_samples"][0])
        self.assertIn("raw_head", r.meta["debug"])


if __name__ == "__main__":
    unittest.main()
