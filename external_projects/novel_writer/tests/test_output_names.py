"""产出文件名：智能体改名时的归一化（只在唯一匹配时）；候选稿指令写死精确路径；面板随下一步状态变化跟随。"""

from __future__ import annotations

import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from fakes import STUB_HANDLERS  # noqa: F401
from novel_writer import pipeline as P
from novel_writer import runner as R
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.workspace import Workspace
from test_m3 import isolated, new_book  # noqa: F401  (fixture)

CANON = "正文/_drafts/第11章_候选B.md"


def _ws_with_drafts(isolated):
    ws = new_book(isolated, total=2)
    (ws.root / "正文" / "_drafts").mkdir(parents=True, exist_ok=True)
    return ws


def _alt_step(n=11):
    return S.alt_write_step(n, prev_last="ch11.write_chapter")


def test_single_fresh_misnamed_candidate_is_normalized(isolated):
    ws = _ws_with_drafts(isolated)
    since = time.time() - 5
    odd = ws.root / "正文" / "_drafts" / "第11章 赵天豪的影子_候选B.md"
    odd.write_text("候选稿正文", encoding="utf-8")
    renamed = R._normalize_outputs(ws, _alt_step(), since=since)
    assert renamed and not odd.exists()
    assert (ws.root / CANON).read_text(encoding="utf-8") == "候选稿正文"
    assert any("已归一化" in line for line in ws.path("studio_log").read_text(encoding="utf-8").splitlines())


def test_ambiguous_candidates_are_not_guessed(isolated):
    ws = _ws_with_drafts(isolated)
    since = time.time() - 5
    d = ws.root / "正文" / "_drafts"
    (d / "第11章 甲_候选B.md").write_text("a", encoding="utf-8")
    (d / "第11章 乙_候选B.md").write_text("b", encoding="utf-8")
    assert R._normalize_outputs(ws, _alt_step(), since=since) == []
    assert not (ws.root / CANON).exists()


def test_stale_files_from_earlier_runs_are_ignored(isolated):
    ws = _ws_with_drafts(isolated)
    old = ws.root / "正文" / "_drafts" / "第11章 旧稿_候选B.md"
    old.write_text("旧", encoding="utf-8")
    import os
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert R._normalize_outputs(ws, _alt_step(), since=time.time() - 5) == []


def test_candidate_instructions_pin_the_exact_path(isolated):
    ws = new_book(isolated, total=2)
    step = _alt_step()
    assert CANON in step.instructions
    prompt = R.build_prompt(ws, step, hint="")
    assert CANON in prompt


def test_panel_follows_when_next_step_status_changes(isolated):
    """回归：手动选过别的步骤后，下一步从「待运行」变为「失败」，选择框必须自动跟随到它。"""
    ws = new_book(isolated, total=2)
    assert tool_api.update_settings(str(ws.root), {"quality": "premium"})["ok"]
    for sid in ["init_workspace", "plan_blueprint", "design_theme", "design_pacing", "build_characters",
                "build_world", "register_plot", "init_word_count", "design_opening", "ch01.word_quota",
                "ch01.plot_tasks", "ch01.context_pack", "ch01.style_hint", "ch01.write_chapter"]:
        st.update_step(ws, sid, status=st.ST_PASSED)
    root_app = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(root_app), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    at.selectbox(key="cp_step_pick").set_value("ch01.style_hint").run()  # 作者手动选了别的步骤
    st.update_step(ws, "ch01.alt_write", status=st.ST_FAILED, error="缺少产出")
    at.run()  # 监视器检测到变化后的整页刷新
    assert at.selectbox(key="cp_step_pick").value == "ch01.alt_write"
