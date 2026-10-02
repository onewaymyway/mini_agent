"""tests/test_p9_kb_writeback.py — 第二十二轮 P9：校准率与树声明统计跨实例写入 `knowledge_base`。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P9。
覆盖：新字段向后兼容；两类写入（绝对值覆盖、幂等、小样本不写）；P9 条目不被旧来源合并/检索污染；
读取侧自报标注；撤销（含前缀碰撞、合并条目不动、dry-run）；`advance()`/`advance_lines()` 两条路径的
端到端写入；分叉不重复计数；旁路失败不影响推进；CLI。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import knowledge_base as kb
from world_simulator import tree_effects as te
from world_simulator import tree_grounding as tg

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim
from test_tree_grounding import _b, _line

CAL = kb.CALIBRATION_ORIGIN
TREE = kb.TREE_DECLARATION_ORIGIN


def _items(data_dir, origin=None):
    return [it for it in kb.load_all(data_dir) if origin is None or it.origin == origin]


def _tiers(**kw):
    return {t: {"resolved": 0, "expired": 0, "invalidated": 0, **kw.get(t, {})} for t in ("high", "medium", "low")}


def _record_cal(d, sim="s1", branch="main", min_samples=3, **tiers):
    return kb.record_likelihood_calibration(
        d, sim_id=sim, template="life_sim", branch=branch, tiers=_tiers(**tiers), min_samples=min_samples)


def _row(edge="tree:econ/t->industry", realized=3, countered=0, cause="分支被激活", effect="产业"):
    return {"edge_id": edge, "cause": cause, "effect": effect, "mechanism": "补贴", "realized": realized,
            "countered": countered}


def _record_tree(d, rows, sim="s1", branch="main", min_samples=3):
    return kb.record_tree_declaration_stats(
        d, sim_id=sim, template="life_sim", branch=branch, stats=rows, min_samples=min_samples)


# ═══ 1. 条目字段：向后兼容 ═══════════════════════════════════════════


def test_legacy_item_serialization_has_no_new_keys_and_roundtrips():
    item = kb.KnowledgeItem(id="a", cause="c", effect="e")
    d = item.to_dict()
    assert not {"origin", "self_reported", "source_instance"} & set(d)  # 旧文件逐字节不变
    restored = kb.KnowledgeItem.from_dict(d)
    assert (restored.origin, restored.self_reported, restored.source_instance) == ("", False, "")
    full = kb.KnowledgeItem(id="b", cause="c", effect="e", origin=CAL, self_reported=True, source_instance="s1")
    again = kb.KnowledgeItem.from_dict(json.loads(json.dumps(full.to_dict())))
    assert (again.origin, again.self_reported, again.source_instance) == (CAL, True, "s1")


def test_old_jsonl_without_new_fields_loads(tmp_path):
    path = kb.knowledge_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "x", "cause": "a", "effect": "b", "validated_count": 2}) + "\n", encoding="utf-8")
    [item] = kb.load_all(tmp_path)
    assert item.validated_count == 2 and item.origin == "" and not item.self_reported


@pytest.mark.parametrize("bad", [None, True, 0, -1, "3", float("nan"), []])
def test_clean_min_samples_rejects_garbage(bad):
    assert kb.clean_min_samples(bad) == kb.DEFAULT_MIN_SAMPLES


def test_clean_min_samples_accepts_positive_numbers():
    assert kb.clean_min_samples(5) == 5 and kb.clean_min_samples(2.9) == 2 and kb.clean_min_samples(1) == 1


# ═══ 2. 校准写入 ═════════════════════════════════════════════════════


def test_calibration_writes_only_tiers_with_enough_samples(tmp_path):
    res = _record_cal(tmp_path, high={"resolved": 2, "expired": 1}, medium={"resolved": 1, "invalidated": 1})
    assert res == {"created": 1, "updated": 0, "unchanged": 0, "skipped_small": 2}  # high 3 条够；medium 2 条、low 0 条不够
    [item] = _items(tmp_path, CAL)
    assert item.validated_count == 2 and item.contradicted_count == 1  # expired+invalidated 都算未命中
    assert item.self_reported and item.source_instance == "s1" and item.confidence == "hypothesis"
    assert "n=3" in item.mechanism and "likelihood=high" in item.cause


def test_calibration_counts_expired_and_invalidated_both_as_misses(tmp_path):
    _record_cal(tmp_path, high={"resolved": 1, "expired": 1, "invalidated": 2})
    [item] = _items(tmp_path, CAL)
    assert (item.validated_count, item.contradicted_count) == (1, 3) and "n=4" in item.mechanism


def test_calibration_below_threshold_writes_nothing_and_not_even_the_file(tmp_path):
    res = _record_cal(tmp_path, high={"resolved": 2})
    assert res["skipped_small"] == 3 and res["created"] == 0
    assert not kb.knowledge_path(tmp_path).exists()


def test_calibration_is_idempotent_and_overwrites_with_absolute_counts(tmp_path, monkeypatch):
    _record_cal(tmp_path, high={"resolved": 2, "expired": 1})
    before = kb.knowledge_path(tmp_path).read_bytes()
    saves = []
    real_save = kb._save_all
    monkeypatch.setattr(kb, "_save_all", lambda *a, **k: (saves.append(1), real_save(*a, **k))[1])
    assert _record_cal(tmp_path, high={"resolved": 2, "expired": 1})["unchanged"] == 1
    assert kb.knowledge_path(tmp_path).read_bytes() == before and saves == []  # 没变化就不重写（不是写了同样的内容）
    assert _record_cal(tmp_path, high={"resolved": 3, "expired": 1})["updated"] == 1
    [item] = _items(tmp_path, CAL)
    assert (item.validated_count, item.contradicted_count) == (3, 1)  # 覆盖，不是累加（累加会得 5/2）


def test_calibration_items_are_keyed_by_instance_branch_and_tier(tmp_path):
    for sim, branch in (("s1", "main"), ("s1", "alt"), ("s2", "main")):
        _record_cal(tmp_path, sim=sim, branch=branch, high={"resolved": 3})
    assert len(_items(tmp_path, CAL)) == 3
    assert {it.source_instance for it in _items(tmp_path, CAL)} == {"s1", "s2"}


def test_calibration_respects_custom_threshold(tmp_path):
    assert _record_cal(tmp_path, min_samples=1, low={"expired": 1})["created"] == 1
    assert _record_cal(tmp_path, sim="s2", min_samples=5, low={"expired": 4})["created"] == 0


# ═══ 3. 树声明写入 ═══════════════════════════════════════════════════


def test_tree_declaration_threshold_counts_only_decisive_verdicts(tmp_path):
    res = _record_tree(tmp_path, [_row(realized=2, countered=0), _row(edge="tree:econ/u->x", realized=2, countered=1)])
    assert res["created"] == 1 and res["skipped_small"] == 1
    [item] = _items(tmp_path, TREE)
    assert (item.validated_count, item.contradicted_count) == (2, 1)
    assert item.self_reported and item.origin == TREE and item.source_instance == "s1"


def test_tree_declaration_idempotent_overwrite_and_skips_unnamed_rows(tmp_path, monkeypatch):
    _record_tree(tmp_path, [_row(realized=3)])
    saves = []
    real_save = kb._save_all
    monkeypatch.setattr(kb, "_save_all", lambda *a, **k: (saves.append(1), real_save(*a, **k))[1])
    assert _record_tree(tmp_path, [_row(realized=3)])["unchanged"] == 1 and saves == []
    monkeypatch.setattr(kb, "_save_all", real_save)
    assert _record_tree(tmp_path, [_row(realized=4)])["updated"] == 1
    assert _items(tmp_path, TREE)[0].validated_count == 4 and len(_items(tmp_path, TREE)) == 1
    assert _record_tree(tmp_path, [_row(edge="tree:a/b->c", realized=9, cause="", effect="x")]) == {
        "created": 0, "updated": 0, "unchanged": 0, "skipped_small": 0}
    assert _record_tree(tmp_path, [{"cause": "c", "effect": "e", "realized": 9}, "junk", None])["created"] == 0


def test_tree_declaration_mechanism_falls_back_when_missing(tmp_path):
    _record_tree(tmp_path, [dict(_row(realized=3), mechanism="")])
    assert "未来树分支声明" in _items(tmp_path, TREE)[0].mechanism


# ═══ 4. P9 条目与旧来源互不污染 ══════════════════════════════════════


def test_legacy_writers_never_merge_into_p9_items(tmp_path):
    _record_tree(tmp_path, [_row(cause="补贴政策推动产业升级", effect="产业规模扩大", realized=3)])
    p9 = _items(tmp_path, TREE)[0]
    # 文本完全相同的因果链、P5b 边结论、现实反馈证伪：都不能落到 P9 条目上
    kb.record_causal_links(tmp_path, sim_id="s2", template="t", step=1,
                           causal_links=[{"driver": "补贴政策推动产业升级", "effect": "产业规模扩大"}])
    kb.record_edge_outcomes(tmp_path, sim_id="s2", template="t", branch="main", step=2, outcomes=[
        {"edge_id": "e", "cause": "补贴政策推动产业升级", "effect": "产业规模扩大", "outcome": "countered"}])
    assert kb.update_confidence_from_reality_check(
        tmp_path, [{"driver": "补贴政策推动产业升级", "effect": "产业规模扩大"}]) != [p9.id]
    after = {it.id: it for it in kb.load_all(tmp_path)}
    assert (after[p9.id].validated_count, after[p9.id].contradicted_count) == (3, 0)
    legacy = [it for it in after.values() if not it.origin]
    assert len(after) == 2 and len(legacy) == 1  # 旧来源之间照常合并（因果链新建、P5b 结论并入），但不碰 P9 条目
    assert (legacy[0].validated_count, legacy[0].contradicted_count) == (0, 2)  # 边结论 +1、现实反馈证伪 +1


def test_search_hides_calibration_but_shows_tree_declaration(tmp_path):
    _record_cal(tmp_path, high={"resolved": 3})
    _record_tree(tmp_path, [_row(cause="补贴启动产业升级", effect="产业升级", realized=3)])
    texts = [it.origin for it in kb.search(tmp_path, "未来树分支 likelihood high 补贴启动产业升级")]
    assert CAL not in texts and TREE in texts
    assert CAL in [it.origin for it in kb.search(tmp_path, "未来树分支 likelihood high", include_calibration=True)]
    assert "校准" not in kb.suggest_for_prompt(tmp_path, "未来树分支 likelihood")  # 不进提示词


def test_reflexivity_annotation_search_skips_calibration(tmp_path):
    _record_cal(tmp_path, high={"resolved": 3})
    assert kb.annotate_reflexivity_observation(tmp_path, query_text="未来树分支 likelihood high", note="n") == []


# ═══ 5. 读取侧自报标注 ═══════════════════════════════════════════════


def test_prompt_tags_self_reported_items_only():
    plain = kb.KnowledgeItem(id="a", cause="甲", effect="乙")
    flagged = kb.KnowledgeItem(id="b", cause="丙", effect="丁", self_reported=True)
    lines = kb.format_for_prompt([plain, flagged]).splitlines()
    assert kb.SELF_REPORTED_NOTE not in lines[0] and kb.SELF_REPORTED_NOTE in lines[1]


def test_p5b_edge_outcomes_are_marked_self_reported_on_create_and_merge(tmp_path):
    out = lambda step, edge="e": [{"edge_id": edge, "cause": "甲", "effect": "乙", "outcome": "realized"}]  # noqa: E731
    kb.record_edge_outcomes(tmp_path, sim_id="s1", template="t", branch="main", step=1, outcomes=out(1))
    [item] = kb.load_all(tmp_path)
    assert item.self_reported and item.origin == ""  # 旧来源条目：只多一个标注，不是 P9 条目
    kb.record_causal_links(tmp_path, sim_id="s3", template="t", causal_links=[{"driver": "丙", "effect": "丁"}])
    kb.record_edge_outcomes(tmp_path, sim_id="s2", template="t", branch="main", step=1,
                            outcomes=[{"edge_id": "e", "cause": "丙", "effect": "丁", "outcome": "countered"}])
    merged = next(it for it in kb.load_all(tmp_path) if it.cause == "丙")
    assert merged.self_reported and merged.contradicted_count == 1


def test_record_causal_links_alone_does_not_flag_self_reported(tmp_path):
    kb.record_causal_links(tmp_path, sim_id="s1", template="t", causal_links=[{"driver": "甲", "effect": "乙"}])
    assert not kb.load_all(tmp_path)[0].self_reported


# ═══ 6. 撤销 ═════════════════════════════════════════════════════════


def _seed_mixed(d):
    _record_cal(d, sim="s1", high={"resolved": 3})
    _record_tree(d, [_row(realized=3)], sim="s1")
    _record_cal(d, sim="s10", high={"resolved": 3})  # id 以 s1 开头，不能被误伤
    kb.record_causal_links(d, sim_id="s1", template="t", step=1, causal_links=[{"driver": "独占甲", "effect": "独占乙"}])
    kb.record_causal_links(d, sim_id="s1", template="t", step=2, causal_links=[{"driver": "共享甲", "effect": "共享乙"}])
    kb.record_causal_links(d, sim_id="s2", template="t", step=1, causal_links=[{"driver": "共享甲", "effect": "共享乙"}])


def test_retract_removes_p9_items_of_that_instance_only(tmp_path):
    _seed_mixed(tmp_path)
    res = kb.retract_instance(tmp_path, "s1")
    assert len(res["removed_p9"]) == 2 and res["removed_legacy"] == []
    assert {it.source_instance for it in _items(tmp_path) if it.origin} == {"s10"}  # s10 没被误伤
    assert any(it.cause == "独占甲" for it in kb.load_all(tmp_path))  # 默认不碰旧来源


def test_retract_include_legacy_removes_sole_source_but_not_shared(tmp_path):
    _seed_mixed(tmp_path)
    res = kb.retract_instance(tmp_path, "s1", include_legacy=True)
    causes = {it.cause for it in kb.load_all(tmp_path)}
    assert "独占甲" not in causes and "共享甲" in causes  # 共享条目计数是合并和，无法精确回退，不动
    assert len(res["removed_legacy"]) == 1 and res["left_shared"] == 1


def test_retract_include_legacy_does_not_touch_instance_whose_id_shares_a_prefix(tmp_path):
    kb.record_causal_links(tmp_path, sim_id="s1", template="t", step=1, causal_links=[{"driver": "甲独占", "effect": "x"}])
    kb.record_causal_links(tmp_path, sim_id="s10", template="t", step=1, causal_links=[{"driver": "乙独占", "effect": "y"}])
    res = kb.retract_instance(tmp_path, "s1", include_legacy=True)
    assert len(res["removed_legacy"]) == 1
    assert res["left_shared"] == 0  # `s10#step1` 不是 `s1` 的引用，不能算进"保留的合并条目"
    assert {it.cause for it in kb.load_all(tmp_path)} == {"乙独占"}


def test_retract_never_removes_legacy_items_with_notes_or_no_evidence(tmp_path):
    kb.record_causal_links(tmp_path, sim_id="s1", template="t", step=1, causal_links=[{"driver": "有标注", "effect": "乙"}])
    kb.annotate_reflexivity_observation(tmp_path, query_text="有标注 乙", note="观察")
    items = kb.load_all(tmp_path)
    items.append(kb.KnowledgeItem(id="noev", cause="无来源", effect="x", source_sim_id="s1"))
    kb._save_all(tmp_path, items)
    res = kb.retract_instance(tmp_path, "s1", include_legacy=True)
    assert res["removed_legacy"] == [] and len(kb.load_all(tmp_path)) == 2


def test_retract_dry_run_changes_nothing_and_missing_instance_is_noop(tmp_path):
    _seed_mixed(tmp_path)
    before = kb.knowledge_path(tmp_path).read_bytes()
    dry = kb.retract_instance(tmp_path, "s1", include_legacy=True, dry_run=True)
    assert dry["dry_run"] and len(dry["removed_p9"]) == 2 and len(dry["removed_legacy"]) == 1
    assert kb.knowledge_path(tmp_path).read_bytes() == before
    none = kb.retract_instance(tmp_path, "ghost", include_legacy=True)
    assert none["removed_p9"] == [] and none["removed_legacy"] == [] and none["left_shared"] == 0
    assert kb.knowledge_path(tmp_path).read_bytes() == before


def test_retract_on_empty_knowledge_base(tmp_path):
    assert kb.retract_instance(tmp_path, "s1")["removed_p9"] == []
    assert not kb.knowledge_path(tmp_path).exists()


def test_summarize_sources(tmp_path):
    _seed_mixed(tmp_path)
    kb.record_edge_outcomes(tmp_path, sim_id="s7", template="t", branch="alt", step=2, outcomes=[
        {"edge_id": "e", "cause": "边甲", "effect": "边乙", "outcome": "realized"}])  # 引用形如 s7@alt#step2:e
    rows = kb.summarize_sources(tmp_path)
    assert rows["s7"]["shared_refs"] == 1 and "s7@alt" not in rows
    assert rows["s1"][CAL] == 1 and rows["s1"][TREE] == 1 and rows["s1"]["shared_refs"] == 2
    assert rows["s10"][CAL] == 1 and rows["s2"]["shared_refs"] == 1


# ═══ 7. 输入提取：开关 / 账本 / 树统计 ═══════════════════════════════


def test_switch_matrix_defaults_on_but_requires_prerequisites():
    assert not tg.kb_calibration_enabled({}) and not tg.kb_calibration_enabled({"kb_calibration_writeback": True})
    assert tg.kb_calibration_enabled({"tree_grounding_enabled": True})  # 默认开
    assert not tg.kb_calibration_enabled({"tree_grounding_enabled": True, "kb_calibration_writeback": False})
    on = {"tree_effects_enabled": True, "causal_engine_enabled": True}
    assert te.kb_writeback_enabled(on) and not te.kb_writeback_enabled({**on, "tree_kb_writeback": False})
    assert not te.kb_writeback_enabled({"tree_effects_enabled": True})
    assert not te.kb_writeback_enabled({"causal_engine_enabled": True})


def _snap_state(step, *statuses):
    branches = [{"id": bid, "description": bid, "likelihood": lik, "status": st} for bid, lik, st in statuses]
    return SimpleNamespace(step=step, dynamic_snapshot={"causal_lines": [
        {"id": "econ", "label": "econ", "future_tree": {"branches": branches}}]})


def test_calibration_tiers_follow_ledger_and_filter_inherited_steps():
    hist = [
        _snap_state(0, ("a", "high", "dormant"), ("b", "high", "dormant"), ("c", "low", "dormant")),
        _snap_state(1, ("a", "high", "resolved"), ("b", "high", "dormant"), ("c", "low", "dormant")),
        _snap_state(2, ("a", "high", "resolved"), ("b", "high", "expired"), ("c", "low", "dormant")),
        _snap_state(3, ("a", "high", "resolved"), ("b", "high", "expired"), ("c", "low", "invalidated")),
    ]
    full = tg.kb_calibration_tiers(hist)
    assert full["high"] == {"resolved": 1, "expired": 1, "invalidated": 0}
    assert full["low"] == {"resolved": 0, "expired": 0, "invalidated": 1} and full["medium"]["resolved"] == 0
    own = tg.kb_calibration_tiers(hist, own_after=1)  # 第 1 步及之前是分叉继承的 → 不算
    assert own["high"] == {"resolved": 0, "expired": 1, "invalidated": 0}
    assert tg.kb_calibration_tiers(hist, own_after=3)["low"]["invalidated"] == 0


def _disp(step, edge, disposition):
    return SimpleNamespace(step=step, causal_queued=[], effect_dispositions=[
        {"edge_id": edge, "disposition": disposition, "closed": True}])


def test_kb_stats_only_tree_edges_decisive_verdicts_and_own_steps():
    settings = {
        "causal_lines": [{"id": "econ", "label": "经济", "future_tree": {"branches": [
            {"id": "t", "description": "补贴启动", "likelihood": "high", "status": "active",
             "effects_if_active": [{"to_line_id": "industry", "mechanism": "拉动投资"}]}]}},
            {"id": "industry", "label": "产业"}],
    }
    edge = "tree:econ/t->industry"
    hist = [_disp(1, edge, "realized"), _disp(2, edge, "realized"), _disp(3, edge, "countered"),
            _disp(4, edge, "dampened"), _disp(5, "plain_edge", "realized"),
            _disp(5, "ab:econ/t->industry", "realized"),  # 普通边 id 恰好长得像"去掉前缀后的树边"，不能被当成树边
            _disp(6, "tree:econ/gone->industry", "dampened")]
    rows = te.kb_stats(settings, hist)
    assert len(rows) == 1  # 非树边、只有 dampened 的都不出
    row = rows[0]
    assert (row["realized"], row["countered"]) == (2, 1) and row["edge_id"] == edge
    assert row["cause"] == "未来树分支「补贴启动」（线「经济」）被激活" and row["effect"] == "产业"
    assert row["mechanism"] == "拉动投资"
    own = te.kb_stats(settings, hist, own_after=2)
    assert (own[0]["realized"], own[0]["countered"]) == (0, 1)


def test_kb_stats_falls_back_to_ids_when_declaration_was_deleted():
    rows = te.kb_stats({}, [_disp(1, "tree:econ/t->industry", "countered")])
    assert rows[0]["cause"].startswith("未来树分支「t」") and rows[0]["effect"] == "industry"
    assert rows[0]["mechanism"] == ""
    assert te.kb_stats({}, [_disp(1, "tree:malformed", "realized")]) == []


# ═══ 8. advance() 端到端 ═════════════════════════════════════════════


def _tree_settings(*lines, **extra):
    s = {"causal_lines": list(lines), "tree_grounding_enabled": True, "kb_min_samples": 1}
    s.update(extra)
    return s


def _upd(*pairs):
    return [{"line_id": "econ", "status_updates": [{"branch_id": b, "status": s} for b, s in pairs]}]


def _baseline(monkeypatch, tmp_path, data_dir):
    """第一个被推进的步只落基线快照：账本要靠\"前一份快照\"识别终结事件（P5c 既有口径），所以终结要从第 2 步起。"""
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))


