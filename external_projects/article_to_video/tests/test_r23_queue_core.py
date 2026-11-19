"""R23 第一期：G3 挡位 + 批量队列核心（存储、串行运行、失败重试、运行中追加、停止与恢复）。"""
import threading

import pytest

from conftest import REFERENCE_ARTICLE
from article_to_video.batch import runner, store
from article_to_video.batch.store import QueueError
from article_to_video.pipeline.gates import (GEARS, PipelineError, check_budget_requirement,
                                             manual_checkpoints)
from article_to_video.project_store import list_projects

ARTICLE = "# 标题甲\n\n作者：某作者\n\n## 第一节\n\n这是第一节的正文内容。\n\n## 第二节\n\n这是第二节的正文内容。\n"


def add(root, n=1, title=None, text=None):
    out = []
    for i in range(n):
        t = text if text is not None else ARTICLE + f"\n独有内容{i}-{title}\n"
        out.append(store.enqueue(root, source_text=t, source_name=f"a{i}.md",
                                 title=title or f"文章{i + 1}", author=f"作者{i + 1}")["item"])
    return out


def ok_runner(calls=None):
    def run(pdir, params):
        if calls is not None:
            calls.append(pdir.name)
        return {"state": "done", "outputs": [str(pdir / "exports" / "x.mp4")]}
    return run


def states(root):
    return [i["state"] for i in store.load(root)["items"]]


# ---------- G3 挡位 ----------

def test_g3_gear_is_unattended_and_needs_no_budget():
    assert "G3" in GEARS
    assert manual_checkpoints("G3") == set()
    check_budget_requirement("G3", None, None)          # 无预算也允许
    with pytest.raises(PipelineError, match="预算"):
        check_budget_requirement("G2", None, None)      # G2 的要求不变


# ---------- 参数 ----------

def test_default_params_and_validation(tmp_path):
    p = store.load(tmp_path)["params"]
    assert p["gear"] == "G3" and p["max_retries"] == 3 and p["retry_delays"] == [30.0, 60.0, 120.0]
    p, w = store.normalize_params({"gear": "G9", "until": "nope", "max_retries": 99})
    assert p["gear"] == "G3" and p["until"] == "export" and p["max_retries"] == 10 and len(w) == 2
    with pytest.raises(QueueError, match="预算"):
        store.set_params(tmp_path, {"gear": "G2"})
    store.set_params(tmp_path, {"gear": "G2", "max_images": 5})
    assert store.load(tmp_path)["params"]["gear"] == "G2"


# ---------- 入队 ----------

def test_enqueue_requires_title_and_text_and_warns_duplicates(tmp_path):
    with pytest.raises(QueueError, match="标题"):
        store.enqueue(tmp_path, source_text=ARTICLE, source_name="a.md", title="  ", author="x")
    with pytest.raises(QueueError, match="为空"):
        store.enqueue(tmp_path, source_text="  \n", source_name="a.md", title="t", author="x")
    r1 = store.enqueue(tmp_path, source_text=ARTICLE, source_name="a.md", title="甲", author="")
    assert r1["warnings"] == [] and r1["item"]["id"] == "q0001" and r1["item"]["author"] == ""
    r2 = store.enqueue(tmp_path, source_text=ARTICLE, source_name="b.md", title="乙", author="丙")
    assert any("相同" in w for w in r2["warnings"]) and r2["item"]["id"] == "q0002"   # 提醒但不拦截
    assert (store.queue_dir(tmp_path) / r1["item"]["source_path"]).read_text(encoding="utf-8") == ARTICLE


def test_duplicate_of_existing_project_is_warned(tmp_path):
    add(tmp_path, 1, "甲")
    runner.run_queue(tmp_path, pipeline_runner=ok_runner())
    again = store.enqueue(tmp_path, source_text=store.queue_dir(tmp_path).joinpath(
        store.load(tmp_path)["items"][0]["source_path"]).read_text(encoding="utf-8"),
        source_name="z.md", title="再来", author="")
    assert again["warnings"]


# ---------- 运行 ----------

