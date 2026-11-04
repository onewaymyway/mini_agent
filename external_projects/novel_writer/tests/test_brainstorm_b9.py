"""灵感构思 B9：文件模式——完整内容 + 模型可提出整体替换模块的提议（需作者确认，冲突检测）。"""

import json

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    C.put_module(w, "premise", "旧命题")
    C.put_module(w, "cast", {"lin": {"name": "林", "role": "主角", "relations": []}})
    return w


def _reply(items):
    return "改一下。\n```proposals\n" + json.dumps(items, ensure_ascii=False) + "\n```"


class _Runner:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, prompt, sid):
        self.prompts.append(prompt)
        return self.reply, None


def test_files_scope_can_be_selected(ws):
    assert CH.set_scope(ws, "files") == "files"


def test_replace_proposal_only_in_files_scope(ws):
    new_cast = {"lin": {"name": "林薇", "role": "主角", "relations": []}}
    reply = _reply([{"module": "cast", "op": "replace", "value": new_cast}])
    CH.set_scope(ws, "full")
    out = CH.send(ws, "改", runner=_Runner(reply))
    assert out["proposals"] == [] and out["invalid"] == 1          # 完整模式下拒绝整体替换
    CH.set_scope(ws, "files")
    out = CH.send(ws, "改", runner=_Runner(reply))
    assert len(out["proposals"]) == 1 and out["proposals"][0]["op"] == "replace"


def test_files_prompt_documents_replace_and_has_full_content(ws):
    CH.set_scope(ws, "files")
    prompt = CH.build_prompt(ws, "看看", "追问")
    assert "replace" in prompt and "整体替换" in prompt
    assert "林" in prompt and "旧命题" in prompt                    # 完整内容已附上


def test_full_prompt_has_no_replace_instruction(ws):
    CH.set_scope(ws, "full")
    assert "replace" not in CH.build_prompt(ws, "看看", "追问")


def test_replace_is_not_applied_until_accepted(ws):
    before = C.content_hash(ws)
    new_cast = {"lin": {"name": "林薇", "role": "主角", "relations": []}}
    P.create(ws, "cast", "replace", new_cast)
    assert C.content_hash(ws) == before
    P.accept(ws, P.list_items(ws, "pending")[0]["pid"])
    assert C.get_module(ws, "cast") == new_cast


def test_replace_value_must_be_valid_at_creation(ws):
    with pytest.raises(BrainstormError):
        P.create(ws, "premise", "replace", {"not": "text"})
    with pytest.raises(BrainstormError):
        P.create(ws, "world", "replace", {"rules": "不是列表"})


def test_replace_conflict_is_detected_and_keeps_proposal_pending(ws):
    P.create(ws, "premise", "replace", "新命题")
    pid = P.list_items(ws, "pending")[0]["pid"]
    C.put_module(ws, "premise", "作者又手改了")                    # 提议之后模块被改过
    with pytest.raises(BrainstormError) as exc:
        P.accept(ws, pid)
    assert "已被修改" in str(exc.value)
    assert C.get_module(ws, "premise") == "作者又手改了"           # 不覆盖作者的修改
    item = P.list_items(ws, "pending")[0]
    assert item["status"] == "pending" and "已被修改" in item["error"]


def test_replace_cannot_target_options(ws):
    with pytest.raises(BrainstormError):
        P.create(ws, "options", "replace", {"x": 1})


def test_preview_shows_before_and_after(ws):
    item = P.create(ws, "premise", "replace", "全新的命题")
    diff = P.preview(ws, item["pid"])
    assert "-\"旧命题\"" in diff.replace("\n", "") or "旧命题" in diff
    assert "全新的命题" in diff
    assert P.preview(ws, item["pid"]) != ""
    op_item = P.create(ws, "premise", "set", "另一个")
    assert P.preview(ws, op_item["pid"]) == ""


def test_api_preview(tmp_path):
    root = BA.create_project("雾", base=str(tmp_path))["data"]["root"]
    w = BrainstormWorkspace(root)
    item = P.create(w, "premise", "replace", "X")
    res = BA.proposal_preview(root, item["pid"])
    assert res["ok"] and "X" in res["data"]["diff"]


def test_end_to_end_model_replace_then_accept(ws):
    CH.set_scope(ws, "files")
    new_cast = {"lin": {"name": "林薇", "role": "反派", "relations": []}}
    out = CH.send(ws, "把林改成反派", runner=_Runner(_reply([{"module": "cast", "op": "replace",
                                                              "value": new_cast, "reason": "需要反转"}])))
    pid = out["proposals"][0]["pid"]
    assert C.get_module(ws, "cast")["lin"]["role"] == "主角"        # 确认前不变
    P.accept(ws, pid)
    assert C.get_module(ws, "cast")["lin"]["role"] == "反派"
