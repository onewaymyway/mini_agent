"""
wiki/decommission.py — 旧图书馆索引（分类树/实体索引/编年目录）下线评估
（next_doc/wiki_next_phase_improvement_plan.md 第 1 节）

背景：`wiki/promotion.py::evaluate_promotion_readiness()` 已经把"wiki 转正"
的三条量化标准做成了可随时查询的观测指标，但从未接到任何后续动作——标准
满足与否只是给人看的一个报告。本模块只补这一环："标准满足了，然后呢"，
不做任何自动改代码/自动删文件的动作（下线是不可逆操作，必须人工确认）。

`check_and_plan()` 是纯只读函数：
  1. 复用 `evaluate_promotion_readiness()` 判断三条标准是否满足；
  2. ready=True 时，生成一份"下线执行清单"（分三步：冻结旧索引写入 →
     观察期 → 真正移除，对应改进计划 1.2.2 节），描述每一步具体要改哪个
     配置项/哪个文件，但不执行；
  3. 把结果写入 `paths.wiki_decommission_report_path`（可重建的观测快照）。

daemon 侧的用法（见 evolution/cron_scheduler.py 的 `sys:consolidation` job）：
每次巩固循环触发后顺带调用一次 `check_and_plan()`，只在 ready 状态
**由 False 变为 True** 的瞬间才提醒一次，避免每 6 小时重复打扰。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_json
from mini_agent.wiki.promotion import PromotionReadiness, evaluate_promotion_readiness

# 改进计划 1.2.2 节三步下线流程的静态描述——不是可执行代码，是给人看的操作指引。
_DECOMMISSION_STEPS = [
    {
        "step": 1,
        "name": "冻结旧索引写入",
        "action": (
            "MemoryConfig 新增 legacy_index_enabled=False，"
            "library.consolidate() 跳过步骤 1-4（分类树生长/合并、实体摘要重写、"
            "实体去噪合并）；shelf_search 兜底路径保留但不再更新。"
        ),
        "reversible": True,
    },
    {
        "step": 2,
        "name": "观察期",
        "action": (
            "建议观察 >= 2 周，或直到 wiki/promotion.py 的 A/B 样本量"
            "（_AB_MIN_SAMPLES）继续稳定增长且命中率不低于冻结前，"
            "确认冻结旧索引没有引入检索质量回归。"
        ),
        "reversible": True,
    },
    {
        "step": 3,
        "name": "真正移除",
        "action": (
            "context_builder.py::_inject_shelf_search_chain 改为直接返回空上下文；"
            "classification.py/entity_index.py/catalog.py 移到 src/mini_agent/_deprecated/"
            "（不放进正式包路径避免被误 import，但保留代码方便回滚）。"
        ),
        "reversible": False,
    },
]


@dataclass
class DecommissionPlan:
    ready: bool
    blocking_reasons: list[str] = field(default_factory=list)
    readiness: Optional[PromotionReadiness] = None
    steps: list[dict] = field(default_factory=list)
    generated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "blocking_reasons": self.blocking_reasons,
            "readiness": self.readiness.to_dict() if self.readiness is not None else None,
            "steps": self.steps if self.ready else [],
            "generated_at": self.generated_at,
        }


def _blocking_reasons(readiness: PromotionReadiness) -> list[str]:
    reasons: list[str] = []
    if not readiness.ratio_ok:
        reasons.append(
            f"目标来源占比未连续 {readiness.ratio_days_required} 天达标"
            f"（当前观察到 {readiness.ratio_days_observed} 天，最新占比 "
            f"{readiness.current_ratio:.2f}）"
        )
    if not readiness.validation_ok:
        reasons.append(
            f"wiki 校验未连续 {readiness.validation_days_required} 天零 error"
            f"（当前观察到 {readiness.validation_days_observed} 天）"
        )
    if readiness.ab_ok is None:
        reasons.append(
            f"A/B 对比样本量不足（当前 {readiness.ab_sample_size} 条），"
            "暂无法下结论"
        )
    elif not readiness.ab_ok:
        reasons.append(
            f"wiki_search 命中率（{readiness.wiki_hit_rate}）低于 "
            f"shelf_search（{readiness.shelf_hit_rate}）"
        )
    return reasons


def check_and_plan(paths: AgentPaths, *, write_report: bool = True) -> DecommissionPlan:
    """只读评估：三条转正标准是否都满足，满足则附上三步下线执行清单。

    不执行任何下线动作，不修改除 `wiki_decommission_report_path` 之外的任何
    文件。评估或写入过程中的异常一律吞掉、返回 `ready=False` 的保守结果——
    宁可漏报"已经可以下线了"，也不能因为本函数出错而误导性地建议下线。
    """
    import time

    try:
        readiness = evaluate_promotion_readiness(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.decommission.check_and_plan')
        return DecommissionPlan(ready=False, blocking_reasons=["评估过程出错，本次视为未就绪"])

    ready = readiness.overall_ready
    reasons = [] if ready else _blocking_reasons(readiness)
    plan = DecommissionPlan(
        ready=ready,
        blocking_reasons=reasons,
        readiness=readiness,
        steps=_DECOMMISSION_STEPS if ready else [],
        generated_at=time.time(),
    )

    if write_report:
        try:
            _write_report(paths, plan)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.decommission.check_and_plan')
            pass

    return plan


def _write_report(paths: AgentPaths, plan: DecommissionPlan) -> None:
    path = paths.wiki_decommission_report_path
    atomic_write_json(path, plan.to_dict())


def load_last_report(paths: AgentPaths) -> Optional[dict]:
    """读取上一次 `check_and_plan()` 写入的报告，供 `/wiki promotion` 展示。"""
    path = paths.wiki_decommission_report_path
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.decommission.load_last_report')
        return None


def check_ready_transition(paths: AgentPaths) -> bool:
    """判断本次 check_and_plan() 是否是"由未就绪变为就绪"的那一次触发。

    daemon cron job 用这个函数决定要不要发一次提醒——只在状态翻转时提醒一次，
    避免每次巩固循环（默认 6h 一次）都重复打扰。读取失败视为"没有翻转"，
    不误报。
    """
    prev = load_last_report(paths)
    prev_ready = bool(prev.get("ready")) if prev else False
    plan = check_and_plan(paths)
    return (not prev_ready) and plan.ready


__all__ = [
    "DecommissionPlan",
    "check_and_plan",
    "load_last_report",
    "check_ready_transition",
]
