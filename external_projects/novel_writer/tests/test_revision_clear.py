"""一键清空评估数据：必须确认；删除评估文件、未执行的评估变更与其预览；保留版本、已应用的变更与手动变更。不调用真实 LLM。"""

import json

import pytest

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import review_adopt as RA
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_clear as RCL
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, revision_root
from novel_writer.revision_flow import review_cards as RC

from test_revision_review_adopt import _ready
from test_revision_review_llm import ReviewStub


def _populate(ws):
    RK.run_checks(ws)
    RL.run_review(ws, None, ReviewStub(whole_issue=False))
    win = next(f for f in RK.load_findings(ws, "v001")["findings"] if f["check"] == "window_review")
    adopted = RA.adopt(ws, None, win["id"], allow_unverified=True, target_chapters=[2])     # 评估来源的草稿变更
    manual = CH.save_change(ws, {"kind": "chapter", "title": "手动修改", "reason": "作者自己加的",
                                 "instruction": "把第二章的标题改成更醒目的样子", "chapters": [2]})
    return adopted, manual


def test_clear_requires_explicit_confirmation(tmp_path):
    ws = _ready(tmp_path)
    _populate(ws)
    with pytest.raises(FlowError, match="确认"):
        RCL.clear(ws, confirm=False)
    assert (revision_root(ws) / "review").exists()                         # 没有确认就什么都不删


def test_summary_previews_without_changing_anything(tmp_path):
    ws = _ready(tmp_path)
    adopted, manual = _populate(ws)
    before = sorted(p.name for p in revision_root(ws).rglob("*"))
    sm = RCL.summary(ws)
    assert adopted["id"] in sm["draft_changes"] and manual["id"] not in sm["draft_changes"]
    assert sorted(p.name for p in revision_root(ws).rglob("*")) == before


def test_clear_removes_evaluation_but_keeps_versions_and_manual_changes(tmp_path):
    ws = _ready(tmp_path)
    adopted, manual = _populate(ws)
    head_before = V.load_index(ws)["head"]
    res = RCL.clear(ws, confirm=True)
    assert "review" in res["removed_dirs"] and adopted["id"] in res["removed_changes"]
    assert not (revision_root(ws) / "review").exists()
    assert not (revision_root(ws) / "pipeline").exists()
    ids = {c["id"] for c in CH.list_changes(ws)}
    assert adopted["id"] not in ids and manual["id"] in ids               # 手动变更保留
    assert V.load_index(ws)["head"] == head_before and V.verify(ws, head_before) == []   # 版本完好


def test_applied_evaluation_changes_are_kept_as_history(tmp_path):
    ws = _ready(tmp_path)
    adopted, _ = _populate(ws)
    path = CH._change_dir(ws) / f"{adopted['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"], data["applied_version"] = "applied", "v002"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    RCL.clear(ws, confirm=True)
    assert adopted["id"] in {c["id"] for c in CH.list_changes(ws)}        # 已应用的历史不删


def test_previews_referencing_removed_changes_are_removed(tmp_path):
    ws = _ready(tmp_path)
    adopted, manual = _populate(ws)
    pv = CH.build_preview(ws, "v001", [adopted["id"]])
    preview_file = revision_root(ws) / "preview" / f"{pv['id']}.json"
    assert preview_file.exists()
    RCL.clear(ws, confirm=True)
    assert not preview_file.exists()


def test_after_clear_evaluation_starts_from_scratch(tmp_path):
    ws = _ready(tmp_path)
    _populate(ws)
    RCL.clear(ws, confirm=True)
    with pytest.raises(FlowError, match="事实卡"):
        RK.run_checks(ws)                                                  # 事实卡已清空，需要重新生成
    RC.generate_cards(ws, None, __import__("test_revision_review_cards").CardInvoker())
    assert RK.run_checks(ws)["findings"] is not None


def test_clear_control_renders_on_review_tab(tmp_path, monkeypatch):
    from test_revision_review_llm import _render
    ws = _ready(tmp_path)
    _populate(ws)
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert any("清空评估数据" in e.label for e in at.expander)


def test_keep_cards_clears_results_but_keeps_fact_cards(tmp_path):
    ws = _ready(tmp_path)
    adopted, manual = _populate(ws)
    cards_before = sorted(p.relative_to(revision_root(ws)).as_posix() for p in (revision_root(ws) / "review" / "cards").rglob("*") if p.is_file())
    assert cards_before
    res = RCL.clear(ws, confirm=True, keep_cards=True)
    assert res["keep_cards"] is True
    cards_after = sorted(p.relative_to(revision_root(ws)).as_posix() for p in (revision_root(ws) / "review" / "cards").rglob("*") if p.is_file())
    assert cards_after == cards_before                                     # 事实卡一个不少
    review = revision_root(ws) / "review"
    assert not any(c.name != "cards" for c in review.iterdir())             # 评估结果都已删除
    assert adopted["id"] not in {c["id"] for c in CH.list_changes(ws)} and manual["id"] in {c["id"] for c in CH.list_changes(ws)}
    RK.run_checks(ws)                                                      # 不需要重新生成事实卡


def test_keep_cards_summary_reports_cards_kept(tmp_path):
    ws = _ready(tmp_path)
    _populate(ws)
    sm = RCL.summary(ws, keep_cards=True)
    assert sm["keep_cards"] is True and sm["cards_kept"] > 0
    assert sm["files"]["review"] >= 0
    before = sorted(p.name for p in revision_root(ws).rglob("*"))
    RCL.summary(ws, keep_cards=True)
    assert sorted(p.name for p in revision_root(ws).rglob("*")) == before   # 预览不改动文件
