import unittest

from core.settings import PROJECT_ROOT, load_settings


class SettingsTests(unittest.TestCase):
    def test_loads_default_project_config(self):
        s = load_settings()
        self.assertEqual(s.paths.root, PROJECT_ROOT)
        self.assertTrue(s.paths.source_index.name == "_index.json")
        self.assertIn("hot_community", s.category_ids)
        self.assertTrue(any(r["id"] == "rolling" for r in s.reports))


if __name__ == "__main__":
    unittest.main()
