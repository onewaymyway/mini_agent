"""关键文件乱码检测 G1：配置校验、注册表、只读体检与归因、report 模式记录（不改状态、不重跑）。"""

from __future__ import annotations

import json

import pytest

from fakes import make_blueprint, put_blueprint
from novel_writer import config, garble_watch as GW, pipeline as P, state as st, steps as S, tool_api
from novel_writer.runner import run_step
from novel_writer.settings import DEFAULT_SETTINGS, SettingsError, validate_settings
from novel_writer.workspace import Workspace

NORMAL = ("清晨的街道上，行人渐渐多了起来。林小雨站在医院门口，看着手里的工作证发呆。"
          "她已经在这家医院工作了三年，每天早上七点准时到岗。\n")


def mojibake(text: str) -> str:
    return text.encode("utf-8").decode("gbk", errors="replace")


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("乱码体检测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(3))
    return ws


def _write(ws: Workspace, rel: str, text: str) -> None:
    path = ws.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _set_mode(ws: Workspace, mode: str, **extra) -> None:
    tool_api.update_settings(str(ws.root), {"garble_check": {"mode": mode, **extra}})


# ── 配置 ─────────────────────────────────────────────────────────────────────

def test_default_garble_check_is_off():
    assert DEFAULT_SETTINGS["garble_check"]["mode"] == "off"
    assert validate_settings({})["garble_check"]["mode"] == "off"


def test_invalid_garble_check_values_are_rejected():
    with pytest.raises(SettingsError):
        validate_settings({"garble_check": {"mode": "always"}})
    with pytest.raises(SettingsError):
        validate_settings({"garble_check": {"max_reruns": -1}})
    with pytest.raises(SettingsError):
        validate_settings({"garble_check": {"watch_extra": "章节总结/*.md"}})
    with pytest.raises(SettingsError):
        validate_settings({"garble_check": {"unknown": 1}})


# ── 注册表 ───────────────────────────────────────────────────────────────────

def test_watch_registry_includes_key_files_and_excludes_noise(book):
    _write(book, "章节总结/第01章_总结.md", NORMAL)
    _write(book, "章节总结/全书章节索引.md", NORMAL)
    _write(book, "正文/_drafts/第01章_草稿.md", NORMAL)
    _write(book, "导出/全书.md", NORMAL)
    _write(book, "_studio/inbox/x.json", "{}")
    _write(book, "章节总结/第01章_总结.md.bak", NORMAL)
    _write(book, "章节总结/旧.docx", NORMAL)
    files = GW.watched_files(book)
    assert "章节总结/第01章_总结.md" in files
    assert "章节总结/全书章节索引.md" in files
    assert not any(f.startswith(("正文/_drafts", "导出", "_studio")) for f in files)
    assert not any(f.endswith((".bak", ".docx")) for f in files)


def test_watch_extra_adds_custom_paths(book):
    _write(book, "自定义/笔记.md", NORMAL)
    settings = {"garble_check": {"mode": "report", "max_reruns": 2, "watch_extra": ["自定义/*.md"]}}
    assert "自定义/笔记.md" in GW.watched_files(book, settings)


# ── 只读体检与归因 ───────────────────────────────────────────────────────────

def test_scan_finds_mojibake_summary_and_is_read_only(book):
    rel = "章节总结/第01章_总结.md"
    raw = mojibake(NORMAL * 20).encode("utf-8")
    (book.root / rel).parent.mkdir(parents=True, exist_ok=True)
    (book.root / rel).write_bytes(raw)
    before = (book.root / rel).read_bytes()

    res = tool_api.scan_garble(str(book.root))
    assert res["ok"] is True
    hits = {item["file"]: item for item in res["data"]["garbled"]}
    assert rel in hits
    assert hits[rel]["reasons"]
    assert (book.root / rel).read_bytes() == before     # 体检不改写文件


def test_scan_reports_clean_workspace_as_clean(book):
    _write(book, "章节总结/第01章_总结.md", NORMAL * 5)
    data = tool_api.scan_garble(str(book.root))["data"]
    assert data["garbled_count"] == 0
    assert data["files_checked"] >= 1


def test_attribution_declared_step_is_auto_rerunnable(book):
    rel = "章节总结/第01章_总结.md"
    _write(book, rel, mojibake(NORMAL * 20))
    step_id = P.build_plan(book)[0].id
    st.record_writer_paths(book, [rel], step_id, kind="declared")
    item = GW.attribute(book, rel)
    assert item["writer"] == step_id
    assert item["auto_rerunnable"] is True


def test_attribution_author_edit_is_not_auto_rerunnable(book):
    rel = "章节总结/第01章_总结.md"
    _write(book, rel, mojibake(NORMAL * 20))
    st.record_writer_paths(book, [rel], "author", kind="author")
    item = GW.attribute(book, rel)
    assert item["auto_rerunnable"] is False
    assert "作者" in item["note"]


def test_attribution_unknown_writer_is_not_auto_rerunnable(book):
    rel = "章节总结/第01章_总结.md"
    _write(book, rel, mojibake(NORMAL * 20))
    item = GW.attribute(book, rel)
    assert item["auto_rerunnable"] is False


# ── report 模式（步骤结束后的记录，不改状态） ────────────────────────────────

def _writer_step(rel: str, content: str):
    def handler(ws, step, settings, invoker=None):
        _write(ws, rel, content)
        return {"summary": "写入完成"}

    step = S.StepDef(id="test.write_summary", title="测试写总结", stage="p0", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_PYTHON, handler="writer")
    return step, {"writer": handler}


def test_off_mode_leaves_no_trace(book):
    rel = "章节总结/第01章_总结.md"
    step, handlers = _writer_step(rel, mojibake(NORMAL * 20))
    status = run_step(book, step, invoker=None, settings=book.settings(), handlers=handlers)
    assert status in (st.ST_PASSED, st.ST_WAITING_REVIEW)
    assert not (book.studio / "inbox" / "garble_test.write_summary.json").exists()
    assert "乱码检测" not in book.path("studio_log").read_text(encoding="utf-8")


def test_report_mode_records_but_does_not_change_status(book):
    _set_mode(book, "report")
    rel = "章节总结/第01章_总结.md"
    step, handlers = _writer_step(rel, mojibake(NORMAL * 20))
    status = run_step(book, step, invoker=None, settings=book.settings(), handlers=handlers)
    assert status in (st.ST_PASSED, st.ST_WAITING_REVIEW)      # 状态不因检测改变
    record = json.loads((book.studio / "inbox" / "garble_test.write_summary.json").read_text(encoding="utf-8"))
    assert record["garbled"][0]["file"] == rel
    assert "乱码检测" in book.path("studio_log").read_text(encoding="utf-8")


def test_report_mode_clean_output_writes_nothing(book):
    _set_mode(book, "report")
    step, handlers = _writer_step("章节总结/第01章_总结.md", NORMAL * 20)
    run_step(book, step, invoker=None, settings=book.settings(), handlers=handlers)
    assert not (book.studio / "inbox" / "garble_test.write_summary.json").exists()


def test_rerun_mode_is_reported_honestly_until_g3(book):
    _set_mode(book, "rerun")
    step, handlers = _writer_step("章节总结/第01章_总结.md", mojibake(NORMAL * 20))
    run_step(book, step, invoker=None, settings=book.settings(), handlers=handlers)
    log = book.path("studio_log").read_text(encoding="utf-8")
    assert "尚未启用" in log


# ── G2：skill 步骤内重试环（A 层） ───────────────────────────────────────────

from novel_writer.runner import InvokeResult, StepFailed, run_skill_step  # noqa: E402

SUMMARY = "章节总结/第01章_总结.md"


def _skill_step():
    return S.StepDef(id="test.summary_skill", title="测试总结", stage="p0", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_SKILL, skill="chapter-writer", outputs=(SUMMARY,))


def _scripted_invoker(contents):
    """每次调用依次写出 contents 中的一个版本（用尽后沿用最后一个），并写回执。"""
    calls = []

    def invoke(*, skill, prompt, export_dir, step_id, max_turns=None, timeout=None):
        i = len(calls)
        calls.append(prompt)
        body = contents[min(i, len(contents) - 1)]
        _write(Workspace(_ROOT[0]), SUMMARY, body)
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "receipt.json").write_text(json.dumps({"status": "done", "summary": "ok"}, ensure_ascii=False),
                                                 encoding="utf-8")
        return InvokeResult(True)

    invoke.calls = calls
    return invoke


