"""R23 第三期：队列报告（report.json / report.md）、命令行入口 entrypoints/batch_run.py、退出码与中断。"""
import json
import os
import subprocess
import sys

import pytest

from conftest import PROJECT_DIR
from article_to_video.batch import report, runner, store

sys.path.insert(0, str(PROJECT_DIR / "entrypoints"))
import batch_run  # noqa: E402

ARTICLE = "# 标题甲\n\n作者：某作者\n\n## 第一节\n\n这是第一节的正文内容。\n\n## 第二节\n\n这是第二节的正文内容。\n"


def add(root, n=1):
    out = []
    for i in range(n):
        text = ARTICLE + f"\n独有内容{i}\n"
        out.append(store.enqueue(root, source_text=text, source_name=f"a{i}.md",
                                 title=f"文章{i + 1}", author=f"作者{i + 1}")["item"])
    return out


def ok_runner(pdir, params):
    (pdir / "budget.json").write_text(json.dumps({"image_calls": 5}), encoding="utf-8")
    return {"state": "done", "outputs": [str(pdir / "exports" / "成片.mp4")]}


def fail_runner(pdir, params):
    return {"state": "failed", "error": "坏|字\n第二行"}


def states(root):
    return [i["state"] for i in store.load(root)["items"]]


def read_report(root):
    return json.loads((store.queue_dir(root) / "report.json").read_text(encoding="utf-8"))


# ---------- 报告 ----------

def test_report_on_empty_queue_writes_both_files(tmp_path):
    rep = report.write_report(tmp_path)
    assert rep["total"] == 0 and rep["items"] == []
    assert (store.queue_dir(tmp_path) / "report.json").is_file()
    md = (store.queue_dir(tmp_path) / "report.md").read_text(encoding="utf-8")
    assert "队列为空" in md and "批量生成队列报告" in md


def test_report_rows_carry_state_images_outputs_and_error(tmp_path):
    store.set_params(tmp_path, {"max_retries": 0})
    add(tmp_path, 2)
    calls = []

    def run(pdir, params):
        calls.append(pdir.name)
        return ok_runner(pdir, params) if len(calls) == 1 else fail_runner(pdir, params)

    runner.run_queue(tmp_path, pipeline_runner=run)
    rep = read_report(tmp_path)
    a, b = rep["items"]
    assert a["state"] == "done" and a["image_calls"] == 5
    assert a["outputs"] == [f"{a['project_id']}/exports/成片.mp4"]          # 相对于项目集合根目录
    assert a["wall_seconds"] is not None and a["wall_seconds"] >= 0
    assert b["state"] == "failed" and b["error"] == "坏|字\n第二行" and b["failures"] == 1
    assert rep["counts"]["done"] == 1 and rep["counts"]["failed"] == 1
    assert rep["totals"]["image_calls"] == 5 and rep["totals"]["failed_items"] == [b["id"]]


def test_markdown_table_is_not_broken_by_pipe_or_newline(tmp_path):
    store.set_params(tmp_path, {"max_retries": 0})
    store.enqueue(tmp_path, source_text=ARTICLE, source_name="x.md", title="甲|乙", author="丙\n丁")
    runner.run_queue(tmp_path, pipeline_runner=fail_runner)
    md = (store.queue_dir(tmp_path) / "report.md").read_text(encoding="utf-8")
    table_rows = [ln for ln in md.splitlines() if ln.startswith("| q")]
    assert len(table_rows) == 1 and "甲\\|乙" in table_rows[0]          # 标题里的管道符已转义，整行仍是一行
    assert "丙 丁" in table_rows[0]                                       # 作者里的换行被压成空格
    bullet = [ln for ln in md.splitlines() if ln.startswith("- q0001")]
    assert bullet and "第二行" in bullet[0]                               # 错误原因中的换行被压成空格，仍在同一行


def test_report_written_at_end_of_every_run_even_when_items_fail(tmp_path):
    store.set_params(tmp_path, {"max_retries": 0})
    add(tmp_path, 1)
    runner.run_queue(tmp_path, pipeline_runner=fail_runner)
    assert read_report(tmp_path)["counts"]["failed"] == 1


def test_report_written_when_run_is_interrupted(tmp_path):
    add(tmp_path, 1)

    def interrupted(pdir, params):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.run_queue(tmp_path, pipeline_runner=interrupted)
    rep = read_report(tmp_path)
    assert rep["items"][0]["state"] == "stopped" and not rep["runner_alive"]


def test_report_failure_does_not_break_the_queue(tmp_path, monkeypatch):
    add(tmp_path, 1)

    def broken(root, **kw):
        raise OSError("磁盘写满")

    monkeypatch.setattr(report, "write_report", broken)
    res = runner.run_queue(tmp_path, pipeline_runner=ok_runner)
    assert res["started"] and states(tmp_path) == ["done"]               # 报告写失败只忽略


