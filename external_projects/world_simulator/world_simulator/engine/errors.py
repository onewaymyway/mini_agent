"""world_simulator/engine/errors.py — 引擎异常类型。

从原单体 `engine.py` 拆分而来（阶段三十，`next_doc/
world_simulator_universal_simulator_gap_analysis_and_roadmap_v2_
plan.md` 4.18 节剩余部分）。纯粹的内部重组：三个异常类型原样迁移，
不改变任何行为，其它子模块统一从这里导入，避免相互之间产生循环
依赖。
"""

from __future__ import annotations


class SimEngineError(RuntimeError):
    pass


class SimAlreadyEndedError(SimEngineError):
    pass


class SimPausedError(SimEngineError):
    pass


class OwnedVarsOverlapError(SimEngineError):
    """两条及以上因果线声明的 `owned_vars` 存在重叠字段（2.3 节，
    `next_doc/world_simulator_event_driven_engine_and_full_architecture_
    plan.md`）——字段归属声明是"多条线各自独立发起调用、同时改同一个
    字段"数据竞争问题的前提条件，重叠时直接拒绝独立推进，不静默各打
    各的（那样谁的结果最终生效完全不可预期）。"""
