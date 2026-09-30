"""world_simulator/dynamic_state.py — 分支作用域动态状态（第二十二轮 WP0）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§2.4（缺陷）/§4 WP0（方案）。

## 要解决的问题

因果树（`settings.causal_lines`）和待兑现关系（`settings.relationship_
pending_effects`）是"随时间线变化"的状态，却存在整个实例共享的
`manifest.settings` 里：分叉后在新分支上推进会改写这份状态，切回主线
看到的是被"另一条时间线"改过的树。

## 方案（三条规则）

1. **快照随步落盘**：某一步推进后，如果这类状态相对"该分支上一份
   快照"有变化，就把完整快照写进该步的 `SimState.dynamic_snapshot`；
   读取时向前回溯最近一份（`latest_snapshot`）。`fork_branch` 复制历史
   时快照跟着走 → 新分支天然拿到"分叉那一刻的树"（回滚语义）。
2. **`manifest.settings` = 当前活跃分支的工作副本**：既有读取方（界面、
   HTML 导出、prompt 构造共十几处）继续读 `manifest.settings`，一处都
   不用改。工作副本在两个时点与分支对齐：
   - 离开分支（`switch_branch` / `fork_branch(switch=True)`）时，先把
     工作副本"提交"到该分支头部（`commit_working_copy`）——覆盖旧实例
     （头部还没有快照）和用户在设置面板里手改因果线（只改了工作副本）
     两种情况；若工作副本与头部快照一致则什么都不写。
   - 进入分支时，用该分支最近一份快照刷新工作副本（`enter_branch`）；
     该分支没有任何快照（旧实例、且从未被离开过）则不动工作副本——与
     今天的行为完全一致。
3. **不做数据迁移**：旧实例历史里的旧步不会被批量改写。唯一的写入是
   "离开分支时把当时的工作副本提交到该分支头部一份快照"，它属于新写入
   的一次提交，不是回填。

## 已知边界（如实记录）

- 旧实例 + 从非活跃分支分叉、且源分支没有任何快照：源分支当时的树
  无从得知，新分支不带快照，沿用工作副本（等同旧行为，不算回退）。
- 旧实例在分叉点之前的历史步没有快照，从更早的步分叉时，回滚到的是
  "源分支离开时的工作副本"而不是当时真实的树（旧数据里本来就没有
  这个信息）。
- 快照是完整拷贝而非增量：只在有变化的步写，`causal_lines` 较大时
  历史文件会相应变大；这是为"任意步分叉都能拿到一致的树"付出的代价。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, atomic_write_json, atomic_write_jsonl

# 纳入分支作用域的 `manifest.settings` key。后续工作包新增的动态状态
# （WP1 `tech_state`、WP3 `causal_pending` 等）在这里追加即可，不需要
# 改其它地方。
DYNAMIC_KEYS = ("causal_lines", "relationship_pending_effects")


def _normalize(value: Any) -> Any:
    """深拷贝并归一成 JSON 兼容形态（与落盘再读回的形态一致），保证
    "工作副本 vs 已落盘快照"的相等比较不会因为 tuple/list 之类的
    类型差异误判为"有变化"。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def extract(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 `manifest.settings` 取出当前的动态状态（深拷贝）。空值（`None`/
    空列表/空字典）视同"不存在"——`auto_register_lines()` 会把缺省的
    `causal_lines` 规整成 `[]`，这不算状态变化，不该产生快照。"""
    settings = settings or {}
    return {
        key: _normalize(settings[key])
        for key in DYNAMIC_KEYS
        if key in settings and settings[key]
    }


def latest_snapshot(history: List[SimState]) -> Optional[Dict[str, Any]]:
    """向前回溯 `history`（按 step 升序）里最近一份快照；没有返回 None
    （旧数据/从未变化）。"""
    for state in reversed(history or []):
        snap = getattr(state, "dynamic_snapshot", None)
        if isinstance(snap, dict):
            return snap
    return None


def snapshot_if_changed(
    settings: Optional[Dict[str, Any]], history: List[SimState]
) -> Optional[Dict[str, Any]]:
    """`advance()` 落盘新一步之前调用：工作副本相对该分支最近一份快照有
    变化就返回要写进新一步的快照，否则返回 None（不重复写）。

    `history` 是该分支推进*之前*的历史（含当前步）。没有任何快照时，
    以"空"为基线——旧实例第一次推进就会写下第一份快照（隔离从新写入
    的步开始），而完全没有动态状态的实例不产生任何快照。
    """
    current = extract(settings)
    baseline = latest_snapshot(history) or {}
    if current == baseline:
        return None
    return current


def apply_to_settings(
    settings: Optional[Dict[str, Any]], snapshot: Dict[str, Any]
) -> Dict[str, Any]:
    """用快照刷新 settings 里的动态 key，返回新字典（不就地改）。快照里
    没有的 key 视为"当时不存在"，会从结果里移除。"""
    result = dict(settings or {})
    for key in DYNAMIC_KEYS:
        if key in snapshot:
            result[key] = copy.deepcopy(snapshot[key])
        else:
            result.pop(key, None)
    return result


def _rewrite_head(store: SimStore, branch: str, history: List[SimState]) -> None:
    """整体重写该分支历史与 `state_current.json`（仅"离开分支时提交"这一种
    例外场景使用，语义与 `advance()` 里回写"已选选项"那一处一致）。"""
    atomic_write_jsonl(store.state_history_path(branch), [s.to_dict() for s in history])
    atomic_write_json(store.state_current_path(branch), history[-1].to_dict())


def commit_working_copy(store: SimStore, manifest: SimManifest, branch: str) -> bool:
    """把工作副本（`manifest.settings` 里的动态 key）提交到 `branch` 头部。

    仅当工作副本与该分支最近一份快照不一致时才写（写在头部那一步上）。
    返回是否写了盘。调用方负责保证 `branch` 就是工作副本当前对应的
    分支（即 `manifest.branch`）。
    """
    history = store.load_history(branch)
    if not history:
        return False
    snap = snapshot_if_changed(manifest.settings, history)
    if snap is None:
        return False
    history[-1].dynamic_snapshot = snap
    _rewrite_head(store, branch, history)
    return True


def enter_branch(store: SimStore, manifest: SimManifest, branch: str) -> bool:
    """用 `branch` 最近一份快照刷新 `manifest.settings` 里的工作副本
    （只改内存里的 `manifest`，调用方紧接着统一落盘）。该分支没有任何
    快照时不动（旧实例行为）。返回是否刷新了。"""
    snap = latest_snapshot(store.load_history(branch))
    if snap is None:
        return False
    manifest.settings = apply_to_settings(manifest.settings, snap)
    return True


def load_branch_dynamic_state(store: SimStore, branch: str) -> Optional[Dict[str, Any]]:
    """只读：取某条分支（不必是活跃分支）当前的动态状态快照，没有返回
    None。给对比视图/后续工作包读取非活跃分支的树使用。"""
    snap = latest_snapshot(store.load_history(branch))
    return copy.deepcopy(snap) if snap is not None else None
