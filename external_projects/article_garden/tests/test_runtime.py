"""后台运行的可靠性回归测试：陈旧停止信号、停止的时效、单次调用超时、单篇超时不拖垮整批、真实的运行状态。全部假模型。"""

import os
import time

import pytest

from article_garden import budget, cancel, domains, joblog, store
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


def test_legacy_stop_flag_from_an_earlier_version_is_ignored(data_home):
    """旧版本写入的停止文件内容是 ISO 时间字符串；它只是陈旧记录，不得让新任务立即停止。"""
    ctx = _library(data_home, 1)
    p = ctx.path("logs", "stop.flag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("2026-11-23T14:50:00", encoding="utf-8")
    old = time.time() - 3600
    os.utime(p, (old, old))
    g = budget.guard(ctx, Echo())
    assert g.ask("<article>\n" + "x") != ""


def test_unreadable_or_odd_stop_content_does_not_stop_new_runs(data_home):
    ctx = _library(data_home, 1)
    p = ctx.path("logs", "stop.flag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe garbage")
    old = time.time() - 60
    os.utime(p, (old, old))
    assert budget.stop_requested(ctx, since=time.time()) is False


def test_runs_page_shows_stopping_only_for_a_stop_after_the_run_started(data_home):
    from tests.test_ui_smoke import run_app
    ctx = _library(data_home, 1)
    rid = joblog.run_start(ctx.root, "digest", "文章摘要")          # 正在运行（本进程）
    # 1) 遗留的停止文件（早于本次运行）：不得显示“正在停止”
    p = ctx.path("logs", "stop.flag")
    p.write_text("stop\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(p, (old, old))
    at = run_app("runs", domain="ai")
    assert not at.exception and not any("正在停止" in w.value for w in at.warning)
    # 2) 运行开始之后发出的停止请求：应显示
    budget.request_stop(ctx)
    at = run_app("runs", domain="ai")
    assert any("正在停止" in w.value for w in at.warning)
    joblog.run_end(ctx.root, "digest", rid, "文章摘要", status="stopped")


def test_stale_stop_file_is_removed_when_nothing_is_running(data_home):
    from tests.test_ui_smoke import run_app
    ctx = _library(data_home, 1)
    p = ctx.path("logs", "stop.flag")
    p.write_text("stop\n", encoding="utf-8")
    run_app("runs", domain="ai")
    assert not p.exists()


def test_global_budget_message_names_the_setting_usage_and_file(data_home):
    from article_garden import paths
    ctx = _library(data_home, 1)
    store.write_yaml_atomic(paths.global_settings_path(), {"budget": {"global_max_llm_calls_per_day": 2}})
    store.write_json_atomic(budget._global_path(), {"date": budget._today(), "calls": 2})
    with pytest.raises(budget.BudgetStop) as info:
        budget.charge(ctx)
    msg = str(info.value)
    assert "已用 2 / 上限 2" in msg and "设置 → 全局默认" in msg and str(paths.global_settings_path()) in msg


def test_library_budget_message_points_to_library_setting(data_home):
    ctx = _library(data_home, 1)
    store.write_yaml_atomic(ctx.settings_path, {"budget": {"max_llm_calls_per_day": 1}})
    store.write_json_atomic(ctx.path("logs", "budget.json"), {"date": budget._today(), "calls": 1})
    with pytest.raises(budget.BudgetStop) as info:
        budget.charge(ctx)
    assert "设置 → 本库" in str(info.value) and "已用 1 / 上限 1" in str(info.value)


def test_library_settings_tab_shows_the_global_cap_read_only(data_home):
    from tests.test_ui_smoke import run_app
    from article_garden import paths
    _library(data_home, 1)
    store.write_yaml_atomic(paths.global_settings_path(), {"budget": {"global_max_llm_calls_per_day": 1234}})
    at = run_app("settings", domain="ai")
    assert not at.exception
    assert any("1234" in c.value for c in at.caption)
