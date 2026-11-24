import time

import pytest

from world_forge import engine, jobs, proposals, repo
from world_forge.providers.image import StubImageProvider


def _char(world, name, status="alive", role="次要"):
    cid = proposals.submit(world, "create_character", payload={"name": name, "role": role,
                                                              "summary": f"{name}的简介"})["result"]["id"]
    if status != "alive":
        chars = repo.load_characters(world)
        repo.find_by_id(chars, cid)["status"] = status
        repo.save_characters(world, chars)
    return cid


def test_generates_for_all_alive_characters_without_portrait(world, llm):
    a = _char(world, "甲")
    b = _char(world, "乙")
    dead = _char(world, "丙", status="dead")
    img = StubImageProvider()
    out = engine.make_all_portraits(llm, img, world)
    assert out["ok"] == 2 and out["failed"] == 0
    for cid in (a, b):
        assert repo.portrait_path(world, cid).is_file()
        ch = repo.find_by_id(repo.load_characters(world), cid)
        assert ch["attrs"]["visual_en"] and ch["attrs"]["portrait"]
    assert not repo.portrait_path(world, dead).is_file()    # 已死亡角色不生成
    assert len(img.calls) == 2


def test_default_skips_existing_portraits(world, llm):
    _char(world, "甲")
    engine.make_all_portraits(llm, StubImageProvider(), world)
    img = StubImageProvider()
    out = engine.make_all_portraits(llm, img, world)
    assert out["total"] == 0 and out["skipped"] == 1
    assert img.calls == []


def test_overwrite_regenerates_and_archives_old(world, llm):
    cid = _char(world, "甲")
    engine.make_all_portraits(llm, StubImageProvider(), world)
    out = engine.make_all_portraits(llm, StubImageProvider(), world, overwrite=True)
    assert out["total"] == 1 and out["ok"] == 1
    hist = list((repo.world_dir(world) / "cast" / "portraits" / "history").glob(f"{cid}.*.png"))
    assert len(hist) == 1


def test_single_portrait_uses_same_core_logic(world, llm):
    cid = _char(world, "甲")
    res = engine.make_portrait(llm, StubImageProvider(), world, cid)
    assert res["ok"]
    assert repo.find_by_id(repo.load_characters(world), cid)["attrs"]["visual_en"]


def test_failures_are_recorded_per_character(world, llm):
    _char(world, "甲")
    _char(world, "乙")

    class Failing(StubImageProvider):
        def generate(self, prompt, save_path, **kw):
            from world_forge.providers.image import ImageResult
            return ImageResult(False, error="额度不足")

    out = engine.make_all_portraits(llm, Failing(), world)
    assert out["ok"] == 0 and out["failed"] == 2
    assert [r["error"] for r in out["results"]] == ["额度不足", "额度不足"]   # 失败不中断整批


def test_stop_midway_keeps_finished_portraits(world, llm):
    for i in range(4):
        _char(world, f"角色{i}")

    class Slow(StubImageProvider):
        def generate(self, prompt, save_path, **kw):
            time.sleep(0.3)
            return super().generate(prompt, save_path, **kw)

    job = jobs.start(world, "portrait", "一键生成人设图", engine.make_all_portraits, llm, Slow(), world)
    time.sleep(0.6)
    jobs.request_stop(job.id)
    t0 = time.time()
    while job.running and time.time() - t0 < 15:
        time.sleep(0.02)
    assert job.status == "stopped" and job.result["cancelled"]
    done = sum(1 for c in repo.load_characters(world) if repo.portrait_path(world, c["id"]).is_file())
    assert 0 < done < 4


def test_all_portraits_logs_each_character(world, llm):
    from world_forge import runlog
    _char(world, "甲")
    _char(world, "乙")
    log = runlog.RunLog(world, "portrait")
    out = engine.make_all_portraits(llm, StubImageProvider(), world, log=log)
    assert [r["name"] for r in out["results"]] == ["甲", "乙"]
    panel_lines = [e["msg"] for e in log.entries if e["stage"] == "panel" and "完成" in e["msg"]]
    assert len(panel_lines) == 2 and "「甲」" in panel_lines[0]
    assert log.entries[-1]["stage"] == "done"
