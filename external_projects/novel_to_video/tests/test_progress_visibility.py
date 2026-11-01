"""进度可见性：长阶段有“仍在进行”心跳；引擎初始化失败进入章节日志与章节状态。"""
import time
from pathlib import Path

import pytest

from novel_to_video import chapter_io as cio
from novel_to_video import joblog
from novel_to_video import tool_api
from novel_to_video.providers.factory import ProviderError
from test_failure_reasons import proj  # noqa: F401  (fixture)


def _chapter_msgs(p):
    return [x["message"] for x in joblog.tail(p, scopes=["chapter"], limit=500)]


def test_heartbeat_reports_progress_while_blocked(proj):
    with joblog.heartbeat(proj, "chapter", "测试阶段", every=0.05):
        time.sleep(0.2)
    msgs = _chapter_msgs(proj)
    assert any("测试阶段：开始" in m for m in msgs)
    assert any("测试阶段：仍在进行" in m for m in msgs), msgs
    assert any("测试阶段：完成" in m for m in msgs)


def test_heartbeat_logs_error_end_and_reraises(proj):
    with pytest.raises(ValueError):
        with joblog.heartbeat(proj, "chapter", "坏阶段", every=10):
            raise ValueError("boom")
    errs = [x for x in joblog.tail(proj, scopes=["chapter"], limit=50) if x["level"] == "error"]
    assert any("坏阶段：异常结束" in x["message"] for x in errs)


def test_heartbeat_stops_after_block(proj):
    with joblog.heartbeat(proj, "chapter", "短阶段", every=0.02):
        pass
    before = len(_chapter_msgs(proj))
    time.sleep(0.1)
    assert len(_chapter_msgs(proj)) == before                 # 结束后不再写心跳


def test_provider_setup_failure_reaches_chapter_log_and_status(proj, monkeypatch):
    def _bad(_):
        raise ProviderError("未找到可用的 Agnes API key（图像）")
    monkeypatch.setattr(tool_api, "_providers_for", _bad)
    r = tool_api.run_chapter(str(proj), 1)
    assert not r["ok"] and "初始化引擎失败" in r["error"]["message"]
    assert any("初始化引擎失败" in m and "Agnes" in m for m in _chapter_msgs(proj))
    row = cio.chapter_row(proj, 1)                 # 章节状态与原因写在章节索引里，界面读的就是它
    assert row["status"] == "failed" and "初始化引擎失败" in row["message"] and "Agnes" in row["message"]


def test_unexpected_provider_error_is_logged_with_traceback(proj, monkeypatch):
    def _boom(_):
        raise TypeError("bad config")
    monkeypatch.setattr(tool_api, "_providers_for", _boom)
    r = tool_api.run_chapter(str(proj), 1)
    assert not r["ok"]
    errs = [x["message"] for x in joblog.tail(proj, scopes=["chapter"], limit=100) if x["level"] == "error"]
    assert any("TypeError" in m and "bad config" in m for m in errs), errs
