import json

import pytest

from world_forge import engine, proposals, repo, runlog


def _stages(entries):
    return [e["stage"] for e in entries]


def test_advance_logs_every_stage_and_persists(world, llm):
    out = engine.advance(llm, world, 2)
    entries = out["log"]
    stages = _stages(entries)
    for needed in ("start", "step", "context", "model", "parse", "write", "clock", "done"):
        assert needed in stages, f"缺少阶段：{needed}"
    assert entries[0]["stage"] == "start" and entries[-1]["stage"] == "done"
    # 两步 → 两次时钟推进、两次完成
    assert stages.count("clock") == 2
    assert stages.count("step") >= 4   # 每步“收集”与“完成”
    # 模型调用带有任务名与用时
    model_msgs = [e["msg"] for e in entries if e["stage"] == "model"]
    assert any("（advance）" in m for m in model_msgs)
    assert any("用时" in m and "秒" in m for m in model_msgs)
    # 持久化：文件内容与返回的日志一致
    saved = runlog.read_run(out["log_path"])
    assert len(saved) == len(entries)
    assert saved[-1]["msg"] == entries[-1]["msg"]


def test_write_logs_name_status_and_reason(world, llm):
    class Scripted:
        def ask(self, prompt, system=""):
            return json.dumps({"new_characters": [], "entities": [], "relations": [], "canon_updates": [],
                               "events": [{"title": "幽灵事件", "summary": "", "involves": ["不存在的人"],
                                           "caused_by": [], "tags": [], "changes": [], "violates_rules": []}]},
                              ensure_ascii=False)

    out = engine.advance(Scripted(), world, 1)
    writes = [e for e in out["log"] if e["stage"] == "write"]
    blocked = [e for e in writes if "已拦截" in e["msg"]]
    assert blocked, "被拦截的写入必须出现在日志中"
    assert blocked[0]["level"] == "warn"
    assert "幽灵事件" in blocked[0]["msg"] and "不存在" in blocked[0]["msg"]


def test_retry_is_logged(world, llm):
    calls = {"n": 0}

    class FlakyOnce:
        def ask(self, prompt, system=""):
            calls["n"] += 1
            if calls["n"] == 1:
                return "这不是 JSON"
            return llm.ask(prompt, system)

    out = engine.advance(FlakyOnce(), world, 1)
    retries = [e for e in out["log"] if e["stage"] == "retry"]
    assert retries and "无法解析" in retries[0]["msg"]
    assert "将重试" in retries[0]["msg"]


def test_failure_is_logged_then_raised(world):
    class Boom:
        def ask(self, prompt, system=""):
            raise RuntimeError("网络断开")

    log = runlog.RunLog(world, "advance")
    with pytest.raises(RuntimeError):
        engine.advance(Boom(), world, 1, log=log)
    assert log.entries[-1]["stage"] == "error"
    assert "网络断开" in log.entries[-1]["msg"]
    saved = runlog.read_run(str(log.path))
    assert saved[-1]["stage"] == "error"


def test_sink_receives_entries_in_real_time(world, llm):
    seen = []
    log = runlog.RunLog(world, "advance", sink=seen.append)
    engine.advance(llm, world, 1, log=log)
    assert len(seen) == len(log.entries)
    assert seen[0]["stage"] == "start"


def test_list_and_read_runs(world, llm):
    engine.advance(llm, world, 1)
    engine.advance(llm, world, 1)
    runs = runlog.list_runs(world)
    assert len(runs) == 2
    assert all(r["kind"] == "advance" for r in runs)
    assert runlog.read_run(runs[0]["path"])
    line = runlog.format_entry({"t": 1.5, "level": "ok", "stage": "write", "msg": "完成"})
    assert "✓" in line and "完成" in line and "1.50s" in line


def _story_setup(world, llm):
    engine.advance(llm, world, 2)
    evs = repo.load_events(world, "main")
    return [e["id"] for e in evs]


