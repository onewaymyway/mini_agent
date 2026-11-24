import time

import pytest

from world_forge import cancel, engine, jobs, proposals, repo, store
from world_forge.providers.image import ImageResult, StubImageProvider
from world_forge.providers.text import RuleTextLLM


def _wait(job, timeout=15.0):
    t0 = time.time()
    while job.running:
        if time.time() - t0 > timeout:
            raise AssertionError("任务超时未结束")
        time.sleep(0.02)
    return job


class SlowLLM:
    """每次模型调用慢一点，便于在中途发出停止信号。"""

    def __init__(self, delay=0.15):
        self.inner = RuleTextLLM()
        self.delay = delay

    def ask(self, prompt, system=""):
        time.sleep(self.delay)
        return self.inner.ask(prompt, system)


class SlowImage(StubImageProvider):
    def __init__(self, delay=0.15):
        super().__init__()
        self.delay = delay

    def generate(self, prompt, save_path, **kw):
        time.sleep(self.delay)
        return super().generate(prompt, save_path, **kw)


# ---------- 后台任务基础

def test_job_completes_with_result(world, llm):
    job = _wait(jobs.start(world, "advance", "推进 1 步", engine.advance, llm, world, 1))
    assert job.status == "done"
    assert job.result["steps"] and not job.result["cancelled"]
    assert job.entries and job.entries[-1]["stage"] == "done"


def test_job_error_is_reported_not_raised(world):
    def boom(log=None):
        raise RuntimeError("网络断了")

    job = _wait(jobs.start(world, "x", "出错的任务", boom))
    assert job.status == "error" and "网络断了" in job.error


def test_only_one_running_job_per_world(world, llm):
    slow = jobs.start(world, "advance", "慢推进", engine.advance, SlowLLM(0.3), world, 3)
    with pytest.raises(jobs.JobBusy):
        jobs.start(world, "overview", "另一个任务", engine.generate_overview, llm, world)
    assert jobs.is_busy(world)
    jobs.request_stop(slow.id)
    _wait(slow)
    assert not jobs.is_busy(world)


def test_request_stop_on_finished_job_is_noop(world, llm):
    job = _wait(jobs.start(world, "advance", "推进", engine.advance, llm, world, 1))
    assert jobs.request_stop(job.id) is False


# ---------- 停止

def test_stop_advance_midway_keeps_completed_steps(world):
    job = jobs.start(world, "advance", "推进 5 步", engine.advance, SlowLLM(0.2), world, 5)
    time.sleep(0.5)
    assert jobs.request_stop(job.id)
    _wait(job)
    assert job.status == "stopped"
    assert job.result["cancelled"] is True
    done_steps = len(job.result["steps"])
    assert 0 < done_steps < 5
    # 已完成的步骤对应的时钟和事件都保留
    from world_forge.calendar import MINUTES_PER_UNIT
    meta = repo.load_meta(world)
    assert meta["clock"]["minutes"] == done_steps * 10 * MINUTES_PER_UNIT["year"]   # 精确：每完成一步前进 10 年
    assert any("已停止" in e["msg"] for e in job.entries if e["stage"] == "cancel")


def test_stop_comic_midway_keeps_finished_panels(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "老人"})
    engine.advance(llm, world, 3)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    sid = engine.make_arc(llm, world, event_ids=ids)
    engine.make_storyboard(llm, world, sid)
    board = store.read_json(repo.story_dir(world, sid) / "storyboard.json")
    total = sum(len(p["panels"]) for p in board["pages"])
    assert total >= 3
    job = jobs.start(world, "comic", "生成漫画图", engine.render_comic, SlowImage(0.3), world, sid)
    time.sleep(0.5)
    jobs.request_stop(job.id)
    _wait(job)
    assert job.status == "stopped"
    d = repo.story_dir(world, sid)
    saved = store.read_json(d / "storyboard.json")
    done = [pn for pg in saved["pages"] for pn in pg["panels"] if pn.get("image")]
    assert 0 < len(done) < total
    assert all((d / pn["image"]).is_file() for pn in done)
    assert (d / "comic.html").is_file()   # 部分漫画仍然拼装保存


