"""乱码自愈：修改循环写出乱码 → 回滚；无法回滚 → 从写作步骤重走；检测本身只读。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novel_writer import chapter_ops as C
from novel_writer import steps as S
from novel_writer.garble import is_garbled
from novel_writer.runner import RestartChain
from novel_writer.textio import read_text_utf8

GOOD = ("第十六章 裂缝之后\n\n林晚站在码头上，望着远处的灯火。她已经等了三个小时，船迟迟没有靠岸。"
        "陈伯说过，今晚的货必须在天亮前运走。她把手插进外套口袋，摸到那张纸条。\n") * 20
GOOD_REVISED = GOOD + "（修改后）\n"


def _mojibake(text: str) -> str:
    """模拟真实事故：UTF-8 字节被按 GBK 解码后再以 UTF-8 存盘（双重编码）。"""
    return text.encode("utf-8").decode("gbk", errors="replace").encode("utf-8").decode("utf-8")


class _WS:
    def __init__(self, root: Path):
        self.root = root

    def path(self, name, nn=None):
        return self.root / f"{name}_{nn}.md"


class SelfHealTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = _WS(self.root)
        self.chapter = self.root / "第16章 裂缝之后.md"
        self.logs = []
        patches = [
            mock.patch.object(C, "chapter_file", lambda ws, n: self.chapter),
            mock.patch.object(C, "_log", lambda ws, n, msg: self.logs.append(msg)),
            mock.patch.object(C, "_refresh_check_steps", lambda *a, **k: None),
            mock.patch.object(C, "_review_step", lambda n: S.StepDef(
                id="review", title="核查", stage="s", category=S.CAT_CHAPTER_REVIEW,
                executor=S.EXECUTOR_SKILL, skill="chapter-writer", chapter=n)),
            mock.patch.object(C, "write_machine_report", lambda ws, n, settings: {"hard": []}),
            # 正文里没有修改标记时仍有 🔴 问题，修改后清零（模拟写作修好了问题）
            mock.patch.object(C, "red_items", lambda text: (
                [] if "（修改后）" in self.chapter.read_text(encoding="utf-8", errors="replace") else ["问题"])),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _writer_script(self, outputs):
        """每次 chapter-writer 调用依次写入 outputs 中的一项；review 调用不改正文。"""
        calls = iter(outputs)

        def fake_run_skill_step(ws, step, **kwargs):
            if step.skill == "chapter-writer" and ".revise.r" in step.id:
                self.chapter.write_text(next(calls), encoding="utf-8")
            return None

        return mock.patch("novel_writer.runner.run_skill_step", side_effect=fake_run_skill_step)

    def test_detection_is_read_only_for_damaged_utf8(self):
        data = bytearray(GOOD.encode("utf-8"))
        data[300] = 0xC3
        self.chapter.write_bytes(bytes(data))
        self.assertTrue(C._is_garbled_file(self.chapter))
        self.assertEqual(self.chapter.read_bytes(), bytes(data), "检测不得改写文件")

    def test_sample_mojibake_is_recognized(self):
        self.assertTrue(is_garbled(_mojibake(GOOD)).garbled)
        self.assertFalse(is_garbled(GOOD).garbled)

    def test_garbled_round_rolls_back_and_revision_continues(self):
        self.chapter.write_text(GOOD, encoding="utf-8")
        with self._writer_script([_mojibake(GOOD), GOOD_REVISED]):
            result = C.revise_cycle(self.ws, 16, stage="s", invoker=None, settings={},
                                    id_prefix="ch16")
        self.assertTrue(result["clean"])
        self.assertEqual(read_text_utf8(self.chapter), GOOD_REVISED)
        self.assertTrue(any("已回滚到上一版" in m for m in self.logs))

    def test_repeated_garbage_restarts_chapter_chain(self):
        self.chapter.write_text(GOOD, encoding="utf-8")
        bad = _mojibake(GOOD)
        with self._writer_script([bad, bad, bad]):
            with self.assertRaises(RestartChain) as cm:
                C.revise_cycle(self.ws, 16, stage="s", invoker=None, settings={}, id_prefix="ch16")
        self.assertEqual(cm.exception.step_ids, C.chapter_chain_ids(16))
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), read_text_utf8(self.chapter))

    def test_already_garbled_chapter_restarts_without_writing(self):
        self.chapter.write_text(_mojibake(GOOD), encoding="utf-8")
        with self._writer_script([GOOD]) as writer:
            with self.assertRaises(RestartChain):
                C.revise_cycle(self.ws, 16, stage="s", invoker=None, settings={}, id_prefix="ch16")
        writer.assert_not_called()

    def test_chain_ids_follow_writing_step(self):
        ids = C.chapter_chain_ids(16)
        self.assertEqual(ids[0], S.chapter_id(16, "write_chapter"))
        self.assertEqual(ids[-1], S.chapter_id(16, "revise_loop"))



class RunnerRestartTests(unittest.TestCase):
    """端到端：RestartChain 由 run_step 接住后，第 16 章从写作步骤起的整条链全部回到待运行。"""

    def setUp(self):
        import os
        from novel_writer import config, tool_api
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(config, "NOVELS_DIR", tmp / "novels"),
            mock.patch.object(config, "SHELF_INDEX_FILE", tmp / "shelf.json"),
            mock.patch.dict(os.environ, {"NOVEL_WRITER_NO_SPAWN": "1"}),
        ]
        for p in self._patches:
            p.start()
        r = tool_api.create_novel("乱码自愈测试", novels_dir=str(tmp / "novels"),
                                  index_file=str(tmp / "shelf.json"), gear="G3", total_chapters_planned=20)
        from novel_writer.workspace import Workspace
        self.ws = Workspace(r["data"]["workspace"])

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_restart_resets_whole_chapter_chain_and_keeps_others(self):
        from novel_writer import runner, state as st
        from novel_writer import steps as S

        ids = C.chapter_chain_ids(16)
        for sid in ids:
            st.update_step(self.ws, sid, status=st.ST_PASSED)
        st.update_step(self.ws, S.chapter_id(15, "archive"), status=st.ST_PASSED)

        def handler(ws, step, settings, invoker=None):
            raise RestartChain("第 16 章正文疑似乱码，从写作步骤重走", ids)

        step = S.StepDef(id=S.chapter_id(16, "revise_loop"), title="修改循环", stage="s",
                         category=S.CAT_CHAPTER_REVIEW, executor=S.EXECUTOR_PYTHON,
                         handler="restart_probe", chapter=16)
        status = runner.run_step(self.ws, step, invoker=None, settings={},
                                 handlers={"restart_probe": handler})

        self.assertEqual(status, st.ST_PENDING)
        for sid in ids:
            self.assertEqual(st.get_step_state(self.ws, sid)["status"], st.ST_PENDING, sid)
        self.assertEqual(st.get_step_state(self.ws, S.chapter_id(15, "archive"))["status"], st.ST_PASSED,
                         "上一章的步骤不应被重置")


if __name__ == "__main__":
    unittest.main()
