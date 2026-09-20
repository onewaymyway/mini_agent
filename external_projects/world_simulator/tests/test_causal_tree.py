"""tests/test_causal_tree.py — 阶段二十六（因果线未来因果树）单元测试。

对应 `next_doc/world_simulator_causal_line_future_tree_plan.md` 的验收
标准：创建模拟时因果线一定有未来树（就算完全没有声明也会兜底生成
"主线"）、`tree_updates` 能正确合并印证/排除/新增分支、用户手动
`set_branch_status()` 能直接生效。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_tree


def test_ensure_future_trees_backfills_empty_list():
    result = causal_tree.ensure_future_trees([], as_of_step=0)
    assert len(result) == 1
    assert result[0]["id"] == "main_line"
    branches = result[0]["future_tree"]["branches"]
    assert len(branches) >= 2
    assert all(b["status"] == "dormant" for b in branches)


def test_ensure_future_trees_backfills_missing_tree_only():
    lines = [
        {"id": "tech", "label": "技术线"},
        {
            "id": "negotiation",
            "label": "谈判线",
            "future_tree": {
                "branches": [{"id": "deal", "description": "达成协议", "likelihood": "high"}]
            },
        },
    ]
    result = causal_tree.ensure_future_trees(lines, as_of_step=2)
    by_id = {line["id"]: line for line in result}
    # 没有 future_tree 的线被兜底补全
    assert len(by_id["tech"]["future_tree"]["branches"]) == 3
    # 已经有合法 future_tree 的线原样保留，不被覆盖
    assert by_id["negotiation"]["future_tree"]["branches"][0]["id"] == "deal"


def test_normalize_future_tree_rejects_invalid_shapes():
    assert causal_tree.normalize_future_tree(None) is None
    assert causal_tree.normalize_future_tree({"branches": []}) is None
    assert causal_tree.normalize_future_tree({"branches": [{"id": "x"}]}) is None  # 缺 description
    ok = causal_tree.normalize_future_tree(
        {"branches": [{"id": "x", "description": "d", "likelihood": "weird"}]}
    )
    assert ok is not None
    assert ok["branches"][0]["likelihood"] == "medium"  # 非法值回退默认


def test_auto_register_lines_adds_default_tree_for_new_line():
    result = causal_tree.auto_register_lines(
        [], line_update_ids=["tech"], causal_link_line_ids=[], as_of_step=1
    )
    assert len(result) == 1
    assert result[0]["id"] == "tech"
    assert result[0]["auto_discovered"] is True
    assert len(result[0]["future_tree"]["branches"]) == 3


def test_apply_tree_updates_confirm_prune_and_new_branch():
    causal_lines = [
        {
            "id": "tech",
            "label": "技术线",
            "future_tree": {
                "branches": [
                    {"id": "fast", "description": "快速下降", "likelihood": "medium", "status": "open"},
                    {"id": "slow", "description": "缓慢下降", "likelihood": "medium", "status": "open"},
                ]
            },
        }
    ]
    updates = [
        {
            "line_id": "tech",
            "confirmed_branch": "fast",
            "pruned_branches": ["slow"],
            "new_branches": [{"description": "政策突变导致成本骤降", "likelihood": "low"}],
        }
    ]
    updated_lines, audit = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=3)
    tree = updated_lines[0]["future_tree"]
    by_id = {b["id"]: b for b in tree["branches"]}
    # 阶段三十三第四批：6 态生命周期下，confirmed_branch 落盘为
    # resolved，pruned_branches 落盘为 invalidated（旧名 confirmed/
    # pruned 的新对应，见 causal_tree.py 顶部 docstring）。
    assert by_id["fast"]["status"] == "resolved"
    assert by_id["slow"]["status"] == "invalidated"
    assert len(tree["branches"]) == 3
    assert tree["as_of_step"] == 3
    assert len(audit) == 1
    assert audit[0]["line_id"] == "tech"
    assert audit[0]["confirmed_branch"] == "fast"
    assert audit[0]["pruned_branches"] == ["slow"]
    assert len(audit[0]["new_branch_ids"]) == 1


def test_apply_tree_updates_no_effect_returns_empty_audit():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "fast", "description": "d", "status": "open"}]},
        }
    ]
    # 引用不存在的 line_id，应该被忽略，不产生 audit
    updated_lines, audit = causal_tree.apply_tree_updates(
        causal_lines, [{"line_id": "unknown", "confirmed_branch": "fast"}], as_of_step=1
    )
    assert audit == []
    # 没有命中的这条线完全没被 apply_tree_updates 处理（也没有走
    # normalize_future_tree），原始的旧状态值原样保留在数据里。
    assert updated_lines[0]["future_tree"]["branches"][0]["status"] == "open"


def test_set_branch_status_manual_override():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "fast", "description": "d", "status": "open"}]},
        }
    ]
    updated = causal_tree.set_branch_status(causal_lines, "tech", "fast", "pruned")
    # 阶段三十三第四批：接受旧名 "pruned"，落盘为新 6 态里的
    # "invalidated"（见 causal_tree._LEGACY_STATUS_MAP）。
    assert updated[0]["future_tree"]["branches"][0]["status"] == "invalidated"
    # 也接受新 6 态写法，直接生效
    updated2 = causal_tree.set_branch_status(updated, "tech", "fast", "emerging")
    assert updated2[0]["future_tree"]["branches"][0]["status"] == "emerging"
    # 非法状态值不生效
    unchanged = causal_tree.set_branch_status(updated2, "tech", "fast", "not_a_status")
    assert unchanged[0]["future_tree"]["branches"][0]["status"] == "emerging"


# ── 阶段三十三第四批（4.9 节：KeyNode 6 态生命周期 + 4.11 节：
#    渐进式展开）────────────────────────────────────────────────────


def test_canonical_status_maps_legacy_names():
    assert causal_tree.canonical_status("open") == "dormant"
    assert causal_tree.canonical_status("confirmed") == "resolved"
    assert causal_tree.canonical_status("diverged") == "expired"
    assert causal_tree.canonical_status("pruned") == "invalidated"


def test_canonical_status_passes_through_new_names():
    for status in ("dormant", "emerging", "active", "resolved", "expired", "invalidated"):
        assert causal_tree.canonical_status(status) == status


def test_canonical_status_falls_back_to_dormant_for_unknown():
    assert causal_tree.canonical_status("something_weird") == "dormant"
    assert causal_tree.canonical_status(None) == "dormant"


def test_normalize_branch_includes_keynode_fields_with_safe_defaults():
    tree = causal_tree.normalize_future_tree(
        {"branches": [{"id": "x", "description": "d"}]}
    )
    branch = tree["branches"][0]
    assert branch["semantic_event"] == ""
    assert branch["trigger_conditions"] == ""
    assert branch["prerequisites"] == []
    assert branch["candidate_actions"] == []
    assert branch["time_window"] == ""
    assert branch["urgency"] is None
    assert branch["expansion_level"] == "compressed"
    assert branch["sub_branches"] == []


def test_normalize_branch_preserves_declared_keynode_fields():
    tree = causal_tree.normalize_future_tree(
        {
            "branches": [
                {
                    "id": "x",
                    "description": "监管突然收紧",
                    "semantic_event": "监管机构发布新规",
                    "trigger_conditions": "行业投诉达到阈值",
                    "prerequisites": ["p1", "p2"],
                    "candidate_actions": ["提前合规", "游说延后"],
                    "time_window": "未来 2~3 个季度",
                    "urgency": "high",
                }
            ]
        }
    )
    branch = tree["branches"][0]
    assert branch["semantic_event"] == "监管机构发布新规"
    assert branch["trigger_conditions"] == "行业投诉达到阈值"
    assert branch["prerequisites"] == ["p1", "p2"]
    assert branch["candidate_actions"] == ["提前合规", "游说延后"]
    assert branch["time_window"] == "未来 2~3 个季度"
    assert branch["urgency"] == "high"


def test_normalize_branch_rejects_invalid_urgency():
    tree = causal_tree.normalize_future_tree(
        {"branches": [{"id": "x", "description": "d", "urgency": "extremely_bad"}]}
    )
    assert tree["branches"][0]["urgency"] == "medium"


def test_normalize_branch_parses_expanded_sub_branches():
    tree = causal_tree.normalize_future_tree(
        {
            "branches": [
                {
                    "id": "x",
                    "description": "d",
                    "expansion_level": "expanded",
                    "sub_branches": [
                        {"id": "x1", "description": "子分支 1"},
                        {"id": "x2", "description": "子分支 2"},
                        {"id": "no_desc"},  # 非法（无描述），应被过滤
                    ],
                }
            ]
        }
    )
    branch = tree["branches"][0]
    assert branch["expansion_level"] == "expanded"
    assert [sb["id"] for sb in branch["sub_branches"]] == ["x1", "x2"]


def test_apply_tree_updates_status_updates_sets_emerging_and_active():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "a", "description": "d", "status": "dormant"}]},
        }
    ]
    updates = [
        {
            "line_id": "tech",
            "status_updates": [{"branch_id": "a", "status": "emerging"}],
        }
    ]
    updated_lines, audit = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=2)
    assert updated_lines[0]["future_tree"]["branches"][0]["status"] == "emerging"
    assert audit[0]["status_updates"] == [{"branch_id": "a", "status": "emerging"}]


def test_apply_tree_updates_status_updates_accepts_legacy_status_name():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "a", "description": "d", "status": "dormant"}]},
        }
    ]
    updates = [{"line_id": "tech", "status_updates": [{"branch_id": "a", "status": "confirmed"}]}]
    updated_lines, _ = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=2)
    assert updated_lines[0]["future_tree"]["branches"][0]["status"] == "resolved"


def test_apply_tree_updates_status_updates_ignores_unknown_branch_or_status():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "a", "description": "d", "status": "dormant"}]},
        }
    ]
    updates = [
        {
            "line_id": "tech",
            "status_updates": [
                {"branch_id": "not_exist", "status": "active"},
                {"branch_id": "a", "status": "not_a_real_status"},
            ],
        }
    ]
    updated_lines, audit = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=2)
    assert updated_lines[0]["future_tree"]["branches"][0]["status"] == "dormant"
    assert audit == []


def test_apply_tree_updates_expand_branches_sets_expansion_level_and_sub_branches():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "a", "description": "d", "status": "active"}]},
        }
    ]
    updates = [
        {
            "line_id": "tech",
            "expand_branches": [
                {
                    "branch_id": "a",
                    "sub_branches": [
                        {"description": "更细粒度的子分支 1"},
                        {"description": "更细粒度的子分支 2"},
                    ],
                }
            ],
        }
    ]
    updated_lines, audit = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=4)
    branch = updated_lines[0]["future_tree"]["branches"][0]
    assert branch["expansion_level"] == "expanded"
    assert len(branch["sub_branches"]) == 2
    assert audit[0]["expanded_branch_ids"] == ["a"]


def test_apply_tree_updates_expand_branches_without_sub_branches_only_flips_flag():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {
                "branches": [
                    {
                        "id": "a",
                        "description": "d",
                        "status": "active",
                        "sub_branches": [{"id": "existing", "description": "既有子分支"}],
                    }
                ]
            },
        }
    ]
    updates = [{"line_id": "tech", "expand_branches": [{"branch_id": "a"}]}]
    updated_lines, _ = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=4)
    branch = updated_lines[0]["future_tree"]["branches"][0]
    assert branch["expansion_level"] == "expanded"
    assert [sb["id"] for sb in branch["sub_branches"]] == ["existing"]


def test_apply_tree_updates_expand_branches_ignores_unknown_branch():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "a", "description": "d"}]},
        }
    ]
    updates = [{"line_id": "tech", "expand_branches": [{"branch_id": "not_exist"}]}]
    updated_lines, audit = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=1)
    assert audit == []
    assert updated_lines[0]["future_tree"]["branches"][0].get("expansion_level", "compressed") == "compressed"


def test_suggest_status_transitions_flags_stale_dormant_branch():
    line = {
        "id": "tech",
        "label": "技术线",
        "advance_every_n_steps": 2,
        "future_tree": {
            "branches": [
                {"id": "a", "description": "d", "status": "dormant", "first_seen_step": 0},
            ]
        },
    }
    # 阈值 = advance_every_n_steps(2) * stale_multiplier(默认 3) = 6
    suggestions = causal_tree.suggest_status_transitions(line, current_step=6)
    assert len(suggestions) == 1
    assert suggestions[0]["line_id"] == "tech"
    assert suggestions[0]["branch_id"] == "a"
    assert suggestions[0]["current_status"] == "dormant"
    assert suggestions[0]["suggested_status"] == "expired"


def test_suggest_status_transitions_not_yet_stale_returns_empty():
    line = {
        "id": "tech",
        "advance_every_n_steps": 2,
        "future_tree": {
            "branches": [
                {"id": "a", "description": "d", "status": "dormant", "first_seen_step": 0},
            ]
        },
    }
    # 只过了 5 步，阈值是 6，还不够
    assert causal_tree.suggest_status_transitions(line, current_step=5) == []


def test_suggest_status_transitions_skips_branches_without_first_seen_step():
    line = {
        "id": "tech",
        "future_tree": {
            "branches": [
                {"id": "a", "description": "d", "status": "dormant"},
            ]
        },
    }
    assert causal_tree.suggest_status_transitions(line, current_step=100) == []


def test_suggest_status_transitions_ignores_active_and_terminal_statuses():
    line = {
        "id": "tech",
        "future_tree": {
            "branches": [
                {"id": "a", "description": "d", "status": "active", "first_seen_step": 0},
                {"id": "b", "description": "d", "status": "resolved", "first_seen_step": 0},
                {"id": "c", "description": "d", "status": "expired", "first_seen_step": 0},
                {"id": "e", "description": "d", "status": "invalidated", "first_seen_step": 0},
            ]
        },
    }
    assert causal_tree.suggest_status_transitions(line, current_step=999) == []


def test_suggest_status_transitions_defaults_cadence_to_one_when_unset():
    line = {
        "id": "tech",
        "future_tree": {
            "branches": [
                {"id": "a", "description": "d", "status": "emerging", "first_seen_step": 0},
            ]
        },
    }
    # 未声明 advance_every_n_steps 时按 1 计算，阈值 = 1 * 3 = 3
    assert causal_tree.suggest_status_transitions(line, current_step=2) == []
    result = causal_tree.suggest_status_transitions(line, current_step=3)
    assert len(result) == 1 and result[0]["branch_id"] == "a"


def test_apply_tree_updates_new_branch_gets_first_seen_step_stamped():
    causal_lines = [{"id": "tech", "future_tree": {"branches": [{"id": "a", "description": "d"}]}}]
    updates = [
        {
            "line_id": "tech",
            "new_branches": [{"description": "全新分支"}],
        }
    ]
    updated_lines, _ = causal_tree.apply_tree_updates(causal_lines, updates, as_of_step=5)
    branches = updated_lines[0]["future_tree"]["branches"]
    new_branch = [b for b in branches if b["description"] == "全新分支"][0]
    assert new_branch["first_seen_step"] == 5


def test_build_default_future_tree_stamps_first_seen_step():
    tree = causal_tree.build_default_future_tree("主线", as_of_step=3)
    assert all(b["first_seen_step"] == 3 for b in tree["branches"])
