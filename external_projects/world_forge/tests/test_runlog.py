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
