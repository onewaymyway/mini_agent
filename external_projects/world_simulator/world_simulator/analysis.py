"""world_simulator/analysis.py — 结果聚合（阶段十 / 演进计划 4.2.2 节）
以及目标驱动排序（阶段十四 / 演进计划 4.6 节）

设计依据：`next_doc/world_simulator_universal_world_model_upgrade_plan.md`
4.2 节、4.6 节。输入一组 `vars`（通常是 `autopilot.run_repeated_experiment()`
返回的多条分支的 `final_vars`）+ 用户指定要关注的字段路径列表，计算
这些字段的统计摘要——数值型字段给均值/最小/最大/标准差，枚举型（字符
串/布尔等不可平均的）字段给众数分布。

刻意不引入 numpy/pandas：这里的数据量（几条到几十条分支）用标准库
`statistics` 模块 + 几十行纯 Python 就够，参考 `achievements.py`
"纯函数计算，无新增持久化结构"的取舍——不带来任何新的依赖、不落盘任何
新的数据结构，调用方（`app.py`）需要什么时候用什么时候算。

阶段十四新增 `normalize_objectives()`/`rank_by_objectives()`：把
`manifest.settings.objectives` 里声明了 `field`/`direction` 的条目
拿来对一组分支按"逐项胜负计数"排序，仍然是纯函数、不做任何加权求和，
排序结果只是辅助参考，见 4.6 节"方案"第 2 条的取舍说明。
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


def _get_nested(data: Dict[str, Any], path: str) -> Any:
    """按 `.` 分隔的路径读取（最多支持一层嵌套，约定同
    `engine._get_nested()`），路径不存在时返回 `None`。

    单独在这里再实现一次（而不是从 `engine.py` 导入）：`analysis.py`
    是纯函数模块，不依赖 `engine.py`/`store.py` 这些带副作用的模块，
    保持"给一组数据、算一份摘要"这个最小职责，方便脱离整个引擎单独
    测试/复用。
    """
    parts = path.split(".", 1)
    if len(parts) == 1:
        return data.get(parts[0])
    outer = data.get(parts[0])
    if not isinstance(outer, dict):
        return None
    return outer.get(parts[1])


@dataclass
class FieldStats:
    """单个字段路径的统计摘要。"""

    field: str
    kind: str  # "numeric" | "categorical" | "missing"
    count: int
    """有效样本数（排除了字段缺失/取不到值的分支）。"""
    mean: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    stdev: Optional[float] = None
    """样本标准差（`count < 2` 时为 `0.0`，不是 `None`——"只有一个样本"
    本身就是"零波动"的一种诚实表达，`None` 容易被界面误当成"没算出来"
    展示成空白）。仅 `kind == "numeric"` 时有意义。"""
    distribution: Optional[Dict[str, int]] = None
    """值 → 出现次数（仅 `kind == "categorical"` 时有意义），按次数
    降序排列，供界面直接展示"出现频率最高的取值"。"""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def aggregate_field_stats(
    vars_list: List[Dict[str, Any]], field_paths: List[str]
) -> List[FieldStats]:
    """对一组 `vars`（每条分支的终态变量）按字段路径分别做统计聚合。

    每个字段路径独立处理：先收集所有分支里这个字段的有效值（跳过取不到
    值的分支，不强行当成 0/空字符串），再按取到的值是不是数字决定走
    "数值型"还是"枚举型"分支——同一个字段路径在不同分支上出现的值理论
    上应该类型一致（都来自同一个模板/skill 的输出约定），但这里不做
    强校验，遇到混合类型时按"多数值的类型"处理，少数不匹配类型的值
    直接跳过（不报错中断整份摘要，一个字段统计异常不该拖累其它字段）。

    Args:
        vars_list: 多条分支的 `vars` 字典列表（如
            `[r.final_vars for r in results if r.final_vars]`，调用方
            自己过滤掉 `None`）。
        field_paths: 要关注的字段路径列表（同 `resource_fields` 的路径
            格式，`.` 分隔一层嵌套，比如 `"resources.cash"`）。

    Returns: 按 `field_paths` 顺序排列的 `FieldStats` 列表，长度恒等于
        `len(field_paths)`（每个路径都有一条结果，哪怕完全没取到值，
        这种情况下 `kind="missing"`、`count=0`，其它字段为 `None`，
        方便调用方不用额外判断"这个字段是不是被跳过了"）。
    """
    results: List[FieldStats] = []
    for path in field_paths:
        raw_values = [_get_nested(v, path) for v in vars_list]
        present = [val for val in raw_values if val is not None]
        if not present:
            results.append(FieldStats(field=path, kind="missing", count=0))
            continue

        numeric_values = [
            float(val) for val in present
            if isinstance(val, (int, float)) and not isinstance(val, bool)
        ]
        # "多数值是数字" 才按数值型处理——极少数分支把资源字段写成
        # 字符串（比如 LLM 偶尔输出 "约 500 元" 这种非结构化值）时，
        # 不应该让整个字段退化成"每个值都不一样"的枚举展示。
        if numeric_values and len(numeric_values) >= len(present) / 2:
            results.append(
                FieldStats(
                    field=path,
                    kind="numeric",
                    count=len(numeric_values),
                    mean=statistics.fmean(numeric_values),
                    min=min(numeric_values),
                    max=max(numeric_values),
                    stdev=statistics.stdev(numeric_values) if len(numeric_values) >= 2 else 0.0,
                )
            )
        else:
            counter = Counter(str(val) for val in present)
            distribution = dict(
                sorted(counter.items(), key=lambda item: item[1], reverse=True)
            )
            results.append(
                FieldStats(
                    field=path, kind="categorical", count=len(present),
                    distribution=distribution,
                )
            )
    return results


def discover_numeric_fields(vars_list: List[Dict[str, Any]]) -> List[str]:
    """自动扫出一组分支终态 `vars` 里"数值型、可比较"的字段路径（最多
    支持一层嵌套，同 `_get_nested()` 的路径格式），不需要用户手动敲
    JSON/逗号分隔列表才能用"统计摘要"/"按关注指标排序"这些功能——
    模板输出的 `vars` 本身就带着这些字段，用户没declare不代表系统
    没法自己看出来"哪些字段是数值、可能值得关注"。

    只收"多数分支都能取到数值"的字段（同 `aggregate_field_stats()`
    "多数值是数字才按数值型处理"的取舍一致），避免把偶尔混进来的
    字符串字段、或者只在个别分支出现的噪声字段也当成候选。顶层字段
    和一层嵌套字段（如 `resources.cash`）都会被扫描，嵌套字典本身
    不算作候选（要展开到叶子）。

    Args:
        vars_list: 多条分支的 `vars` 字典列表。

    Returns: 按字段路径字母序排列、去重的字段路径列表；`vars_list`
        为空或扫不出任何数值字段时返回空列表。
    """
    per_field_values: Dict[str, List[Any]] = {}
    per_field_seen: Dict[str, int] = {}

    def _record(path: str, value: Any) -> None:
        per_field_seen[path] = per_field_seen.get(path, 0) + 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            per_field_values.setdefault(path, []).append(value)

    for v in vars_list:
        if not isinstance(v, dict):
            continue
        for key, value in v.items():
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    _record(f"{key}.{nested_key}", nested_value)
            else:
                _record(key, value)

    fields = [
        path
        for path, seen in per_field_seen.items()
        if len(per_field_values.get(path, [])) >= max(1, seen) / 2
        and per_field_values.get(path)
    ]
    fields.sort()
    return fields


@dataclass
class Objective:
    """单条"关注指标"的规范化形式（阶段十四，4.6 节）。

    `objectives` 声明支持两种写法：纯字符串（阶段十二行为，等价于
    `field=None`，只展示不参与排序）或结构化字典
    `{"label": ..., "field": ..., "direction": "max"|"min"}`。
    `normalize_objectives()` 把两种写法统一转成这个 dataclass，供
    `rank_by_objectives()` 和界面展示统一使用。
    """

    label: str
    field: Optional[str] = None
    direction: str = "max"  # "max" | "min"，缺省 "max"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_objectives(objectives: List[Any]) -> List[Objective]:
    """把 `manifest.settings.objectives`（纯字符串/结构化字典混合列表）
    规范化成 `Objective` 列表。

    向后兼容：纯字符串项 → `Objective(label=<字符串>, field=None)`，不
    参与排序，只用于展示（等价于阶段十二的行为）；字典项按
    `label`/`field`/`direction` 读取，`direction` 只接受 `"max"`/
    `"min"`，其它值（含缺省）一律当作 `"max"`，不报错中断。
    """
    result: List[Objective] = []
    for item in objectives or []:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("field") or "").strip()
            field = item.get("field")
            field = str(field).strip() if field else None
            direction = item.get("direction") if item.get("direction") in ("max", "min") else "max"
            if label:
                result.append(Objective(label=label, field=field or None, direction=direction))
        else:
            label = str(item).strip()
            if label:
                result.append(Objective(label=label, field=None))
    return result


@dataclass
class RankedBranch:
    """`rank_by_objectives()` 返回的单条分支排序结果。"""

    index: int
    """在传入的 `vars_list`/`labels` 里的原始下标，供调用方对回具体分支。"""
    label: str
    """展示用标签（比如分支号），来自调用方传入的 `labels`，缺省用
    `f"分支 {index + 1}"`。"""
    values: Dict[str, Any]
    """这条分支在每个可排序目标字段上的取值（取不到时为 `None`）。"""
    score: int
    """逐项胜负计数：这条分支在多少个可排序目标上"不劣于"其它所有
    分支（严格更优记 1 分、并列不重复加分——用"不劣于其它所有分支的
    项数"而不是两两比较的胜场数，避免分支数变化时分数量纲不一致）。"""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def rank_by_objectives(
    vars_list: List[Dict[str, Any]],
    objectives: List[Any],
    labels: Optional[List[str]] = None,
) -> List[RankedBranch]:
    """按声明了 `field` 的 `objectives` 对一组分支的终态 `vars` 排序。

    刻意用最朴素的"逐项胜负计数"而不是加权求和（4.6 节方案第 2 条）：
    给不同指标分配权重本身是一个需要用户输入的主观决定，本次不引入
    权重配置这层复杂度——对每个可排序目标字段，取这一批分支里的最优值
    （按 `direction` 决定是最大还是最小），达到这个最优值的分支各记
    1 分，最终按总分从高到低排序，同分时保持原始顺序（稳定排序）。

    Args:
        vars_list: 多条分支的 `vars` 字典列表。
        objectives: `manifest.settings.objectives`（原始格式，内部会先
            用 `normalize_objectives()` 规范化）。
        labels: 每条分支的展示标签（比如分支号），长度应与 `vars_list`
            一致；缺省时用 `f"分支 {i + 1}"`。

    Returns: 只使用声明了 `field` 的目标字段参与打分；如果一个都没有
        （比如全是纯字符串写法的 `objectives`），返回空列表，调用方
        据此判断"这次不展示排序区"（4.6 节验收标准）。按 `score` 降序
        排列，长度等于 `len(vars_list)`。
    """
    ranked_objectives = [o for o in normalize_objectives(objectives) if o.field]
    if not ranked_objectives or not vars_list:
        return []

    labels = labels or [f"分支 {i + 1}" for i in range(len(vars_list))]

    # 对每个目标字段，先算出这批分支里的"最优值"（数值型才参与打分，
    # 取不到值/非数值的分支在这一项上不计分，不强行拿字符串比较大小）。
    field_best: Dict[str, Optional[float]] = {}
    field_values: Dict[str, List[Optional[float]]] = {}
    for obj in ranked_objectives:
        values: List[Optional[float]] = []
        for v in vars_list:
            raw = _get_nested(v, obj.field)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                values.append(float(raw))
            else:
                values.append(None)
        field_values[obj.field] = values
        numeric = [x for x in values if x is not None]
        if not numeric:
            field_best[obj.field] = None
        else:
            field_best[obj.field] = max(numeric) if obj.direction == "max" else min(numeric)

    results: List[RankedBranch] = []
    for i, v in enumerate(vars_list):
        score = 0
        branch_values: Dict[str, Any] = {}
        for obj in ranked_objectives:
            val = field_values[obj.field][i]
            branch_values[obj.label] = val
            best = field_best[obj.field]
            if val is not None and best is not None and val == best:
                score += 1
        results.append(
            RankedBranch(index=i, label=labels[i], values=branch_values, score=score)
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results
