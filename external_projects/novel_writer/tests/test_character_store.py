"""第四阶段：人物档案小文件化（每个角色一个文件；LLM 直接更新出场角色的文件；确定性刷新与迁移）。

全部使用桩（不调用真实 LLM）。
"""

from __future__ import annotations

import json

import pytest

from novel_writer import character_store as CS
from novel_writer import chapter_ops as C
from novel_writer import config, steps as S, tool_api
from novel_writer import llm_convert as LC
from novel_writer.validators import content_problems
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("人物", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _dirs(ws):
    return ws.path("characters_dir"), ws.path("characters")


def _rec(cid, name, chapter=0, **kw):
    rec = {"id": cid, "name": name, "role": "supporting", "introduced_chapter": 1,
           "identity": {"one_line_essence": f"{name}的本质"},
           "psychology": {"speech_pattern": "话少"},
           "behavior_guide": {"wont_do": ["背叛同伴"]},
           "state_log": [],
           "current_state": {"as_of_chapter": chapter, "location": "青云峰", "emotional": "平静",
                             "known_secrets": ["体质特殊"]}}
    rec.update(kw)
    return rec


def _raw_write(path, rec):
    """绕过校验直接写文件（模拟 LLM 写出了有问题的内容）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CS._encode(rec) + CS._body(rec), encoding="utf-8")


# ── 存储往返与增量写入 ───────────────────────────────────────────────

def test_round_trip_and_unchanged_rewrite_does_not_write(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    chars = {"char_001": _rec("char_001", "李凌云")}
    assert CS.write_store(d, i, chars) is True
    assert CS.write_store(d, i, chars) is False          # 内容不变，不写盘
    assert CS.read_store(d) == chars
    assert "李凌云" in i.read_text(encoding="utf-8")


def test_only_changed_character_file_is_rewritten(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    chars = {"char_001": _rec("char_001", "李凌云"), "char_002": _rec("char_002", "王道长")}
    CS.write_store(d, i, chars)
    untouched = (d / "char_002.md").read_text(encoding="utf-8")
    chars["char_001"]["current_state"]["location"] = "外门"
    assert CS.write_store(d, i, chars) is True
    assert (d / "char_002.md").read_text(encoding="utf-8") == untouched


def test_hand_edit_of_frontmatter_is_validated_and_refreshed(tmp_path):
    """LLM 直接改角色文件的文件头：刷新后正文与索引跟着更新。"""
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    CS.write_store(d, i, {"char_001": _rec("char_001", "李凌云")})
    f = d / "char_001.md"
    f.write_text(f.read_text(encoding="utf-8").replace('"location":"青云峰"', '"location":"后山"'), encoding="utf-8")
    chars, changed = CS.refresh(d, i)
    assert changed is True
    assert chars["char_001"]["current_state"]["location"] == "后山"
    assert "后山" in i.read_text(encoding="utf-8")
    assert "后山" in f.read_text(encoding="utf-8")          # 正文由系统重新生成


# ── 校验：问题必须点名文件 ───────────────────────────────────────────

def test_broken_line_names_the_file_and_line(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    CS.write_store(d, i, {"char_001": _rec("char_001", "李凌云")})
    f = d / "char_001.md"
    f.write_text(f.read_text(encoding="utf-8").replace('name: "李凌云"', "name: 李凌云"), encoding="utf-8")
    with pytest.raises(CS.CharacterStoreError) as e:
        CS.read_store(d)
    assert any("char_001.md" in p and "name" in p for p in e.value.problems)


def test_id_mismatch_duplicate_chapter_and_duplicate_name_are_reported(tmp_path):
    ws = _ws(tmp_path)
    d, _ = _dirs(ws)
    _raw_write(d / "char_001.md", _rec("char_009", "甲"))                       # id 与文件名不一致
    _raw_write(d / "char_002.md", _rec("char_002", "乙", state_log=[
        {"chapter": 8, "status": "a"}, {"chapter": 8, "status": "b"}]))         # 同一章两条
    _raw_write(d / "char_003.md", _rec("char_003", "乙"))                       # 角色名重复
    probs = CS.problems_for_dir(d)
    assert any("与文件名" in p for p in probs)
    assert any("出现了两次" in p for p in probs)
    assert any("角色名重复" in p for p in probs)


def test_problems_for_empty_dir_requires_files(tmp_path):
    ws = _ws(tmp_path)
    d, _ = _dirs(ws)
    assert CS.problems_for_dir(d) == ["角色目录中没有任何角色文件"]


def test_output_check_character_files_is_registered_and_accepts_lists(tmp_path):
    ws = _ws(tmp_path)
    step = S.StepDef(id="x", title="t", stage="p2", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_SKILL, skill="character-manager",
                     extra={"output_check": ["character_files", "plot_delta"]})
    # 人物目录为空：人物校验应报问题（plot_delta 对没有增量的步骤不报 KeyError）
    assert any("角色" in p for p in content_problems(ws, step))
    bad = S.StepDef(id="y", title="t", stage="p2", category=S.CAT_CHAPTER_REVIEW,
                    executor=S.EXECUTOR_SKILL, skill="x", extra={"output_check": "no_such_check"})
    with pytest.raises(KeyError):
        content_problems(ws, bad)


# ── 写作上下文只带出场角色 ───────────────────────────────────────────

def test_brief_only_includes_present_characters():
    chars = {"char_001": _rec("char_001", "李凌云", 8), "char_002": _rec("char_002", "王道长", 8),
             "char_003": _rec("char_003", "路人甲", 8)}
    text = CS.brief_for(chars, ["李凌云"])
    assert "李凌云" in text and "王道长" not in text and "路人甲" not in text
    assert "尚无档案" in CS.brief_for(chars, ["不存在者"])


# ── 读取与未迁移项目 ────────────────────────────────────────────────

def test_load_or_none_reports_unmigrated_legacy_and_returns_none_for_fresh(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    legacy = ws.path("characters_legacy")
    assert CS.load_or_none(d, i, legacy) is None
    legacy.write_text('{"char_001": {"id": "char_001", "name": "甲"}}', encoding="utf-8")
    with pytest.raises(CS.CharacterStoreError) as e:
        CS.load_or_none(d, i, legacy)
    assert "尚未迁移" in e.value.problems[0]


def test_refresh_handler_blocks_invalid_file_with_clear_error(tmp_path):
    ws = _ws(tmp_path)
    d, _ = _dirs(ws)
    d.mkdir(parents=True, exist_ok=True)
    (d / "char_001.md").write_text("不是文件头\n", encoding="utf-8")
    step = S.StepDef(id="characters_refresh", title="t", stage="p1", category=S.CAT_BUILD,
                     executor=S.EXECUTOR_PYTHON, handler="characters_refresh")
    with pytest.raises(C.ChapterOpError) as e:
        C.h_characters_refresh(ws, step, {})
    assert "char_001.md" in str(e.value)


def test_refresh_handler_refuses_unmigrated_project(tmp_path):
    ws = _ws(tmp_path)
    ws.path("characters_legacy").write_text('{"char_001": {"id": "char_001", "name": "甲"}}', encoding="utf-8")
    step = S.StepDef(id="characters_refresh", title="t", stage="p1", category=S.CAT_BUILD,
                     executor=S.EXECUTOR_PYTHON, handler="characters_refresh")
    with pytest.raises(C.ChapterOpError) as e:
        C.h_characters_refresh(ws, step, {})
    assert "迁移" in str(e.value)


def test_refresh_handler_builds_index_for_valid_files(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    CS.write_store(d, i, {"char_001": _rec("char_001", "李凌云")})
    i.unlink()                                           # 模拟索引丢失
    step = S.StepDef(id="characters_refresh", title="t", stage="p1", category=S.CAT_BUILD,
                     executor=S.EXECUTOR_PYTHON, handler="characters_refresh")
    res = C.h_characters_refresh(ws, step, {})
    assert "1 个角色" in res["summary"] and i.is_file()


# ── 步骤接线 ─────────────────────────────────────────────────────────

def _chapter(quality):
    steps = S.chapter_steps(1, "", total=3, prev_last="prev", quality=quality)
    return {s.id: s for s in steps}


def test_standard_sync_updates_character_files_then_refreshes_before_plot():
    plan = _chapter("premium")
    sync = plan["ch01.sync"]
    assert sync.outputs == (S.rel("characters_dir") + "/*.md",)
    assert sync.extra.get("output_check") == "character_files"
    refresh = plan["ch01.characters_refresh"]
    assert refresh.handler == "characters_refresh" and refresh.deps == ("ch01.sync",)
    assert plan["ch01.sync_plot"].deps == ("ch01.characters_refresh",)


def test_draft_sync_checks_both_characters_and_plot_delta():
    plan = _chapter("draft")
    sync = plan["ch01.sync"]
    assert S.rel("plot_delta", nn=1) in sync.outputs
    assert sync.extra.get("output_check") == ["character_files", "plot_delta"]
    assert plan["ch01.plot_merge"].deps == ("ch01.characters_refresh",)


def test_build_characters_outputs_files_and_refresh_follows_it():
    build = S.build_characters_step(["李凌云"])
    assert build.outputs[0] == S.rel("characters_dir") + "/*.md"
    assert build.extra.get("output_check") == "character_files"
    refresh = S.characters_refresh_step()
    assert refresh.deps == ("build_characters",) and refresh.outputs == (S.rel("characters"),)


# ── 迁移：健康 / 损坏 / 复核 ────────────────────────────────────────

LEGACY = {
    "char_001": {"id": "char_001", "name": "李凌云", "state_log": [],
                 "current_state": {"as_of_chapter": 3, "location": "青云峰"}},
    "char_002": {"id": "char_002", "name": "王道长", "state_log": [],
                 "current_state": {"as_of_chapter": 3, "location": "藏经阁"}},
}


def _legacy(ws, data=LEGACY):
    p = ws.path("characters_legacy")
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_migrate_healthy_legacy_dry_run_then_commit_keeps_bak(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    leg = _legacy(ws)
    dry = CS.migrate_legacy(leg, d, i, dry_run=True)
    assert dry["status"] == "dry_run" and dry["count"] == 2
    assert not i.exists() and leg.exists()
    res = CS.migrate_legacy(leg, d, i)
    assert res["status"] == "migrated" and res["count"] == 2
    assert CS.read_store(d) == LEGACY
    assert leg.with_name(leg.name + ".bak").exists() and not leg.exists()


def test_migrate_corrupt_legacy_needs_llm_and_leaves_original(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    leg = ws.path("characters_legacy")
    broken = '{"char_001": {"id": "char_001", "name": "李凌云",}'
    leg.write_text(broken, encoding="utf-8")
    res = CS.migrate_legacy(leg, d, i)
    assert res["status"] == "needs_llm"
    assert leg.read_text(encoding="utf-8") == broken and not i.exists()


def test_migrate_corrupt_with_llm_requires_confirm(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    leg = ws.path("characters_legacy")
    leg.write_text('{"char_001": {"id": "char_001", "name": "李凌云",}', encoding="utf-8")
    staging = tmp_path / "out"
    data = {"char_001": _rec("char_001", "李凌云")}
    fake = lambda legacy: LC.ConvertResult(ok=True, data=data, attempts=1, staging=staging)  # noqa: E731

    res = CS.migrate_legacy(leg, d, i, convert=fake)
    assert res["status"] == "review_required" and res["via"] == "llm"
    assert not i.exists() and leg.exists()

    res2 = CS.migrate_legacy(leg, d, i, convert=fake, confirm=True)
    assert res2["status"] == "migrated"
    assert CS.read_store(d) == data
    assert leg.with_name(leg.name + ".bak").exists()


def test_llm_convert_failure_keeps_original(tmp_path):
    ws = _ws(tmp_path)
    d, i = _dirs(ws)
    leg = ws.path("characters_legacy")
    leg.write_text("{坏掉", encoding="utf-8")
    fake = lambda legacy: LC.ConvertResult(ok=False, errors=["没有写出任何角色文件"], attempts=3)  # noqa: E731
    res = CS.migrate_legacy(leg, d, i, convert=fake)
    assert res["status"] == "llm_failed" and res["attempts"] == 3
    assert leg.read_text(encoding="utf-8") == "{坏掉" and not i.exists()


def test_llm_convert_legacy_validates_output_and_warns_about_missing_ids(tmp_path):
    ws = _ws(tmp_path)
    leg = ws.path("characters_legacy")
    # 原文里有两个 id，但 LLM 只写出一个 → 疑似遗漏提示
    leg.write_text('{"char_001": {"id": "char_001", "name": "甲"}, "char_002": {"id": "char_002", "name": "乙"',
                   encoding="utf-8")
    seen_prompts = []

    def fake_invoker(*, skill, prompt, export_dir, step_id, **kw):
        seen_prompts.append((skill, prompt))
        export_dir.mkdir(parents=True, exist_ok=True)
        rec = _rec("char_001", "甲")
        (export_dir / "char_001.md").write_text(CS._encode(rec) + CS._body(rec), encoding="utf-8")

    res = CS.llm_convert_legacy(leg, tmp_path / "work", fake_invoker, max_attempts=1)
    assert res.ok and set(res.data) == {"char_001"}
    assert any("疑似遗漏" in w for w in res.warnings)
    skill, prompt = seen_prompts[0]
    assert skill == "ledger-convert" and "角色文件格式" in prompt


def test_staging_validator_rejects_empty_output(tmp_path):
    empty = tmp_path / "out"
    empty.mkdir()
    data, errors = CS.staging_validator(empty)
    assert data is None and errors == ["没有写出任何角色文件"]


def test_tool_api_migrate_characters_dry_run(tmp_path):
    ws = _ws(tmp_path)
    _legacy(ws)
    r = tool_api.migrate_characters(str(ws.root), dry_run=True)
    assert r["data"]["status"] == "dry_run" and r["data"]["count"] == 2
    assert not ws.path("characters").exists()
