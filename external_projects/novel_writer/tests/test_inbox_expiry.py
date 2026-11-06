"""收件箱过期项：五条规则自动归档（可撤销），归档后不再计入待处理，条件不再成立时自动恢复。"""

import json

import pytest

from novel_writer import config, inbox
from novel_writer import inbox_expiry as EXP
from novel_writer import ondemand as OD
from novel_writer import patrol_findings as PF
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")


def _ws(tmp_path) -> Workspace:
    """5 章、分两幕的最小蓝图：幕末步骤（act_ideas）会出现在计划中，封存规则才能被验证。"""
    from novel_writer import blueprint_store as BS

    r = tool_api.create_novel("过期项", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G5", total_chapters_planned=5)
    ws = Workspace(r["data"]["workspace"])
    chapters = [{"id": n, "chapter": n, "title": f"第{n}章", "summary": "s", "beats": ["b"],
                 "characters": [], "foreshadowing": []} for n in range(1, 6)]
    bp = {"meta": {"title": "过期项", "target_length": 10000},
          "structure": {"total_chapters": 5, "acts": [{"act": 1, "chapter_range": [1, 3]},
                                                      {"act": 2, "chapter_range": [4, 5]}]},
          "chapters": chapters}
    assert BS.save_for_ws(ws, bp)
    return ws


def _seal_first_act(ws) -> int:
    """让第一幕的幕末步骤通过（即该幕封存），返回封存的最后章号。"""
    acts = [s for s in P.build_plan(ws) if s.id.startswith("act_ideas:")]
    assert acts, "计划里应当有幕末步骤"
    st.update_step(ws, acts[0].id, status=st.ST_PASSED, note="")
    return int(acts[0].extra.get("through_chapter") or 0)


def _chapter_steps(ws, n):
    return [s for s in P.build_plan(ws) if s.chapter == n]


def test_orphan_step_is_archived_and_hidden_then_restored(tmp_path):
    ws = _ws(tmp_path)
    st.update_step(ws, "design_character:已不存在的角色", status=st.ST_PASSED, note="")
    data = inbox.collect(ws)
    assert "design_character:已不存在的角色" not in data["unknown_steps"]
    assert "orphan:design_character:已不存在的角色" in EXP.archived_keys(ws)
    # 角色重新出现在计划中（或状态条件变化）→ 自动恢复
    original = EXP._candidates
    try:
        EXP._candidates = lambda *a, **k: {}
        res = EXP.sync(ws)
        assert "orphan:design_character:已不存在的角色" in res["restored"]
        assert "orphan:design_character:已不存在的角色" not in EXP.archived_keys(ws)
    finally:
        EXP._candidates = original


def test_failed_step_in_sealed_chapter_is_archived_but_active_one_is_kept(tmp_path):
    ws = _ws(tmp_path)
    sealed_to = _seal_first_act(ws)
    assert sealed_to >= 1
    sealed_step = _chapter_steps(ws, 1)[0]
    active_step = _chapter_steps(ws, sealed_to + 1)[0]
    st.update_step(ws, sealed_step.id, status=st.ST_FAILED, note="", error="旧错误")
    st.update_step(ws, active_step.id, status=st.ST_FAILED, note="", error="当前错误")
    inbox.collect(ws)
    archived = EXP.archived_keys(ws)
    assert f"failed:{sealed_step.id}" in archived
    assert f"failed:{active_step.id}" not in archived


def test_waiting_item_superseded_by_revision_is_archived_and_step_reruns(tmp_path):
    ws = _ws(tmp_path)
    step = _chapter_steps(ws, 2)[0]
    st.update_step(ws, step.id, status=st.ST_WAITING_USER, note="等待答复")
    # 一次构思修订在该等待之后完成
    OD.create_request(ws, "concept", direction="改主线", scopes=["theme"], result={"outcome": "已修订"},
                      finished_at="2999-01-01T00:00:00+00:00")
    data = inbox.collect(ws)
    assert all(it["step_id"] != step.id for it in data["items"])
    assert f"answer:{step.id}" in EXP.archived_keys(ws)
    entry = st.load_state(ws)["steps"][step.id]
    assert entry["status"] == st.ST_STALE and entry["note"] == st.REVISION_NOTE   # 不会卡住


def test_budget_pause_is_archived_after_progress(tmp_path):
    ws = _ws(tmp_path)
    ws.path("studio_worker").parent.mkdir(parents=True, exist_ok=True)
    worker = {"reason": "budget_calls", "finished_at": "2000-01-01T00:00:00+00:00"}
    ws.path("studio_worker").write_text(json.dumps(worker, ensure_ascii=False), encoding="utf-8")
    # 暂停之后仍有新进展 → 过期
    st.update_step(ws, _chapter_steps(ws, 1)[0].id, status=st.ST_PASSED, note="")
    assert "budget_pause" in EXP._candidates(ws, P.build_plan(ws), st.load_state(ws)["steps"], st.read_worker(ws))


def test_budget_pause_kept_when_no_progress_since(tmp_path):
    ws = _ws(tmp_path)
    ws.path("studio_worker").parent.mkdir(parents=True, exist_ok=True)
    ws.path("studio_worker").write_text(json.dumps({"reason": "budget_calls",
                                                    "finished_at": "2999-01-01T00:00:00+00:00"}), encoding="utf-8")
    assert "budget_pause" not in EXP._candidates(ws, P.build_plan(ws), st.load_state(ws)["steps"],
                                                 st.read_worker(ws))


def test_patrol_finding_on_sealed_chapter_and_superseded_one_are_archived(tmp_path):
    ws = _ws(tmp_path)
    sealed_to = _seal_first_act(ws)
    plan = P.build_plan(ws)
    states = st.load_state(ws)["steps"]
    ledger = PF.read_ledger(ws)
    ledger["findings"] = [
        {"id": "P1-1", "source": "patrol", "patrol_chapter": 1, "scope": "retro", "severity": "一般",
         "title": "封存章节的旧问题", "chapters": [1], "status": PF.S_PROPOSED, "directive": "x"},
        {"id": "P2-1", "source": "patrol", "patrol_chapter": 2, "scope": "retro", "severity": "一般",
         "title": "被更新巡检覆盖", "chapters": [sealed_to + 1], "status": PF.S_PROPOSED, "directive": "w"},
        {"id": "P3-1", "source": "patrol", "patrol_chapter": 3, "scope": "retro", "severity": "一般",
         "title": "更新巡检覆盖同一章", "chapters": [sealed_to + 1], "status": PF.S_PROPOSED, "directive": "v"},
        {"id": "P9-9", "source": "patrol", "patrol_chapter": 9, "scope": "style", "severity": "一般",
         "title": "其他范围的活跃问题", "chapters": [sealed_to + 1], "status": PF.S_PROPOSED, "directive": "z"},
    ]
    PF._write_ledger(ws, ledger)
    cands = EXP._candidates(ws, plan, states, {})
    assert "patrol:P1-1" in cands                     # 封存章节
    assert "patrol:P2-1" in cands                     # 同章节有更新一轮的巡检
    assert "patrol:P3-1" not in cands                 # 最新一轮仍然有效
    assert "patrol:P9-9" not in cands                 # 其他范围不互相覆盖


def test_collect_excludes_archived_from_total_and_blocking(tmp_path):
    ws = _ws(tmp_path)
    st.update_step(ws, "design_character:旧", status=st.ST_PASSED, note="")
    before = inbox.collect(ws)
    assert before["archived"] >= 1
    assert all(not it["key"].startswith("orphan:") for it in before["items"])
    assert before["total"] == len(before["items"])


def test_archive_record_explains_why(tmp_path):
    ws = _ws(tmp_path)
    st.update_step(ws, "design_character:旧", status=st.ST_PASSED, note="")
    inbox.collect(ws)
    rec = EXP.load_archive(ws)["orphan:design_character:旧"]
    assert rec["rule"] == EXP.RULE_ORPHAN and rec["reason"] and rec["archived_at"]
    raw = json.loads((ws.path("studio_inbox") / EXP.ARCHIVE_FILE).read_text(encoding="utf-8"))
    assert "items" in raw


def test_out_of_range_rule_uses_book_total_not_rolling_plan(tmp_path):
    """回归：计划是滚动生成的，全书 10 章、计划只到第 5 章时，第 8 章的整改项不能因此被判为过期。"""
    from novel_writer import blueprint_store as BS

    ws = _ws(tmp_path)
    bp = BS.load_for_ws(ws, strict=False)
    bp["structure"]["total_chapters"] = 10          # 全书 10 章，计划只展开到第 5 章
    assert BS.save_for_ws(ws, bp)
    ledger = PF.read_ledger(ws)
    ledger["findings"] = [{"id": "P8-1", "source": "patrol", "patrol_chapter": 8, "scope": "retro",
                           "severity": "一般", "title": "后续章节", "chapters": [8],
                           "status": PF.S_PROPOSED, "directive": "x"}]
    PF._write_ledger(ws, ledger)
    assert "patrol:P8-1" not in EXP._candidates(ws, P.build_plan(ws), st.load_state(ws)["steps"], {})
    # 全书章数真的只有 5 章：第 8 章确实超出，才过期
    bp["structure"]["total_chapters"] = 5
    assert BS.save_for_ws(ws, bp)
    assert "patrol:P8-1" in EXP._candidates(ws, P.build_plan(ws), st.load_state(ws)["steps"], {})


def test_out_of_range_rule_off_when_total_unknown(tmp_path):
    """全书章数未知（蓝图与配置都没有）时，「超出总章数」规则不生效，不能据此误判过期。"""
    from novel_writer import blueprint_store as BS

    ws = _ws(tmp_path)
    bp = BS.load_for_ws(ws, strict=False)
    bp["structure"]["total_chapters"] = 0
    assert BS.save_for_ws(ws, bp)
    cfg = ws.workspace_config()
    cfg["total_chapters_planned"] = 0
    ws.path("workspace_config").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    assert EXP._total_chapters(ws) == 0
    ledger = PF.read_ledger(ws)
    ledger["findings"] = [{"id": "P99-1", "source": "patrol", "patrol_chapter": 99, "scope": "retro",
                           "severity": "一般", "title": "未知总章数", "chapters": [99],
                           "status": PF.S_PROPOSED, "directive": "x"}]
    PF._write_ledger(ws, ledger)
    assert "patrol:P99-1" not in EXP._candidates(ws, P.build_plan(ws), st.load_state(ws)["steps"], {})
