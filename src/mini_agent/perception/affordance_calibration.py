"""
perception/affordance_calibration.py — Affordance 排序权重的闭环校准（方案四）

问题：AffordanceAnalyzer.analyze() 对 known_issues/unexplored_areas/
high_risk_zones 三路信号的排序权重是硬编码的（各展示 top 2-3 条，无相对
优先级学习）。如果"AffordanceMap 建议关注 X"之后，X 相关的工作最终被
SoftGoalDeriver derive 成 Goal 并被 outcome_tracker 判定为 improved，
说明这类建议"值得信"；如果多次被用户忽略或 derive 出的 Goal 被 reject，
说明这类建议的信噪比不高，应该降低其展示优先级。

设计原则（与本项目一贯的"感知层只读、不直接改变行为"原则一致）：
  - 不修改 AffordanceAnalyzer.analyze() 的核心聚合逻辑，只新增一个可选的
    weights 参数，默认值保持现有硬编码行为。
  - 校准数据来源全部复用已有基础设施，不新增数据采集：
      - outcome_tracker.py 的 verdict 记录（improved/worsened）
      - 通过 title/description 关键词与 AffordanceMap 三路来源做关联，
        判断某条 Affordance 提示最终演变成的 Goal/commit 效果如何。
  - 权重更新是周期性的（挂在 巩固循环 周期扫描里），不是逐 turn 更新。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

LEARNING_RATE = 0.1
WEIGHT_MIN = 0.3
WEIGHT_MAX = 2.0

_WEIGHTS_FILENAME = "affordance_weights.json"


@dataclass
class AffordanceWeights:
    known_issues_weight: float = 1.0
    unexplored_areas_weight: float = 1.0
    high_risk_zones_weight: float = 1.0

    def clamp(self) -> "AffordanceWeights":
        self.known_issues_weight = min(WEIGHT_MAX, max(WEIGHT_MIN, self.known_issues_weight))
        self.unexplored_areas_weight = min(WEIGHT_MAX, max(WEIGHT_MIN, self.unexplored_areas_weight))
        self.high_risk_zones_weight = min(WEIGHT_MAX, max(WEIGHT_MIN, self.high_risk_zones_weight))
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def _weights_path(paths: "AgentPaths"):
    return paths.workdir_dir / _WEIGHTS_FILENAME


def load_weights(paths: "AgentPaths") -> AffordanceWeights:
    """读取上次 calibrate() 持久化的权重，文件不存在或异常则返回默认权重。"""
    try:
        path = _weights_path(paths)
        if not path.exists():
            return AffordanceWeights()
        data = json.loads(path.read_text(encoding="utf-8"))
        return AffordanceWeights(
            known_issues_weight=float(data.get("known_issues_weight", 1.0)),
            unexplored_areas_weight=float(data.get("unexplored_areas_weight", 1.0)),
            high_risk_zones_weight=float(data.get("high_risk_zones_weight", 1.0)),
        ).clamp()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.affordance_calibration.load_weights')
        return AffordanceWeights()


def _save_weights(paths: "AgentPaths", weights: AffordanceWeights) -> None:
    try:
        path = _weights_path(paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(weights.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.affordance_calibration._save_weights')
        pass


def _classify_source(text: str) -> str:
    """
    启发式关键词匹配：判断一条 outcome_tracker 记录的关联文本更像哪一路
    AffordanceMap 来源。关联失败（都不像）返回空字符串，调用方据此跳过。
    容忍关联失败——并非所有 commit 都能追溯到某条 Affordance 提示。
    """
    text = (text or "").lower()
    if any(kw in text for kw in ("探索能力盲区", "能力盲区", "unexplored", "confidence")):
        return "unexplored_areas"
    if any(kw in text for kw in ("失败", "出错", "崩溃", "误删", "回退", "revert", "risk", "危险")):
        return "high_risk_zones"
    if any(kw in text for kw in ("已知", "issue", "bug", "blocker", "问题")):
        return "known_issues"
    return ""


def calibrate(paths: "AgentPaths") -> AffordanceWeights:
    """
    周期性调用（巩固循环 里新增一步，类似 outcome_tracker.tick()）：
      1. 读取 outcome_tracking.json 里已 resolved 的记录
      2. 尝试关联到 AffordanceMap 三路来源之一（关键词匹配，容忍关联失败）
      3. 按 verdict 调整对应来源权重
      4. 持久化到 <project_root>/.agent/affordance_weights.json
      5. 失败静默降级：任何异常直接返回默认权重（AffordanceWeights()），
         等价于本次校准跳过，不影响 AffordanceAnalyzer 现有行为。
    """
    weights = load_weights(paths)
    try:
        from mini_agent.evolution import outcome_tracker

        all_records = outcome_tracker.get_all(paths)
        records = [r for r in all_records if getattr(r, "status", "") == "resolved"]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.affordance_calibration.calibrate')
        return weights

    try:
        for record in records:
            text = getattr(record, "commit_summary", "") or getattr(record, "trigger_lesson_group_id", "")
            verdict = getattr(record, "verdict", None)
            source = _classify_source(text)
            if not source:
                continue

            attr = f"{source}_weight"
            current = getattr(weights, attr, 1.0)
            if verdict == "improved":
                current = current * (1 + LEARNING_RATE)
            elif verdict == "worsened":
                current = current * (1 - LEARNING_RATE)
            else:
                continue
            setattr(weights, attr, current)

        weights = weights.clamp()
        _save_weights(paths, weights)
        return weights
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.affordance_calibration.calibrate')
        return AffordanceWeights()


__all__ = ["AffordanceWeights", "calibrate", "load_weights", "LEARNING_RATE", "WEIGHT_MIN", "WEIGHT_MAX"]
