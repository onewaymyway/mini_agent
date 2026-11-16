"""起稿输入记录：每次起稿 / 修订的完整输入、状态与结果都被记录，新到旧，失败与拒绝也记录。"""
import json

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import seed_gen as SG
from novel_writer.brainstorm import seed_history as H
from novel_writer.brainstorm import seed_revise as SR
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from test_brainstorm_seed_s0 import good
from test_brainstorm_seed_s1 import _wait


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "p")
    w.create("p")
    return w


class G:
    def __init__(self, *replies):
        self.replies = list(replies)

    def __call__(self, prompt):
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


def test_successful_seed_run_records_full_input_and_version(ws):
    long_idea = "记忆是货币的港口，" * 40
    SG.run(ws, long_idea, "要悬疑", target_chapters=30, structure="三幕", generator=G(good()))
    e = BA.seed_inputs(str(ws.root))["data"]["entries"][0]
    assert e["kind"] == "seed" and e["status"] == "done" and e["ver"] == 1
    assert e["input"]["idea"] == long_idea                       # 原文，不截断
    assert e["input"]["constraints"] == "要悬疑" and e["input"]["target_chapters"] == 30
    assert e["input"]["structure"] == "三幕" and e.get("submitted_at") and e.get("finished_at")


def test_failed_seed_run_records_error(ws):
    with pytest.raises(BrainstormError):
        SG.run(ws, "想法", generator=G("不是 JSON", "还是不是"))
    e = H.history(ws)[0]
    assert e["status"] == "failed" and "次都未通过" in e["error"] and e["input"]["idea"] == "想法"


def test_rejected_submissions_are_recorded(ws):
    with pytest.raises(BrainstormError):
        SG.start_run(ws, "   ")                                    # 空想法
    with pytest.raises(BrainstormError):
        SR.start_run(ws, "")                                      # 空反馈（还没有构思稿也会先被拒绝）
    statuses = [e["status"] for e in H.history(ws)]
    assert statuses == ["rejected", "rejected"]


def test_revise_run_records_long_feedback_scope_and_base(ws):
    SG.run(ws, "想法", generator=G(good()))
    feedback = "把世界观改得更黑暗。" * 150                          # 远超摘要长度，应完整保存
    fixed = good()
    fixed["world"]["taboos"] = ["新禁忌"]
    out = SR.run(ws, feedback, ["world"], base_ver=1, generator=G({"world": fixed["world"]}))
    e = H.history(ws)[0]
    assert e["kind"] == "revise" and e["status"] == "done" and e["ver"] == out["ver"]
    assert e["input"]["feedback"] == feedback and e["input"]["sections"] == ["world"] and e["input"]["base_ver"] == 1


def test_background_revise_is_recorded_once_with_result(ws):
    SG.run(ws, "想法", generator=G(good()))
    fixed = good()
    fixed["premise"] = "新的命题"
    rid = SR.start_run(ws, "改命题", ["premise"], generator=G({"premise": fixed["premise"]}))
    assert _wait(ws, rid)["status"] == "done"
    items = [e for e in H.history(ws) if e["kind"] == "revise"]
    assert len(items) == 1 and items[0]["status"] == "done" and items[0]["input"]["feedback"] == "改命题"


def test_history_is_newest_first(ws):
    SG.run(ws, "第一个", generator=G(good()))
    SG.run(ws, "第二个", generator=G(good()))
    ideas = [e["input"]["idea"] for e in H.history(ws)]
    assert ideas == ["第二个", "第一个"]
