"""world_simulator/causal_tree.py — 因果线的"未来树"（阶段二十六）

设计依据：`next_doc/world_simulator_causal_line_future_tree_plan.md`。

背景：阶段二十二/二十五落地的因果线（`manifest.settings.causal_lines` +
`SimState.line_updates`）只覆盖"已经发生的历史"，`hypothesis.
project_line_futures()` 给出的"未来"也只是"顺着最近一条因果链继续
延伸"的单路径确定性外推——都不是真正的"分叉树"，也都要求历史已经
存在（`state0` 阶段永远是空的）。本模块补的是：

1. **创建模拟时就必须有核心因果线，且每条线自带一棵初始未来树**
   （`ensure_future_trees()`）——不管 skill 有没有认真输出，
   `materialize_simulation()` 都会调用这个函数兜底，保证"没推进也能
   看到因果线和它的未来分支"。
2. **推进过程中允许修正这棵树**（`apply_tree_updates()`）——`advance_step`
   可选输出 `tree_updates`，对某条线的某个分支标记"已印证/已排除"，
   或者长出一个原来没想到的新分支。
3. **用户也可以在详情页手动裁剪/确认分支**（`set_branch_status()`），
   与 4.14 节"Model Regime"里"系统只发现和展示，人工确认才生效"的
   保守思路一致的另一半：这里反过来是"人工也可以直接动手"，因为
   分支状态判断本身风险很低（不像 `structural_change` 那样会改写
   `vars` schema），不需要额外的"提议→确认"两步。

一棵 `future_tree` 的形状：

```text
{
  "as_of_step": 3,            # 这棵树最近一次更新时对应的 step
  "branches": [
    {
      "id": "steady",
      "description": "延续当前趋势，逐步演化，没有剧烈转折",
      "likelihood": "medium",  # high / medium / low
      "status": "open",        # open / confirmed / diverged / pruned
      "children": []           # 可选，同样是 branch 列表，支持多层
    },
    ...
  ]
}
```

`status` 的语义：`open`（仍然开放，尚未印证也未排除）、`confirmed`
（后续推进的实际走向印证了这个分支）、`diverged`（实际走向明显偏离
了这个分支，但不是主动判断"不可能"，只是"没往这边走"）、`pruned`
（用户或 skill 主动判断这个分支已经不再可能）。不做严格互斥——一棵
树上可以同时有多个 `confirmed` 分支（比如"渐进式"和"局部突变"并不
矛盾），不追求"概率归一化"这种伪精确（同项目里 `confidence: high/
medium/low` 一以贯之的克制风格）。
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional, Tuple

_VALID_STATUS = ("open", "confirmed", "diverged", "pruned")
_VALID_LIKELIHOOD = ("high", "medium", "low")

# 兜底用的默认分支模板：当 skill 没有给出 `future_tree`（或给出的形状
# 不合法）时，用这三条通用维度兜底，保证"每条因果线都有未来树"这个
# 承诺不依赖 LLM 是否配合。这三条刻意写得足够通用（适用于任何模拟
# 主题），不是针对某个模板的专用文案。
_DEFAULT_BRANCH_TEMPLATES: Tuple[Tuple[str, str, str], ...] = (
    ("steady", "延续当前趋势，逐步演化，没有剧烈转折", "medium"),
    ("accelerate", "关键驱动因素超预期强化，发展明显加速或转好", "medium"),
    ("setback", "遇到阻力或意外冲击，发展受挫、放缓甚至逆转", "low"),
)


def _normalize_branch(raw: Any, *, used_ids: set) -> Optional[Dict[str, Any]]:
    """校验/规整单个分支，非法（没有描述）返回 None。`used_ids` 用于
    保证同一棵树内 id 不冲突，冲突时自动加后缀。"""
    if not isinstance(raw, dict):
        return None
    description = str(raw.get("description") or "").strip()
    if not description:
        return None
    branch_id = str(raw.get("id") or "").strip() or f"b_{secrets.token_hex(3)}"
    if branch_id in used_ids:
        branch_id = f"{branch_id}_{secrets.token_hex(2)}"
    used_ids.add(branch_id)
    likelihood = str(raw.get("likelihood") or "medium").strip().lower()
    if likelihood not in _VALID_LIKELIHOOD:
        likelihood = "medium"
    status = str(raw.get("status") or "open").strip().lower()
    if status not in _VALID_STATUS:
        status = "open"
    children_raw = raw.get("children")
    children = []
    if isinstance(children_raw, list):
        for child in children_raw:
            normalized_child = _normalize_branch(child, used_ids=used_ids)
            if normalized_child is not None:
                children.append(normalized_child)
    return {
        "id": branch_id,
        "description": description,
        "likelihood": likelihood,
        "status": status,
        "children": children,
    }


def normalize_future_tree(raw: Any, *, as_of_step: int = 0) -> Optional[Dict[str, Any]]:
    """校验/规整一棵 `future_tree`；形状不合法（没有任何有效分支）时
    返回 `None`，调用方应当退化为 `build_default_future_tree()`。"""
    if not isinstance(raw, dict):
        return None
    used_ids: set = set()
    branches = []
    for item in raw.get("branches") or []:
        normalized = _normalize_branch(item, used_ids=used_ids)
        if normalized is not None:
            branches.append(normalized)
    if not branches:
        return None
    resolved_step = raw.get("as_of_step")
    try:
        resolved_step = int(resolved_step)
    except (TypeError, ValueError):
        resolved_step = as_of_step
    return {"as_of_step": resolved_step, "branches": branches}


def build_default_future_tree(line_label: str, as_of_step: int = 0) -> Dict[str, Any]:
    """用通用兜底模板构造一棵未来树，保证"每条因果线一定有未来树"这个
    承诺不依赖 LLM 输出质量。`line_label` 目前只用于保证多条线各自的
    分支 id 不冲突（`build_default_future_tree` 本身不需要按线定制
    文案——真正贴合具体情境的树应该来自 skill 的输出，这里只是保底）。
    """
    branches = [
        {
            "id": f"{suffix}",
            "description": desc,
            "likelihood": likelihood,
            "status": "open",
            "children": [],
        }
        for suffix, desc, likelihood in _DEFAULT_BRANCH_TEMPLATES
    ]
    return {"as_of_step": as_of_step, "branches": branches}


def _has_valid_tree(line: Dict[str, Any]) -> bool:
    tree = line.get("future_tree")
    return isinstance(tree, dict) and bool(
        [b for b in (tree.get("branches") or []) if isinstance(b, dict) and b.get("description")]
    )


def ensure_future_trees(
    causal_lines: Optional[List[Any]], *, as_of_step: int = 0
) -> List[Dict[str, Any]]:
    """保证传入的因果线列表里，每一条都有一棵合法的 `future_tree`；
    列表本身为空时，兜底生成一条"主线"，保证"创建模拟就有核心因果线"
    这个承诺不依赖用户/skill 是否配合声明。

    这是`materialize_simulation()`落盘 step 0 之前必调用的一步——不管
    调用方是独立看板的创建向导（用户可能压根没打开"高级"折叠区）还是
    CLI/entrypoint 的一步到位创建，都会经过这里，行为完全一致。
    """
    lines = [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    valid_lines = [line for line in lines if str(line.get("id") or "").strip()]
    if not valid_lines:
        valid_lines = [{"id": "main_line", "label": "主线", "time_granularity": ""}]

    result: List[Dict[str, Any]] = []
    for line in valid_lines:
        line = dict(line)
        label = str(line.get("label") or line.get("id") or "").strip() or str(line.get("id"))
        normalized_tree = normalize_future_tree(line.get("future_tree"), as_of_step=as_of_step)
        if normalized_tree is None:
            normalized_tree = build_default_future_tree(label, as_of_step)
        line["future_tree"] = normalized_tree
        result.append(line)
    return result


def auto_register_lines(
    causal_lines: Optional[List[Any]],
    *,
    line_update_ids: List[str],
    causal_link_line_ids: List[str],
    as_of_step: int,
) -> List[Dict[str, Any]]:
    """`engine._auto_register_causal_lines()` 的核心逻辑：把这一步
    `line_updates`/`causal_links.line_id` 里出现的、还没登记过的新
    线 id 补一条声明，且同样带上默认未来树（延续"因果线一定有未来树"
    这个承诺，不因为是"中途自发出现的线"就降级）。"""
    existing = [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    existing_ids = {str(line.get("id")) for line in existing if str(line.get("id") or "").strip()}

    discovered: List[str] = []
    for lid in list(line_update_ids) + list(causal_link_line_ids):
        lid = str(lid).strip()
        if lid and lid not in existing_ids and lid not in discovered:
            discovered.append(lid)

    if not discovered:
        return existing

    new_entries = [
        {
            "id": lid,
            "label": lid,
            "time_granularity": "",
            "auto_discovered": True,
            "future_tree": build_default_future_tree(lid, as_of_step),
        }
        for lid in discovered
    ]
    return existing + new_entries


def apply_tree_updates(
    causal_lines: Optional[List[Any]],
    tree_updates: Optional[List[Any]],
    as_of_step: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把 `advance_step` 可选输出的 `tree_updates` 合并进因果线的
    `future_tree`。不做"自动删除分支"——`pruned`/`diverged` 只是状态
    标记，历史分支始终保留在数据里，供回看"当初还设想过这种可能"。

    Returns:
        `(更新后的 causal_lines, audit)`；`audit` 是这一步实际生效的
        修改摘要列表（落到 `SimState.tree_updates`，供时间线展示/
        审计，不影响任何推进逻辑），没有任何有效更新时两者分别是
        原样透传的列表和空列表。
    """
    lines = [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    updates = [u for u in (tree_updates or []) if isinstance(u, dict)]
    if not updates:
        return lines, []

    by_id = {str(line.get("id")): line for line in lines if str(line.get("id") or "").strip()}
    audit: List[Dict[str, Any]] = []

    for update in updates:
        line_id = str(update.get("line_id") or "").strip()
        if not line_id or line_id not in by_id:
            continue
        line = by_id[line_id]
        tree = normalize_future_tree(line.get("future_tree"), as_of_step=as_of_step)
        if tree is None:
            tree = build_default_future_tree(str(line.get("label") or line_id), as_of_step)
        branches = tree["branches"]
        used_ids = {b["id"] for b in branches}

        confirmed_id = str(update.get("confirmed_branch") or "").strip()
        confirmed_applied = False
        if confirmed_id:
            for b in branches:
                if b["id"] == confirmed_id:
                    b["status"] = "confirmed"
                    confirmed_applied = True
                    break

        pruned_ids = [str(x).strip() for x in (update.get("pruned_branches") or []) if str(x).strip()]
        pruned_applied: List[str] = []
        for pid in pruned_ids:
            for b in branches:
                if b["id"] == pid and b["status"] != "pruned":
                    b["status"] = "pruned"
                    pruned_applied.append(pid)
                    break

        new_branch_ids: List[str] = []
        for raw_branch in update.get("new_branches") or []:
            normalized = _normalize_branch(raw_branch, used_ids=used_ids)
            if normalized is not None:
                branches.append(normalized)
                new_branch_ids.append(normalized["id"])

        if not (confirmed_applied or pruned_applied or new_branch_ids):
            continue

        tree["as_of_step"] = as_of_step
        line["future_tree"] = tree
        audit.append(
            {
                "line_id": line_id,
                "confirmed_branch": confirmed_id if confirmed_applied else "",
                "pruned_branches": pruned_applied,
                "new_branch_ids": new_branch_ids,
            }
        )

    return lines, audit


def set_branch_status(
    causal_lines: Optional[List[Any]], line_id: str, branch_id: str, status: str
) -> List[Dict[str, Any]]:
    """供 `app.py` 详情页"手动确认/排除某个分支"按钮调用的直接写入
    （用户手动操作，不需要走 `apply_tree_updates()` 的"advance_step
    输出→合并"链路）。`status` 必须是 `_VALID_STATUS` 之一，非法值
    原样返回不做修改。"""
    if status not in _VALID_STATUS:
        return [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    lines = [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    for line in lines:
        if str(line.get("id")) != str(line_id):
            continue
        tree = line.get("future_tree")
        if not isinstance(tree, dict):
            continue
        for b in tree.get("branches") or []:
            if isinstance(b, dict) and b.get("id") == branch_id:
                b["status"] = status
                break
    return lines
