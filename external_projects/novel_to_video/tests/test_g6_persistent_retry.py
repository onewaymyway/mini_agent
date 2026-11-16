"""G6 不跳过失败章节：失败持续重试，直到成功 / 用户停止 / 预算用尽 / 达到单章重试上限；
后几种情况整个队列停下，后面的章节不会开始。G5 的有限重试行为不变（见 test_phase4）。"""
import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import auto_prepare as ap
from novel_to_video import batch, gates, tool_api
from novel_to_video import cancel
from novel_to_video import runtime as rt
from novel_to_video.providers.image import StubImageProvider
from test_g6_full_auto import BUDGET, CH, FakeLLM, _run_one


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """重试间隔不真的等待；记录每次等待的秒数。"""
    waits = []
    monkeypatch.setattr(batch, "_sleep", lambda sec: waits.append(sec))
    return waits


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1, 2))
    for n in (1, 2):
        (src / "正文" / f"第{n:03d}章 测试章{n}.md").write_text(CH.format(n=n), encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    return Path(res["data"]["project_dir"])


def _flaky(fail_times, ok_status="done"):
    calls = {"n": 0}

    def run_one(no):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            return {"status": "failed", "message": "镜头规划校验未通过：模拟"}
        return {"status": ok_status}
    return run_one, calls


def test_g6_keeps_retrying_far_beyond_old_limit(proj, no_sleep):
    """过去 G6 重试 3 次就放弃并跳到下一章；现在失败 12 次后仍会继续重试直到成功。"""
    assert tool_api.set_gear(str(proj), "G6", BUDGET)["ok"]
    run_one, calls = _flaky(12)
    res = batch.run_queue(proj, [1], gear="G6", run_one=run_one)
    assert res["halted"] is None
    assert res["results"][1]["status"] == "done"
    assert calls["n"] == 13
    assert res["results"][1]["recovery"]["attempts"] == 12


def test_g6_backoff_grows_and_is_capped(proj, no_sleep):
    assert tool_api.set_gear(str(proj), "G6", BUDGET)["ok"]
    run_one, _ = _flaky(20)
    batch.run_queue(proj, [1], gear="G6", run_one=run_one)
    total = sum(no_sleep)
    assert total > 0 and max(no_sleep) <= gates.RETRY_BACKOFF_MAX_SEC
    # 每次重试前的等待：5、10、…、60、60…（sleep 以 0.5 秒为片段，按重试次数聚合）
    assert gates.retry_backoff_sec(1) == gates.RETRY_BACKOFF_BASE_SEC
    assert gates.retry_backoff_sec(1000) == gates.RETRY_BACKOFF_MAX_SEC


def test_g6_does_not_skip_failed_chapter_when_cap_reached(proj):
    """配置了单章重试上限：达到上限后队列停下，第 2 章不会开始（不是跳过）。"""
    assert tool_api.set_gear(str(proj), "G6", {**BUDGET, "max_chapter_retries": 4})["ok"]
    seen = []

    def run_one(no):
        seen.append(no)
        return {"status": "failed", "message": "镜头规划校验未通过：模拟"}

    res = batch.run_queue(proj, [1, 2], gear="G6", run_one=run_one)
    assert seen == [1] * 5                                       # 首次 + 4 次重试，没有碰第 2 章
    assert 2 not in res["results"]
    assert res["halted"] and "已重试 4 次仍失败" in res["halted"] and "第 1 章" in res["halted"]


def test_g6_stops_queue_when_budget_runs_out_mid_retry(proj, monkeypatch):
    """章内重试期间耗时实时计入预算：预算用尽立即停下并说明原因，而不是无限重试或跳到下一章。"""
    assert tool_api.set_gear(str(proj), "G6", {**BUDGET, "max_minutes": 1})["ok"]
    clock = {"t": 1000.0}
    monkeypatch.setattr(batch.time, "monotonic", lambda: clock["t"])
    seen = []

    def run_one(no):
        seen.append(no)
        clock["t"] += 30.0                                       # 每次尝试耗时 30 秒
        return {"status": "failed", "message": "模拟失败"}

    res = batch.run_queue(proj, [1, 2], gear="G6", run_one=run_one)
    assert seen == [1, 1]                                         # 第 2 次尝试结束时已满 60 秒，预算（1 分钟）用尽
    assert 2 not in res["results"]
    assert "预算已用尽" in res["halted"] and "第 1 章仍未成功" in res["halted"]


def test_g6_stop_request_interrupts_retry_loop(proj):
    assert tool_api.set_gear(str(proj), "G6", BUDGET)["ok"]
    seen = []

    def run_one(no):
        seen.append(no)
        if len(seen) == 2:
            rt.request_stop(proj)                                 # 用户在第二次尝试期间点了停止
        return {"status": "failed", "message": "模拟失败"}

    res = batch.run_queue(proj, [1, 2], gear="G6", run_one=run_one)
    assert seen == [1, 1]
    assert res["halted"] == cancel.STOP_MESSAGE
    assert 2 not in res["results"]


def test_g6_retried_chapter_then_waiting_assets_is_prepared(proj):
    """失败重试成功后紧接着遇到缺素材，也会自动补素材并重跑，不会停在半途。"""
    assert tool_api.set_gear(str(proj), "G6", BUDGET)["ok"]
    real = _run_one(proj)
    state = {"n": 0}

    def run_one(no):
        state["n"] += 1
        if state["n"] <= 2:
            return {"status": "failed", "message": "镜头规划校验未通过：模拟"}
        return real(no)                                           # 之后真实流水线：缺定妆图 → 补素材 → 完成

    prepare = lambda no: ap.prepare_chapter_assets(proj, no, FakeLLM(), StubImageProvider())
    res = batch.run_queue(proj, [1], gear="G6", run_one=run_one, prepare=prepare)
    assert res["halted"] is None
    assert res["results"][1]["status"] == "done"
    assert res["results"][1]["auto_prepared"]["progress"] is True


def test_g6_prepare_exception_is_a_failure_not_a_crash(proj):
    """自动补素材抛异常（例如引擎初始化失败）按本章失败处理并继续重试，不会让整个队列线程崩溃。"""
    assert tool_api.set_gear(str(proj), "G6", {**BUDGET, "max_chapter_retries": 2})["ok"]

    def prepare(no):
        raise RuntimeError("引擎初始化失败")

    res = batch.run_queue(proj, [1], gear="G6", run_one=_run_one(proj), prepare=prepare)
    assert res["results"][1]["status"] == "failed"
    assert "自动补素材出错" in res["results"][1]["message"]
    assert res["halted"] and "已重试 2 次" in res["halted"]


def test_g5_still_has_fixed_limit_and_does_not_halt(proj):
    """对照：G5 仍是有限重试，失败后记录并继续后面的章节（行为不变）。"""
    assert tool_api.set_gear(str(proj), "G5", BUDGET)["ok"]
    seen = []

    def run_one(no):
        seen.append(no)
        return {"status": "failed", "message": "模拟失败"}

    res = batch.run_queue(proj, [1, 2], gear="G5", run_one=run_one)
    assert seen == [1] * 4 + [2] * 4
    assert res["halted"] is None
    assert "g6_halt" not in res["results"][1]
