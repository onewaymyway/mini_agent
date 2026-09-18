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
    assert all(b["status"] == "open" for b in branches)


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
    assert by_id["fast"]["status"] == "confirmed"
    assert by_id["slow"]["status"] == "pruned"
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
    assert updated_lines[0]["future_tree"]["branches"][0]["status"] == "open"


def test_set_branch_status_manual_override():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {"branches": [{"id": "fast", "description": "d", "status": "open"}]},
        }
    ]
    updated = causal_tree.set_branch_status(causal_lines, "tech", "fast", "pruned")
    assert updated[0]["future_tree"]["branches"][0]["status"] == "pruned"
    # 非法状态值不生效
    unchanged = causal_tree.set_branch_status(updated, "tech", "fast", "not_a_status")
    assert unchanged[0]["future_tree"]["branches"][0]["status"] == "pruned"