def test_runs_serially_in_order_and_creates_projects_lazily(tmp_path):
    add(tmp_path, 3)
    assert list_projects(tmp_path) == []                 # 入队不创建项目
    calls = []
    res = runner.run_queue(tmp_path, pipeline_runner=ok_runner(calls))
    assert res["started"] and res["processed"] == 3 and states(tmp_path) == ["done"] * 3
    items = store.load(tmp_path)["items"]
    assert calls == [i["project_id"] for i in items] and len(set(calls)) == 3
    projs = {p["id"]: p for p in list_projects(tmp_path)}
    assert projs[items[0]["project_id"]]["title"] == "文章1" and projs[items[0]["project_id"]]["author"] == "作者1"
    assert items[0]["outputs"] and items[0]["finished_at"]
    assert not store.runner_alive(tmp_path)              # 运行结束释放运行者锁


def test_title_and_author_come_from_enqueue_not_article(tmp_path):
    add(tmp_path, 1, "我设置的标题")                      # 文章里写的是「标题甲 / 某作者」
    runner.run_queue(tmp_path, pipeline_runner=ok_runner())
    p = list_projects(tmp_path)[0]
    assert p["title"] == "我设置的标题" and p["author"] == "作者1"


def test_shared_params_reach_import_and_pipeline(tmp_path):
    store.set_params(tmp_path, {"gear": "G3", "settings": {"mode": "faithful"},
                                "branding": {"watermark": {"enabled": True, "text": "水印甲"}}})
    add(tmp_path, 2)
    seen = []
    runner.run_queue(tmp_path, pipeline_runner=lambda pdir, params: seen.append(params["gear"]) or {"state": "done"})
    assert seen == ["G3", "G3"]
    from article_to_video.project_store import load_project
    pj = load_project(tmp_path / store.load(tmp_path)["items"][1]["project_id"])
    assert pj["settings"]["mode"] == "faithful" and pj["branding"]["watermark"]["text"] == "水印甲"


# ---------- 失败重试 ----------

def test_retries_three_times_then_succeeds(tmp_path):
    add(tmp_path, 1)
    n = {"c": 0}
    waits = []

    def flaky(pdir, params):
        n["c"] += 1
        return {"state": "failed", "error": "模型超时"} if n["c"] <= 3 else {"state": "done", "outputs": ["o.mp4"]}

    runner.run_queue(tmp_path, pipeline_runner=flaky, sleep=lambda s: waits.append(s))
    it = store.load(tmp_path)["items"][0]
    assert it["state"] == "done" and it["failures"] == 3 and n["c"] == 4
    assert len(waits) > 0                                  # 重试前确实等待过


def test_fails_after_three_retries_then_continues_with_next(tmp_path):
    store.set_params(tmp_path, {"retry_delays": [0]})
    add(tmp_path, 2)
    calls = []

    def run(pdir, params):
        calls.append(pdir.name)
        return {"state": "failed", "error": "坏"} if pdir.name == calls[0] else {"state": "done"}

    runner.run_queue(tmp_path, pipeline_runner=run)
    a, b = store.load(tmp_path)["items"]
    assert a["state"] == "failed" and a["failures"] == 4 and a["error"] == "坏"   # 1 次 + 3 次重试
    assert calls.count(a["project_id"]) == 4
    assert b["state"] == "done"                                                   # 失败后继续下一篇


def test_retry_reuses_same_project_and_delays_follow_params(tmp_path):
    store.set_params(tmp_path, {"retry_delays": [5, 7], "max_retries": 3})
    add(tmp_path, 1)
    waits = []
    runner.run_queue(tmp_path, pipeline_runner=lambda p, q: {"state": "failed", "error": "x"},
                     sleep=lambda s: waits.append(round(s, 1)))
    assert len(list_projects(tmp_path)) == 1                # 重试不会重复创建项目
    assert round(sum(waits), 1) == 5 + 7 + 7           # 第 1 次等 5 秒，第 2、3 次沿用最后一个值 7 秒