def test_stop_before_any_model_call_raises_cancel_in_checkpoint(world):
    cancel.bind(None)
    ev = __import__("threading").Event()
    ev.set()
    cancel.bind(ev)
    try:
        with pytest.raises(cancel.Cancelled):
            cancel.checkpoint()
    finally:
        cancel.unbind()
    cancel.checkpoint()   # 解绑后为空操作


# ---------- 重新生成

def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def test_regenerate_arc_keeps_id_and_archives_old(world, llm):
    sid = _story(world, llm)
    old = store.read_json(repo.story_dir(world, sid) / "arc.json")

    class Other(RuleTextLLM):
        def _task_arc(self, ctx):
            out = super()._task_arc(ctx)
            out["title"] = "重新生成的故事线"
            return out

    again = engine.regenerate_arc(Other(), world, sid)
    assert again == sid
    new = store.read_json(repo.story_dir(world, sid) / "arc.json")
    assert new["title"] == "重新生成的故事线" and new != old
    hist = list((repo.story_dir(world, sid) / "history").glob("arc.*.json"))
    assert len(hist) == 1
    assert store.read_json(hist[0])["title"] == old["title"]


def test_regenerate_short_uses_original_inputs_and_archives(world, llm):
    short = engine.write_short(llm, world, brief="守灯人的最后一夜", form="novel")
    d = repo.story_dir(world, short)
    first = (d / "story.md").read_text(encoding="utf-8")
    assert repo.load_story_meta(world, short)["request"]["brief"] == "守灯人的最后一夜"
    engine.regenerate_short(llm, world, short)
    assert (d / "story.md").read_text(encoding="utf-8") == first   # 同一输入、同一规则输出
    assert list((d / "history").glob("story.*.md"))                # 旧正文已归档
    assert repo.load_story_meta(world, short).get("regenerated_at")


def test_regenerate_short_without_request_is_refused(world, llm):
    sid = _story(world, llm)
    with pytest.raises(engine.EngineError):
        engine.regenerate_short(llm, world, sid)   # 小故事线不是短篇，且没有 request


def test_regenerate_storyboard_archives_old(world, llm):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid)
    engine.make_storyboard(llm, world, sid)
    assert len(list((repo.story_dir(world, sid) / "history").glob("storyboard.*.json"))) == 1


def test_regenerate_single_panel_only_touches_that_panel(world, llm):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid)
    img = StubImageProvider()
    engine.render_comic(img, world, sid)
    before = store.read_json(repo.story_dir(world, sid) / "storyboard.json")
    total = sum(len(p["panels"]) for p in before["pages"])
    img2 = StubImageProvider()
    res = engine.render_comic(img2, world, sid, only=[(1, 1)])
    assert len(img2.calls) == 1
    assert res["skipped"] == total - 1
    d = repo.story_dir(world, sid)
    assert list((d / "panels" / "history").glob("p01_01.*.png"))   # 只有这一格的旧图被归档
    assert not list((d / "panels" / "history").glob("p01_02.*.png"))


def test_regenerate_portrait_archives_old(world, llm):
    cid = proposals.submit(world, "create_character", payload={"name": "甲", "role": "次要"})["result"]["id"]
    img = StubImageProvider()
    engine.make_portrait(llm, img, world, cid)
    engine.make_portrait(llm, img, world, cid)
    assert list((repo.world_dir(world) / "cast" / "portraits" / "history").glob(f"{cid}.*.png"))


def test_regenerate_overview_archives_old(world, llm):
    engine.generate_overview(llm, world)
    engine.generate_overview(llm, world)
    assert list((repo.world_dir(world) / "history").glob("overview.*.md"))
