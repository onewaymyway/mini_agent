import json
import tempfile
import unittest
from pathlib import Path

from core import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_atomic_write_json_roundtrip_keeps_chinese(self):
        p = self.root / "a" / "b.json"
        store.atomic_write_json(p, {"标题": "知乎热榜", "n": 1})
        text = p.read_text(encoding="utf-8")
        self.assertIn("知乎热榜", text)  # 不转义为 \uXXXX，便于人读
        self.assertEqual(store.read_json(p), {"标题": "知乎热榜", "n": 1})

    def test_atomic_write_leaves_no_tmp_files(self):
        p = self.root / "x.txt"
        store.atomic_write_text(p, "hello")
        store.atomic_write_text(p, "world")
        self.assertEqual(p.read_text(encoding="utf-8"), "world")
        leftovers = [f for f in self.root.iterdir() if f.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_read_json_default_when_missing(self):
        self.assertEqual(store.read_json(self.root / "nope.json", default={"k": 1}), {"k": 1})

    def test_append_and_read_jsonl(self):
        p = self.root / "items.jsonl"
        store.append_jsonl(p, {"id": 1})
        store.append_jsonl(p, {"id": 2})
        self.assertEqual([r["id"] for r in store.read_jsonl(p)], [1, 2])
        for line in p.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # 每行都是合法 json

    def test_read_jsonl_missing_file_yields_nothing(self):
        self.assertEqual(list(store.read_jsonl(self.root / "none.jsonl")), [])

    def test_file_lock_is_reentrant_sequentially(self):
        target = self.root / "state.json"
        with store.file_lock(target):
            pass
        with store.file_lock(target):
            pass
        self.assertTrue((self.root / "state.json.lock").exists())


if __name__ == "__main__":
    unittest.main()
