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

一棵 `future_tree` 的形状（阶段三十三第四批起，`next_doc/
world_simulator_potential_causal_space_and_decision_engine_plan.md`
4.9/4.11 节，在原有形状基础上扩展，字段全部可选、向后兼容）：

```text
{
  "as_of_step": 3,            # 这棵树最近一次更新时对应的 step
  "branches": [
    {
      "id": "steady",
      "description": "延续当前趋势，逐步演化，没有剧烈转折",
      "likelihood": "medium",  # high / medium / low
      "status": "dormant",     # 见下方"状态生命周期"，6 态之一
      "children": [],          # 可选，同样是 branch 列表，支持多层
      # ↓ 以下六个字段是 4.9 节 KeyNode 形状的新增部分，全部可选：
      "semantic_event": "",        # 这个节点的现实语义描述
      "trigger_conditions": "",    # 一句话触发条件
      "prerequisites": [],         # 前置节点 id 列表（同线内部或跨线）
      "candidate_actions": [],     # 一旦激活，通常对应哪些候选行动方向
      "time_window": "",           # 时间窗口描述，复用 4.3 节的措辞
      "urgency": None,             # low/medium/high/critical，复用 4.3
      # ↓ 以下两个字段是 4.11 节"渐进式展开"的新增部分：
      "expansion_level": "compressed",  # compressed / expanded
      "sub_branches": []                # expanded 时的下一层子分支，形状同 branches
    },
    ...
  ]
}
```

**状态生命周期（阶段三十三第四批，4.9 节，3 态扩展为 6 态）**：
`dormant`（潜伏，默认初始状态）→ `emerging`（正在形成）→ `active`
（已激活）→ `resolved`（已解决）/ `expired`（错过窗口）/
`invalidated`（因世界变化而失效）。不做严格互斥/线性推进的强制
校验——一棵树上可以同时有多个 `resolved` 分支（比如"渐进式"和
"局部突变"并不矛盾），不追求"概率归一化"这种伪精确（同项目里
`confidence: high/medium/low` 一以贯之的克制风格）。

**旧数据兼容**：阶段二十六~三十二产生的历史数据用的是旧的 4 态
`open`/`confirmed`/`diverged`/`pruned`，`canonical_status()` 会把
它们分别映射到 `dormant`/`resolved`/`expired`/`invalidated`——
`diverged`（"实际走向明显偏离了这个分支，但不是主动判断不可能"）
映射到 `expired`（"错过窗口"）是这批新增的对应关系，语义上最接近：
两者都表示"没有沿着这个分支走，但不是被主动排除"。历史数据不需要
批量迁移，读取（`_normalize_branch()`）时统一转换即可，展示层看到
的永远是新 6 态之一。
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional, Tuple

_VALID_STATUS: Tuple[str, ...] = ("dormant", "emerging", "active", "resolved", "expired", "invalidated")
_LEGACY_STATUS_MAP: Dict[str, str] = {
    "open": "dormant",
    "confirmed": "resolved",
    "diverged": "expired",
    "pruned": "invalidated",
}
_ACCEPTED_STATUS_INPUTS = set(_VALID_STATUS) | set(_LEGACY_STATUS_MAP.keys())
_VALID_LIKELIHOOD = ("high", "medium", "low")
_VALID_URGENCY = ("low", "medium", "high", "critical")
_VALID_EXPANSION_LEVEL = ("compressed", "expanded")


