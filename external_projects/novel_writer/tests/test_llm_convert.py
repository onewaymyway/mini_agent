"""损坏的旧伏笔账本：由 LLM（ledger-convert）直接读原文生成小文件；带错误重试；结果必须复核后才提交（M9 第二阶段）。"""

import json
from pathlib import Path

import pytest

from novel_writer import config, plot_store as PS, tool_api
from novel_writer.workspace import Workspace

CORRUPT = ('{"foreshadowing": [{"id": "F1", "name": "玉佩", "status": "planted", "plant_chapter": 1}, '
           '{"id": "F2", "name": "信", "status": "planted", "plant_chapter": 2}, {"id": "F3", "na')

GOOD_F1 = '---\nid: "F1"\nname: "玉佩"\nstatus: "planted"\nplant_chapter: 1\nresolve_chapter: null\nprogress: []\n---\n'
GOOD_F2 = '---\nid: "F2"\nname: "信"\nstatus: "advanced"\nplant_chapter: 2\nresolve_chapter: null\nprogress: [{"chapter":4,"note":"出现"}]\n---\n'
BAD_F2 = GOOD_F2.replace('"advanced"', '"进行中"')          # 状态不合法


class ScriptedInvoker:
    """按脚本依次「写出」产出；记录每次收到的提示词。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.prompts = []

    def __call__(self, *, skill, prompt, export_dir, step_id, **kw):
        assert skill == "ledger-convert"
        self.prompts.append(prompt)
        script = self.scripts[min(len(self.prompts), len(self.scripts)) - 1]
        if isinstance(script, Exception):
            raise script
        base = export_dir / "伏笔"
        base.mkdir(parents=True, exist_ok=True)
        for name, text in script.items():
            (base / name).write_text(text, encoding="utf-8")
        class R: ok = True; error = ""
        return R()


def _legacy(tmp_path, text=CORRUPT):
    p = tmp_path / "plot_tracker.json"
    p.write_text(text, encoding="utf-8")
    return p


def _convert(invoker, tmp_path):
    return lambda legacy: PS.llm_convert_legacy(legacy, tmp_path / "work", invoker)


def test_llm_direct_conversion_needs_review_then_commits(tmp_path):
    legacy = _legacy(tmp_path)
    inv = ScriptedInvoker([{"F1.md": GOOD_F1, "F2.md": GOOD_F2, "F3.md": GOOD_F1.replace("F1", "F3")}])
    base = tmp_path / "伏笔"
    r = PS.migrate_legacy(legacy, base, convert=_convert(inv, tmp_path))
    assert r["status"] == "needs_review" and r["source"] == "llm" and r["item_ids"] == ["F1", "F2", "F3"]
    assert legacy.exists() and not PS.is_store(base)            # 未确认：什么都没写
    report = (tmp_path / "伏笔迁移报告.md").read_text(encoding="utf-8")
    assert "F2 信" in report and "LLM 直接转换" in report
    r2 = PS.migrate_legacy(legacy, base, convert=_convert(inv, tmp_path), confirm=True)
    assert r2["status"] == "migrated" and not legacy.exists() and (tmp_path / "plot_tracker.json.bak").exists()
    back = PS.read_store(base)
    assert [i["id"] for i in back["foreshadowing"]] == ["F1", "F2", "F3"]
    assert back["foreshadowing"][1]["progress"] == [{"chapter": 4, "note": "出现"}]


def test_invalid_output_is_fed_back_and_retried(tmp_path):
    inv = ScriptedInvoker([{"F1.md": GOOD_F1, "F2.md": BAD_F2},          # 第一次：状态非法
                           {"F1.md": GOOD_F1, "F2.md": GOOD_F2}])       # 第二次：修正
    res = _convert(inv, tmp_path)(_legacy(tmp_path))
    assert res.ok and res.attempts == 2
    assert "status 必须是" in inv.prompts[1] and "进行中" in inv.prompts[1]   # 具体错误被反馈给 LLM


def test_gives_up_after_max_attempts_and_leaves_original(tmp_path):
    inv = ScriptedInvoker([{"F1.md": BAD_F2}])
    legacy = _legacy(tmp_path)
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔",
                          convert=lambda p: PS.llm_convert_legacy(p, tmp_path / "work", inv, max_attempts=2))
    assert r["status"] == "failed" and r["attempts"] == 2 and legacy.exists()
    assert len(inv.prompts) == 2


def test_invoker_exception_counts_as_failed_attempt(tmp_path):
    inv = ScriptedInvoker([RuntimeError("模型超时"), {"F1.md": GOOD_F1, "F2.md": GOOD_F2}])
    res = PS.llm_convert_legacy(_legacy(tmp_path), tmp_path / "work", inv)
    assert res.ok and res.attempts == 2


def test_suspected_omission_is_warned_for_review(tmp_path):
    inv = ScriptedInvoker([{"F1.md": GOOD_F1}])                   # 原文有 3 个 id，只转出 1 条
    res = PS.llm_convert_legacy(_legacy(tmp_path), tmp_path / "work", inv)
    assert res.ok and any("疑似遗漏" in w for w in res.warnings)
    r = PS.migrate_legacy(_legacy(tmp_path), tmp_path / "伏笔", convert=lambda p: res)
    assert r["status"] == "needs_review" and r["warnings"]


def test_without_llm_corrupt_legacy_is_not_touched(tmp_path):
    legacy = _legacy(tmp_path)
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔")
    assert r["status"] == "needs_llm" and legacy.read_text(encoding="utf-8") == CORRUPT


def test_dry_run_with_llm_writes_no_ledger(tmp_path):
    legacy = _legacy(tmp_path)
    inv = ScriptedInvoker([{"F1.md": GOOD_F1, "F2.md": GOOD_F2}])
    r = PS.migrate_legacy(legacy, tmp_path / "伏笔", dry_run=True, convert=_convert(inv, tmp_path))
    assert r["status"] == "dry_run" and r["items"] == 2
    assert legacy.exists() and not (tmp_path / "伏笔").exists() and not (tmp_path / "伏笔迁移报告.md").exists()


def test_tool_api_end_to_end_with_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    r = tool_api.create_novel("LLM转换", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    ws.path("plot_legacy").write_text(CORRUPT, encoding="utf-8")
    inv = ScriptedInvoker([{"F1.md": GOOD_F1, "F2.md": GOOD_F2}])
    import novel_writer.runner as runner
    monkeypatch.setattr(runner, "FrameworkInvoker", lambda *a, **k: inv)
    out = tool_api.migrate_plot_ledger(str(ws.root))                       # 不开 use_llm：提示需要转换
    assert out["ok"] and out["data"]["status"] == "needs_llm"
    out = tool_api.migrate_plot_ledger(str(ws.root), use_llm=True)
    assert out["ok"] and out["data"]["status"] == "needs_review"
    out = tool_api.migrate_plot_ledger(str(ws.root), use_llm=True, confirm=True)
    assert out["ok"] and out["data"]["status"] == "migrated"
    assert PS.is_store(ws.path("plot_items_dir"))
    assert ws.path("plot_register").is_file()                              # 注册产物副本仍会生成
