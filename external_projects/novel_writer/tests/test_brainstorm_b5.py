"""灵感构思工作室 B5：交接到小说（定版约束、向导意图映射、种子复制与溯源、版本选择、提示词注入）。"""

import json
from pathlib import Path

import pytest

from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import handoff as H
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.runner import build_prompt
from novel_writer.workspace import Workspace

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "雾港")
    w.create("雾港")
    C.put_module(w, "brief", {"genre": "悬疑", "theme": "记忆与代价", "protagonist": "守灯人林"})
    C.put_module(w, "premise", "一座用记忆换航线的港口")
    C.put_module(w, "world", {"rules": [{"text": "雾中可见死者", "cost": "失去一段记忆"}],
                              "locations": [{"text": "钟楼"}]})
    C.put_module(w, "cast", {"c1": {"name": "林", "role": "主角", "want": "找回弟弟"}})
    C.put_module(w, "plot", {"acts": [{"title": "上升", "beats": ["入港"]}], "twists": ["守灯人是灯"],
                             "endings": ["开放"]})
    C.put_module(w, "open_questions", [{"id": "q1", "text": "弟弟还活着吗", "closed": False}])
    C.snapshot(w, "初稿")
    return w


@pytest.fixture
def novels(tmp_path):
    return {"novels_dir": str(tmp_path / "novels"), "index_file": str(tmp_path / "shelf.json")}


# ── 定版约束 ──

def test_handoff_requires_a_snapshot(tmp_path):
    w = BrainstormWorkspace(tmp_path / "空")
    w.create("空")
    with pytest.raises(BrainstormError):
        H.handoff(w, novels_dir=str(tmp_path / "n"), index_file=str(tmp_path / "s.json"))


def test_handoff_rejects_missing_version(bws, novels):
    with pytest.raises(BrainstormError):
        H.handoff(bws, version=9, **novels)


# ── 创建与映射 ──

def test_handoff_creates_novel_with_intake_from_brief(bws, novels):
    res = H.handoff(bws, **novels)
    root = Path(res["novel_root"])
    assert (root / "_studio").is_dir() and Workspace(root).is_initialized()
    intake = tool_api.get_intake(str(root))["data"]["intake"]
    assert intake["genre"] == "悬疑" and intake["protagonist"] == "守灯人林"
    assert intake["ending"] == ""                                       # 题材卡没填的字段保持为空
    assert res["title"] == "雾港" and res["version"] == 1


def test_handoff_custom_title(bws, novels):
    res = H.handoff(bws, title="雾港·第二部", **novels)
    assert res["title"] == "雾港·第二部"


# ── 种子复制、溯源、摘要 ──

def test_seed_copied_with_provenance(bws, novels):
    res = H.handoff(bws, **novels)
    root = Path(res["novel_root"])
    seed = root / H.SEED_DIR_REL / "v1"
    assert (seed / "premise.md").read_text(encoding="utf-8").strip() == "一座用记忆换航线的港口"
    assert (seed / "version.json").is_file()
    source = json.loads((root / H.SEED_DIR_REL / "source.json").read_text(encoding="utf-8"))
    assert source["version"] == 1
    version_info = json.loads((bws.version_dir(1) / "version.json").read_text(encoding="utf-8"))
    assert source["content_hash"] == version_info["hash"] and len(source["content_hash"]) == 64
    assert source["brainstorm_title"] == "雾港" and source["handed_off_at"]


def test_seed_note_summarises_the_card_content(bws, novels):
    res = H.handoff(bws, **novels)
    note = (Path(res["novel_root"]) / "_studio" / "seed" / "brainstorm_seed.md").read_text(encoding="utf-8")
    for fragment in ("用记忆换航线", "雾中可见死者", "失去一段记忆", "林（主角）", "想要找回弟弟",
                     "第1幕「上升」", "守灯人是灯", "开放", "弟弟还活着吗"):
        assert fragment in note, fragment


