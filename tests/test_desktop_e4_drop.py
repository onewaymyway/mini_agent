"""E4 拖文件到面板：暂存规则、消息构造、过期清理、面板发送时带上批次（离屏 Qt）。"""

import os
import time

import httpx
import pytest

from mini_agent_desktop.drop_stage import (
    build_message, cleanup_old, stage_paths,
)


def _write(p, text="hello"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_files_are_copied_and_originals_untouched(tmp_path):
    src = _write(tmp_path / "src" / "a.txt", "原文")
    base = tmp_path / "drop"
    batch = stage_paths([src], base)
    assert batch.dir is not None and batch.dir.parent == base
    copied = batch.dir / "a.txt"
    assert copied.read_text(encoding="utf-8") == "原文"
    assert src.read_text(encoding="utf-8") == "原文"       # 原文件未被移动或修改
    assert [f.rel for f in batch.files] == ["a.txt"]


def test_folder_structure_is_preserved(tmp_path):
    folder = tmp_path / "proj"
    _write(folder / "x.md")
    _write(folder / "sub" / "y.py")
    batch = stage_paths([folder], tmp_path / "drop")
    assert (batch.dir / "proj" / "x.md").exists()
    assert (batch.dir / "proj" / "sub" / "y.py").exists()
    assert sorted(f.rel for f in batch.files) == ["proj/sub/y.py", "proj/x.md"]


def test_same_name_top_level_items_do_not_overwrite(tmp_path):
    a = _write(tmp_path / "1" / "note.txt", "one")
    b = _write(tmp_path / "2" / "note.txt", "two")
    batch = stage_paths([a, b], tmp_path / "drop")
    names = sorted(f.rel for f in batch.files)
    assert names == ["note (2).txt", "note.txt"]
    assert (batch.dir / "note.txt").read_text(encoding="utf-8") == "one"


def test_file_count_limit_skips_extra(tmp_path):
    paths = [_write(tmp_path / f"f{i}.txt") for i in range(5)]
    batch = stage_paths(paths, tmp_path / "drop", max_files=3)
    assert len(batch.files) == 3
    assert len(batch.skipped) == 2 and "数量上限" in batch.skipped[0][1]


def test_size_limit_skips_large_file(tmp_path):
    small = _write(tmp_path / "s.txt", "ab")
    big = _write(tmp_path / "b.txt", "x" * 100)
    batch = stage_paths([small, big], tmp_path / "drop", max_total_bytes=10)
    assert [f.rel for f in batch.files] == ["s.txt"]
    assert any("总大小" in reason for _p, reason in batch.skipped)


def test_missing_path_is_reported_and_no_empty_dir_left(tmp_path):
    base = tmp_path / "drop"
    batch = stage_paths([tmp_path / "nope.txt"], base)
    assert batch.dir is None and batch.files == []
    assert batch.skipped and batch.skipped[0][1] == "不存在"
    assert not base.exists() or not any(base.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="符号链接在 Windows 上需要权限")
def test_symlinks_are_not_followed(tmp_path):
    target = _write(tmp_path / "real.txt")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    batch = stage_paths([link], tmp_path / "drop")
    assert batch.dir is None and batch.files == []
    assert "符号链接" in batch.skipped[0][1]


def test_build_message_points_agent_at_directory(tmp_path):
    src = _write(tmp_path / "报告.md", "内容")
    batch = stage_paths([src], tmp_path / "drop")
    msg = build_message("帮我总结一下", batch)
    assert str(batch.dir) in msg
    assert "报告.md" in msg and "副本" in msg
    assert msg.endswith("帮我总结一下")


def test_build_message_without_files_returns_user_text():
    from mini_agent_desktop.drop_stage import StagedBatch
    assert build_message("你好", StagedBatch()) == "你好"


def test_cleanup_removes_only_old_batch_directories(tmp_path):
    base = tmp_path / "drop"
    old = base / "old-batch"
    new = base / "new-batch"
    old.mkdir(parents=True)
    new.mkdir()
    (base / "stray.txt").write_text("keep", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 48 * 3600, now - 48 * 3600))
    removed = cleanup_old(base, max_age_hours=24, now=now)
    assert removed == 1
    assert not old.exists() and new.exists() and (base / "stray.txt").exists()


def test_cleanup_handles_missing_root(tmp_path):
    assert cleanup_old(tmp_path / "missing") == 0


def test_panel_sends_attachment_path_with_message(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.api import DaemonApi
    from mini_agent_desktop.drop_stage import stage_paths as stage
    from mini_agent_desktop.quick import QuickSession
    from mini_agent_desktop.quick_panel import QuickPanel

    app = QApplication.instance() or QApplication([])
    sent = []

    def handler(request):
        if request.method == "POST" and request.url.path == "/v1/chat":
            import json
            sent.append(json.loads(request.content)["message"])
            return httpx.Response(200, json={"turn_id": "t1", "queued": True})
        if request.url.path == "/v1/turns/t1":
            return httpx.Response(200, json={"turn_id": "t1", "state": "done"})
        if request.url.path == "/v1/history":
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(200, json={"turns": []})

    def factory():
        return QuickSession(DaemonApi("http://x", "tok", transport=httpx.MockTransport(handler)))

    src = _write(tmp_path / "data.csv", "a,b")
    panel = QuickPanel(factory, drop_root=tmp_path / "drop")
    panel._on_staged(stage([src], tmp_path / "drop"))
    assert panel._batch is not None and len(panel._batch.files) == 1

    panel.input.setPlainText("分析一下")
    panel._submit()
    deadline = time.monotonic() + 10
    while not sent and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert sent, "消息应已发送"
    assert "data.csv" in sent[0] and "分析一下" in sent[0]
    assert panel._batch is None
    panel.hide()