def canonical_status(raw: Any) -> str:
    """把任意历史（旧 4 态）或当前（新 6 态）状态值统一映射到 6 态
    之一（阶段三十三第四批，4.9 节）。不认识的取值（既不是新 6 态、
    也不是旧 4 态里的任何一个）统一归一化为 `dormant`——延续
    `risk_level`/`urgency` 那类"不认识就退到最保守/最中性档"的一贯
    风格，而不是原样展示一个奇怪的标签。
    """
    value = str(raw or "").strip().lower()
    if value in _VALID_STATUS:
        return value
    return _LEGACY_STATUS_MAP.get(value, "dormant")

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
    """校验/规整单个分支（阶段三十三第四批起，形状同时覆盖 4.9 节
    KeyNode 字段和 4.11 节展开标记），非法（没有描述）返回 None。
    `used_ids` 用于保证同一棵树内 id 不冲突，冲突时自动加后缀；
    `sub_branches` 递归调用本函数，与 `used_ids` 共享同一个去重集合
    （子分支和同层分支不应该撞 id，简化展示层引用）。"""
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
    status = canonical_status(raw.get("status"))
    children_raw = raw.get("children")
    children = []
    if isinstance(children_raw, list):
        for child in children_raw:
            normalized_child = _normalize_branch(child, used_ids=used_ids)
            if normalized_child is not None:
                children.append(normalized_child)

    # 4.9 节 KeyNode 字段，全部可选，未声明时给"空/未知"而不是伪造值。
    semantic_event = str(raw.get("semantic_event") or "").strip()
    trigger_conditions = str(raw.get("trigger_conditions") or "").strip()
    prerequisites = [str(x).strip() for x in (raw.get("prerequisites") or []) if str(x).strip()]
    candidate_actions = [str(x).strip() for x in (raw.get("candidate_actions") or []) if str(x).strip()]
    time_window = str(raw.get("time_window") or "").strip()
    urgency = raw.get("urgency")
    if urgency is not None:
        urgency = str(urgency).strip().lower()
        if urgency not in _VALID_URGENCY:
            urgency = "medium"

    # 4.11 节渐进式展开：`expansion_level` 未声明或不认识的取值一律
    # 归一化为 `compressed`（"没有再展开"是更保守的默认状态）；
    # `sub_branches` 形状同 `branches`，递归规整。
    expansion_level = str(raw.get("expansion_level") or "compressed").strip().lower()
    if expansion_level not in _VALID_EXPANSION_LEVEL:
        expansion_level = "compressed"
    sub_branches_raw = raw.get("sub_branches")
    sub_branches: List[Dict[str, Any]] = []
    if isinstance(sub_branches_raw, list):
        for sub in sub_branches_raw:
            normalized_sub = _normalize_branch(sub, used_ids=used_ids)
            if normalized_sub is not None:
                sub_branches.append(normalized_sub)

    return {
        "id": branch_id,
        "description": description,
        "likelihood": likelihood,
        "status": status,
        "children": children,
        "semantic_event": semantic_event,
        "trigger_conditions": trigger_conditions,
        "prerequisites": prerequisites,
        "candidate_actions": candidate_actions,
        "time_window": time_window,
        "urgency": urgency,
        "expansion_level": expansion_level,
        "sub_branches": sub_branches,
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
            "status": "dormant",
            "children": [],
            "semantic_event": "",
            "trigger_conditions": "",
            "prerequisites": [],
            "candidate_actions": [],
            "time_window": "",
            "urgency": None,
            "expansion_level": "compressed",
            "sub_branches": [],
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
    `future_tree`。不做"自动删除分支"——分支的各种状态只是标记，
    历史分支始终保留在数据里，供回看"当初还设想过这种可能"。

    每一项 `tree_updates` 支持以下可选 key（全部可选，按需组合）：
    - `confirmed_branch`（沿用阶段二十六既有语义）：这一步印证了
      哪个分支——落盘为 `status = "resolved"`（阶段三十三第四批，
      4.9 节 6 态生命周期，旧名 `confirmed` 现在是 `resolved` 的
      别名，见 `canonical_status()`）。
    - `pruned_branches`（沿用既有语义）：这一步排除了哪些分支——
      落盘为 `status = "invalidated"`（旧名 `pruned` 的新对应）。
    - `new_branches`（沿用既有语义）：新增分支，形状同 `branches`，
      现在也支持 4.9/4.11 节的全部新字段（`semantic_event`/
      `urgency`/`expansion_level`/`sub_branches` 等），`_normalize_
      branch()` 会一并规整。
    - `status_updates`（阶段三十三第四批新增，4.9 节"激活判定"）：
      数组，每项 `{"branch_id": ..., "status": ...}`——用于声明
      `confirmed_branch`/`pruned_branches` 覆盖不到的另外四态
      （尤其是 `emerging`/`active`，"正在形成"/"已激活"这两个状态
      没有对应的旧字段可以复用）。`status` 接受新 6 态或旧 4 态
      写法，统一按 `canonical_status()` 落盘；引用不存在的
      `branch_id` 或给出无法识别的 `status` 时，这一项被忽略，不
      报错、不影响其它项。
    - `expand_branches`（阶段三十三第四批新增，4.11 节"渐进式
      展开"）：数组，每项 `{"branch_id": ..., "sub_branches": [...]}`
      ——把某个已有分支标记为 `expansion_level: "expanded"`，并
      （如果给出了 `sub_branches`）用给出的内容整体替换这个分支的
      子分支列表；只给 `branch_id` 不给 `sub_branches` 时，只切换
      展开标记，保留分支原有的子分支不变。引用不存在的 `branch_id`
      时这一项被忽略。

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
                    b["status"] = "resolved"
                    confirmed_applied = True
                    break

        pruned_ids = [str(x).strip() for x in (update.get("pruned_branches") or []) if str(x).strip()]
        pruned_applied: List[str] = []
        for pid in pruned_ids:
            for b in branches:
                if b["id"] == pid and b["status"] != "invalidated":
                    b["status"] = "invalidated"
                    pruned_applied.append(pid)
                    break

        new_branch_ids: List[str] = []
        for raw_branch in update.get("new_branches") or []:
            normalized = _normalize_branch(raw_branch, used_ids=used_ids)
            if normalized is not None:
                branches.append(normalized)
                new_branch_ids.append(normalized["id"])

        # 4.9 节：任意 6 态的显式声明（主要用于 confirmed_branch/
        # pruned_branches 覆盖不到的 emerging/active）。
        status_updates_applied: List[Dict[str, str]] = []
        for raw_su in update.get("status_updates") or []:
            if not isinstance(raw_su, dict):
                continue
            target_id = str(raw_su.get("branch_id") or "").strip()
            raw_status = str(raw_su.get("status") or "").strip().lower()
            if not target_id or raw_status not in _ACCEPTED_STATUS_INPUTS:
                continue
            resolved_status = _LEGACY_STATUS_MAP.get(raw_status, raw_status)
            for b in branches:
                if b["id"] == target_id:
                    b["status"] = resolved_status
                    status_updates_applied.append({"branch_id": target_id, "status": resolved_status})
                    break

        # 4.11 节：把某个已有分支标记为已展开，并（可选）写入子分支。
        expanded_branch_ids: List[str] = []
        for raw_eb in update.get("expand_branches") or []:
            if not isinstance(raw_eb, dict):
                continue
            target_id = str(raw_eb.get("branch_id") or "").strip()
            if not target_id:
                continue
            for b in branches:
                if b["id"] != target_id:
                    continue
                b["expansion_level"] = "expanded"
                raw_subs = raw_eb.get("sub_branches")
                if isinstance(raw_subs, list) and raw_subs:
                    sub_used_ids = set(used_ids)
                    normalized_subs = []
                    for raw_sub in raw_subs:
                        normalized_sub = _normalize_branch(raw_sub, used_ids=sub_used_ids)
                        if normalized_sub is not None:
                            normalized_subs.append(normalized_sub)
                    if normalized_subs:
                        b["sub_branches"] = normalized_subs
                expanded_branch_ids.append(target_id)
                break

        if not (
            confirmed_applied
            or pruned_applied
            or new_branch_ids
            or status_updates_applied
            or expanded_branch_ids
        ):
            continue

        tree["as_of_step"] = as_of_step
        line["future_tree"] = tree
        audit.append(
            {
                "line_id": line_id,
                "confirmed_branch": confirmed_id if confirmed_applied else "",
                "pruned_branches": pruned_applied,
                "new_branch_ids": new_branch_ids,
                "status_updates": status_updates_applied,
                "expanded_branch_ids": expanded_branch_ids,
            }
        )

    return lines, audit


def set_branch_status(
    causal_lines: Optional[List[Any]], line_id: str, branch_id: str, status: str
) -> List[Dict[str, Any]]:
    """供 `app.py` 详情页"手动确认/排除某个分支"按钮调用的直接写入
    （用户手动操作，不需要走 `apply_tree_updates()` 的"advance_step
    输出→合并"链路）。`status` 接受新 6 态（阶段三十三第四批，4.9
    节）或旧 4 态写法（统一按 `canonical_status()` 落盘），非法值
    原样返回不做修改。"""
    key = str(status or "").strip().lower()
    if key not in _ACCEPTED_STATUS_INPUTS:
        return [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    resolved_status = _LEGACY_STATUS_MAP.get(key, key)
    lines = [dict(x) for x in (causal_lines or []) if isinstance(x, dict)]
    for line in lines:
        if str(line.get("id")) != str(line_id):
            continue
        tree = line.get("future_tree")
        if not isinstance(tree, dict):
            continue
        for b in tree.get("branches") or []:
            if isinstance(b, dict) and b.get("id") == branch_id:
                b["status"] = resolved_status
                break
    return lines
