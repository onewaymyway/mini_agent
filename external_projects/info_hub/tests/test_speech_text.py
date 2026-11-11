import unittest

from core.speech_text import MAX_SEGMENT_CHARS, clean, digest_to_segments, item_to_segments, split_long


class SpeechTextTests(unittest.TestCase):
    def test_clean_removes_urls_and_markdown(self):
        self.assertEqual(clean("**重要**：见 https://ex.com/a?b=1 `代码`"), "重要：见 代码")

    def test_split_long_respects_limit(self):
        text = "甲" * 100 + "。" + "乙" * 300
        parts = split_long(text)
        self.assertTrue(all(len(p) <= MAX_SEGMENT_CHARS for p in parts))
        self.assertEqual("".join(parts), text)

    def test_digest_to_segments_covers_overview_sections_and_items(self):
        digest = {
            "summary": {"new_items": 3, "source_count": 2, "error_count": 1},
            "errors": [{"source_id": "hot_a", "error": "超时"}],
            "sections": [{
                "name": "热点社区",
                "sources": [{
                    "source_name": "热榜A",
                    "hot_changes": {"new": [{"title": "新热点"}], "rising": [{"title": "上升热点"}], "falling": []},
                    "items": [{"title": "条目一"}],
                }],
            }],
        }
        segs = digest_to_segments(digest)
        kinds = [s["kind"] for s in segs]
        self.assertEqual(kinds[0], "overview")
        self.assertIn("error", kinds)
        self.assertIn("section", kinds)
        joined = "".join(s["text"] for s in segs)
        self.assertIn("新进：新热点", joined)
        self.assertIn("上升：上升热点", joined)
        self.assertIn("条目一", joined)

    def test_item_segments_include_summary(self):
        segs = item_to_segments({"title": "标题", "summary": "摘要内容"})
        self.assertIn("标题。摘要内容", segs[0]["text"])


if __name__ == "__main__":
    unittest.main()
