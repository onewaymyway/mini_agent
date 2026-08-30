"""evolution/self_model_drift.py — 自我模型漂移检测
（next_doc/self_awareness_identity_evolution_plan.md §2.6）。

`self_assessment.confidence_by_domain`（perception/global_knowledge.py，
global scope，历史累积信念）与当前 workdir 的 `capability_map`（evolution/
consolidation.py::build_capability_map，最近实测）是两份独立数据，此前
没有任何机制主动比对——一个从不检查自己判断准不准的评价机制是空转的。

本模块只做一件事：只读比较这两份数据，找出\"信念与实测差距较大\"的领域，
生成信号列表。不自动覆盖 `confidence_by_domain`，不做任何写入——落差
只作为 §2.2 自我叙事生成的上下文信号（"我曾经认为...，但最近的实测显示
..."），由叙事 job 决定如何措辞呈现，保持"不臆造、不静默覆盖"的一贯
克制原则。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_DRIFT_THRESHOLD = 0.3  # 置信度落差超过这个值才算"值得关注的漂移"


@dataclass
class DriftSignal:
    domain: str
    belief_confidence: float   # self_assessment.confidence_by_domain 里的历史信念
    actual_confidence: float   # capability_map 里的最近实测
    delta: float                # actual - belief；正值代表"实测比信念更好"

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "belief_confidence": round(self.belief_confidence, 3),
            "actual_confidence": round(self.actual_confidence, 3),
            "delta": round(self.delta, 3),
        }


def compute_belief_drift_signals(
    paths, *, threshold: float = DEFAULT_DRIFT_THRESHOLD
) -> list[DriftSignal]:
    """只读比较 `self_assessment.confidence_by_domain`（信念）与当前
    workdir `capability_map`（实测），返回落差超过 `threshold` 的领域，
    按落差绝对值降序排列。

    两份数据里都没出现的领域不比较（缺失信念或缺失实测都无法构成"漂移"，
    只有两边都有数据、且差距明显时才算真正的校准问题）。任一数据源读取
    失败时返回空列表，不影响调用方（这是一个辅助信号，不是关键路径）。
    """
    try:
        from mini_agent.perception.global_knowledge import load_self_profile
        from mini_agent.evolution.consolidation import build_capability_map

        profile = load_self_profile(paths)
        belief = dict(profile.self_assessment.confidence_by_domain) if profile else {}
        if not belief:
            return []

        actual_entries = build_capability_map(paths, None)
        actual = {e.domain: e.confidence for e in actual_entries}
        if not actual:
            return []
    except Exception:
        return []

    signals = []
    for domain in sorted(set(belief) & set(actual)):
        b, a = float(belief[domain]), float(actual[domain])
        delta = a - b
        if abs(delta) >= threshold:
            signals.append(DriftSignal(domain=domain, belief_confidence=b, actual_confidence=a, delta=delta))

    signals.sort(key=lambda s: -abs(s.delta))
    return signals
