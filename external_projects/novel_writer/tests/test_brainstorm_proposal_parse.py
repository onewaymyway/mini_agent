"""提议块解析：严格解析、失败时保留全文原文供手动修改；自动修复只给建议，不直接登记。"""

import json

import pytest

from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormWorkspace


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "雾港")
    w.create("雾港")
    return w


# 你遇到的情况：模型把中文值直接写进 JSON，没有加引号
BROKEN = ('[{"module": "premise", "op": "set", "value": 当一套"完美"的意识，'
          '"reason": "整合的核心命题"}]')
GOOD = json.dumps([{"module": "premise", "op": "set",
                    "value": "一套完美的意识，承载着更大系统的一部分。", "reason": "整合"}],
                  ensure_ascii=False)


def reply_with(block, text="好的，我整理了命题。"):
    return f"{text}\n\n```proposals\n{block}\n```"


def test_valid_block_yields_proposals_and_no_raw():
    d = CH.parse_reply_detail(reply_with(GOOD))
    assert len(d["proposals"]) == 1 and d["invalid"] == 0 and d["raw"] is None and d["error"] is None
    assert d["text"] == "好的，我整理了命题。"


def test_broken_block_keeps_full_raw_and_reports_location():
    d = CH.parse_reply_detail(reply_with(BROKEN))
    assert d["proposals"] == []                       # 不会猜测修复
    assert d["invalid"] == 1
    assert d["raw"] == BROKEN                          # 原文全文保留
    assert "第 1 行" in d["error"]                     # 报出位置，便于手动修


def test_unterminated_fence_keeps_everything_after_marker():
    reply = "正文\n```proposals\n" + GOOD          # 没有结束的 ```
    d = CH.parse_reply_detail(reply)
    assert d["raw"] == GOOD and "缺少结束" in d["error"]


def test_send_persists_raw_block_for_failed_turn(ws):
    r = CH.send(ws, "修改命题", "收敛", runner=lambda p, s: (reply_with(BROKEN), "sid"))
    assert r["proposals"] == [] and r["invalid"] == 1
    assert r["raw_proposals"] == BROKEN
    hist = CH.history(ws)
    assistant = [h for h in hist if h["role"] == "assistant"][-1]
    assert assistant["raw_proposals"] == BROKEN


def test_manual_fix_then_register_creates_pending_proposal(ws):
    before = len(P.list_items(ws))
    res = CH.register_proposal_block(ws, GOOD)
    assert len(res["proposals"]) == 1 and res["failures"] == []
    assert len(P.list_items(ws)) == before + 1
    assert res["proposals"][0]["status"] == "pending"   # 仍需采纳才写入卡片


def test_register_rejects_still_broken_text(ws):
    with pytest.raises(CH.BrainstormError):
        CH.register_proposal_block(ws, BROKEN)
    assert P.list_items(ws) == []


def test_repair_suggestion_is_not_registered_automatically(ws):
    suggestion = CH.suggest_repair(BROKEN)
    # 无论 json_repair 是否可用，修复建议都不能直接登记
    assert P.list_items(ws) == []
    if suggestion is not None:
        assert isinstance(json.loads(suggestion), list)
