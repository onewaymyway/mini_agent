"""损坏的旧 JSON 账本：抢救、按条目拆分、LLM 修复（仅不合格的条目）、隔离、复核后才提交（M9 第二阶段起通用）。"""

import json
from pathlib import Path

import pytest

from novel_writer import config, json_repair as JR, plot_store as PS, tool_api
from novel_writer.workspace import Workspace


def _good(iid, name="玉佩", **extra):
    return {"id": iid, "name": name, "status": "planted", "plant_chapter": 1, **extra}


# ── 抢救的分层与来源标记 ─────────────────────────────────────────────

def test_strict_file_is_fully_recovered_with_strict_origin():
    text = json.dumps({"foreshadowing": [_good("F1"), _good("F2")]}, ensure_ascii=False)
    sv = JR.salvage_ledger(text, "foreshadowing", "applied_deltas")
    assert sv.whole_ok and [o for _, o in sv.records] == ["strict", "strict"] and not sv.pending


def test_trailing_comma_is_repaired_deterministically_as_lenient():
    text = '{"foreshadowing": [{"id": "F1", "name": "a",}, {"id": "F2", "name": "b"}]}'
    sv = JR.salvage_ledger(text, "foreshadowing", "applied_deltas")
    assert [(o["id"], src) for o, src in sv.records] == [("F1", "lenient"), ("F2", "lenient")]


def test_truncated_tail_is_split_per_record_and_only_bad_record_is_pending():
    text = '{"foreshadowing": [{"id": "F1", "name": "甲"}, {"id": "F2", "name": "乙"}, {"id": "F3", "na'
    sv = JR.salvage_ledger(text, "foreshadowing", "applied_deltas")
    assert [o["id"] for o, _ in sv.records] == ["F1", "F2"]    # 前两条保留，不被第三条连累
    assert len(sv.pending) == 1 and '"F3"' in sv.pending[0]
    assert any("截断" in n for n in sv.notes)


def test_string_with_brackets_does_not_confuse_the_splitter():
    text = '{"foreshadowing": [{"id": "F1", "name": "括号}]{和\\"引号\\""}, {"id": "F2"'
    sv = JR.salvage_ledger(text, "foreshadowing", "applied_deltas")
    assert sv.records[0][0]["name"] == '括号}]{和"引号"'
    assert len(sv.pending) == 1


def test_corrupt_applied_block_is_reported_as_lost():
    text = ('{"foreshadowing": [{"id": "F1"}], "applied_deltas": {"3": {"before": {"F1": null}, "delta": {bad}}')
    sv = JR.salvage_ledger(text, "foreshadowing", "applied_deltas")
    assert sv.applied_lost and sv.applied is None
    assert any("撤销记录丢失" in n for n in sv.notes)


# ── 修复与隔离 ───────────────────────────────────────────────────────

def _validate(obj):
    return None if isinstance(obj, dict) and str(obj.get("id") or "").startswith("F") else "id 不合法"


def test_llm_repair_only_called_for_pending_records_and_result_is_validated():
    calls = []

    def fake_repair(raw, hint):
        calls.append(raw)
        return '{"id": "F3", "name": "修好的", "status": "planted", "plant_chapter": 3}'

    fixed, failed = JR.repair_pending(['{"id": "F3", "na'], fake_repair, "hint", _validate)
    assert len(calls) == 1 and fixed[0][1] == "llm" and fixed[0][0]["name"] == "修好的" and not failed


def test_repair_result_failing_validation_is_not_accepted():
    fixed, failed = JR.repair_pending(['{"id": "F3'], lambda raw, hint: '{"id": "bad id!"}', "h", _validate)
    assert not fixed and len(failed) == 1


def test_repair_exception_does_not_abort_migration():
    def boom(raw, hint):
        raise RuntimeError("LLM 不可用")
    fixed, failed = JR.repair_pending(['{"id": "F3'], boom, "h", _validate)
    assert not fixed and len(failed) == 1


def test_skill_repair_reads_result_file_written_by_invoker(tmp_path):
    def fake_invoker(*, skill, prompt, export_dir, step_id, **kw):
        assert skill == "json-repair" and "损坏原文" in prompt
        (export_dir / "repaired.json").write_text('{"id": "F9", "name": "x"}', encoding="utf-8")
        class R: ok = True; error = ""
        return R()
    repair = JR.make_skill_repair(fake_invoker, tmp_path / "work")
    assert json.loads(repair('{"id": "F9", "na', "h"))["id"] == "F9"