def test_seed_note_is_truncated_with_pointer(bws, tmp_path):
    C.put_module(bws, "premise", "长" * 5000)
    C.snapshot(bws, "长")
    note = H.build_seed_note(bws.version_dir(2), "雾港", 2)
    assert len(note) < 3300 and "完整内容见" in note


# ── 版本选择：交接的是所选版本，不是草稿 ──

def test_handoff_uses_chosen_version_not_working_draft(bws, novels):
    C.put_module(bws, "premise", "第二版命题")
    C.snapshot(bws, "第二版")
    C.put_module(bws, "premise", "未定版的草稿")                         # 草稿继续改动
    res = H.handoff(bws, version=1, **novels)
    seed_premise = (Path(res["novel_root"]) / H.SEED_DIR_REL / "v1" / "premise.md").read_text(encoding="utf-8")
    assert "一座用记忆换航线的港口" in seed_premise and "未定版" not in seed_premise


def test_default_version_is_latest_snapshot(bws, novels):
    C.put_module(bws, "premise", "第二版命题")
    C.snapshot(bws, "第二版")
    res = H.handoff(bws, **novels)
    assert res["version"] == 2


def test_snapshots_after_handoff_do_not_change_seed(bws, novels):
    res = H.handoff(bws, **novels)
    before = (Path(res["novel_root"]) / H.SEED_DIR_REL / "v1" / "premise.md").read_text(encoding="utf-8")
    C.put_module(bws, "premise", "之后的改动")
    C.snapshot(bws, "之后")
    after = (Path(res["novel_root"]) / H.SEED_DIR_REL / "v1" / "premise.md").read_text(encoding="utf-8")
    assert before == after                                              # 交接之后不自动同步


# ── 记录与重复交接 ──

def test_linked_novels_recorded_and_multiple_handoffs_allowed(bws, novels):
    H.handoff(bws, **novels)
    H.handoff(bws, title="第二本", **novels)
    linked = H.linked_novels(bws)
    assert [x["title"] for x in linked] == ["雾港", "第二本"]
    assert bws.meta()["linked_novels"] == linked


# ── 提示词注入（运行器） ──

def _step():
    return S.StepDef(id="build_world", title="建立世界观设定", stage="p1", category=S.CAT_BUILD,
                     executor=S.EXECUTOR_PYTHON, handler="x")


def test_prompt_includes_seed_for_handed_off_novel(bws, novels):
    res = H.handoff(bws, **novels)
    prompt = build_prompt(Workspace(res["novel_root"]), _step())
    assert "构思种子" in prompt and "仅供参考，与蓝图冲突时以蓝图为准" in prompt
    assert "用记忆换航线" in prompt and "悬疑" in prompt                  # 向导意图与种子都在


def test_prompt_without_seed_is_unchanged_in_shape(tmp_path):
    root = tmp_path / "普通"
    ws = Workspace(root)
    ws.init("普通", gear="G0", quality="standard", total_chapters_planned=0)
    prompt = build_prompt(ws, _step())
    assert "构思种子" not in prompt


def test_resaving_intake_keeps_seed(bws, novels):
    res = H.handoff(bws, **novels)
    root = res["novel_root"]
    tool_api.save_intake(root, {"genre": "科幻"})
    prompt = build_prompt(Workspace(root), _step())
    assert "用记忆换航线" in prompt                                      # 种子独立存放，不随向导重存而丢失


# ── 接口与页面 ──

def test_api_handoff_and_linked(bws, novels):
    ok = BA.handoff_to_novel(str(bws.root), **novels)
    assert ok["ok"] is True and Path(ok["data"]["novel_root"]).is_dir()
    assert BA.linked_novels(str(bws.root))["data"][0]["title"] == "雾港"
    fail = BA.handoff_to_novel(str(bws.root), version=99, **novels)
    assert fail["ok"] is False and fail["error_type"] == "BrainstormError"


def test_page_has_handoff_tab(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("港", base=str(tmp_path))["data"]["root"]
    BA.snapshot_version(root, "一")
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][-1] == "交接"