def _econ(*extra_branches):
    return _line("econ", _b("a", likelihood="high"), _b("b", likelihood="high"), *extra_branches)


def test_advance_writes_calibration_for_terminal_branches(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _tree_settings(_econ()))
    _baseline(monkeypatch, tmp_path, data_dir)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, tree_updates=_upd(("a", "resolved"), ("b", "expired"))))
    [item] = _items(data_dir, CAL)
    assert (item.validated_count, item.contradicted_count) == (1, 1)
    assert item.source_instance == "sim1" and item.self_reported and "likelihood=high" in item.cause
    # 再推进一步（无新终结）：幂等，不重复、不累加
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3))
    [again] = _items(data_dir, CAL)
    assert again.id == item.id and (again.validated_count, again.contradicted_count) == (1, 1)


def test_advance_first_step_terminal_events_are_not_counted_for_lack_of_baseline(tmp_path, monkeypatch):
    """既有口径（P5c 账本）：第一个被推进的步没有\"前一份快照\"，在这一步就终结的分支不记——如实记在已知边界里。"""
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _tree_settings(_econ()))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=_upd(("a", "resolved"), ("b", "expired"))))
    assert _items(data_dir, CAL) == []


def test_advance_default_threshold_needs_three_terminal_events(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _tree_settings(_econ(_b("c", likelihood="high")), kb_min_samples=None))  # 缺省阈值 = 3
    _baseline(monkeypatch, tmp_path, data_dir)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, tree_updates=_upd(("a", "resolved"), ("b", "expired"))))
    assert _items(data_dir, CAL) == []  # 2 个事件：不写
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3, tree_updates=_upd(("c", "invalidated"))))
    assert len(_items(data_dir, CAL)) == 1  # 第 3 个：写


