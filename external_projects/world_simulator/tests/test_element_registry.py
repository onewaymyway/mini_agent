"""tests/test_element_registry.py — 第二十三轮 E1：统一元素模型的注册表与存取层。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.1/§4.2/§5/§6 E1。

覆盖：元素字段规整（旧式线逐字节不变）、id/label/别名解析（只做精确规范化匹配，不模糊）、
领域分组、技术节点 ↔ lifecycle 往返、写回（建线/更新/摘除）、旧 `tech_state` 折叠
（幂等、id 冲突规则）、新实例开关与种子折叠。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import element_registry as er
from world_simulator import tech_model as tm
from world_simulator.engine.materialize import materialize_simulation
from world_simulator.store import SimStore


def _node(tid="t1", stage="lab", **extra):
    n = {"id": tid, "name": tid, "stage": stage, "progress": 0.25,
         "typical_dwell_days": {s: 100 for s in tm.STAGES}, "dwell_source": "user"}
    n.update(extra)
    return tm.normalize_node(n)


# ── 开关 ─────────────────────────────────────────────────────────────


def test_switch_is_off_when_key_missing():
    assert er.is_enabled(None) is False
    assert er.is_enabled({}) is False
    assert er.is_enabled({"element_modeling_enabled": False}) is False
    assert er.is_enabled({"element_modeling_enabled": True}) is True


# ── 规整 ─────────────────────────────────────────────────────────────


def test_norm_key_is_exact_normalization_not_fuzzy():
    assert er.norm_key("GPT-5") == er.norm_key("gpt_5") == er.norm_key(" ｇｐｔ 5 ") == "gpt5"
    assert er.norm_key("固态 电池") == er.norm_key("固态电池")
    # 相近但不同的东西不会被当成同一个
    assert er.norm_key("gpt5") != er.norm_key("gpt5pro")
    assert er.norm_key("   ") == "" and er.norm_key(None) == ""


def test_normalize_element_leaves_legacy_line_byte_identical():
    legacy = {"id": "tech", "label": "技术线", "time_granularity": "年",
              "advance_every_n_steps": 2, "owned_vars": ["a"], "future_tree": {"branches": []},
              "some_future_field": {"x": 1}}
    assert er.normalize_element(legacy) == legacy
    assert er.normalize_element(legacy) is not legacy  # 浅拷贝，不改入参


def test_normalize_element_validates_only_present_fields_and_drops_bad_values():
    raw = {
        "id": "x", "kind": "weird", "parent": "x", "element_type": "  ", "aliases": ["x", "A", "A", " ", "B"],
        "origin": "bogus", "born_step": -3, "profile_status": "??", "status": "gone", "merged_into": " ",
        "tier_pin": "watch", "var_refs": ["vars.cash", "", "vars.cash"], "lifecycle": "not a dict",
    }
    out = er.normalize_element(raw)
    assert out == {"id": "x", "aliases": ["A", "B"], "var_refs": ["vars.cash"]}


def test_normalize_element_keeps_valid_values():
    raw = {"id": "x", "kind": "element", "parent": " tech ", "element_type": "project", "origin": "split",
           "born_step": "4", "profile_status": "fallback", "status": "retired", "merged_into": "y",
           "tier_pin": "active", "lifecycle": {"stage": "lab"}}
    out = er.normalize_element(raw)
    assert out["parent"] == "tech" and out["born_step"] == 4 and out["kind"] == "element"
    assert out["status"] == "retired" and out["tier_pin"] == "active" and out["lifecycle"] == {"stage": "lab"}


# ── 解析 / 查询 ──────────────────────────────────────────────────────


def _lines():
    return [
        {"id": "tech", "label": "技术", "kind": "domain"},
        {"id": "gpu_supply", "label": "GPU 供给", "kind": "element", "parent": "tech", "aliases": ["显卡供应"]},
        {"id": "model_v2", "label": "Model V2", "kind": "element", "parent": "tech"},
        {"id": "rate_cycle", "label": "利率周期", "kind": "element", "parent": "ghost_domain"},
        {"id": "old_line", "label": "旧式线"},
    ]


def test_resolve_exact_id_then_normalized_then_label_and_alias():
    lines = _lines()
    assert er.resolve(lines, "gpu_supply")["id"] == "gpu_supply"
    assert er.resolve(lines, "GPU-Supply")["id"] == "gpu_supply"
    assert er.resolve(lines, "GPU 供给")["id"] == "gpu_supply"
    assert er.resolve(lines, "显卡 供应")["id"] == "gpu_supply"
    assert er.resolve(lines, "model v2")["id"] == "model_v2"
    assert er.resolve({"causal_lines": lines}, "旧式线")["id"] == "old_line"


def test_resolve_does_not_do_fuzzy_matching():
    lines = _lines()
    assert er.resolve(lines, "gpu") is None
    assert er.resolve(lines, "model_v3") is None
    assert er.resolve(lines, "") is None and er.resolve(lines, None) is None


def test_resolve_exact_id_beats_alias_of_another_line():
    lines = [{"id": "a", "label": "A"}, {"id": "b", "label": "B", "aliases": ["a"]}]
    assert er.resolve(lines, "a")["id"] == "a"


def test_domains_children_and_grouping():
    lines = _lines()
    assert [d["id"] for d in er.domains(lines)] == ["tech"]
    assert [c["id"] for c in er.children_of(lines, "tech")] == ["gpu_supply", "model_v2"]
    groups = er.group_by_domain(lines)
    assert [(g[0]["id"] if g[0] else None, [x["id"] for x in g[1]]) for g in groups] == [
        ("tech", ["gpu_supply", "model_v2"]),
        (None, ["rate_cycle", "old_line"]),  # 指向不存在领域 / 旧式线 → 未归类，不猜
    ]


def test_group_by_domain_without_domains_is_flat():
    lines = [{"id": "a"}, {"id": "b"}]
    assert er.group_by_domain(lines) == [(None, lines)]
    assert er.group_by_domain([]) == []


def test_is_alive_defaults_to_alive():
    assert er.is_alive({"id": "a"}) and er.is_alive({"id": "a", "status": "alive"})
    assert not er.is_alive({"id": "a", "status": "retired"}) and not er.is_alive({"id": "a", "status": "merged"})


# ── 技术节点 ↔ lifecycle ─────────────────────────────────────────────


def test_node_lifecycle_roundtrip_is_lossless():
    node = _node("ai", stage="expert", kind="硬件", requires=[{"tech_id": "chip", "min_stage": "lab", "mode": "soft"}],
                 bottleneck="供应", bottleneck_severity="high", adoption=0.2, market="m", substitutes=["x"],
                 perceived_stage="developer", investment="high", preexisting=True)
    lifecycle = er.node_to_lifecycle(node)
    assert "id" not in lifecycle and "name" not in lifecycle and "kind" not in lifecycle
    assert lifecycle["sub_kind"] == "硬件"
    line = {"id": "ai", "label": node["name"], "lifecycle": lifecycle}
    assert tm.normalize_node(er.line_to_node_raw(line)) == node


def test_lifecycle_is_deep_copied():
    node = _node("ai")
    lifecycle = er.node_to_lifecycle(node)
    lifecycle["typical_dwell_days"]["lab"] = 1
    assert node["typical_dwell_days"]["lab"] == 100


# ── 写回 ─────────────────────────────────────────────────────────────


def test_write_creates_element_line_for_new_node_with_default_future_tree():
    settings = {"element_modeling_enabled": True, "causal_lines": [{"id": "main_line", "label": "主线"}]}
    seed = _node("ai", name="人工智能")
    new = tm.normalize_node({**seed, "created_step": 5, "name": "芯片", "id": "chip"})
    er.write_tech_nodes(settings, [seed, new])
    by_id = {x["id"]: x for x in settings["causal_lines"]}
    assert set(by_id) == {"main_line", "ai", "chip"}
    ai, chip = by_id["ai"], by_id["chip"]
    assert ai["kind"] == "element" and ai["element_type"] == "technology" and ai["origin"] == "seed"
    assert ai["profile_status"] == "complete" and ai["born_step"] == 0 and "parent" not in ai
    assert chip["origin"] == "discovered" and chip["profile_status"] == "pending_enrichment"
    assert chip["born_step"] == 5 and chip["auto_discovered"] is True and chip["label"] == "芯片"
    assert ai["future_tree"]["branches"] and chip["future_tree"]["branches"]  # "一定有未来树"
    assert "tech_state" not in settings


def test_write_updates_existing_line_in_place_and_keeps_other_fields():
    line = {"id": "ai", "label": "旧名", "element_type": "technology", "aliases": ["AI"], "owned_vars": ["v"]}
    settings = {"element_modeling_enabled": True, "causal_lines": [line]}
    er.write_tech_nodes(settings, [_node("ai", name="新名", stage="developer")])
    out = settings["causal_lines"][0]
    assert out["label"] == "新名" and out["aliases"] == ["AI"] and out["owned_vars"] == ["v"]
    assert out["lifecycle"]["stage"] == "developer"
    assert "lifecycle" not in line  # 不改动传入的旧对象


def test_write_merges_into_plain_legacy_line_without_changing_its_kind():
    settings = {"element_modeling_enabled": True, "causal_lines": [{"id": "ai", "label": "AI 线"}]}
    er.write_tech_nodes(settings, [_node("ai", name="AI 技术")])
    out = settings["causal_lines"][0]
    assert out["element_type"] == "technology" and "kind" not in out
    assert out["label"] == "AI 线"  # 非技术元素线的 label 不被节点名覆盖
    assert out["lifecycle"]["stage"] == "lab"


def test_write_removes_lifecycle_of_nodes_no_longer_present_but_keeps_line():
    settings = {"element_modeling_enabled": True, "causal_lines": []}
    er.write_tech_nodes(settings, [_node("a"), _node("b")])
    er.write_tech_nodes(settings, [_node("a")])
    by_id = {x["id"]: x for x in settings["causal_lines"]}
    assert "lifecycle" in by_id["a"] and "lifecycle" not in by_id["b"]


def test_write_with_no_nodes_and_no_lines_does_not_create_causal_lines_key():
    settings = {"element_modeling_enabled": True}
    er.write_tech_nodes(settings, [])
    assert "causal_lines" not in settings


def test_read_union_of_lines_and_leftover_legacy_nodes():
    settings = {
        "element_modeling_enabled": True,
        "causal_lines": [{"id": "a", "label": "A", "lifecycle": er.node_to_lifecycle(_node("a"))}],
        "tech_state": {"nodes": [_node("a", stage="expert"), _node("z")]},
    }
    ids = [n["id"] for n in tm.get_nodes(settings)]
    assert ids == ["a", "z"]
    assert tm.get_nodes(settings)[0]["stage"] == "lab"  # 线上的优先
    before = copy.deepcopy(settings)
    tm.get_nodes(settings)
    assert settings == before  # 读不改 settings


# ── 折叠 ─────────────────────────────────────────────────────────────


def test_fold_converts_legacy_nodes_and_is_idempotent():
    settings = {"element_modeling_enabled": True, "causal_lines": [{"id": "main_line", "label": "主线"}],
                "tech_state": {"nodes": [_node("ai"), _node("chip")]}}
    assert er.fold_legacy_tech_state(settings) is True
    assert "tech_state" not in settings
    by_id = {x["id"]: x for x in settings["causal_lines"]}
    assert by_id["ai"]["origin"] == "legacy_tech" and by_id["ai"]["element_type"] == "technology"
    assert [n["id"] for n in tm.get_nodes(settings)] == ["ai", "chip"]
    snapshot = copy.deepcopy(settings)
    assert er.fold_legacy_tech_state(settings) is False
    assert settings == snapshot


def test_fold_attaches_to_same_id_plain_line_instead_of_duplicating():
    settings = {"causal_lines": [{"id": "ai", "label": "AI 线"}], "tech_state": {"nodes": [_node("ai")]}}
    er.fold_legacy_tech_state(settings)
    assert [x["id"] for x in settings["causal_lines"]] == ["ai"]
    assert settings["causal_lines"][0]["lifecycle"]["stage"] == "lab"


def test_fold_renames_node_colliding_with_domain_line_and_rewrites_requires():
    settings = {
        "causal_lines": [{"id": "tech", "label": "技术", "kind": "domain"}],
        "tech_state": {"nodes": [
            _node("tech"),
            _node("chip", requires=[{"tech_id": "tech", "min_stage": "lab", "mode": "hard"}]),
        ]},
    }
    er.fold_legacy_tech_state(settings)
    by_id = {x["id"]: x for x in settings["causal_lines"]}
    assert by_id["tech"]["kind"] == "domain" and "lifecycle" not in by_id["tech"]  # 领域线不被占用
    assert by_id["tech_tech"]["aliases"] == ["tech"] and by_id["tech_tech"]["origin"] == "legacy_tech"
    chip = next(n for n in tm.get_nodes({"element_modeling_enabled": True, "causal_lines": settings["causal_lines"]})
                if n["id"] == "chip")
    assert chip["requires"][0]["tech_id"] == "tech_tech"


def test_fold_without_tech_state_is_noop_and_empty_tech_state_is_just_removed():
    s = {"causal_lines": [{"id": "a"}]}
    assert er.fold_legacy_tech_state(s) is False and s == {"causal_lines": [{"id": "a"}]}
    s2 = {"tech_state": {}}
    assert er.fold_legacy_tech_state(s2) is True and s2 == {}


# ── 新实例开关与种子折叠 ─────────────────────────────────────────────


def _materialize(tmp_path, settings):
    manifest = materialize_simulation(
        tmp_path / "data", template="life_sim", intent="i", title="t", summary="s",
        vars={"age": 20}, options=[], settings=settings,
    )
    return SimStore.for_root(tmp_path / "data", manifest.sim_id).load_manifest()


def test_new_instance_gets_switch_on_and_keeps_main_line_fallback(tmp_path):
    manifest = _materialize(tmp_path, {})
    assert manifest.settings["element_modeling_enabled"] is True
    assert [x["id"] for x in manifest.settings["causal_lines"]] == ["main_line"]


def test_explicit_switch_value_is_respected(tmp_path):
    manifest = _materialize(tmp_path, {"element_modeling_enabled": False, "tech_state": {"nodes": [_node("ai")]}})
    assert manifest.settings["element_modeling_enabled"] is False
    assert manifest.settings["tech_state"]["nodes"][0]["id"] == "ai"  # 旧存储原样


def test_new_instance_folds_tech_seed_into_element_lines(tmp_path):
    manifest = _materialize(tmp_path, {"tech_model_enabled": True, "tech_state": {"nodes": [_node("ai")]}})
    assert "tech_state" not in manifest.settings
    ids = [x["id"] for x in manifest.settings["causal_lines"]]
    assert ids == ["main_line", "ai"]  # 兜底主线仍在
    assert [n["id"] for n in tm.get_nodes(manifest.settings)] == ["ai"]


def test_resolve_prefers_exact_id_over_earlier_line_with_same_normalized_id():
    lines = [{"id": "gpt-5", "label": "A"}, {"id": "gpt5", "label": "B"}]
    assert er.resolve(lines, "gpt5")["label"] == "B"
    assert er.resolve(lines, "GPT 5")["label"] == "A"  # 只有规范化匹配时，先到先得


def test_write_removes_empty_legacy_tech_state_key():
    settings = {"element_modeling_enabled": True, "tech_state": {}, "causal_lines": [{"id": "a"}]}
    er.write_tech_nodes(settings, [])
    assert "tech_state" not in settings
