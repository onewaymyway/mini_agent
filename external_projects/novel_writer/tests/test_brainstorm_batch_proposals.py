"""待确认提议的全部采纳 / 全部拒绝：逐条处理，失败的保持待确认并给出原因，不影响其它条。"""
from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import proposals as PR
from novel_writer.brainstorm.workspace import BrainstormWorkspace
import pytest


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    return w


def test_accept_all_accepts_every_pending_and_writes_cards(ws):
    PR.create(ws, "premise", "set", "命题一")
    PR.create(ws, "ideas", "add", {"text": "灵感甲"})
    r = PR.accept_all(ws)
    assert len(r["accepted"]) == 2 and r["failed"] == []
    assert C.get_module(ws, "premise").strip() == "命题一"
    assert PR.list_items(ws, "pending") == []


def test_failing_item_stays_pending_with_reason_and_others_still_accepted(ws):
    stale = PR.create(ws, "premise", "replace", "提议的命题")          # 基于当前（空）命题
    PR.create(ws, "ideas", "add", {"text": "灵感乙"})
    PR.create(ws, "premise", "set", "另一个命题")
    C.put_module(ws, "premise", "之后被手动改过的命题")                  # 使替换提议过期
    r = PR.accept_all(ws)
    assert len(r["accepted"]) == 2 and len(r["failed"]) == 1
    f = r["failed"][0]
    assert f["pid"] == stale["pid"] and "过期" in f["error"]
    pending = PR.list_items(ws, "pending")
    assert [p["pid"] for p in pending] == [stale["pid"]] and pending[0]["error"]


def test_reject_all_only_changes_status(ws):
    PR.create(ws, "premise", "set", "命题一")
    PR.create(ws, "ideas", "add", {"text": "灵感甲"})
    r = PR.reject_all(ws)
    assert len(r["rejected"]) == 2 and PR.list_items(ws, "pending") == []
    assert C.get_module(ws, "premise").strip() != "命题一"


def test_api_batch_endpoints(ws):
    PR.create(ws, "ideas", "add", {"text": "灵感甲"})
    out = BA.accept_all_proposals(str(ws.root))
    assert out["ok"] and len(out["data"]["accepted"]) == 1
    PR.create(ws, "ideas", "add", {"text": "灵感乙"})
    out = BA.reject_all_proposals(str(ws.root))
    assert out["ok"] and len(out["data"]["rejected"]) == 1
