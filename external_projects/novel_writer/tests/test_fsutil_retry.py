"""Windows 文件占用的瞬时 PermissionError：写入/读取应重试；状态文件读失败不能被当作空状态。"""

from __future__ import annotations

import os

import pytest

from novel_writer import fsutil, state as st
from novel_writer.workspace import Workspace


def _flaky_replace(real, failures):
    calls = {"n": 0}

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(13, "拒绝访问", str(dst))
        return real(src, dst)

    return fake, calls


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    fake, calls = _flaky_replace(os.replace, failures=3)
    monkeypatch.setattr(fsutil.os, "replace", fake)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    target = tmp_path / "pipeline_state.json"
    fsutil.atomic_write_json(target, {"ok": 1})
    assert calls["n"] == 4
    assert fsutil.read_json(target) == {"ok": 1}
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]  # 临时文件已清理


def test_atomic_write_gives_up_after_retries_and_cleans_tmp(tmp_path, monkeypatch):
    fake, _ = _flaky_replace(os.replace, failures=999)
    monkeypatch.setattr(fsutil.os, "replace", fake)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        fsutil.atomic_write_json(tmp_path / "x.json", {"a": 1})
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_read_json_retries_transient_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text('{"a": 2}', encoding="utf-8")
    real_read = type(target).read_text
    calls = {"n": 0}

    def flaky_read(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "拒绝访问")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(type(target), "read_text", flaky_read)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    assert fsutil.read_json(target) == {"a": 2}


def test_load_state_raises_instead_of_returning_empty_when_file_unreadable(tmp_path):
    ws = Workspace(tmp_path / "novel")
    ws.path("studio_state").parent.mkdir(parents=True, exist_ok=True)
    ws.path("studio_state").write_text("{ 损坏", encoding="utf-8")
    with pytest.raises(st.StateReadError):
        st.load_state(ws)
