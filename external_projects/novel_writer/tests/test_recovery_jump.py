"""状态恢复：跳到最新进度。"""

from pathlib import Path

import pytest

from novel_writer import config, recovery as REC
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.pipeline import build_plan
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("跳转", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G5", total_chapters_planned=3,
                              pause_after_ideation=True)
    return Workspace(r["data"]["workspace"])


def _plan_ids(ws):
    return [s.id for s in build_plan(ws)]


def test_no_progress_refuses(tmp_path):
    ws = _ws(tmp_path)
    info = REC.jump_preview(ws)
    assert info["target"] is None and "还没有" in info["reason"]


def test_jump_marks_everything_before_latest_progress(tmp_path):
    ws = _ws(tmp_path)
    ids = _plan_ids(ws)
    # 模拟：第 1 章的完章步骤已通过，之前的全书步骤仍是待运行或需重跑
    st.update_step(ws, "design_pacing", status=st.ST_STALE, note=f"{st._UPSTREAM_NOTE}（测试）")
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)
    info = REC.jump_preview(ws)
    assert info["last_finished"] == "ch01.sync"
    assert info["target"] == ids[ids.index("ch01.sync") + 1]
    marked = {i["id"] for i in info["to_mark"]}
    assert "init_workspace" in marked and "design_pacing" in marked
    assert "ch01.sync" not in marked

    REC.jump_apply(ws)
    states = st.load_state(ws)["steps"]
    assert states["design_pacing"]["status"] == st.ST_PASSED
    assert states["design_pacing"]["note"] == REC.JUMP_NOTE
    # 前沿现在直接是目标步骤
    nxt = tool_api.next_step(str(ws.root))["data"]
    assert nxt["step"] == info["target"]


def test_jump_refuses_when_author_must_act_before_target(tmp_path):
    ws = _ws(tmp_path)
    ids = _plan_ids(ws)
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)
    st.update_step(ws, "design_theme", status=st.ST_WAITING_USER)
    info = REC.jump_preview(ws)
    assert [b["id"] for b in info["blockers"]] == ["design_theme"]
    with pytest.raises(REC.RecoveryError):
        REC.jump_apply(ws)
    assert st.get_step_state(ws, "design_theme")["status"] == st.ST_WAITING_USER


def test_jump_is_refused_while_worker_alive(tmp_path):
    ws = _ws(tmp_path)
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)
    st.acquire_worker(ws, mode="until_gate")
    with pytest.raises(REC.RecoveryError):
        REC.jump_apply(ws)


def test_jump_backs_up_state_before_writing(tmp_path):
    ws = _ws(tmp_path)
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)
    res = REC.jump_apply(ws)
    backup_dir = Path(res["backup"])
    assert (backup_dir / "pipeline_state.json").exists()
    # 备份里的状态是跳转之前的（ch01.sync 之前的步骤还没有被标记）
    import json
    old = json.loads((backup_dir / "pipeline_state.json").read_text(encoding="utf-8"))["steps"]
    assert old.get("init_workspace", {}).get("note") != REC.JUMP_NOTE
