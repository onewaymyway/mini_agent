"""日志时间戳、心跳与图像调用超时：每行带墙上时钟，等待中定时心跳，超时放弃该格并继续。"""

import re
import time

from world_forge import engine, runlog
from world_forge.providers.image import ImageResult


class _Log:
    def __init__(self):
        self.entries = []

    def log(self, stage, msg, level="info", **data):
        self.entries.append({"stage": stage, "msg": msg, "level": level})


def test_each_log_line_starts_with_wall_clock_time():
    log = runlog.RunLog("wld_test", "demo")
    e = log.log("start", "一条日志")
    line = runlog.format_entry(e)
    assert re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3} \[\s*\d+\.\d{2}s\]", line), line


def test_heartbeat_logs_while_waiting_and_returns_value():
    log = _Log()

    def slow():
        time.sleep(0.12)
        return "done"

    value, exc, timed_out = runlog.call_with_heartbeat(slow, log, "测试调用", beat=0.03)
    assert value == "done" and exc is None and not timed_out
    assert any(e["stage"] == "wait" and "仍在等待" in e["msg"] for e in log.entries)


def test_timeout_gives_up_and_reports_it():
    log = _Log()
    value, exc, timed_out = runlog.call_with_heartbeat(lambda: time.sleep(5), log, "卡住的调用",
                                                       timeout=0.1, beat=0.03)
    assert timed_out and value is None and exc is None
    assert any("已放弃等待" in e["msg"] for e in log.entries)


def test_exception_is_returned_not_swallowed():
    value, exc, timed_out = runlog.call_with_heartbeat(lambda: 1 / 0, None, "出错的调用")
    assert isinstance(exc, ZeroDivisionError) and not timed_out


class HangingImage:
    name = "hang"
    is_stub = False

    def generate(self, prompt, save_path, *, ratio=None, reference_images=None):
        time.sleep(3)
        return ImageResult(True)


def test_image_call_that_hangs_is_recorded_as_failure_and_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_image_timeout", lambda: 0.1)
    monkeypatch.setattr(runlog, "HEARTBEAT_SECONDS", 0.03)
    log = _Log()
    res = engine._gen_image(HangingImage(), log, "第 1 页第 1 格", "x", tmp_path / "a.png")
    assert not res.ok and "未返回" in res.error
    stages = [e["stage"] for e in log.entries]
    assert "image" in stages and "wait" in stages


def test_image_call_logs_submit_and_finish_with_clock(tmp_path, img):
    log = _Log()
    res = engine._gen_image(img, log, "测试格", "a prompt", tmp_path / "b.png")
    assert res.ok
    msgs = [e["msg"] for e in log.entries if e["stage"] == "image"]
    assert any("提交图像请求" in m for m in msgs) and any("完成" in m and "用时" in m for m in msgs)


def test_run_log_is_readable_while_the_run_is_still_going(tmp_path, monkeypatch):
    """实时刷新依赖：每条日志写入时就落盘，运行中读取文件即可看到已有记录。"""
    from world_forge import repo
    monkeypatch.setattr(repo, "world_dir", lambda wid: tmp_path)
    log = runlog.RunLog("wld_live", "comic")
    log.log("start", "第一条")
    seen_first = runlog.read_run(str(log.path))
    log.log("image", "第二条")
    seen_second = runlog.read_run(str(log.path))
    assert [e["msg"] for e in seen_first] == ["第一条"]
    assert [e["msg"] for e in seen_second] == ["第一条", "第二条"]