@pytest.mark.parametrize("override", [
    {"kb_calibration_writeback": False},   # 显式关
    {"tree_grounding_enabled": False},     # 没开树接地（旧实例）→ 不往共享库写
])
def test_advance_switches_off_write_nothing(tmp_path, monkeypatch, override):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _tree_settings(_econ(), **override))
    _baseline(monkeypatch, tmp_path, data_dir)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, tree_updates=_upd(("a", "resolved"), ("b", "expired"))))
    assert not kb.knowledge_path(data_dir).exists()


def test_advance_forked_branch_does_not_recount_inherited_events(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _tree_settings(_econ(_b("c", likelihood="high"))))
    _baseline(monkeypatch, tmp_path, data_dir)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, tree_updates=_upd(("a", "resolved"))))
    main_items = _items(data_dir, CAL)
    assert len(main_items) == 1 and main_items[0].validated_count == 1
    alt = bm.fork_branch(data_dir, "sim1", from_step=2)
    # 分叉后推进一步但没有新终结：分叉分支没有自己的事件 → 不写（继承的那一条不能再算一遍）
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3))
    assert len(_items(data_dir, CAL)) == 1
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(4, tree_updates=_upd(("b", "expired"))))
    items = {it.evidence[0].split("#")[0]: it for it in _items(data_dir, CAL)}
    assert set(items) == {"sim1@main", f"sim1@{alt}"}
    assert (items["sim1@main"].validated_count, items["sim1@main"].contradicted_count) == (1, 0)
    fork = items[f"sim1@{alt}"]
    assert (fork.validated_count, fork.contradicted_count) == (0, 1)  # 只有分叉后自己产生的 expired