def test_arc_logs_context_model_parse_save(world, llm):
    ids = _story_setup(world, llm)
    log = runlog.RunLog(world, "arc")
    sid = engine.make_arc(llm, world, event_ids=ids, log=log)
    stages = _stages(log.entries)
    for needed in ("start", "context", "model", "parse", "save", "done"):
        assert needed in stages, needed
    assert any("（arc）" in e["msg"] for e in log.entries if e["stage"] == "model")
    assert log.entries[-1]["data"]["story"] == sid
    assert runlog.read_run(str(log.path))


def test_short_logs_mode_and_length(world, llm):
    ids = _story_setup(world, llm)
    log = runlog.RunLog(world, "short")
    engine.write_short(llm, world, brief="守灯人的最后一夜", event_ids=ids[:1], log=log)
    start = log.entries[0]
    assert "直接创作" in start["msg"]
    assert any("正文生成完成" in e["msg"] for e in log.entries)


def test_storyboard_logs_pages_and_panels(world, llm):
    ids = _story_setup(world, llm)
    sid = engine.make_arc(llm, world, event_ids=ids)
    log = runlog.RunLog(world, "board")
    engine.make_storyboard(llm, world, sid, log=log)
    msgs = [e["msg"] for e in log.entries]
    assert any("分镜结构通过" in m for m in msgs)
    assert any(m.startswith("第 1 页") for m in msgs)


def test_comic_logs_each_panel_and_summary(world, llm, img):
    ids = _story_setup(world, llm)
    sid = engine.make_arc(llm, world, event_ids=ids)
    engine.make_storyboard(llm, world, sid)
    log = runlog.RunLog(world, "comic")
    res = engine.render_comic(img, world, sid, log=log)
    panel_lines = [e for e in log.entries if e["stage"] == "panel" and "完成" in e["msg"]]
    assert len(panel_lines) == res["ok"]
    assert any("占位图" in e["msg"] for e in log.entries if e["stage"] == "start")
    assert log.entries[-1]["stage"] == "done" and "成功" in log.entries[-1]["msg"]


def test_comic_failure_per_panel_is_logged_not_fatal(world, llm):
    ids = _story_setup(world, llm)
    sid = engine.make_arc(llm, world, event_ids=ids)
    engine.make_storyboard(llm, world, sid)

    class Failing:
        name = "fail"
        is_stub = False

        def generate(self, prompt, save_path, **kw):
            from world_forge.providers.image import ImageResult
            return ImageResult(False, error="额度不足")

    log = runlog.RunLog(world, "comic")
    res = engine.render_comic(Failing(), world, sid, log=log)
    assert res["failed"] > 0 and res["ok"] == 0
    fails = [e for e in log.entries if e["stage"] == "panel" and "失败" in e["msg"]]
    assert fails and "额度不足" in fails[0]["msg"]
    assert log.entries[-1]["level"] == "warn"


def test_export_logs_each_file(world, llm):
    ids = _story_setup(world, llm)
    sid = engine.make_arc(llm, world, event_ids=ids)
    log = runlog.RunLog(world, "export")
    engine.export_bundle(world, sid, log=log)
    saves = [e["msg"] for e in log.entries if e["stage"] == "save"]
    assert any(m.startswith("outline.md") for m in saves)
    assert any(m.startswith("setting.md") for m in saves)
    assert log.entries[-1]["stage"] == "done"


def test_story_error_is_logged_then_raised(world, llm):
    log = runlog.RunLog(world, "arc")
    with pytest.raises(engine.EngineError):
        engine.make_arc(llm, world, event_ids=["evt_none"], log=log)
    assert log.entries[-1]["stage"] == "error"


def test_list_runs_can_filter_by_kind(world, llm):
    engine.advance(llm, world, 1)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    engine.make_arc(llm, world, event_ids=ids)
    assert {r["kind"] for r in runlog.list_runs(world)} == {"advance", "arc"}
    assert {r["kind"] for r in runlog.list_runs(world, kinds={"arc"})} == {"arc"}
