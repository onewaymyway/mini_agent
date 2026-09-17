"""world_simulator/analysis.py — 结果聚合（阶段十 / 演进计划 4.2.2 节）

设计依据：`next_doc/world_simulator_universal_world_model_upgrade_plan.md`
4.2 节。输入一组 `vars`（通常是 `autopilot.run_repeated_experiment()`
返回的多条分支的 `final_vars`）+ 用户指定要关注的字段路径列表，计算
这些字段的统计摘要——数值型字段给均值/最小/最大/标准差，枚举型（字符
串/布尔等不可平均的）字段给众数分布。

刻意不引入 numpy/pandas：这里的数据量（几条到几十条分支）用标准库
`statistics` 模块 + 几十行纯 Python 就够，参考 `achievements.py`
"纯函数计算，无新增持久化结构"的取舍——不带来任何新的依赖、不落盘任何
新的数据结构，调用方（`app.py`）需要什么时候用什么时候算。
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