def test_advance_forked_branch_without_meta_is_skipped_rather_than_double_counted(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _tree_settings(_econ()))
    _baseline(monkeypatch, tmp_path, data_dir)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, tree_updates=_upd(("a", "resolved"))))
    bm.fork_branch(data_dir, "sim1", from_step=2)
    monkeypatch.setattr(type(store), "load_branch_meta", lambda self, branch="main": {})
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3, tree_updates=_upd(("b", "expired"))))
    assert len(_items(data_dir, CAL)) == 1  # 只有主线那条；元信息缺失的分叉分支宁可不写


def test_advance_writer_failure_is_isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    line = _line("econ", _b("a", likelihood="high"), _b("t", effects_if_active=[{"to_line_id": "industry"}]))
    _make_sim(data_dir, _tree_settings(line, _line("industry"), causal_engine_enabled=True, tree_effects_enabled=True))
    def _boom(*a, **k):
        raise RuntimeError("calibration writer exploded")

    monkeypatch.setattr(kb, "record_likelihood_calibration", _boom)
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        1, tree_updates=_upd(("a", "resolved"), ("t", "active"))))
    assert state.step == 1  # 推进成功
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, effect_dispositions=[
        {"pending_id": "tree:econ/t->industry@1", "disposition": "realized", "reason": "r"}]))
    assert len(_items(data_dir, TREE)) == 1  # 校准那一路坏了，树声明那一路照常写


