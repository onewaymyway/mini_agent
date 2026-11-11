"""编辑现有信息源：回填、保留未在表单中的配置、ID 不变、校验失败不写入、启用状态同步。"""
import json
import unittest

import yaml

from core.settings import load_settings
from server import api
from tests.helpers import make_project

RICH = {
    "id": "rich_src", "name": "富配置源", "category": "news", "kind": "feed", "adapter": "html_list",
    "enabled": True, "tags": ["a"],
    "request": {"url": "https://x.test/list", "headers": {"Referer": "https://x.test/"}},
    "policy": {"max_items": 30, "dedup_by": "title"},
    "schedule": {"every": "1h"},
    "parse": {"list_selector": ".item", "date_pattern": r"(\d{4}-\d{2}-\d{2})",
              "fields": {"title": "a", "url": "a@href", "published_at": "parent", "summary": ".desc"}},
}


class SourceEditTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"rich_src": RICH})
        self.settings = load_settings(self.root)
        self.path = self.root / "sources" / "rich_src" / "source.yaml"

    def _disk(self):
        return yaml.safe_load(self.path.read_text(encoding="utf-8"))

    def _form(self, **over):
        form = {"name": "改名后", "category": "news", "kind": "feed", "adapter": "html_list",
                "every": "2h", "url": "https://x.test/list2", "max_items": 20, "tags": "x,y",
                "enabled": True, "list_selector": ".item2",
                "fields": {"title": "h3", "url": "h3 a@href"}}
        form.update(over)
        return {"source": form}

    def test_get_source_prefills_form(self):
        f = api.get_source(self.settings, "rich_src")
        self.assertEqual(f["name"], "富配置源")
        self.assertEqual(f["list_selector"], ".item")
        self.assertEqual(f["fields"], {"title": "a", "url": "a@href"})
        self.assertEqual(f["every"], "1h")
        self.assertTrue(f["enabled"])

    def test_update_changes_form_fields_and_keeps_unlisted_config(self):
        api.update_source(self.settings, "rich_src", self._form())
        d = self._disk()
        self.assertEqual(d["name"], "改名后")
        self.assertEqual(d["schedule"]["every"], "2h")
        self.assertEqual(d["request"]["url"], "https://x.test/list2")
        self.assertEqual(d["request"]["headers"], {"Referer": "https://x.test/"})   # 保留
        self.assertEqual(d["parse"]["date_pattern"], r"(\d{4}-\d{2}-\d{2})")        # 保留
        self.assertEqual(d["parse"]["fields"]["published_at"], "parent")             # 保留
        self.assertEqual(d["parse"]["fields"]["summary"], ".desc")                   # 保留
        self.assertEqual(d["parse"]["fields"]["title"], "h3")                        # 表单覆盖
        self.assertEqual(d["parse"]["list_selector"], ".item2")
        self.assertEqual(d["policy"]["dedup_by"], "title")                           # 保留
        self.assertEqual(d["tags"], ["x", "y"])

    def test_empty_form_field_removes_managed_key(self):
        api.update_source(self.settings, "rich_src", self._form(fields={"title": "a", "url": ""}))
        self.assertNotIn("url", self._disk()["parse"]["fields"])

    def test_id_cannot_change(self):
        api.update_source(self.settings, "rich_src", self._form(id="other_id"))
        self.assertTrue(self.path.exists())
        self.assertFalse((self.root / "sources" / "other_id").exists())

    def test_invalid_category_rejected_and_not_written(self):
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(api.ApiError):
            api.update_source(self.settings, "rich_src", self._form(category="no_such"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_enabled_syncs_to_index(self):
        api.update_source(self.settings, "rich_src", self._form(enabled=False))
        data = json.loads(self.settings.paths.source_index.read_text(encoding="utf-8"))
        self.assertFalse(next(e for e in data["sources"] if e["id"] == "rich_src")["enabled"])
        self.assertFalse(self._disk()["enabled"])

    def test_unknown_source_404(self):
        with self.assertRaises(api.ApiError):
            api.get_source(self.settings, "nope")


if __name__ == "__main__":
    unittest.main()
