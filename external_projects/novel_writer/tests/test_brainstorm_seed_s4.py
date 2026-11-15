"""一句话起稿 S4：阅读视图渲染（纯函数）+ 界面冒烟（AppTest）。

界面只调用既有接口：本测试断言点击「预览 / 登记」后卡片哈希不变、提议出现在待确认列表。
"""
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm.workspace import BrainstormWorkspace
from test_brainstorm_seed_s0 import good

PROJECT = Path(__file__).resolve().parent.parent


# ── 阅读视图（纯函数） ──

def test_markdown_contains_all_six_parts_in_order():
    md = SD.draft_markdown(SD.validate_draft(good()))
    heads = [line for line in md.splitlines() if line.startswith("### ")]
    assert heads == ["### 核心命题", "### 故事走向", "### 世界观", "### 角色", "### 核心剧情", "### 待定问题"]


def test_markdown_shows_content_and_labels():
    md = SD.draft_markdown(SD.validate_draft(good()))
    assert "记忆是港口的货币" in md
    assert "**起**：她发现母亲的记忆被卖" in md
    assert "典当越多越健忘（代价：失去自我）" in md
    assert "**海棠**（定位：主角；想要：找回母亲）" in md
    assert "- 港口为何吞噬记忆？" in md


def test_markdown_marks_closed_question_and_handles_empty_lists():
    d = good()
    d["open_questions"] = ["问题甲"]
    d["world"]["taboos"] = []
    draft = SD.validate_draft(d)
    draft["open_questions"][0]["closed"] = True
    md = SD.draft_markdown(draft)
    assert "问题甲（已关闭）" in md
    assert "- （无）" in md


def test_markdown_is_read_only(tmp_path):
    ws = BrainstormWorkspace(tmp_path / "p")
    ws.create("p")
    SD.save_version(ws, good(), kind="seed", reason="x", seed={"idea": "i"})
    before = C.content_hash(ws)
    SD.draft_markdown(SD.load(ws)["draft"])
    assert C.content_hash(ws) == before


# ── 界面冒烟（AppTest） ──

@pytest.fixture
def project(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    root = BA.create_project("雾港", base=str(tmp_path))["data"]["root"]
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    return root


def _app(root):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    return at


def test_tab_added_at_end_and_existing_tabs_unchanged(project):
    at = _app(project)
    at.run()
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    assert labels == ["对话", "题材卡", "灵感池", "发散", "世界观", "角色", "剧情", "质检", "交接", "🌱 起稿"]


def test_empty_project_shows_hint_and_start_box(project):
    at = _app(project)
    at.run()
    assert not at.exception, at.exception
    assert any("还没有构思稿" in i.value for i in at.info)
    assert any(b.label == "▶ 起稿" for b in at.button)


def test_page_shows_latest_version_and_compare_restore(project):
    ws = BrainstormWorkspace(Path(project))
    SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "记忆当货币"})
    d2 = good()
    d2["premise"] = "记忆是港口的货币（修订）"
    SD.save_version(ws, d2, kind="revise", reason="反馈", seed=None)
    at = _app(project)
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["bs_seed_view"] == 2
    assert any("记忆是港口的货币（修订）" in m.value for m in at.markdown)
    assert any(b.key == "bs_seed_restore" for b in at.button)


def test_restore_button_adds_version_without_deleting(project):
    ws = BrainstormWorkspace(Path(project))
    SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "i"})
    d2 = good()
    d2["premise"] = "另一个命题"
    SD.save_version(ws, d2, kind="revise", reason="反馈", seed=None)
    at = _app(project)
    at.session_state["bs_seed_view"] = 1
    at.run()
    at.button(key="bs_seed_restore").click().run()
    assert not at.exception, at.exception
    assert [v["ver"] for v in BA.seed_versions(project)["data"]["versions"]] == [1, 2, 3]
    assert SD.load(ws, 3)["draft"]["premise"] == good()["premise"]


def test_adopt_preview_and_register_leaves_cards_untouched(project):
    ws = BrainstormWorkspace(Path(project))
    SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "i"})
    before = C.content_hash(ws)
    at = _app(project)
    at.run()
    at.button(key="bs_seed_plan_go").click().run()
    assert not at.exception, at.exception
    assert any(c.value and "预览：将生成" in c.value for c in at.caption)
    at.button(key="bs_seed_adopt_go").click().run()
    assert not at.exception, at.exception
    assert C.content_hash(ws) == before
    pending = P.list_items(ws, "pending")
    assert pending and all(p["reason"].startswith("[构思稿 v001]") for p in pending)
    assert any("请到「对话」标签" in s.value for s in at.success)


def test_adopt_button_disabled_until_preview(project):
    ws = BrainstormWorkspace(Path(project))
    SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "i"})
    at = _app(project)
    at.run()
    assert at.button(key="bs_seed_adopt_go").disabled


def test_finished_run_jumps_to_new_version_once_and_keeps_user_choice(project):
    """回归：任务完成后自动显示新版本；之后作者切换的版本不能被每次重跑拉回最新。"""
    from novel_writer.brainstorm import seed_gen as SG
    from test_brainstorm_seed_s1 import Gen, _wait

    ws = BrainstormWorkspace(Path(project))
    SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "i"})
    rid = SG.start_run(ws, "新想法", generator=Gen(good()))
    assert _wait(ws, rid)["status"] == "done"

    at = _app(project)
    at.session_state["bs_seed_last"] = rid
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["bs_seed_view"] == 2
    at.selectbox(key="bs_seed_view").set_value(1).run()
    assert at.session_state["bs_seed_view"] == 1
    at.run()
    assert at.session_state["bs_seed_view"] == 1


def test_revise_button_runs_in_background_and_shows_new_version(project, monkeypatch):
    """点「按反馈修订」：后台完成后显示新版本、提示修订完成；范围外的部分保持原样。"""
    import copy
    import time
    from test_brainstorm_seed_s1 import Gen

    ws = BrainstormWorkspace(Path(project))
    SD.save_version(ws, good(), kind="seed", reason="起稿", seed={"idea": "i"})
    revised = copy.deepcopy(good())
    revised["world"]["rules"] = ["典当只能一次"]
    orig = BA.seed_revise_start
    monkeypatch.setattr(BA, "seed_revise_start",
                        lambda root, fb, secs=None, base_ver=None, generator=None:
                        orig(root, fb, secs, base_ver=base_ver, generator=Gen(revised)))

    at = _app(project)
    at.run()
    at.multiselect(key="bs_seed_secs").set_value(["world"]).run()
    at.text_area(key="bs_seed_fb").input("世界观更简单").run()
    at.button(key="bs_seed_rev_go").click().run()
    assert not at.exception, at.exception
    for _ in range(200):
        at.run()
        if SD.latest_version(ws) == 2 and not at.session_state.get("bs_seed_run"):
            break
        time.sleep(0.05)
    assert not at.exception, at.exception
    assert SD.load(ws, 2)["kind"] == "revise"
    assert SD.load(ws, 2)["draft"]["premise"] == good()["premise"]   # 范围外（命题）保持原样
    assert at.session_state["bs_seed_view"] == 2
    assert any("修订完成" in s.value for s in at.success)