# ── 树声明 ──

def _effects_settings(**extra):
    line = _line("econ", _b("t", effects_if_active=[{"to_line_id": "industry", "mechanism": "补贴拉动"}]))
    settings = _tree_settings(line, _line("industry"), causal_engine_enabled=True, tree_effects_enabled=True)
    settings.update(extra)
    return settings


def _activate_and_realize(monkeypatch, tmp_path, data_dir):
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=_upd(("t", "active"))))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, effect_dispositions=[
        {"pending_id": "tree:econ/t->industry@1", "disposition": "realized", "reason": "产业投资上升"}]))


def test_advance_writes_tree_declaration_stats(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _effects_settings())
    _activate_and_realize(monkeypatch, tmp_path, data_dir)
    [item] = _items(data_dir, TREE)
    assert item.validated_count == 1 and item.contradicted_count == 0 and item.self_reported
    assert "补贴拉动" in item.mechanism and item.effect == "industry" and "「t」" in item.cause
    # P5b 的边回写不受影响：树边仍然不进 P5b 的条目
    assert [it for it in kb.load_all(data_dir) if not it.origin] == []


@pytest.mark.parametrize("override", [
    {"tree_kb_writeback": False}, {"tree_effects_enabled": False}, {"causal_engine_enabled": False},
])
def test_advance_tree_declaration_switches_write_nothing(tmp_path, monkeypatch, override):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _effects_settings(**override))
    _activate_and_realize(monkeypatch, tmp_path, data_dir)
    assert _items(data_dir, TREE) == []


