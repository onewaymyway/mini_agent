"""回归：章节修改后「整本书从头重跑」的两个成因。

1. 章节修改产生的「需重跑」标记曾经对所有步骤生效，包括第 0 章的全书前序步骤，
   托管挡（G5）不再确认，直接重跑。现在章节修改只自动重跑该章的步骤。
2. pipeline_state.json 的读-改-写原先只有进程内的线程锁。看板、批量入口、worker
   是不同进程，会互相覆盖步骤状态（丢失更新）。现在有跨进程文件锁。
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from novel_writer import config
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.pipeline import build_plan
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import revision_autorun_covers, run_worker
from novel_writer.workspace import Workspace

from fakes import FakeInvoker

PKG_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("自动重跑范围", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G5", total_chapters_planned=3,
                              pause_after_ideation=True)
    ws = Workspace(r["data"]["workspace"])
    ws.save_settings({**ws.settings(), "gear": "G5"})
    return ws


def _prepare_stale_global_step(ws):
    """审核之前全部通过；全书级的节奏设计变为需重跑（第 0 章，全书前序步骤）。"""
    plan = build_plan(ws)
    review_at = [s.id for s in plan].index(S.IDEATION_REVIEW_ID)
    for step in plan[:review_at]:
        st.update_step(ws, step.id, status=st.ST_PASSED)
    st.update_step(ws, "design_pacing", status=st.ST_STALE, note=f"{st._UPSTREAM_NOTE}（测试）")


# ── 1. 修改范围 ─────────────────────────────────────────────────────────


def test_chapter_scoped_arming_does_not_rerun_global_steps(tmp_path):
    """章节 36 的修改产生的需重跑，不能让第 0 章的全书步骤（节奏设计）自动重跑。"""
    ws = _ws(tmp_path)
    _prepare_stale_global_step(ws)
    st.arm_revision_autorun(ws, chapter=36)
    res = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS,
                     heartbeat_interval=0.01)
    assert res["reason"] == "stale_confirm", res
    assert st.get_step_state(ws, "design_pacing")["status"] == st.ST_STALE


def test_unscoped_arming_keeps_book_level_behavior(tmp_path):
    """全书/蓝图级修订（不限章节）保持原有行为：影响范围内的步骤自动重跑。"""
    ws = _ws(tmp_path)
    _prepare_stale_global_step(ws)
    st.arm_revision_autorun(ws)
    res = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS,
                     heartbeat_interval=0.01)
    assert res["reason"] != "stale_confirm", res
    assert st.get_step_state(ws, "design_pacing")["status"] == st.ST_PASSED


def test_revision_covers_only_the_revised_chapter():
    control = {"revision_autorun": True, "revision_autorun_chapter": 36}
    assert revision_autorun_covers(control, S.StepDef(id="ch36.sync", title="t", stage="p", category="c",
                                                     executor="skill", chapter=36))
    assert not revision_autorun_covers(control, S.StepDef(id="ch35.sync", title="t", stage="p", category="c",
                                                         executor="skill", chapter=35))
    assert not revision_autorun_covers(control, S.StepDef(id="design_pacing", title="t", stage="p",
                                                         category="c", executor="skill", chapter=0))


def test_revision_not_armed_covers_nothing():
    step = S.StepDef(id="ch36.sync", title="t", stage="p", category="c", executor="skill", chapter=36)
    assert not revision_autorun_covers({"revision_autorun": False, "revision_autorun_chapter": 36}, step)
    assert not revision_autorun_covers({}, step)


# ── 2. 跨进程状态锁 ──────────────────────────────────────────────────────

_WRITER_SCRIPT = textwrap.dedent("""
    import sys
    from novel_writer import state as st
    from novel_writer.workspace import Workspace

    ws = Workspace(sys.argv[1])
    tag = sys.argv[2]
    for i in range(40):
        st.update_step(ws, f"{tag}.{i}", status=st.ST_PASSED)
""")


def test_concurrent_processes_do_not_lose_step_updates(tmp_path):
    """多个进程同时改写 pipeline_state.json，所有写入都必须保留（不能互相覆盖）。"""
    ws = _ws(tmp_path)
    env = {**os.environ, "NOVEL_WRITER_DATA_DIR": str(tmp_path),
           "PYTHONPATH": str(PKG_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER_SCRIPT, str(ws.root), f"p{k}"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for k in range(4)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=240)
        assert proc.returncode == 0, err.decode("utf-8", "replace")

    steps = st.load_state(ws)["steps"]
    expected = {f"p{k}.{i}" for k in range(4) for i in range(40)}
    missing = expected - set(steps)
    assert not missing, f"丢失 {len(missing)} 次步骤状态写入，例如 {sorted(missing)[:5]}"


def test_state_lock_is_reentrant_in_one_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with st._LOCK:
        with st._LOCK:
            pass  # 同一线程嵌套获取不能死锁
