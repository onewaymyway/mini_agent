"""world_simulator/element_tiers.py — 元素的派生分级与 prompt 预算（第二十三轮 E4）。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.6、§5.1、§6 E4。

## 这个模块做什么

元素会越来越多，不能每步把全部元素的全部分支都拼进 prompt。这里把元素分成三档，**只决定"本步
prompt 里展示多详细"，不决定"能不能更新"**：

- `active`：完整信息（类型/领域/节奏/生命周期/`var_refs` 当前值/**未终态分支**，终态分支只给计数）；
- `watch`：一行摘要；
- `dormant`：不展开，只留一份 id+label 的精简索引（数量有上限），目的是让 LLM 优先复用已有 id、
  不重复"发现"。

## 分级由历史派生，不存储

唯一存储的分级信息是用户的 `tier_pin`（只有 `active`）。其余全部由 `(settings, history, step)`
现算，所以：天然按分支正确（两个分支历史不同 → 分级不同）；分级变化不会让 `causal_lines` 快照
反复重写。

## 规则（`derive_tiers`）

active（任一成立）：`tier_pin=active`；用户在这条线上留有修改意见（`user_feedback`，不能因为休眠就把
用户的话从 prompt 里拿掉）；最近 `active_window_steps`（按线自身 `advance_every_n_steps` 缩放）步内有
`line_updates`/`tree_updates`/技术阶段迁移，或刚登记（`born_step`）；有分支处于 `emerging`/`active`；
有到期的待兑现因果指向它；本步被外生事件 `affects` 命中（领域 id 展开为其下 alive 元素）；被上述
"基础 active"元素经因果边**一跳**触发。
watch：不满足 active，但在 `watch_window_steps`（同样按线缩放）内有过动静。
dormant：其余。

active 数超过 `max_active_in_prompt` 时按（有到期压力 > 有活跃分支 > 有用户意见 > 最近进展 > 边入度）
排序，其余降为 watch；`null` = 不限。`tier_pin=active` 的元素**不受预算裁剪**（用户明确固定的不替他丢掉）。

领域线不参与分级（数量少、只做分组，始终完整展示）；`retired`/`merged` 的元素不进 prompt
（历史与树保留，LLM 引用它们的 id 时引擎照常解析，见"不拦截"）。

## 不拦截

LLM 对任何分级的元素输出 `line_updates`/`tree_updates` 都照常接受；被更新的休眠元素下一步自动升为
active（因为"最近有进展"是 active 条件之一）。

纯 Python，不调 LLM，不读写磁盘。依赖 `element_registry`；对 `causal_engine` 只做函数内延迟导入
（它反过来依赖 `element_registry`，避免循环）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from world_simulator import element_registry as er

ACTIVE, WATCH, DORMANT = "active", "watch", "dormant"
TIERS = (ACTIVE, WATCH, DORMANT)

_TERMINAL_STATUSES = ("resolved", "expired", "invalidated")
_LIVE_BRANCH_STATUSES = ("emerging", "active")

# 分级参数（写在 `settings.element_params` 里，与创建期/运行期参数同一个字典）。
TIER_DEFAULT_PARAMS: Dict[str, Optional[int]] = {
    "max_active_in_prompt": 12,   # 同时以完整信息进 prompt 的元素数；None = 不限
    "active_window_steps": 3,     # >= 1
    "watch_window_steps": 10,     # >= 1；小于 active_window 时按 active_window 处理
    "dormant_index_max": 60,      # 休眠索引条数上限，>= 0（0 = 不列索引）
}


def _valid_at_least(value: Any, minimum: int) -> Tuple[bool, Optional[int]]:
    return er._valid_int_at_least(value, minimum)


_VALIDATORS = {
    "max_active_in_prompt": er._valid_limit,
    "active_window_steps": lambda v: _valid_at_least(v, 1),
    "watch_window_steps": lambda v: _valid_at_least(v, 1),
    "dormant_index_max": lambda v: _valid_at_least(v, 0),
}


def get_tier_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """`TIER_DEFAULT_PARAMS` 叠加 `settings.element_params`；非法值回退默认。`null` 只对
    `max_active_in_prompt` 合法（= 不限）。"""
    params = dict(TIER_DEFAULT_PARAMS)
    override = (settings or {}).get("element_params")
    if isinstance(override, dict):
        for key, check in _VALIDATORS.items():
            if key in override:
                ok, val = check(override[key])
                if ok:
                    params[key] = val
    return params


def invalid_tier_param_keys(settings: Optional[Dict[str, Any]]) -> List[str]:
    """`element_params` 里分级参数取值非法（已回退默认）的 key，给设置页提示。"""
    override = (settings or {}).get("element_params")
    if not isinstance(override, dict):
        return []
    return [k for k, check in _VALIDATORS.items() if k in override and not check(override[k])[0]]


# ── 派生分级 ─────────────────────────────────────────────────────────


def _every_n(line: Dict[str, Any]) -> int:
    raw = line.get("advance_every_n_steps")
    try:
        n = int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        n = 1
    return max(1, n)


def _branches(line: Dict[str, Any]) -> List[Dict[str, Any]]:
    tree = line.get("future_tree")
    return [b for b in ((tree or {}).get("branches") or []) if isinstance(b, dict)] if isinstance(tree, dict) else []


def _has_live_branch(line: Dict[str, Any]) -> bool:
    return any(str(b.get("status") or "dormant") in _LIVE_BRANCH_STATUSES for b in _branches(line))


def _born(line: Dict[str, Any]) -> int:
    born = er._int_or_none(line.get("born_step"))
    return 0 if born is None else born


class _Index:
    """一次性建好的"引用 → 线 id"索引，语义与 `element_registry.resolve()` 一致
    （精确 id > 规范化 id > 规范化 label/别名，先登记者优先），但不用每次线性扫描。"""

    def __init__(self, lines: List[Dict[str, Any]]):
        self.exact: Dict[str, str] = {}
        self.norm_id: Dict[str, str] = {}
        self.norm_name: Dict[str, str] = {}
        # 第二十三轮 E5：被合并的元素，历史里的旧 id/名称落到合并目标上（历史不可变，只在读取时改写）
        target = {
            str(line["id"]).strip(): str(er.follow_merge(lines, line)["id"]).strip() for line in lines
        }
        for line in lines:
            lid = str(line["id"]).strip()
            self.exact.setdefault(lid, target[lid])
            key = er.norm_key(lid)
            if key:
                self.norm_id.setdefault(key, target[lid])
        for line in lines:
            lid = str(line["id"]).strip()
            for name in [line.get("label")] + list(line.get("aliases") or []):
                if name is None or not str(name).strip():
                    continue
                key = er.norm_key(name)
                if key:
                    self.norm_name.setdefault(key, target[lid])

    def get(self, ref: Any) -> Optional[str]:
        text = str(ref if ref is not None else "").strip()
        if not text:
            return None
        if text in self.exact:
            return self.exact[text]
        key = er.norm_key(text)
        if not key:
            return None
        return self.norm_id.get(key) or self.norm_name.get(key)


def _step_of(state: Any) -> Optional[int]:
    step = getattr(state, "step", None)
    return step if isinstance(step, int) and not isinstance(step, bool) else None


def _progress_steps(history: Iterable[Any], index: _Index, oldest: int) -> Dict[str, int]:
    """每个线 id 最近一次"有进展"的 step（只扫 `step >= oldest` 的历史）。进展 = `line_updates`
    出现该线、`tree_updates` 动了该线、技术阶段迁移/倒退。历史里的旧 id/别名经索引解析到当前 id。"""
    last: Dict[str, int] = {}

    def mark(ref: Any, step: int) -> None:
        lid = index.get(ref)
        if lid is not None and step > last.get(lid, -1):
            last[lid] = step

    for state in history or []:
        step = _step_of(state)
        if step is None or step < oldest:
            continue
        for key in (getattr(state, "line_updates", None) or {}):
            mark(key, step)
        for item in getattr(state, "tree_updates", None) or []:
            if isinstance(item, dict):
                mark(item.get("line_id"), step)
        for item in getattr(state, "tech_updates", None) or []:
            if isinstance(item, dict) and item.get("action") in ("transition", "regression"):
                mark(item.get("tech_id"), step)
    return last


def _due_targets(settings: Dict[str, Any], history: Any, step: int, index: _Index) -> set:
    """有到期待兑现因果指向的线 id。因果引擎未开启/没有待兑现项时为空；任何异常按空处理（分级是旁路信息）。"""
    pending = settings.get("causal_pending")
    if not pending:
        return set()
    try:
        from world_simulator import causal_engine

        if not causal_engine.is_enabled(settings):
            return set()
        out = set()
        for item in causal_engine.due_entries(pending, list(history or []), step):
            lid = index.get(item.get("to_line_id"))
            if lid is not None:
                out.add(lid)
        return out
    except Exception:  # noqa: BLE001
        return set()


def _edges(settings: Dict[str, Any]) -> List[Tuple[str, str]]:
    """启用的因果边 `(from, to)` 原始端点（未解析）。因果引擎未开启时也读：分级只关心"谁影响谁"，
    不依赖兑现机制。"""
    try:
        from world_simulator import causal_engine

        edges, _ = causal_engine.get_edges(settings)
    except Exception:  # noqa: BLE001
        return []
    return [(e["from_line_id"], e["to_line_id"]) for e in edges if e.get("enabled", True)]


def expand_hits(lines: List[Dict[str, Any]], refs: Iterable[Any], index: Optional[_Index] = None) -> set:
    """把一批引用（元素 id/别名/领域 id）解析成"被命中的 alive 元素 id"；领域 id 展开为其下 alive 元素。"""
    index = index or _Index(lines)
    by_id = {str(x["id"]).strip(): x for x in lines}
    hit: set = set()
    for ref in refs or []:
        lid = index.get(ref)
        line = by_id.get(lid) if lid is not None else None
        if line is None:
            continue
        if line.get("kind") == "domain":
            hit.update(
                str(c["id"]).strip() for c in er.children_of(lines, lid) if er.is_alive(c)
            )
        elif er.is_alive(line):
            hit.add(lid)
    return hit


def tiered_ids(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """参与分级的线：alive 且不是领域线。"""
    return [x for x in lines if er.is_alive(x) and x.get("kind") != "domain"]


def derive_tiers(
    history: Optional[Iterable[Any]],
    settings: Optional[Dict[str, Any]],
    *,
    step: int,
    hit_refs: Iterable[Any] = (),
) -> Dict[str, Any]:
    """按规则派生每个（alive、非领域）元素在 `step`（正在生成的这一步）的分级。

    返回 `{"tiers": {id: tier}, "reasons": {id: [原因...]}, "age": {id: 距上次动静的步数},
    "rank": [按重要性降序的 active id], "demoted": [被预算降为 watch 的 id], "params": {...}}`。
    `history` 是推进前的该分支历史（按 step 升序）；`hit_refs` 是本步已知会命中的引用（外生事件的
    `affects`）。不改入参。
    """
    settings = settings or {}
    params = get_tier_params(settings)
    active_w = int(params["active_window_steps"] or 1)
    watch_w = max(active_w, int(params["watch_window_steps"] or 1))
    all_lines = er.get_lines(settings)
    lines = tiered_ids(all_lines)
    index = _Index(all_lines)
    history = list(history or [])

    max_n = max([_every_n(x) for x in lines] or [1])
    progress = _progress_steps(history, index, oldest=step - watch_w * max_n - 1)
    due = _due_targets(settings, history, step, index)
    hits = expand_hits(all_lines, hit_refs, index)

    edges = _edges(settings)
    indeg: Dict[str, int] = {}
    resolved_edges: List[Tuple[str, str]] = []
    for src, dst in edges:
        a, b = index.get(src), index.get(dst)
        if a is None or b is None:
            continue
        resolved_edges.append((a, b))
        indeg[b] = indeg.get(b, 0) + 1

    ids = [str(x["id"]).strip() for x in lines]
    by_id = {str(x["id"]).strip(): x for x in lines}
    age: Dict[str, int] = {}
    reasons: Dict[str, List[str]] = {}
    base_active: set = set()
    for lid in ids:
        line = by_id[lid]
        n = _every_n(line)
        last = max(progress.get(lid, -1), _born(line))
        age[lid] = max(0, step - last)
        why: List[str] = []
        if line.get("tier_pin") == "active":
            why.append("用户固定")
        if str(line.get("user_feedback") or "").strip():
            why.append("用户留有修改意见")
        if age[lid] <= active_w * n:
            why.append("近期有进展" if progress.get(lid, -1) >= _born(line) and lid in progress else "刚登记")
        if _has_live_branch(line):
            why.append("有活跃分支")
        if lid in due:
            why.append("有到期的待兑现因果")
        if lid in hits:
            why.append("被本步事件命中")
        if why:
            base_active.add(lid)
            reasons[lid] = why
    for a, b in resolved_edges:   # 一跳：被基础 active 元素经因果边触发
        if a in base_active and b in by_id and b not in base_active and b not in reasons:
            reasons[b] = ["被活跃元素经因果边触发"]
    active = set(base_active) | {k for k, v in reasons.items() if v}

    def sort_key(lid: str) -> Tuple:
        why = reasons.get(lid, [])
        return (
            1 if "有到期的待兑现因果" in why else 0,
            1 if "有活跃分支" in why else 0,
            1 if "用户留有修改意见" in why else 0,
            -age[lid],
            indeg.get(lid, 0),
        )

    pinned = [lid for lid in ids if lid in active and by_id[lid].get("tier_pin") == "active"]
    rest = sorted((lid for lid in ids if lid in active and lid not in pinned), key=sort_key, reverse=True)
    demoted: List[str] = []
    limit = params["max_active_in_prompt"]
    if limit is not None and len(rest) > limit:
        # 钉住的元素不受预算裁剪，但仍占用名额：剩余名额给其余 active。
        room = max(0, int(limit) - len(pinned))
        demoted = rest[room:]
        rest = rest[:room]
    rank = pinned + rest

    tiers: Dict[str, str] = {}
    for lid in ids:
        if lid in rank:
            tiers[lid] = ACTIVE
        elif lid in demoted:
            tiers[lid] = WATCH
            reasons[lid] = reasons.get(lid, []) + ["超出 active 预算，降为 watch"]
        elif age[lid] <= watch_w * _every_n(by_id[lid]):
            tiers[lid] = WATCH
        else:
            tiers[lid] = DORMANT
    return {
        "tiers": tiers,
        "reasons": reasons,
        "age": age,
        "rank": rank,
        "demoted": demoted,
        "params": params,
    }


def counts(result: Dict[str, Any]) -> Dict[str, int]:
    """`derive_tiers()` 结果里三档各有多少个。"""
    out = {ACTIVE: 0, WATCH: 0, DORMANT: 0}
    for tier in (result.get("tiers") or {}).values():
        out[tier] = out.get(tier, 0) + 1
    return out


# ── prompt 展示 ──────────────────────────────────────────────────────


def _dig(vars_: Any, path: str) -> Tuple[bool, Any]:
    """按点号路径取 `vars` 里的值，路径可带前导 `vars.`；列表下标用数字。取不到返回 (False, None)。"""
    parts = [p for p in str(path).strip().split(".") if p != ""]
    if parts and parts[0] == "vars":
        parts = parts[1:]
    cur = vars_
    if not parts:
        return False, None
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def var_refs_text(line: Dict[str, Any], current_vars: Any, *, limit: int = 3, width: int = 40) -> str:
    """`var_refs` 当前值的一句话（取不到的路径跳过；最多 `limit` 条；值过长截断）。无可展示内容返回空串。"""
    if not isinstance(current_vars, dict):
        return ""
    pieces: List[str] = []
    for path in line.get("var_refs") or []:
        ok, value = _dig(current_vars, path)
        if not ok:
            continue
        text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        if len(text) > width:
            text = text[: width - 1] + "…"
        pieces.append(f"{path}={text}")
        if len(pieces) >= limit:
            break
    return "；".join(pieces)


def _lifecycle_text(line: Dict[str, Any]) -> str:
    life = line.get("lifecycle")
    if not isinstance(life, dict):
        return ""
    stage = str(life.get("stage") or "").strip()
    if not stage:
        return ""
    progress = life.get("progress")
    if isinstance(progress, (int, float)) and not isinstance(progress, bool):
        return f"{stage}（进度 {progress:g}）"
    return stage


def active_piece(line: Dict[str, Any], current_vars: Any = None) -> str:
    """active 元素/领域线在"因果线设置"里的完整一项。"""
    lid = str(line["id"]).strip()
    label = str(line.get("label") or lid).strip()
    gran = str(line.get("time_granularity") or "").strip()
    feedback = str(line.get("user_feedback") or "").strip()
    piece = f"{lid}（{label}"
    etype = str(line.get("element_type") or "").strip()
    if line.get("kind") == "domain":
        piece += "，领域线"
    else:
        if etype:
            piece += f"，类型：{etype}"
        parent = str(line.get("parent") or "").strip()
        if parent:
            piece += f"，领域：{parent}"
    life = _lifecycle_text(line)
    if life:
        piece += f"，发展阶段：{life}"
    if gran:
        piece += f"，节奏参考：{gran}"
    refs = var_refs_text(line, current_vars)
    if refs:
        piece += f"，对应状态：{refs}"
    if feedback:
        piece += f"，用户对这条线的修改意见：{feedback}"
    return piece + "）"


def watch_piece(line: Dict[str, Any], age: int) -> str:
    lid = str(line["id"]).strip()
    label = str(line.get("label") or lid).strip()
    extra = []
    etype = str(line.get("element_type") or "").strip()
    if etype:
        extra.append(etype)
    parent = str(line.get("parent") or "").strip()
    if parent:
        extra.append(f"领域 {parent}")
    extra.append("本步刚登记" if age == 0 else f"{age} 步前有动静")
    return f"{lid}（{label}；{'，'.join(extra)}）"


def branch_text(line: Dict[str, Any]) -> Tuple[str, int]:
    """`(未终态分支的文字, 终态分支数)`。active 元素的树只展示未终态分支，终态只给计数。"""
    live, done = [], 0
    for b in _branches(line):
        if str(b.get("status") or "dormant") in _TERMINAL_STATUSES:
            done += 1
        else:
            live.append(f'{b.get("id")}[{b.get("status", "dormant")}]：{b.get("description", "")}')
    return "；".join(live), done


def dormant_view(
    lines: List[Dict[str, Any]], result: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], int]:
    """`(要列进休眠索引的线, 因上限被省略的数量)`。按最近动静（age 小者在前）截断，同龄按登记顺序。"""
    cap = int(result["params"]["dormant_index_max"] or 0)
    dorm = [x for x in tiered_ids(lines) if result["tiers"].get(str(x["id"]).strip()) == DORMANT]
    order = {str(x["id"]).strip(): i for i, x in enumerate(dorm)}
    dorm.sort(key=lambda x: (result["age"].get(str(x["id"]).strip(), 0), order[str(x["id"]).strip()]))
    return dorm[:cap], max(0, len(dorm) - cap)


def view(
    settings: Optional[Dict[str, Any]],
    history: Optional[Iterable[Any]],
    *,
    step: int,
    hit_refs: Iterable[Any] = (),
) -> Dict[str, Any]:
    """把 `settings.causal_lines` 切成 prompt 要用的几块：`domains`（始终完整）、`active`（按重要性降序）、
    `watch`、`dormant_shown`/`dormant_hidden`，以及原始分级结果 `result`。`retired`/`merged` 不在其中。"""
    lines = er.get_lines(settings)
    history = list(history or [])
    result = derive_tiers(history, settings, step=step, hit_refs=hit_refs)
    by_id = {str(x["id"]).strip(): x for x in lines}
    domains_ = [x for x in lines if x.get("kind") == "domain" and er.is_alive(x)]
    active = [by_id[i] for i in result["rank"]]
    watch = sorted(
        (by_id[i] for i, t in result["tiers"].items() if t == WATCH),
        key=lambda x: result["age"].get(str(x["id"]).strip(), 0),
    )
    if sum(1 for t in result["tiers"].values() if t == DORMANT) > int(result["params"]["dormant_index_max"] or 0):
        # 休眠索引要截断时，"最近动静"才有意义：休眠元素的最后一次进展早已超出分级用的有限扫描窗口，
        # 这里（且只在需要截断时）再扫一遍完整历史，让索引优先保留最近动过的。
        index = _Index(lines)
        full = _progress_steps(history or [], index, oldest=-1)
        for lid, tier in result["tiers"].items():
            if tier == DORMANT:
                result["age"][lid] = max(0, step - max(full.get(lid, -1), _born(by_id[lid])))
    shown, hidden = dormant_view(lines, result)
    return {
        "domains": domains_, "active": active, "watch": watch,
        "dormant_shown": shown, "dormant_hidden": hidden, "result": result,
    }
