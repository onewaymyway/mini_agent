"""日志可见性：无步骤时限的 skill 调用也走子进程（输出镜像进 run.log）；构思修订子步骤的运行序号与详情聚合。"""

import textwrap

import pytest

from novel_writer import concept_revision as CR
from novel_writer import config, outlog, tool_api
from novel_writer import runner as R
from novel_writer import step_timeout as STO
from novel_writer.workspace import Workspace


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("日志可见", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _stub_child(tmp_path, monkeypatch):
    stub_dir = tmp_path / "stubpkg"
    stub_dir.mkdir()
    (stub_dir / "stub_child.py").write_text(textwrap.dedent("""
        import json, sys
        from pathlib import Path
        for i in range(5):
            print(f"agent-step-{i}", flush=True)
        Path(sys.argv[2]).write_text(json.dumps({"ok": True, "error": ""}), encoding="utf-8")
    """), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(stub_dir))
    monkeypatch.setattr(R.FrameworkInvoker, "_CHILD_MODULE", "stub_child")


def test_no_deadline_still_runs_in_subprocess_and_mirrors_to_run_log(ws, tmp_path, monkeypatch):
    _stub_child(tmp_path, monkeypatch)
    assert STO.remaining_sec() is None          # 无步骤时限
    sink = outlog.run_log_sink(ws)
    outlog.set_line_sink(sink)
    try:
        res = R.FrameworkInvoker()(skill="x", prompt="p", export_dir=ws.step_dir("t.nolimit", 1), step_id="t.nolimit")
    finally:
        outlog.set_line_sink(None)
    assert res.ok and res.output_lines == 5
    text = ws.path("studio_log").read_text(encoding="utf-8")
    assert "agent-step-0" in text and "agent-step-4" in text
    assert "agent-step-4" in (ws.step_dir("t.nolimit", 1) / "agent.log").read_text(encoding="utf-8")


def test_with_deadline_behaves_the_same(ws, tmp_path, monkeypatch):
    _stub_child(tmp_path, monkeypatch)
    outlog.set_line_sink(outlog.run_log_sink(ws))
    try:
        with STO.deadline_scope(60.0):
            res = R.FrameworkInvoker()(skill="x", prompt="p", export_dir=ws.step_dir("t.limit", 1), step_id="t.limit")
    finally:
        outlog.set_line_sink(None)
    assert res.ok and "agent-step-2" in ws.path("studio_log").read_text(encoding="utf-8")


def test_next_run_no_increments_without_overwriting(ws):
    sid = "cpt.abc.blueprint"
    assert CR._next_run_no(ws, sid) == 1
    ws.step_dir(sid, 1).mkdir(parents=True)
    ws.step_dir(sid, 2).mkdir(parents=True)
    assert CR._next_run_no(ws, sid) == 3


def test_step_detail_aggregates_concept_substeps_and_agent_log(ws):
    for sid, n in (("cpt.abc.blueprint", 1), ("cpt.abc.theme", 2)):
        d = ws.step_dir(sid, n)
        d.mkdir(parents=True)
        (d / "prompt_1.md").write_text("提示词", encoding="utf-8")
        (d / "agent.log").write_text("第一行\n第二行\n", encoding="utf-8")
    d = tool_api.step_detail(str(ws.root), "cpt.abc")["data"]
    assert d["runs"] == 0
    assert {s["step"] for s in d["substeps"]} == {"cpt.abc.blueprint", "cpt.abc.theme"}
    assert all("第二行" in s["agent_log_tail"] for s in d["substeps"])
    # 普通步骤也带 agent 输出
    n = ws.step_dir("plan_blueprint", 1)
    n.mkdir(parents=True)
    (n / "agent.log").write_text("x\ny\n", encoding="utf-8")
    assert tool_api.step_detail(str(ws.root), "plan_blueprint")["data"]["agent_log_tail"] == "x\ny"
