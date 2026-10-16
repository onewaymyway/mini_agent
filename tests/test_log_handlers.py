"""tests/test_log_handlers.py — SafeRotatingFileHandler 单元测试

覆盖：正常写入/轮转、写完不持有句柄、轮转遇 PermissionError 时重试并跳过且
不丢记录/不破坏备份链、冷却期、回调上报、多线程写入。
Windows 的 WinError 32 在 Linux 上无法真实复现，这里用 monkeypatch os.replace 模拟。
"""

from __future__ import annotations

import logging
import os
import threading

import pytest

from mini_agent.log_handlers import SafeRotatingFileHandler


def _make(path, **kw):
    failures = []
    h = SafeRotatingFileHandler(
        path, maxBytes=kw.pop("maxBytes", 200), backupCount=kw.pop("backupCount", 3),
        rotate_retry_delay=0, open_retry_delay=0,
        on_failure=lambda w, e, t: failures.append((w, e, t)), **kw,
    )
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.Logger("t_" + os.path.basename(str(path)))
    lg.addHandler(h)
    return lg, h, failures


def test_write_does_not_hold_handle(tmp_path):
    p = tmp_path / "e.jsonl"
    lg, h, _ = _make(p)
    lg.error("hello")
    assert h.stream is None  # 写完即关，不保存流
    assert p.read_text(encoding="utf-8") == "hello\n"
    p.rename(tmp_path / "moved.jsonl")  # 无持有句柄，随时可改名


def test_normal_rotation_keeps_backups(tmp_path):
    p = tmp_path / "e.jsonl"
    lg, _, failures = _make(p, maxBytes=100, backupCount=2)
    for i in range(30):
        lg.error("x" * 20 + str(i))
    assert (tmp_path / "e.jsonl.1").exists()
    assert (tmp_path / "e.jsonl.2").exists()
    assert not (tmp_path / "e.jsonl.3").exists()
    assert not list(tmp_path.glob("*.rolling"))
    assert failures == []


def test_rotation_permission_error_skips_and_keeps_records(tmp_path, monkeypatch):
    p = tmp_path / "e.jsonl"
    lg, h, failures = _make(p, maxBytes=100, backupCount=3)
    # 先造出一个备份，用来验证失败时备份链不被破坏
    (tmp_path / "e.jsonl.1").write_text("OLD1\n", encoding="utf-8")
    lg.error("a" * 90)

    real_replace = os.replace

    def fake_replace(src, dst):
        if str(src) == str(p):
            raise PermissionError(32, "另一个程序正在使用此文件")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)
    for i in range(5):
        lg.error(f"record-{i}")  # 本应触发轮转，但被拒绝

    content = p.read_text(encoding="utf-8")
    assert all(f"record-{i}" in content for i in range(5))  # 记录不丢
    assert (tmp_path / "e.jsonl.1").read_text(encoding="utf-8") == "OLD1\n"  # 备份未被动
    assert not (tmp_path / "e.jsonl.2").exists()
    assert len(failures) == 1  # 冷却期内只上报一次
    assert isinstance(failures[0][1], PermissionError)


def test_rotation_retries_then_succeeds(tmp_path, monkeypatch):
    p = tmp_path / "e.jsonl"
    lg, _, failures = _make(p, maxBytes=100, backupCount=2)
    lg.error("a" * 90)
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        if str(src) == str(p) and calls["n"] < 2:
            calls["n"] += 1
            raise PermissionError(32, "busy")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    lg.error("b" * 20)
    assert (tmp_path / "e.jsonl.1").exists()
    assert failures == []


def test_cooldown_expires_and_rotation_resumes(tmp_path, monkeypatch):
    p = tmp_path / "e.jsonl"
    lg, h, failures = _make(p, maxBytes=100, backupCount=2, rotate_cooldown=0.0)
    lg.error("a" * 90)
    real_replace = os.replace
    block = {"on": True}

    def fake(src, dst):
        if block["on"] and str(src) == str(p):
            raise PermissionError(32, "busy")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake)
    lg.error("b" * 20)
    assert failures
    block["on"] = False
    lg.error("c" * 20)
    assert (tmp_path / "e.jsonl.1").exists()


def test_write_failure_reports_original_record(tmp_path, monkeypatch):
    p = tmp_path / "e.jsonl"
    lg, h, failures = _make(p, maxBytes=0)
    monkeypatch.setattr(h, "_append", lambda text: (_ for _ in ()).throw(PermissionError("denied")))
    monkeypatch.setattr(h, "handleError", lambda record: None)
    lg.error("important")
    assert failures and failures[0][2] == "important"


def test_threaded_writes_no_loss(tmp_path):
    p = tmp_path / "e.jsonl"
    lg, _, _ = _make(p, maxBytes=0)

    def work(k):
        for i in range(50):
            lg.error(f"t{k}-{i}")

    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(p.read_text(encoding="utf-8").splitlines()) == 200
