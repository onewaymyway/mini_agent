"""确认提交必须复用干跑时的候选结果，不能再次调用 LLM，也不能在没有候选时提交。全部使用桩执行器。"""

from __future__ import annotations

import json

from novel_writer import blueprint_store as BS
from novel_writer import llm_convert as LC


def _bp():
    return {"meta": {"target_length": 16000}, "structure": {"total_chapters": 2},
            "chapters": [{"id": 1, "title": "第1章"}, {"id": 2, "title": "第2章"}]}


def _fake(calls):
    def invoker(*, skill, prompt, export_dir, step_id, **kw):
        calls.append(step_id)
        BS.write_store(export_dir, export_dir / "_index.md", _bp())
    return invoker


def _legacy(tmp_path):
    p = tmp_path / "小说蓝图.json"
    p.write_text('{"meta": {}, "chapters": [{"id": 1,}', encoding="utf-8")   # 损坏
    return p


def test_dry_run_calls_llm_once_and_saves_candidate(tmp_path):
    calls = []
    legacy, workdir = _legacy(tmp_path), tmp_path / "wd"
    res = BS.llm_convert_legacy(legacy, workdir, _fake(calls), max_attempts=1)
    assert res.ok and len(calls) == 1 and not res.reused
    assert (workdir / LC.CANDIDATE_FILE).is_file()


def test_confirm_reuses_candidate_without_calling_llm(tmp_path):
    calls = []
    legacy, workdir = _legacy(tmp_path), tmp_path / "wd"
    BS.llm_convert_legacy(legacy, workdir, _fake(calls), max_attempts=1)
    res = BS.llm_convert_legacy(legacy, workdir, _fake(calls), reuse_only=True)
    assert res.ok and res.reused and len(calls) == 1          # 没有新的 LLM 调用
    assert [c["id"] for c in res.data["chapters"]] == [1, 2]


def test_candidate_is_rejected_when_legacy_file_changed(tmp_path):
    calls = []
    legacy, workdir = _legacy(tmp_path), tmp_path / "wd"
    BS.llm_convert_legacy(legacy, workdir, _fake(calls), max_attempts=1)
    legacy.write_text(legacy.read_text(encoding="utf-8") + " ", encoding="utf-8")
    res = BS.llm_convert_legacy(legacy, workdir, _fake(calls), reuse_only=True)
    assert not res.ok and len(calls) == 1 and res.errors


def test_confirm_without_any_candidate_does_not_call_llm_or_commit(tmp_path):
    calls = []
    legacy, workdir = _legacy(tmp_path), tmp_path / "wd"
    res = BS.llm_convert_legacy(legacy, workdir, _fake(calls), reuse_only=True)
    assert not res.ok and calls == [] and "干跑" in res.errors[0]


def test_full_confirm_path_commits_reviewed_candidate_only(tmp_path):
    calls = []
    legacy, workdir = _legacy(tmp_path), tmp_path / "wd"
    d, i = tmp_path / "蓝图", tmp_path / "蓝图" / "_index.md"
    BS.llm_convert_legacy(legacy, workdir, _fake(calls), max_attempts=1)           # 干跑
    out = BS.migrate_legacy(legacy, d, i, confirm=True, draft_path=tmp_path / "_draft.json",
                            convert=lambda src: BS.llm_convert_legacy(src, workdir, _fake(calls), reuse_only=True))
    assert out["status"] == "migrated"
    assert len(calls) == 1                                                          # 确认阶段没有调用 LLM
    assert BS.read_store(d)["chapters"][0]["title"] == "第1章"
