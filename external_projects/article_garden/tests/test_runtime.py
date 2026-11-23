"""后台运行的可靠性回归测试：陈旧停止信号、停止的时效、单次调用超时、单篇超时不拖垮整批、真实的运行状态。全部假模型。"""

import time

import pytest

from article_garden import budget, cancel, domains, joblog
from article_garden.digest import runner as drunner
from article_garden.library import catalog, importer
from tests.fakes import article, good_reply


class Echo:
    def __init__(self, delay=None, delay_for=None, on_call=None):
        self.delay = delay
        self.delay_for = delay_for
        self.on_call = on_call
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        if self.delay and (self.delay_for is None or self.delay_for in prompt):
            time.sleep(self.delay)
        return good_reply(prompt)


def _library(data_home, n=2):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    files = [(f"文章{i}.md", article(f"文章{i}", f"标记{i}").encode("utf-8")) for i in range(1, n + 1)]
    importer.apply(ctx, importer.collect(files))
    return ctx


# ------------------------------------------------------------------ 停止信号

def test_stale_stop_request_does_not_stop_a_new_run(data_home):
    ctx = _library(data_home, 1)
    budget.request_stop(ctx)                                  # 上一次运行遗留的停止请求
    time.sleep(0.01)
    g = budget.guard(ctx, Echo())                             # 新的运行，开始于停止请求之后
    assert g.ask("<article>\n" + "x") != ""                   # 不会被旧请求打断


def test_stop_requested_during_a_run_stops_its_next_call(data_home):
    ctx = _library(data_home, 1)
    g = budget.guard(ctx, Echo())
    budget.request_stop(ctx)                                  # 运行开始之后发出的停止
    with pytest.raises(cancel.Cancelled):
        g.ask("p")


def test_digest_run_stops_promptly_and_records_stopped(data_home):
    ctx = _library(data_home, 2)
    llm = Echo(on_call=lambda n: budget.request_stop(ctx) if n == 1 else None)
    g = budget.guard(ctx, llm)
    with pytest.raises(cancel.Cancelled):
        drunner.run_digest(ctx, g, force=True)
    assert llm.calls == 1                                     # 第一次调用后即停，第二篇没有发起调用
    rows = joblog.tail(ctx.root, limit=200)
    ends = [r for r in rows if r.get("event") == "run_end" and r.get("run") == "文章摘要"]
    assert ends and ends[-1]["status"] == "stopped"
    assert joblog.running_runs(ctx.root) == []                # 停止之后不再显示为运行中


# ------------------------------------------------------------------ 超时

def test_timeout_applies_per_call_not_to_the_whole_batch(data_home):
    ctx = _library(data_home, 1)
    g = budget.GuardedLLM(Echo(), ctx, timeout_sec=0.3)
    time.sleep(0.5)                                           # 旧实现：创建后经过的时间已超过时限
    assert g.ask("<article>\n" + "x") != ""                   # 新实现：每次调用各自计时，不受影响


def test_one_article_timing_out_does_not_abort_the_batch(data_home):
    ctx = _library(data_home, 2)
    slow_first = Echo(delay=1.0, delay_for="标记1")          # 第一篇的调用会超时
    g = budget.GuardedLLM(slow_first, ctx, timeout_sec=0.3)
    counts = drunner.run_digest(ctx, g, force=True)
    assert counts["failed"] == 1 and counts["ok"] == 1        # 第二篇照常完成
    rows = catalog.list_articles(ctx)
    assert {drunner.load_digest(ctx, r["id"])["status"] for r in rows} == {"failed", "ok"}


# ------------------------------------------------------------------ 真实的运行状态

def test_running_state_comes_from_run_records_not_time_heuristics(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    rid = joblog.run_start(ctx.root, "digest", "文章摘要")
    running = joblog.running_runs(ctx.root)
    assert [r["run_id"] for r in running] == [rid] and joblog.is_running(ctx.root, scopes=("digest",))
    joblog.run_end(ctx.root, "digest", rid, "文章摘要", status="stopped")
    assert joblog.running_runs(ctx.root) == []                # 结束记录写入后立即不再运行中
    assert not joblog.is_running(ctx.root)


def test_dead_process_run_is_not_reported_as_running(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    joblog.log(ctx.root, "digest", "文章摘要：开始", event="run_start", run="文章摘要",
               run_id="deadbeef", started_at="2026-11-23T00:00:00", pid=999999)
    assert joblog.running_runs(ctx.root) == []                # 进程已不存在：只是“中断”的历史


def test_run_context_manager_records_failure_and_stop(data_home):
    ctx = domains.create_domain("ai", "人工智能", "ai")
    with pytest.raises(ValueError):
        with joblog.run(ctx.root, "plan", "缺口与选题"):
            raise ValueError("坏了")
    with pytest.raises(cancel.Cancelled):
        with joblog.run(ctx.root, "plan", "缺口与选题"):
            raise cancel.Cancelled("用户请求停止")
    ends = [r["status"] for r in joblog.tail(ctx.root, limit=50) if r.get("event") == "run_end"]
    assert ends == ["failed", "stopped"] and joblog.running_runs(ctx.root) == []