def test_runner_exception_counts_as_failure_and_is_retried(tmp_path):
    store.set_params(tmp_path, {"retry_delays": [0], "max_retries": 1})
    add(tmp_path, 1)
    c = {"n": 0}

    def boom(pdir, params):
        c["n"] += 1
        raise PipelineError("写稿需要文本模型（未配置）")

    runner.run_queue(tmp_path, pipeline_runner=boom)
    it = store.load(tmp_path)["items"][0]
    assert it["state"] == "failed" and c["n"] == 2 and "文本模型" in it["error"]


def test_import_failure_is_not_retried(tmp_path):
    add(tmp_path, 1)
    it = store.load(tmp_path)["items"][0]
    (store.queue_dir(tmp_path) / it["source_path"]).write_text("   ", encoding="utf-8")   # 原文被清空
    c = {"n": 0}
    runner.run_queue(tmp_path, pipeline_runner=lambda p, q: c.__setitem__("n", c["n"] + 1) or {"state": "done"})
    it = store.load(tmp_path)["items"][0]
    assert it["state"] == "failed" and "导入失败" in it["error"] and c["n"] == 0 and list_projects(tmp_path) == []


@pytest.mark.parametrize("st", ["waiting", "budget_exceeded"])
def test_waiting_and_budget_exceeded_are_not_retried_and_do_not_block(tmp_path, st):
    add(tmp_path, 2)
    c = {"n": 0}

    def run(pdir, params):
        c["n"] += 1
        return {"state": st, "waiting": {"stage": "outline", "detail": "待确认"}, "error": "超了"} if c["n"] == 1 \
            else {"state": "done"}

    runner.run_queue(tmp_path, pipeline_runner=run)
    assert states(tmp_path) == [st, "done"] and c["n"] == 2


# ---------- 运行中追加 ----------

def test_items_added_while_running_are_processed_in_same_run(tmp_path):
    add(tmp_path, 1)
    order = []

    def run(pdir, params):
        order.append(pdir.name)
        if len(order) == 1:                                  # 第一篇运行中又加入一篇
            store.enqueue(tmp_path, source_text=ARTICLE + "\n追加的内容\n", source_name="late.md",
                          title="后来加的", author="丁")
        return {"state": "done"}

    res = runner.run_queue(tmp_path, pipeline_runner=run)
    assert res["processed"] == 2 and states(tmp_path) == ["done", "done"]
    assert list_projects(tmp_path)[0]["title"] in ("后来加的", "文章1")


def test_enqueue_is_thread_safe(tmp_path):
    def worker(i):
        store.enqueue(tmp_path, source_text=ARTICLE + f"\n线程{i}\n", source_name=f"t{i}.md", title=f"T{i}", author="")
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [i["id"] for i in store.load(tmp_path)["items"]]
    assert len(ids) == 8 and len(set(ids)) == 8


# ---------- 停止与恢复 ----------

def test_stop_request_keeps_remaining_queued_and_resume_continues(tmp_path):
    add(tmp_path, 3)

    def run(pdir, params):
        if not store.stop_requested(tmp_path) and store.load(tmp_path)["items"][0]["project_id"] == pdir.name:
            runner.request_stop(tmp_path)                    # 第一篇运行中被要求停止
            return {"state": "stopped", "error": "用户请求停止"}
        return {"state": "done"}

    res = runner.run_queue(tmp_path, pipeline_runner=run)
    assert states(tmp_path) == ["stopped", "queued", "queued"] and res["processed"] == 1
    assert store.requeue(tmp_path) == 1                      # 已停止的条目可重新排队
    res2 = runner.run_queue(tmp_path, pipeline_runner=ok_runner())   # 再次开始会清除停止标志
    assert states(tmp_path) == ["done"] * 3 and res2["processed"] == 3


def test_stop_during_retry_wait(tmp_path):
    add(tmp_path, 2)
    store.set_params(tmp_path, {"retry_delays": [30]})
    runner_calls = {"n": 0}

    def run(pdir, params):
        runner_calls["n"] += 1
        return {"state": "failed", "error": "x"}

    def sleep(s):
        runner.request_stop(tmp_path)                        # 等待重试时用户点了停止

    runner.run_queue(tmp_path, pipeline_runner=run, sleep=sleep)
    assert states(tmp_path) == ["stopped", "queued"] and runner_calls["n"] == 1


