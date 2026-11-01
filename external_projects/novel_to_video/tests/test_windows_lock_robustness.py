"""Windows 上文件被短暂占用（WinError 5）时：重试；心跳失败不中断运行；后台异常进入日志。"""
import json
import os
import time
from pathlib import Path

import pytest

from novel_to_video import joblog, project_store as ps, runlock, runtime
from novel_to_video.project_store import write_json_atomic


def test_replace_retries_then_succeeds(tmp_path, monkeypatch):
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "拒绝访问", str(dst))
        return real(src, dst)
    monkeypatch.setattr(ps.os, "replace", flaky)
    write_json_atomic(tmp_path / "x.json", {"a": 1})
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": 1}
    assert calls["n"] == 3


def test_replace_gives_up_after_retries(tmp_path, monkeypatch):
    def always(src, dst):
        raise PermissionError(5, "拒绝访问", str(dst))
    monkeypatch.setattr(ps.os, "replace", always)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        ps.replace_with_retry(tmp_path / "a", tmp_path / "b")


def _lock_for_this_process(p: Path):
    (p / runlock.LOCK_FILE).write_text(json.dumps({"pid": os.getpid(), "heartbeat": 0}), encoding="utf-8")


def test_heartbeat_returns_false_instead_of_raising(tmp_path, monkeypatch):
    _lock_for_this_process(tmp_path)
    def always(src, dst):
        raise PermissionError(5, "拒绝访问", str(dst))
    monkeypatch.setattr(ps.os, "replace", always)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert runlock.heartbeat(tmp_path) is False          # 不抛异常，运行可以继续


def test_heartbeat_succeeds_normally(tmp_path):
    _lock_for_this_process(tmp_path)
    assert runlock.heartbeat(tmp_path) is True
    assert json.loads((tmp_path / runlock.LOCK_FILE).read_text(encoding="utf-8"))["heartbeat"] > 0


def test_background_failure_reaches_job_log(tmp_path):
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    (tmp_path / "logs").mkdir()

    def run_fn():
        raise PermissionError(5, "拒绝访问", "x")
    r = runtime.start_background(tmp_path, [1], "G0", run_fn)
    assert r["ok"]
    for _ in range(100):
        if runtime.read_state(tmp_path).get("error"):
            break
        time.sleep(0.05)
    msgs = [x for x in joblog.tail(tmp_path, scopes=["queue"], limit=50) if x["level"] == "error"]
    assert any("PermissionError" in x["message"] and "拒绝访问" in x["message"] for x in msgs), msgs
