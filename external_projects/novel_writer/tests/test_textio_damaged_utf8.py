"""textio：局部损坏的 UTF-8 文件不得被整体转码（否则正文被改成乱码）。"""

import tempfile
import unittest
from pathlib import Path

from novel_writer.textio import TextEncodingError, drain_repairs, read_text_utf8

SAMPLE = ("第十六章 夜雨\n\n林晚站在码头上，望着远处的灯火。她已经等了三个小时，船迟迟没有靠岸。") * 30


class DamagedUtf8Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        drain_repairs()

    def tearDown(self):
        self._tmp.cleanup()

    def test_utf8_with_few_damaged_bytes_is_not_transcoded(self):
        data = bytearray(SAMPLE.encode("utf-8"))
        data[500] = 0xC3  # 单字节损坏
        p = self.dir / "ch16.md"
        p.write_bytes(bytes(data))
        with self.assertRaises(TextEncodingError) as cm:
            read_text_utf8(p)
        self.assertIn("局部损坏", str(cm.exception))
        self.assertEqual(p.read_bytes(), bytes(data), "原文件必须保持不动")
        self.assertEqual(drain_repairs(), [], "不应记录任何自动转存")

    def test_real_gbk_file_is_still_converted(self):
        p = self.dir / "gbk.md"
        p.write_bytes(SAMPLE.encode("gbk"))
        self.assertTrue(read_text_utf8(p).startswith("第十六章"))
        self.assertEqual(p.read_bytes().decode("utf-8")[:4], "第十六章")

    def test_real_utf16_file_is_still_converted(self):
        p = self.dir / "utf16.md"
        p.write_bytes(SAMPLE.encode("utf-16"))
        self.assertTrue(read_text_utf8(p).startswith("第十六章"))


if __name__ == "__main__":
    unittest.main()