def test_recover_running_items_after_crash(tmp_path):
    add(tmp_path, 2)
    store.claim_next(tmp_path)                               # 模拟上次运行中途进程被杀：条目停在 running
    assert states(tmp_path) == ["running", "queued"]
    runner.run_queue(tmp_path, pipeline_runner=ok_runner())
    assert states(tmp_path) == ["done", "done"]


def test_only_one_runner_at_a_time(tmp_path):
    add(tmp_path, 1)
    assert store.acquire_runner(tmp_path)
    res = runner.run_queue(tmp_path, pipeline_runner=ok_runner())
    assert res["started"] is False and states(tmp_path) == ["queued"]
    store.release_runner(tmp_path)
    assert runner.run_queue(tmp_path, pipeline_runner=ok_runner())["started"] is True


def test_remove_and_requeue_rules(tmp_path):
    a, b = add(tmp_path, 2)
    store.remove_item(tmp_path, a["id"])
    assert [i["id"] for i in store.load(tmp_path)["items"]] == [b["id"]]
    store.claim_next(tmp_path)
    with pytest.raises(QueueError, match="正在运行"):
        store.remove_item(tmp_path, b["id"])
    with pytest.raises(QueueError):
        store.remove_item(tmp_path, "q9999")


def test_queue_dir_is_not_listed_as_project(tmp_path):
    add(tmp_path, 1)
    assert list_projects(tmp_path) == []


def test_queue_log_written(tmp_path):
    add(tmp_path, 1)
    runner.run_queue(tmp_path, pipeline_runner=ok_runner())
    text = (store.queue_dir(tmp_path) / "logs" / "job.jsonl").read_text(encoding="utf-8")
    assert "队列开始运行" in text and "完成" in text


def test_reference_article_can_be_queued(tmp_path):
    text = REFERENCE_ARTICLE.read_text(encoding="utf-8")
    it = store.enqueue(tmp_path, source_text=text, source_name="r.md", title="参考", author="")["item"]
    assert it["chars"] == len(text.lstrip("﻿"))


# ---------- G3 与真实流水线 ----------

def test_g3_runs_through_storyboard_without_any_manual_checkpoint_or_budget(tmp_path, monkeypatch):
    """G3：大纲未确认也会自动确认；不需要预算；口播与分镜自动确认（用假模型，不产生费用）。"""
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    from article_to_video.pipeline.run import run_pipeline
    from article_to_video.planning.runner import load_spine
    from article_to_video.providers.image import StubImageProvider
    from article_to_video.providers.tts import SilenceTTS
    from article_to_video.script.writer import load_script
    from article_to_video.visual.storyboard import storyboard_status
    from helpers_p2 import ScriptLLM, good_script, make_project
    pdir = make_project(tmp_path)                           # 大纲尚未确认
    st = run_pipeline(pdir, outline_engine="rules", until="render", gear="G3", llm=ScriptLLM(good_script),
                      tts=SilenceTTS(), image_provider=StubImageProvider(), storyboard_engine="rules")
    assert st["state"] == "done" and st["waiting"] is None
    assert load_spine(pdir)["confirmed"] is True
    for p in load_spine(pdir)["parts"]:
        assert load_script(pdir, p["id"])["confirmed"] is True
        assert storyboard_status(pdir, p["id"])["confirmed"] is True
    assert st["budget"]["max_images"] in (None, 0) and st["budget"]["image_calls"] > 0   # 生图不设上限


def test_queue_default_runner_uses_unlimited_budget_for_g3(monkeypatch, tmp_path):
    """默认运行器把「0 = 不限」转成 None 传给流水线，而不是 0。"""
    add(tmp_path, 1)
    seen = {}
    monkeypatch.setattr(runner, "default_providers", lambda pdir, offline=False: {"llm": None, "tts": None, "image": None})
    monkeypatch.setattr(runner, "run_pipeline", lambda pdir, **kw: seen.update(kw) or {"state": "done"})
    runner.run_queue(tmp_path)
    assert seen["gear"] == "G3" and seen["max_images"] is None and seen["max_minutes"] is None
    assert seen["until"] == "export"
