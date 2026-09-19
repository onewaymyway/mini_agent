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
