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
    """把 `manifest.settings.resource_relations` 归一化（第八轮批次二，
    `next_doc/world_simulator_c_category_precision_upgrade_
    improvement_plan.md` 第 3 节）。

    识别三种 `type`：
    - `"transfer"`（或没写 `type`，默认按 `transfer` 处理）：归一化成
      `{"type": "transfer", "from": str, "to": str, "tolerance": float}`。
      只应该用于同一种量纲/记账单位之间的搬运（见
      `state_model.SimManifest.settings` 里 `resource_relations` 的
      格式说明）——量纲不同的一对字段应该声明成 `"conversion"`。
    - `"conversion"`（有代价的跨量纲转化，第九轮批次一新增）：归一化成
      `{"type": "conversion", "from": str, "to": str, "note": str}`。
      **不参与任何数值核对**，只做结构化记录，`_check_resource_
      relations()` 里直接跳过、永不产生不一致项。
    - `"production"`（持续产出关系）：归一化成
      `{"type": "production", "field": str, "amount_per_step":
      float | None, "source_line_id": str, "tolerance": float}`。
      `amount_per_step` 缺省或非法时是 `None`，表示"只标记这是一个
      持续产出关系，不做速率层面的核对"；`source_line_id` 缺省时是
      空字符串，表示背景产出，不归因到具体某条因果线。

    其它 `type` 值本版本不支持，直接跳过；非法/无法解析的项也直接
    跳过，不抛错——同 `_normalize_resource_fields()`，配置写错了不
    应该让整个推进流程失败。
    """
    relations: list = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rel_type = str(item.get("type") or "transfer").strip() or "transfer"
        try:
            tolerance = float(item.get("tolerance", 0.1) or 0.1)
        except (TypeError, ValueError):
            tolerance = 0.1

        if rel_type == "transfer":
            from_field = str(item.get("from") or "").strip()
            to_field = str(item.get("to") or "").strip()
            if not from_field or not to_field:
                continue
            relations.append(
                {"type": "transfer", "from": from_field, "to": to_field, "tolerance": tolerance}
            )
        elif rel_type == "conversion":
            from_field = str(item.get("from") or "").strip()
            to_field = str(item.get("to") or "").strip()
            if not from_field or not to_field:
                continue
            relations.append(
                {
                    "type": "conversion",
                    "from": from_field,
                    "to": to_field,
                    "note": str(item.get("note") or "").strip(),
                }
            )
        elif rel_type == "production":
            field_name = str(item.get("field") or "").strip()
            if not field_name:
                continue
            amount_per_step: Any = item.get("amount_per_step")
            if amount_per_step is None or amount_per_step == "":
                amount_per_step = None
            else:
                try:
                    amount_per_step = float(amount_per_step)
                except (TypeError, ValueError):
                    amount_per_step = None
            source_line_id = str(item.get("source_line_id") or "").strip()
            relations.append(
                {
                    "type": "production",
                    "field": field_name,
                    "amount_per_step": amount_per_step,
                    "source_line_id": source_line_id,
                    "tolerance": tolerance,
                }
            )
        else:
            continue
    return relations


def _normalize_resource_transfers(raw: Any) -> Dict[str, float]:
    """把 `advance_step` 阶段 skill 可选给出的 `resource_transfers`
    （见 `state_model.SimState.resource_transfers` 的格式说明）归一化
    成 `{"{from}->{to}": amount}` 的查找表，`amount` 取绝对值（方向由
    `relation` 字符串本身、也就是对应哪条 `transfer` 关系决定，不需要
    在这里判断正负号）。`relation` 缺失、和已声明关系对不上、或
    `amount` 不是数字的项直接跳过——这是 LLM 的可选自报数据，格式
    不对不应该让整个核对流程失败，只是这一条退化成没报账。
    """
    ledger: Dict[str, float] = {}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        relation_key = str(item.get("relation") or "").strip()
        if not relation_key:
            continue
        amount = item.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            continue
        ledger[relation_key] = abs(float(amount))
    return ledger


def _count_field_references(relations: list) -> Dict[str, int]:
    """统计 `transfer`/`conversion` 关系里每个字段路径被引用的次数，
    用于判断"这个字段这一步是否可能有多个来源同时在变"（第九轮批次
    一新增）。`production` 关系不计入——它描述的是单一字段自身的
    产出速率，不涉及"和另一个字段的变化量做零和核对"，不存在这里
    要规避的归因混淆问题。
    """
    counts: Dict[str, int] = {}
    for spec in relations:
        if spec["type"] not in ("transfer", "conversion"):
            continue
        for path in (spec["from"], spec["to"]):
            counts[path] = counts.get(path, 0) + 1
    return counts


