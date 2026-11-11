import unittest

from adapters.base import FetchContext
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from server import api
from tests.helpers import make_project

RSS = ('<?xml version="1.0"?><rss version="2.0"><channel><item><title>甲</title><link>https://n/1</link></item>'
       '</channel></rss>').encode("utf-8")
SRC = {"id": "s1", "name": "S", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/f"}}


class SnapshotApiTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(make_project({"s1": SRC}))

    def _run(self):
        spec = load_sources(self.settings).specs["s1"]
        run_source(spec, self.settings, FetchContext(http_get=lambda u, **k: RSS))

    def test_empty(self):
        self.assertEqual(api.list_snapshots(self.settings, "s1")["snapshots"], [])
        with self.assertRaises(api.ApiError) as cm:
            api.read_snapshot(self.settings, "s1")
        self.assertEqual(cm.exception.status, 404)

    def test_latest_and_by_id(self):
        self._run()
        snaps = api.list_snapshots(self.settings, "s1")["snapshots"]
        self.assertEqual(len(snaps), 1)
        d = api.read_snapshot(self.settings, "s1")
        self.assertEqual(d["items"][0]["title"], "甲")
        self.assertEqual(api.read_snapshot(self.settings, "s1", snaps[0]["id"])["item_count"], 1)

    def test_path_traversal_rejected(self):
        for sid, snap in (("..", None), ("s1", "../../config/settings.yaml"), ("s1", "x.json")):
            with self.assertRaises(api.ApiError):
                api.read_snapshot(self.settings, sid, snap)
        self.assertEqual(api.list_snapshots(self.settings, "../x")["snapshots"], [])


if __name__ == "__main__":
    unittest.main()
