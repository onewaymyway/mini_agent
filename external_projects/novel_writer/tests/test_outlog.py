"""日志单写者：子进程输出走管道写入 agent.log，run.log 只由 log_line 写入，互不覆盖。"""

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from novel_writer import outlog, state as st, tool_api
from novel_writer import runner as R
from novel_writer.workspace import Workspace
from novel_writer import config

LINE_RE = re.compile(r"^\[\d\d:\d\d:\d\d\] parent-(\d{5}) 中文日志行 ——— 完整\s*$")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("日志测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def test_child_output_and_parent_log_do_not_overwrite_each_other(ws, tmp_path):
    """子进程持续输出大量多字节文本；父进程同时写 run.log。两边的每一行都必须完整保留。"""
    child_lines, parent_lines = 20000, 2000
    child_src = textwrap.dedent(f"""
        import sys
        for i in range({child_lines}):
            sys.stdout.write(f"child-{{i:05d}} 子进程输出：用于占满管道的多字节文本 ——— 完整\\n")
        sys.stdout.flush()
    """)
    proc = subprocess.Popen([sys.executable, "-u", "-c", child_src],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    pump = outlog.LinePump(proc.stdout, tmp_path / "agent.log")
    for i in range(parent_lines):
        st.log_line(ws, f"parent-{i:05d} 中文日志行 ——— 完整")
    proc.wait(timeout=120)
    assert pump.join(60)

    run_lines = ws.path("studio_log").read_text(encoding="utf-8").splitlines()
    got = [m.group(1) for l in run_lines for m in [LINE_RE.match(l)] if m]
    assert got == [f"{i:05d}" for i in range(parent_lines)]           # 父进程的行一行不少、顺序不乱
    assert not any("⚠" in l for l in run_lines)                        # 没有被外部改写的告警
    agent_lines = (tmp_path / "agent.log").read_text(encoding="utf-8").splitlines()
    assert len(agent_lines) == child_lines == pump.line_count
    assert all(l.startswith("child-") and l.endswith("完整") for l in agent_lines)   # 没有截断或交错
    assert "\ufffd" not in "".join(agent_lines)


def test_worker_stdout_is_not_run_log(ws, monkeypatch):
    """worker 进程的 stdout 必须指向独立文件，不能是 run.log 的句柄。"""
    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(args, **kwargs):
        captured["stdout"] = kwargs["stdout"]
        return FakeProc()

    monkeypatch.setattr(tool_api._subprocess, "Popen", fake_popen)
    pid = tool_api._spawn_worker(ws, "until_gate")
    assert pid == 4242
    assert Path(captured["stdout"].name).name == "worker.stdout.log"
    assert captured["stdout"].closed                                     # with 结束后句柄已关闭，不被父进程继续使用


def test_run_log_detects_external_truncation(ws):
    st.log_line(ws, "第一行")
    st.log_line(ws, "第二行")
    path = ws.path("studio_log")
    path.write_text("[00:00:00] 外部写入的一行\n", encoding="utf-8")      # 模拟被别的进程截断/覆盖
    st.log_line(ws, "第三行")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any("run.log 被外部缩小" in l for l in lines)
    assert lines[-1].endswith("第三行")


def test_subprocess_invoker_keeps_full_agent_output_in_its_own_file(ws, tmp_path, monkeypatch):
    """_invoke_subprocess：agent 输出写进本次调用的 agent.log，结果里带上位置与行数。"""
    stub_dir = tmp_path / "stubpkg"
    stub_dir.mkdir()
    (stub_dir / "stub_child.py").write_text(textwrap.dedent("""
        import json, sys
        from pathlib import Path
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        for i in range(3000):
            print(f"agent-{i:05d} 框架输出 ——— 完整", flush=True)
        Path(sys.argv[2]).write_text(json.dumps({"ok": True, "error": ""}), encoding="utf-8")
    """), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(stub_dir))
    inv = R.FrameworkInvoker()
    inv._CHILD_MODULE = "stub_child"
    run_dir = ws.step_dir("t.agent", 1)
    result = inv._invoke_subprocess(skill="x", prompt="p", export_dir=run_dir, step_id="t.agent",
                                    max_turns=None, remaining=60)
    assert result.ok
    assert result.output_lines == 3000
    agent = Path(result.output_path)
    assert agent.name == outlog.AGENT_LOG_NAME and agent.parent == run_dir
    lines = agent.read_text(encoding="utf-8").splitlines()
    assert [l.split()[0] for l in lines if l.startswith("agent-")] == [f"agent-{i:05d}" for i in range(3000)]
    log_text = ws.path("studio_log").read_text(encoding="utf-8") if ws.path("studio_log").exists() else ""
    assert "agent-00001" not in log_text                                 # agent 的逐行输出不再进入 run.log
