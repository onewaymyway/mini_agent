"""world_simulator/engine/resource_guard.py — 资源类字段下限校验 +
资源转移关系一致性检查。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from typing import Any, Dict


def _normalize_resource_fields(raw: Any) -> list:
    """把 `manifest.settings.resource_fields` 归一化成
    `[{"field": str, "min": float}, ...]` 的形式。

    支持两种输入写法（见 `state_model.SimManifest.settings` 的字段
    说明）：纯字段名字符串（下限默认 0）、或
    `{"field": ..., "min": ...}` 字典（`min` 缺省也是 0）。非法/无法
    解析的项直接跳过，不抛错——这是一个"锦上添花"的校验功能，配置写
    错了不应该让整个推进流程失败。
    """
    fields: list = []
    for item in raw or []:
        if isinstance(item, str):
            name = item.strip()
            if name:
                fields.append({"field": name, "min": 0})
        elif isinstance(item, dict):
            name = str(item.get("field") or "").strip()
            if not name:
                continue
            try:
                min_value = float(item.get("min", 0) or 0)
            except (TypeError, ValueError):
                min_value = 0
            fields.append({"field": name, "min": min_value})
    return fields


def _get_nested(data: Dict[str, Any], path: str) -> Any:
    """按 `.` 分隔的路径读取（最多支持一层嵌套，见 `resource_fields`
    格式说明），路径不存在时返回 `None`。"""
    parts = path.split(".", 1)
    if len(parts) == 1:
        return data.get(parts[0])
    outer = data.get(parts[0])
    if not isinstance(outer, dict):
        return None
    return outer.get(parts[1])


def _set_nested(data: Dict[str, Any], path: str, value: Any) -> None:
    """按 `.` 分隔的路径写入（最多支持一层嵌套），路径中间层不存在时
    静默放弃（说明这个字段这一步 LLM 根本没给出来，没有可以校正的
    数值——不强行造一个结构出来）。"""
    parts = path.split(".", 1)
    if len(parts) == 1:
        data[parts[0]] = value
        return
    outer = data.get(parts[0])
    if isinstance(outer, dict):
        outer[parts[1]] = value


def _apply_resource_guard(vars_dict: Dict[str, Any], resource_fields_raw: Any) -> list:
    """对 `vars_dict` 就地做资源类字段下限校验（阶段九，4.1 节）。

    低于下限的字段被原地夹到下限，返回本次发现并纠正的越界项列表
    （`SimState.resource_violations` 要落盘的内容）；未声明
    `resource_fields`、字段不存在、或字段值不是数字（比如 LLM 把资源
    字段错写成字符串）时都跳过，不报错——这一步只做"数值下限"这一种
    最简单的校验，其它情况留给未来按需扩展。
    """
    violations: list = []
    for spec in _normalize_resource_fields(resource_fields_raw):
        path = spec["field"]
        min_value = spec["min"]
        current = _get_nested(vars_dict, path)
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            continue
        if current < min_value:
            _set_nested(vars_dict, path, min_value)
            violations.append(
                {"field": path, "llm_value": current, "clamped_value": min_value}
            )
    return violations


def _normalize_resource_relations(raw: Any) -> list:
    """把 `manifest.settings.resource_relations` 归一化成
    `[{"from": str, "to": str, "tolerance": float}, ...]` 的形式
    （阶段十六，4.8 节）。

    只识别 `type == "transfer"`（或没写 `type`，默认按 `transfer`
    处理）的项，`production` 等其它类型本版本不支持，直接跳过；
    非法/无法解析的项也直接跳过，不抛错——同 `_normalize_resource_
    fields()`，配置写错了不应该让整个推进流程失败。
    """
    relations: list = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rel_type = str(item.get("type") or "transfer").strip() or "transfer"
        if rel_type != "transfer":
            continue
        from_field = str(item.get("from") or "").strip()
        to_field = str(item.get("to") or "").strip()
        if not from_field or not to_field:
            continue
        try:
            tolerance = float(item.get("tolerance", 0.1) or 0.1)
        except (TypeError, ValueError):
            tolerance = 0.1
        relations.append({"from": from_field, "to": to_field, "tolerance": tolerance})
    return relations


def _check_resource_relations(
    current_vars: Dict[str, Any], next_vars: Dict[str, Any], resource_relations_raw: Any
) -> list:
    """对声明的 `transfer` 关系做一次事后一致性检查（阶段十六，4.8 节）。

    对每条关系计算 `delta_from = next_vars[from] - current_vars[from]`、
    `delta_to = next_vars[to] - current_vars[to]`，如果两者之和的绝对值
    超出容差（按两者绝对值的较大者衡量），记为一条不一致。**不修改任何
    数值、不拒绝推进**——这里没有"应该是多少"的唯一正确答案，只做
    留痕。任一字段缺失/非数字/变化量都为 0（没有实际发生转移）时跳过，
    不产生误报。
    """
    violations: list = []
    for spec in _normalize_resource_relations(resource_relations_raw):
        from_path = spec["from"]
        to_path = spec["to"]
        tolerance = spec["tolerance"]
        cur_from = _get_nested(current_vars, from_path)
        cur_to = _get_nested(current_vars, to_path)
        next_from = _get_nested(next_vars, from_path)
        next_to = _get_nested(next_vars, to_path)
        values = (cur_from, cur_to, next_from, next_to)
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
            continue
        delta_from = next_from - cur_from
        delta_to = next_to - cur_to
        if delta_from == 0 and delta_to == 0:
            continue
        allowed = tolerance * max(abs(delta_from), abs(delta_to))
        if abs(delta_from + delta_to) > allowed:
            violations.append(
                {
                    "from": from_path,
                    "to": to_path,
                    "delta_from": delta_from,
                    "delta_to": delta_to,
                }
            )
    return violations