_ROOT: list = []


def test_rerun_mode_retries_same_step_until_output_is_clean(book):
    _ROOT[:] = [str(book.root)]
    _set_mode(book, "rerun")
    invoker = _scripted_invoker([mojibake(NORMAL * 20), NORMAL * 20])
    receipt = run_skill_step(book, _skill_step(), invoker=invoker, settings=book.settings(), run_no=1)
    assert receipt["status"] == "done"
    assert len(invoker.calls) == 2                       # 同一步骤重做了一次
    assert "疑似乱码" in invoker.calls[1]                 # 问题清单进入了第二次提示
    assert not GW.check_file(book.root / SUMMARY)
    backups = list((book.studio / "backup" / "garbled").glob("test.summary_skill_*"))
    assert backups and any(p.name == "第01章_总结.md" for p in backups[0].iterdir())


def test_rerun_mode_gives_up_after_max_attempts(book):
    _ROOT[:] = [str(book.root)]
    _set_mode(book, "rerun")
    invoker = _scripted_invoker([mojibake(NORMAL * 20)])
    with pytest.raises(StepFailed):
        run_skill_step(book, _skill_step(), invoker=invoker, settings=book.settings(), run_no=1)
    assert len(invoker.calls) == 3                       # MAX_RECEIPT_ATTEMPTS


def test_report_mode_does_not_retry_skill_step(book):
    _ROOT[:] = [str(book.root)]
    _set_mode(book, "report")
    invoker = _scripted_invoker([mojibake(NORMAL * 20), NORMAL * 20])
    run_skill_step(book, _skill_step(), invoker=invoker, settings=book.settings(), run_no=1)
    assert len(invoker.calls) == 1                       # report 模式只记录，不让 agent 重做


def test_off_mode_skill_step_unchanged(book):
    _ROOT[:] = [str(book.root)]
    invoker = _scripted_invoker([mojibake(NORMAL * 20), NORMAL * 20])
    run_skill_step(book, _skill_step(), invoker=invoker, settings=book.settings(), run_no=1)
    assert len(invoker.calls) == 1
    assert not (book.studio / "backup" / "garbled").exists()
