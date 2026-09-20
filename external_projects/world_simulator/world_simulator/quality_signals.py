"""world_simulator/quality_signals.py — 评估标准（8 条）的轻量自评模块
（第五轮方案 5.7 节，`next_doc/world_simulator_decision_engine_round2_
gap_analysis_plan.md`，阶段三十四第四批）。

参考文档第三十五节给出 8 条评价标准（因果一致性/决策真实性/分支
差异性/动态性/可解释性/跨线影响/渐进展开/不确定性区分），本模块
只覆盖其中**可以靠客观计数回答**的五条，其余两条（因果一致性、
决策真实性）本质上需要理解语义内容才能判断，勉强做基于关键词匹配
的伪指标反而会误导用户，明确不做（详见方案 5.7 节"不做的部分"）。

设计原则（同方案）：
- 只做**纯 Python 统计**，不发起任何新的 LLM 调用，不做主观判断。
- 输出的是**客观计数统计**，不是"这次模拟质量得几分"——调用方
  （`app.py`）展示时必须明确标注"这是统计代理指标，不是质量评分"，
  不做打分/排名。
- 分母为 0（没有可比较的数据）时对应 `ratio` 为 `None`，不伪造成
  0（区分"没有发生"和"没有数据"）。

五项统计指标：
1. **可解释性密度**（`explainability`）：有 `decision_reason`（顶层
   或 `ChoiceOption.action_reason`）的决策点占全部决策点（有
   `options` 的状态）的比例。
2. **跨线影响密度**（`cross_line_influence`）：`causal_links` 里带
   `source_line_id`（阶段十六已有字段）的条目占全部 `causal_links`
   的比例。
3. **分支差异性信号**（`branch_diversity`）：`options` 数量 >= 2
   的决策点占全部决策点的比例（区别于"全程只有单选项/无选项"）。
4. **渐进展开使用率**（`expansion_usage`）：`future_tree` 里
   `expansion_level == "expanded"` 的分支（含 `children`/
   `sub_branches` 递归展开的所有层级）占全部分支的比例；需要传入
   `causal_lines`（`manifest.settings.causal_lines`），因为
   `future_tree` 挂在因果线声明上，不在 `history` 里。
5. **不确定性标注覆盖率**（`uncertainty_coverage`）：`uncertain_
   fields` 非空的步数占全部步数的比例。

输入既接受 `SimState` 对象列表，也接受形状相同的 dict 列表（同
`causal_graph.build_causal_graph()` 的既有取舍，便于测试/未来独立
调用），统一用 `getattr`/dict 两种方式读取字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """从 `SimState` 对象或形状相同的 dict 里取字段，统一处理两种输入。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return numerator / denominator


def _iter_branches(branches: Any):
    """递归遍历 `future_tree.branches`，含 `children`/`sub_branches`
    两种嵌套子分支形状（见 `causal_tree.py` 顶部 docstring 的树形状
    说明），逐个 yield 出遇到的分支 dict（不去重、不校验合法性——
    非 dict 项直接跳过，同项目"宽松解析、坏数据不报错"的一贯风格）。
    """
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        yield branch
        yield from _iter_branches(branch.get("children"))
        yield from _iter_branches(branch.get("sub_branches"))


def summarize_quality_signals(
    history: Sequence[Any],
    causal_lines: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """基于已有字段做统计，得到 5 项客观代理指标，不发起 LLM 调用。

    - `history`：`SimStore.load_history()` 的返回值，或任意带对应
      字段属性/键的对象列表（`state0` 也应包含在内，参与"全部步数"
      的分母计算）。
    - `causal_lines`：`manifest.settings.causal_lines`（可选）——
      仅用于计算 `expansion_usage`；不传或传空时该项 `ratio` 为
      `None`（区分"没有因果线声明"和"因果线里全是 compressed"）。

    空 `history` 不报错，所有 `ratio` 都是 `None`（没有可统计的
    数据），计数字段都是 0。
    """

    total_steps = len(history)

    decision_points = 0
    decision_points_with_reason = 0
    multi_option_decision_points = 0

    total_causal_links = 0
    causal_links_with_source_line = 0

    steps_with_uncertain_fields = 0

    for state in history:
        options = _get(state, "options") or []
        if options:
            decision_points += 1
            if len(options) >= 2:
                multi_option_decision_points += 1

            decision_reason = str(_get(state, "decision_reason") or "").strip()
            has_action_reason = any(
                str(_get(opt, "action_reason", "") or "").strip()
                for opt in options
            )
            if decision_reason or has_action_reason:
                decision_points_with_reason += 1

        causal_links = _get(state, "causal_links") or []
        for link in causal_links:
            total_causal_links += 1
            source_line_id = str(_get(link, "source_line_id", "") or "").strip()
            if source_line_id:
                causal_links_with_source_line += 1

        uncertain_fields = _get(state, "uncertain_fields") or []
        if uncertain_fields:
            steps_with_uncertain_fields += 1

    total_branches = 0
    expanded_branches = 0
    if causal_lines:
        for line in causal_lines:
            future_tree = _get(line, "future_tree") or {}
            branches = _get(future_tree, "branches") or []
            for branch in _iter_branches(branches):
                total_branches += 1
                if str(branch.get("expansion_level") or "").strip() == "expanded":
                    expanded_branches += 1

    return {
        "total_steps": total_steps,
        "explainability": {
            "decision_points": decision_points,
            "with_reason": decision_points_with_reason,
            "ratio": _ratio(decision_points_with_reason, decision_points),
        },
        "cross_line_influence": {
            "total_causal_links": total_causal_links,
            "with_source_line": causal_links_with_source_line,
            "ratio": _ratio(causal_links_with_source_line, total_causal_links),
        },
        "branch_diversity": {
            "decision_points": decision_points,
            "multi_option": multi_option_decision_points,
            "ratio": _ratio(multi_option_decision_points, decision_points),
        },
        "expansion_usage": {
            "total_branches": total_branches,
            "expanded": expanded_branches,
            "ratio": _ratio(expanded_branches, total_branches),
        },
        "uncertainty_coverage": {
            "total_steps": total_steps,
            "with_uncertain_fields": steps_with_uncertain_fields,
            "ratio": _ratio(steps_with_uncertain_fields, total_steps),
        },
    }
