"""world_simulator/engine/background_entities.py — 背景角色简单线性
趋势外推（Hierarchical Agent，4.10 节设计草案第一步）。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from typing import Any, Dict


def _normalize_background_entities(raw: Any) -> list:
    """把 `manifest.settings.background_entities` 归一化成字符串
    列表（Hierarchical Agent，4.10 节设计草案第一步）。非法/空项直接
    跳过，不抛错。"""
    names: list = []
    for item in raw or []:
        name = str(item or "").strip()
        if name:
            names.append(name)
    return names


def _apply_background_entity_extrapolation(
    current_vars: Dict[str, Any],
    next_vars: Dict[str, Any],
    previous_vars: Dict[str, Any],
    background_entities: list,
) -> list:
    """对声明为"背景角色"的主体，用简单线性趋势外推**强制覆盖** LLM
    这一步给出的值（Hierarchical Agent，4.10 节设计草案第一步：先做
    "分层"本身，不做"调度框架"）。

    直接原地修改 `next_vars`（调用方传入的是本次推进即将落盘的那份
    `next_vars`，修改它就是修改最终落盘的结果），返回实际生效的主体
    名字列表，供落盘进 `SimState.background_entities_applied` 留痕。

    外推规则：对每个声明为背景角色的主体，取它"上一步"（`previous_
    vars`，可能不存在——第一次推进时没有"上一步"）到"这一步"
    （`current_vars`，即这次推进*开始前*的状态）之间每个数值字段的
    变化量，按相同变化量再推一步得到覆盖值；没有上一步可参考、或字段
    不是数值类型时，原样保留这一步（`current_vars`）的值（外推量为
    0，不是随便编一个数）。**只处理 `vars.entities` 结构**（要求
    `multi_entity_mode` 同时启用），`current_vars`/`next_vars` 没有
    `entities` 字典时是空操作，不报错——`hierarchical_agent_mode`
    误开在不支持的模板上不应该导致推进失败。
    """
    if not background_entities:
        return []
    entities_current = current_vars.get("entities") if isinstance(current_vars, dict) else None
    entities_next = next_vars.get("entities") if isinstance(next_vars, dict) else None
    if not isinstance(entities_current, dict) or not isinstance(entities_next, dict):
        return []
    entities_previous = previous_vars.get("entities") if isinstance(previous_vars, dict) else None
    applied: list = []
    for name in background_entities:
        cur_entity = entities_current.get(name)
        if not isinstance(cur_entity, dict):
            continue
        prev_entity = entities_previous.get(name) if isinstance(entities_previous, dict) else None
        extrapolated: Dict[str, Any] = {}
        for key, cur_value in cur_entity.items():
            is_numeric = isinstance(cur_value, (int, float)) and not isinstance(cur_value, bool)
            if not is_numeric:
                extrapolated[key] = cur_value
                continue
            prev_value = prev_entity.get(key) if isinstance(prev_entity, dict) else None
            prev_is_numeric = isinstance(prev_value, (int, float)) and not isinstance(prev_value, bool)
            delta = (cur_value - prev_value) if prev_is_numeric else 0
            extrapolated[key] = cur_value + delta
        entities_next[name] = extrapolated
        applied.append(name)
    next_vars["entities"] = entities_next
    return applied