def test_advance_tree_declaration_default_threshold_not_met(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _effects_settings(kb_min_samples=None))
    _activate_and_realize(monkeypatch, tmp_path, data_dir)
    assert _items(data_dir, TREE) == []  # 只有 1 个明确结论 < 3


def test_calibration_and_tree_writers_coexist_and_retract_cleans_both(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    line = _line("econ", _b("t", likelihood="high", effects_if_active=[{"to_line_id": "industry"}]))
    _make_sim(data_dir, _tree_settings(line, _line("industry"), causal_engine_enabled=True, tree_effects_enabled=True))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=_upd(("t", "active"))))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, tree_updates=_upd(("t", "resolved")),
        effect_dispositions=[{"pending_id": "tree:econ/t->industry@1", "disposition": "realized", "reason": "r"}]))
    assert len(_items(data_dir, CAL)) == 1 and len(_items(data_dir, TREE)) == 1
    res = kb.retract_instance(data_dir, "sim1")
    assert len(res["removed_p9"]) == 2 and kb.load_all(data_dir) == []


# ═══ 9. advance_lines() 路径 ═════════════════════════════════════════

from test_advance_lines_mechanisms import LINES, _install_runner, _lines_step, _make_sim as _make_line_sim, _pending  # noqa: E402


def test_advance_lines_writes_p5b_edge_outcomes_and_p9_tree_stats(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    lines = [dict(LINES[0], future_tree={"as_of_step": 0, "branches": [
        {"id": "t", "description": "A 的分支", "likelihood": "medium", "status": "active",
         "effects_if_active": [{"to_line_id": "B", "mechanism": "传导"}]}]}), LINES[1]]
    _make_line_sim(data_dir, lines=lines, settings={
        "causal_engine_enabled": True, "tree_effects_enabled": True, "kb_min_samples": 1,
        "declared_causal_graph": [{"id": "e", "from_line_id": "A", "to_line_id": "B", "mechanism": "边传导"}],
        "causal_pending": [
            _pending(),  # 声明边 e
            _pending(pid="tree:A/t->B@0", edge_id="tree:A/t->B", trigger_reason="tree_effect"),
        ]})
    _install_runner(monkeypatch, tmp_path, {"B": {"effect_dispositions": [
        {"pending_id": "e@0", "disposition": "realized", "reason": "体现"},
        {"pending_id": "tree:A/t->B@0", "disposition": "countered", "reason": "被抵消"},
    ]}}, [])
    _lines_step(tmp_path, data_dir)
    legacy = [it for it in kb.load_all(data_dir) if not it.origin]
    assert len(legacy) == 1 and legacy[0].validated_count == 1 and legacy[0].self_reported  # P8 漏接的 P5b 回写
    [tree_item] = _items(data_dir, TREE)
    assert (tree_item.validated_count, tree_item.contradicted_count) == (0, 1)
    assert tree_item.effect == "线B" and tree_item.source_instance == "sim1"


def test_advance_lines_without_switches_writes_nothing(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_line_sim(data_dir, lines=LINES, settings={})
    _install_runner(monkeypatch, tmp_path, {}, [])
    _lines_step(tmp_path, data_dir)
    assert not kb.knowledge_path(data_dir).exists()


# ═══ 10. CLI ═════════════════════════════════════════════════════════


@pytest.fixture()
def cli(monkeypatch, tmp_path):
    ep_dir = Path(__file__).resolve().parent.parent / "entrypoints"
    monkeypatch.syspath_prepend(str(ep_dir))
    spec = importlib.util.spec_from_file_location("ws_knowledge_cli", ep_dir / "knowledge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    return mod


def test_cli_list_and_retract(cli, tmp_path, capsys):
    assert cli.main(["list"]) == 0 and "没有任何实例" in capsys.readouterr().out
    _seed_mixed(tmp_path)
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "s1" in out and "s10" in out
    assert cli.main(["retract", "s1", "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().out and len(_items(tmp_path, CAL)) == 2
    assert cli.main(["retract", "s1", "--include-legacy"]) == 0
    capsys.readouterr()
    assert [it.source_instance for it in _items(tmp_path) if it.origin] == ["s10"]
    assert "独占甲" not in {it.cause for it in kb.load_all(tmp_path)}


# ═══ 11. 界面静态接线 ════════════════════════════════════════════════


def test_app_wires_new_settings_and_knowledge_labels():
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    for needle in ("kb_calibration_writeback=bool(new_kb_cal)", "tree_kb_writeback=bool(new_tree_kb)",
                   "kb_min_samples=int(new_kb_min)", "knowledge_base_mod.SELF_REPORTED_NOTE",
                   "knowledge_base_mod.CALIBRATION_ORIGIN"):
        assert needle in src