def _check_resource_relations(
    current_vars: Dict[str, Any],
    next_vars: Dict[str, Any],
    resource_relations_raw: Any,
    resource_transfers_raw: Any = None,
) -> list:
    """对声明的 `transfer`/`conversion`/`production` 关系做一次事后
    一致性检查（`transfer` 部分阶段十六 4.8 节实现，`production` 部分
    第八轮批次二，`next_doc/world_simulator_c_category_precision_
    upgrade_improvement_plan.md` 第 3 节新增，`conversion` 类型与
    `transfer` 的核对方式改进见第九轮批次一，`next_doc/world_
    simulator_resource_relation_consistency_fix_plan.md`）。

    `transfer`：默认计算 `delta_from = next_vars[from] -
    current_vars[from]`、`delta_to = next_vars[to] - current_vars[to]`，
    如果两者之和的绝对值超出容差（按两者绝对值的较大者衡量），记为
    一条不一致，返回项形如 `{"from": ..., "to": ..., "delta_from":
    ..., "delta_to": ..., "checked_by": "diff"}`。这个"整体快照差分"
    假设这一步该字段的净变化全部来自这一条关系，在该字段这一步还被
    别的变动影响时会失真，因此有两个改进：
    - **显式流水优先**：如果 `resource_transfers_raw` 里报告了这条
      关系（`"{from}->{to}"` 能在归一化后的流水表里查到），**直接
      信任这份账，跳过差分核对，不产生不一致项**——不再用报告值去
      比对整体快照差分的精确数值，也不做任何反过来验证报告是否"准
      确"的二次校验。这正是原始问题场景要解决的：`to` 端这一步很
      可能还被别的、根本没有声明成 `resource_relations` 的变动影响
      （甚至可能盖过转移本身的方向），净差分本来就不是可靠的校验
      依据，一旦 skill 已经如实报了这条关系的账，继续拿净差分去
      验证它只是把"差分法不可靠"这个问题换个地方重新引入。这一点
      和 `major_decision`/`key_drivers` 等其它"自报"字段的既有取舍
      一致：只做结构化记录、不做无法可靠核实的二次校验。
    - **共享字段自动降级**：没有显式流水报告时，如果 `from`/`to`
      任一字段被两条以上 `transfer`/`conversion` 关系引用（说明这一步
      该字段很可能不止一个来源在变），差分法已知不可靠，直接跳过、
      不产生不一致项——不是检查变宽松了，是不再对一个已知不成立的
      假设继续报警。

    `conversion`：**永不参与数值核对**，只是一条结构化记录，直接
    跳过，不会出现在返回列表里。

    `production`：仅当声明了 `amount_per_step` 时才参与数值核对——
    计算 `actual_delta = next_vars[field] - current_vars[field]`，
    如果和声明速率的偏差超出容差（按两者绝对值的较大者衡量），记为
    一条不一致，返回项形如 `{"kind": "production", "field": ...,
    "amount_per_step": ..., "actual_delta": ...}`（用 `kind` 而不是
    `from`/`to` 区分，方便调用方按 key 判断分支，不需要额外的
    `type` 字段判断）。未声明 `amount_per_step` 的 `production` 关系
    不参与任何数值核对，只是一条结构化记录。

    所有类型都是**只提示，不修改任何数值、不拒绝推进**——这里没有
    "应该是多少"的唯一正确答案，只做留痕。任一字段缺失/非数字/
    变化量都为 0 时跳过，不产生误报。
    """
    relations = _normalize_resource_relations(resource_relations_raw)
    ledger = _normalize_resource_transfers(resource_transfers_raw)
    field_ref_counts = _count_field_references(relations)

    violations: list = []
    for spec in relations:
        if spec["type"] == "conversion":
            continue
        if spec["type"] == "transfer":
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

            relation_key = f"{from_path}->{to_path}"
            if relation_key in ledger:
                # 有显式流水时，**不再**用它去比对整体快照差分——这正
                # 是原始问题场景（`cash` 这一步同时被这条转移和别的、
                # 根本没有声明成 `resource_relations` 的花销共同影响，
                # 净差分本来就不等于这条关系单独的搬运量，甚至连方向
                # 都可能被未建模的其它变动盖过）。既然 skill 已经如实
                # 报了这条关系的账，就直接信任这份账、不产生不一致项，
                # 不再尝试用一个已知会被污染的净差分去反过来验证它——
                # 这和 `major_decision`/`key_drivers` 等其它"自报"字段
                # 的既有取舍一致：只做结构化记录，不做无法可靠核实的
                # 二次校验。
                continue

            # 没有显式流水：任一端字段被多条关系共享时，差分法已知
            # 无法正确归因，跳过差分核对，避免已知不成立的假设继续
            # 误报。
            if field_ref_counts.get(from_path, 0) > 1 or field_ref_counts.get(to_path, 0) > 1:
                continue

            allowed = tolerance * max(abs(delta_from), abs(delta_to))
            if abs(delta_from + delta_to) > allowed:
                violations.append(
                    {
                        "from": from_path,
                        "to": to_path,
                        "delta_from": delta_from,
                        "delta_to": delta_to,
                        "checked_by": "diff",
                    }
                )
        elif spec["type"] == "production":
            amount_per_step = spec["amount_per_step"]
            if amount_per_step is None:
                continue
            field_path = spec["field"]
            tolerance = spec["tolerance"]
            cur_value = _get_nested(current_vars, field_path)
            next_value = _get_nested(next_vars, field_path)
            values = (cur_value, next_value)
            if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
                continue
            actual_delta = next_value - cur_value
            allowed = tolerance * max(abs(actual_delta), abs(amount_per_step))
            if abs(actual_delta - amount_per_step) > allowed:
                violations.append(
                    {
                        "kind": "production",
                        "field": field_path,
                        "amount_per_step": amount_per_step,
                        "actual_delta": actual_delta,
                    }
                )
    return violations