def test_render_markdown_flags_items_with_reasons_only(tmp_path):
    add(tmp_path, 1)
    rep = report.build_report(tmp_path)
    md = report.render_markdown(rep)
    assert "## 逐篇" in md and "## 未完成原因" not in md                  # 排队中的条目没有失败原因


# ---------- 命令行入口 ----------

def test_status_prints_summary_and_does_not_run(tmp_path, capsys):
    add(tmp_path, 2)
    called = []
    code = batch_run.main(["--root", str(tmp_path), "--status"],
                          pipeline_runner=lambda p, q: called.append(1) or ok_runner(p, q))
    assert code == 0 and called == []
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2 and out["counts"]["queued"] == 2


def test_run_processes_queue_and_writes_report(tmp_path, capsys):
    add(tmp_path, 2)
    code = batch_run.main(["--root", str(tmp_path)], pipeline_runner=ok_runner)
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"] is True and out["processed"] == 2
    assert states(tmp_path) == ["done", "done"]
    assert out["report"].endswith("report.md") and (store.queue_dir(tmp_path) / "report.md").is_file()


def test_run_exit_code_is_1_when_any_item_failed(tmp_path, capsys):
    store.set_params(tmp_path, {"max_retries": 0})
    add(tmp_path, 1)
    code = batch_run.main(["--root", str(tmp_path)], pipeline_runner=fail_runner)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ok"] is False and out["counts"]["failed"] == 1


def test_requeue_all_then_run_completes_failed_items(tmp_path, capsys):
    store.set_params(tmp_path, {"max_retries": 0})
    add(tmp_path, 2)
    batch_run.main(["--root", str(tmp_path)], pipeline_runner=fail_runner)
    capsys.readouterr()
    code = batch_run.main(["--root", str(tmp_path), "--requeue"], pipeline_runner=ok_runner)
    assert code == 0 and states(tmp_path) == ["done", "done"]


def test_requeue_single_item_only(tmp_path, capsys):
    store.set_params(tmp_path, {"max_retries": 0})
    a, b = add(tmp_path, 2)
    batch_run.main(["--root", str(tmp_path)], pipeline_runner=fail_runner)
    capsys.readouterr()
    batch_run.main(["--root", str(tmp_path), "--requeue", b["id"]], pipeline_runner=ok_runner)
    assert states(tmp_path) == ["failed", "done"]                        # 只重排了第二篇


def test_requeue_unknown_id_is_an_error(tmp_path, capsys):
    add(tmp_path, 1)
    code = batch_run.main(["--root", str(tmp_path), "--requeue", "q9999"], pipeline_runner=ok_runner)
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and "没有这个条目" in out["error"]


def test_busy_runner_refuses_to_start_a_second_one(tmp_path, capsys):
    add(tmp_path, 1)
    store.touch_runner(tmp_path)                                         # 模拟界面或另一进程正在运行
    called = []
    code = batch_run.main(["--root", str(tmp_path)], pipeline_runner=lambda p, q: called.append(1) or ok_runner(p, q))
    out = json.loads(capsys.readouterr().out)
    assert code == 3 and called == [] and "已有运行者" in out["error"]
    assert states(tmp_path) == ["queued"]


def test_report_only_mode_does_not_run_anything(tmp_path, capsys):
    add(tmp_path, 1)
    called = []
    code = batch_run.main(["--root", str(tmp_path), "--report"],
                          pipeline_runner=lambda p, q: called.append(1) or ok_runner(p, q))
    assert code == 0 and called == [] and states(tmp_path) == ["queued"]
    assert (store.queue_dir(tmp_path) / "report.json").is_file()


def test_empty_queue_exits_cleanly_and_still_writes_report(tmp_path, capsys):
    code = batch_run.main(["--root", str(tmp_path)], pipeline_runner=ok_runner)
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["started"] is False and "队列为空" in out["reason"]
    assert (store.queue_dir(tmp_path) / "report.md").is_file()


def test_ctrl_c_exits_130_and_marks_current_item_stopped(tmp_path, capsys):
    add(tmp_path, 1)

    def interrupted(pdir, params):
        raise KeyboardInterrupt

    code = batch_run.main(["--root", str(tmp_path)], pipeline_runner=interrupted)
    out = json.loads(capsys.readouterr().out)
    assert code == 130 and "Ctrl+C" in out["error"]
    assert states(tmp_path) == ["stopped"]                               # 可再次运行继续


def test_status_and_report_flags_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        batch_run.main(["--root", str(tmp_path), "--status", "--report"])


def test_command_line_status_runs_as_a_real_script(tmp_path):
    add(tmp_path, 1)
    env = dict(os.environ, ARTICLE_TO_VIDEO_HOME=str(tmp_path))
    out = subprocess.run([sys.executable, str(PROJECT_DIR / "entrypoints" / "batch_run.py"), "--status"],
                         capture_output=True, env=env, cwd=str(PROJECT_DIR), timeout=60)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout.decode("utf-8"))
    assert data["total"] == 1 and data["counts"]["queued"] == 1
