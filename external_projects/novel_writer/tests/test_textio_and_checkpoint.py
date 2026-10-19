"""非 UTF-8 文本自动转存（textio）与「进行中」运行记录（worker 每步 checkpoint）。"""

from __future__ import annotations

import pytest

from novel_writer import textio as TX
from novel_writer import state as st
from novel_writer import worker as W
from novel_writer.textio import TextEncodingError, read_text_utf8

TEXT = "第一章\r\n夜色很深，他推开了门。\n\n“你来了。”"
EXPECT = "第一章\n夜色很深，他推开了门。\n\n“你来了。”"


@pytest.fixture(autouse=True)
def _clean():
    TX.drain_repairs()
    yield
    TX.drain_repairs()


def test_plain_utf8_untouched(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes(TEXT.encode("utf-8"))
    assert read_text_utf8(p) == EXPECT
    assert p.read_bytes() == TEXT.encode("utf-8") and TX.drain_repairs() == []


@pytest.mark.parametrize("enc, raw", [
    ("utf-16-le", b"\xff\xfe" + TEXT.encode("utf-16-le")),
    ("utf-16-be", b"\xfe\xff" + TEXT.encode("utf-16-be")),
    ("utf-8-sig", b"\xef\xbb\xbf" + TEXT.encode("utf-8")),
    ("gb18030", TEXT.encode("gb18030")),
    ("utf-16-le", TEXT.encode("utf-16-le")),  # 无 BOM
])
def test_converts_to_utf8_in_place_with_backup(tmp_path, enc, raw):
    p = tmp_path / "第1章.md"
    p.write_bytes(raw)
    assert read_text_utf8(p) == EXPECT
    assert p.read_bytes() == EXPECT.encode("utf-8"), "原文件应被就地转存为 UTF-8（无 BOM）"
    assert (tmp_path / ("第1章.md" + TX.BACKUP_SUFFIX)).read_bytes() == raw
    rec = TX.drain_repairs()
    assert len(rec) == 1 and rec[0]["from"] == enc
    assert read_text_utf8(p) == EXPECT and TX.drain_repairs() == [], "转存后再读不再重复转存"


def test_undetectable_binary_raises_detailed_error_and_keeps_file(tmp_path):
    p = tmp_path / "bad.md"
    raw = b"\x80\x81\x00\x82abc\x00\x00\xff"
    p.write_bytes(raw)
    with pytest.raises(TextEncodingError) as ei:
        read_text_utf8(p)
    msg = str(ei.value)
    assert str(p) in msg and "文件头" in msg and "80 81" in msg and "UTF-8 读取失败" in msg and "处理建议" in msg
    assert p.read_bytes() == raw


def test_write_failure_reports_details_and_keeps_original(tmp_path, monkeypatch):
    p = tmp_path / "x.md"
    raw = b"\xff\xfe" + TEXT.encode("utf-16-le")
    p.write_bytes(raw)

    def boom(path, text):
        raise PermissionError("文件被占用")
    monkeypatch.setattr(TX, "atomic_write_text", boom)
    with pytest.raises(TextEncodingError) as ei:
        read_text_utf8(p)
    assert "转存为 UTF-8 失败" in str(ei.value) and "PermissionError" in str(ei.value) and "utf-16-le" in str(ei.value)
    assert p.read_bytes() == raw and TX.drain_repairs() == []


def test_missing_file_still_oserror(tmp_path):
    with pytest.raises(OSError):
        read_text_utf8(tmp_path / "none.md")


def test_checkpoint_writes_running_record_then_final_overrides(tmp_path, monkeypatch):
    from test_live_output import new_book, run_once
    from fakes import FakeInvoker

    ws = new_book(tmp_path, gear="G0")
    seen = []
    orig = W._checkpoint

    def spy(ws_, **kw):
        orig(ws_, **kw)
        seen.append(st.read_last_run(ws_))
    monkeypatch.setattr(W, "_checkpoint", spy)
    run_once(ws, FakeInvoker(ws.root))
    assert seen and seen[0]["running"] is True and seen[0]["finished_at"] is None and seen[0]["executed"]
    final = st.read_last_run(ws)
    assert final["running"] is False and final["finished_at"] and final["reason"]
