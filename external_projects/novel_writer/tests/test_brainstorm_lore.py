"""设定文档（手动）：对话 → 一份完整的世界观与设定 Markdown。"""

import time

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import distill as DS
from novel_writer.brainstorm import lore as LO
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

DOC = "# 光之岛\n\n## 一、一句话概述\n一座会移动的岛 [0]\n\n## 三、世界观\n- 潮涨时岛会移动 [1]\n" + "说明。" * 20


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "岛")
    w.create("岛")
    for i in range(3):
        CH.send(w, f"作者第{i}句", "自由", runner=lambda p, s, i=i: (f"助手第{i}句", None))
    return w


def test_prompt_has_sections_rules_and_transcript(ws):
    recs = CH.history(ws)
    p = LO.build_prompt(recs, 0)
    for s in LO.SECTIONS:
        assert s in p
    assert "未被认可" in p and "[0] 作者：作者第0句" in p


def test_run_writes_new_file_each_time_and_keeps_old(ws):
    logs = []
    a = LO.run(ws, generator=lambda p: DOC, log=lambda k, t: logs.append((k, t)))
    time.sleep(1.1)
    b = LO.run(ws, generator=lambda p: "```markdown\n" + DOC + "\n```")
    assert a["path"] != b["path"] and len(LO.list_docs(ws)) == 2
    text = LO.read_doc(ws, b["path"])
    assert "## 一、一句话概述" in text and "```" not in text
    assert any(k == "success" for k, _ in logs)


def test_run_does_not_touch_cards_proposals_or_checkpoint(ws):
    before = C.content_hash(ws)
    LO.run(ws, generator=lambda p: DOC)
    assert C.content_hash(ws) == before
    assert DS._checkpoint(ws) == 0
    assert not (ws.root / "_studio/proposals.json").exists()


def test_recent_scope_and_bad_scope(ws):
    out = LO.run(ws, scope="recent", recent_rounds=1, generator=lambda p: DOC)
    assert out["range"] == [4, 6]
    with pytest.raises(BrainstormError):
        LO.run(ws, scope="pending", generator=lambda p: DOC)


def test_garbage_output_is_shown_in_full_and_not_saved_as_doc(ws):
    logs = []
    with pytest.raises(BrainstormError):
        LO.run(ws, generator=lambda p: "无法完成", log=lambda k, t: logs.append((k, t)))
    assert LO.list_docs(ws) == [] and any(k == "error" and "无法完成" in t for k, t in logs)
    assert list((ws.root / "_studio/distill").glob("raw_*.txt"))


def test_read_doc_rejects_outside_paths(ws):
    with pytest.raises(BrainstormError):
        LO.read_doc(ws, "../brief.json")


def test_background_run_and_api(ws):
    run_id = LO.start_run(ws, generator=lambda p: DOC)
    deadline = time.time() + 10
    while time.time() < deadline:
        st_ = BA.lore_state(str(ws.root), run_id)["data"]
        if st_["status"] != "running":
            break
        time.sleep(0.02)
    assert st_["status"] == "done" and st_["result"]["path"].startswith("output/设定/")
    assert BA.lore_list(str(ws.root))["data"] == [st_["result"]["path"]]
    assert "光之岛" in BA.lore_read(str(ws.root), st_["result"]["path"])["data"]
