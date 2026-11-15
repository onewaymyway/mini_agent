"""一句话起稿 S1：生成、解析、校验重试、失败不落盘、后台运行与日志、接口。"""
import json
import time

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm import seed_gen as SG
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from test_brainstorm_seed_s0 import good


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    return w


class Gen:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


def test_success_writes_seed_version_not_cards(ws):
    before = C.content_hash(ws)
    out = SG.run(ws, "记忆是货币的港口", "要悬疑", generator=Gen(good()))
    assert out == {"ver": 1, "attempts": 1, "repaired": False}
    v = SD.load(ws)
    assert v["kind"] == "seed" and v["seed"] == {"idea": "记忆是货币的港口", "constraints": "要悬疑"}
    assert C.content_hash(ws) == before


def test_prompt_contains_idea_constraints_brief_and_schema(ws):
    C.put_module(ws, "brief", {"genre": "悬疑", "theme": "遗忘"})
    g = Gen(good())
    SG.run(ws, "记忆是货币", "不要后宫", generator=g)
    p = g.prompts[0]
    assert "记忆是货币" in p and "不要后宫" in p and "genre：悬疑" in p and '"storyline"' in p


def test_fenced_and_surrounded_json_accepted(ws):
    SG.run(ws, "想法", generator=Gen("好的：\n```json\n" + json.dumps(good(), ensure_ascii=False) + "\n```\n以上"))
    assert SD.latest_version(ws) == 1


def test_retry_with_errors_then_success(ws):
    bad = good()
    bad.pop("world")
    g = Gen(bad, good())
    out = SG.run(ws, "想法", generator=g)
    assert out["attempts"] == 2 and "缺少部分" in g.prompts[1] and "世界观" in g.prompts[1]
    assert [v["reason"] for v in SD.versions(ws)] == ["一句话起稿（重试后通过）"]


def test_two_failures_write_nothing_and_archive_raw(ws):
    with pytest.raises(BrainstormError) as e:
        SG.run(ws, "想法", generator=Gen("根本不是 JSON"))
    assert "没有写入任何版本" in str(e.value)
    assert SD.versions(ws) == []
    assert list((ws.root / "_studio" / "seed_draft").glob("raw_*.txt"))


def test_invalid_input_rejected(ws):
    with pytest.raises(BrainstormError):
        SG.run(ws, "  ", generator=Gen(good()))
    with pytest.raises(BrainstormError):
        SG.run(ws, "长" * 2001, generator=Gen(good()))


def test_new_seed_after_existing_adds_version(ws):
    SG.run(ws, "想法一", generator=Gen(good()))
    SG.run(ws, "想法二", generator=Gen(good()))
    assert SD.latest_version(ws) == 2 and SD.load(ws)["seed"]["idea"] == "想法二"


def _wait(ws, rid, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        st = SG.run_state(ws, rid)
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("超时")


def test_background_run_logs_and_done(ws):
    rid = SG.start_run(ws, "想法", generator=Gen(good()))
    st = _wait(ws, rid)
    assert st["status"] == "done" and st["result"]["ver"] == 1
    assert any("v001" in e["text"] for e in st["events"])


def test_background_failure_reported_with_raw_output(ws):
    rid = SG.start_run(ws, "想法", generator=Gen("乱码输出"))
    st = _wait(ws, rid)
    assert st["status"] == "error" and any("乱码输出" in e["text"] for e in st["events"])
    assert SD.versions(ws) == []


def test_concurrent_run_refused(ws):
    rid = SG.start_run(ws, "想法", generator=Gen(good()))
    st = _wait(ws, rid)
    assert st["status"] == "done"
    # 伪造一个运行中的任务
    from novel_writer.fsutil import atomic_write_json
    atomic_write_json(ws.root / SG.RUNS_DIR / "sfake.json", {"run": "sfake", "status": "running"})
    with pytest.raises(BrainstormError):
        SG.start_run(ws, "想法", generator=Gen(good()))


def test_start_requires_idea_before_thread(ws):
    with pytest.raises(BrainstormError):
        SG.start_run(ws, " ", generator=Gen(good()))
    assert not (ws.root / SG.RUNS_DIR).exists()


def test_api_roundtrip(ws):
    r = BA.seed_start(str(ws.root), "想法", generator=Gen(good()))
    assert r["ok"]
    _wait(ws, r["data"]["run"])
    assert BA.seed_versions(str(ws.root))["data"]["versions"][0]["ver"] == 1
    assert BA.seed_get(str(ws.root))["data"]["draft"]["premise"]
    assert BA.seed_start(str(ws.root), " ")["ok"] is False
    assert BA.seed_state(str(ws.root), "nope")["ok"] is False


def test_truncated_json_repaired_when_json_repair_available(ws):
    pytest.importorskip("json_repair")
    text = json.dumps(good(), ensure_ascii=False)
    # 去掉最后一个字段的结尾，模拟输出被截断
    cut = text[: text.rindex('"open_questions"')] + '"open_questions": ["港口为何吞噬记忆？"'
    out = SG.run(ws, "想法", generator=Gen(cut))
    assert out["repaired"] is True and SD.latest_version(ws) == 1
