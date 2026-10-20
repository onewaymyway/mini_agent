"""以 id 为键的伏笔登记表（foreshadowing_registry）结构：不做确定性迁移，交给 LLM 转换并复核。全部使用桩。"""

from __future__ import annotations

import json

from novel_writer import llm_convert as LC
from novel_writer import plot_store as PS

REGISTRY_LEDGER = {
    "meta": {"novel": "测试书", "last_updated_chapter": 30},
    "foreshadowing_registry": {
        "fs_001": {"id": "fs_001", "content": "铜钱", "planted_chapter": 1, "status": "reinforced"},
    },
    "plot_threads": {"thread_001": {"id": "thread_001", "name": "主线"}},
    "tension_curve": {"checkpoints": []},
    "chapter_hooks": {"ch_030": {"ending_hook": "x"}},
    "health_report": {"current_chapter": 30},
}


def _write_legacy(tmp_path, data):
    p = tmp_path / "plot_tracker.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def test_registry_ledger_is_not_deterministically_migrated(tmp_path):
    legacy = _write_legacy(tmp_path, REGISTRY_LEDGER)
    base = tmp_path / "伏笔"
    res = PS.migrate_legacy(legacy, base)
    assert res["status"] == "needs_llm"
    assert any("foreshadowing_registry" in p for p in res["legacy_problem"])
    assert not base.exists() or not PS.is_store(base)          # 没有写入新存储
    assert legacy.is_file() and not legacy.with_name(legacy.name + ".bak").exists()   # 旧文件不动


def test_registry_ledger_with_llm_is_review_gated_and_reports_out_of_scope_sections(tmp_path):
    legacy = _write_legacy(tmp_path, REGISTRY_LEDGER)
    base = tmp_path / "伏笔"
    converted = {"foreshadowing": [{"id": "fs_001", "name": "铜钱", "plant_chapter": 1, "status": "planted"}]}
    fake = lambda src: LC.ConvertResult(ok=True, data=converted, attempts=1, staging=tmp_path / "out")  # noqa: E731
    res = PS.migrate_legacy(legacy, base, convert=fake)
    assert res["status"] == "needs_review"
    text = " ".join(res.get("warnings", []))
    assert "plot_threads" in text and "tension_curve" in text and "chapter_hooks" in text
    assert "health_report" in text and "meta" in text
    assert legacy.is_file()                                     # 复核前不改动旧文件


def test_ordinary_list_ledger_still_migrates_deterministically(tmp_path):
    legacy = _write_legacy(tmp_path, {"foreshadowing": [{"id": "F1", "name": "甲", "status": "planted"}]})
    base = tmp_path / "伏笔"
    res = PS.migrate_legacy(legacy, base)
    assert res["status"] == "migrated"
    assert PS.is_store(base)