# ── 迁移流程的决策 ───────────────────────────────────────────────────

def _legacy(tmp_path, text):
    p = tmp_path / "plot_tracker.json"
    p.write_text(text, encoding="utf-8")
    return p


def test_trailing_comma_only_migrates_automatically(tmp_path):
    legacy = _legacy(tmp_path, '{"foreshadowing": [{"id": "F1", "name": "a", "status": "planted",}]}')
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔")
    assert r["status"] == "migrated" and PS.read_store(tmp_path / "伏笔")["foreshadowing"][0]["id"] == "F1"


def test_truncated_file_with_llm_repair_needs_confirmation_then_commits(tmp_path):
    legacy = _legacy(tmp_path, '{"foreshadowing": [{"id": "F1", "name": "甲"}, {"id": "F2", "name": "乙'
                              )
    base = tmp_path / "伏笔"
    repair = lambda raw, hint: '{"id": "F2", "name": "乙", "status": "planted", "plant_chapter": 2}'
    r = PS.migrate_legacy(legacy, base, repair=repair)
    assert r["status"] == "needs_review" and r["llm_ids"] == ["F2"]
    assert legacy.exists() and not PS.is_store(base)               # 未确认：什么都没改
    assert (tmp_path / "伏笔迁移报告.md").exists()
    assert "F2" in (tmp_path / "伏笔迁移报告.md").read_text(encoding="utf-8")
    r2 = PS.migrate_legacy(legacy, base, repair=repair, confirm=True)
    assert r2["status"] == "migrated" and not legacy.exists()
    assert PS.read_store(base)["foreshadowing"][1]["name"] == "乙"


def test_unrepairable_record_is_quarantined_not_silently_dropped(tmp_path):
    legacy = _legacy(tmp_path, '{"foreshadowing": [{"id": "F1", "name": "甲"}, {"id": "F2", "na')
    base = tmp_path / "伏笔"
    r = PS.migrate_legacy(legacy, base, repair=None)
    assert r["status"] == "needs_review" and r["quarantined"] == 1
    r = PS.migrate_legacy(legacy, base, repair=None, confirm=True)
    assert r["status"] == "migrated"
    qs = list((base / "_quarantine").glob("*.md"))
    assert len(qs) == 1 and '"F2"' in qs[0].read_text(encoding="utf-8")
    assert [i["id"] for i in PS.read_store(base)["foreshadowing"]] == ["F1"]


def test_invalid_llm_output_is_quarantined(tmp_path):
    legacy = _legacy(tmp_path, '{"foreshadowing": [{"id": "F1"}, {"id": "F2", "na')
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔", repair=lambda raw, hint: "这不是 JSON", confirm=False)
    assert r["status"] == "needs_review" and r["quarantined"] == 1 and r["llm_ids"] == []


def test_duplicate_ids_are_quarantined_and_reviewed(tmp_path):
    legacy = _legacy(tmp_path, json.dumps({"foreshadowing": [_good("F1"), _good("F1", name="重复")]}, ensure_ascii=False))
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔")
    assert r["status"] == "needs_review" and r["quarantined"] == 1 and r["items"] == 1


def test_lost_applied_records_require_review(tmp_path):
    legacy = _legacy(tmp_path, '{"foreshadowing": [{"id": "F1"}], "applied_deltas": {"3": {bad}}')
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔")
    assert r["status"] == "needs_review" and r["applied_lost"]


def test_dry_run_never_writes_anything(tmp_path):
    legacy = _legacy(tmp_path, '{"foreshadowing": [{"id": "F1"}, {"id": "F2", "na')
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔", dry_run=True)
    assert r["status"] == "dry_run" and not (tmp_path / "伏笔").exists() and legacy.exists()


def test_tool_api_exposes_confirmation_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    r = tool_api.create_novel("修复测试", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    ws.path("plot_legacy").write_text('{"foreshadowing": [{"id": "F1"}, {"id": "F2", "na', encoding="utf-8")
    out = tool_api.migrate_plot_ledger(str(ws.root))
    assert out["ok"] and out["data"]["status"] == "needs_review"
    out = tool_api.migrate_plot_ledger(str(ws.root), confirm=True)
    assert out["ok"] and out["data"]["status"] == "migrated"
