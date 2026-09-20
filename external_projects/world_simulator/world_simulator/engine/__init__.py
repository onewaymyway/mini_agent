"""world_simulator/engine — 核心推演循环（阶段三十拆分为包）。

设计依据：`world_simulator_external_project_plan.md` 第 6 节；拆分
依据 `next_doc/world_simulator_universal_simulator_gap_analysis_and_
roadmap_v2_plan.md` 4.18 节剩余部分（阶段二十八只落地了存储层追加
写，`engine.py` 拆分留到本阶段单独评估/实施）。

`create_simulation()` = `spec_generator.generate_scenario()` + 落盘为
step 0 的初始状态；`advance()` = 一次 `advance_step` workflow 调用，
输出 next_state（结构化）+ 叙事文本 + 候选分支选项。

暂停/恢复只是"要不要继续调用引擎推进"的控制位（`manifest.status`），
状态本身每步都已经落盘，天然可恢复——本模块不需要为"恢复"实现任何
额外逻辑，只需要在 `advance()` 前检查 `status`。

**拆分说明**：原来 1143 行的单体 `engine.py` 按职责拆成本包下的
多个子模块（`errors.py`/`ids.py`/`knowledge.py`/`resource_guard.py`/
`background_entities.py`/`materialize.py`/`structural_change.py`/
`causal_lines.py`/`advance.py`/`management.py`），纯内部重组：
- 每个子模块内部函数的实现逐行保持不变，只是换了文件位置；
- 对外公开的函数/异常名字、签名、行为完全不变，全部在这个
  `__init__.py` 里重新导出，`from world_simulator.engine import
  advance`/`import world_simulator.engine as engine_mod` 两种既有的
  导入写法都继续可用，调用方（`app.py`/`autopilot.py`/`hypothesis.py`/
  所有测试/entrypoints）不需要任何改动；
- 不改变任何持久化格式，回归测试（拆分前 198 个用例）是唯一验收
  标准，见 `PROJECT.md` 阶段三十。

`advance_independent.py`（阶段三十六第三批，`next_doc/world_
simulator_event_driven_engine_and_full_architecture_plan.md` 2.3
节）是后续新增的一个独立子模块，不属于上面这次拆分——它是与
`advance()` 长期共存的另一条推进路径（多尺度因果线真正独立推进），
不是原 `engine.py` 拆出来的既有逻辑，见该模块自己的 docstring。
"""

from __future__ import annotations

# 异常类型
from world_simulator.engine.errors import (
    SimAlreadyEndedError,
    SimEngineError,
    SimPausedError,
)

# 实例创建 / 落盘
from world_simulator.engine.materialize import create_simulation, materialize_simulation

# 核心推进循环
from world_simulator.engine.advance import FastForwardResult, advance, fast_forward

# 多尺度因果线真正独立推进（第三批，2.3 节）
from world_simulator.engine.advance_independent import advance_lines
from world_simulator.engine.errors import OwnedVarsOverlapError

# 结构性变化的解析/采纳 + 因果线建议（阶段二十九，4.26 节）
from world_simulator.engine.structural_change import (
    accept_suggested_causal_line,
    apply_structural_change,
    reject_suggested_causal_line,
)

# 实例管理：状态 / 自动挡配置 / 设置 / 重命名 / 删除 / 查询
from world_simulator.engine.management import (
    delete_simulation,
    get_simulation,
    list_simulations,
    rename_simulation,
    set_pilot_config,
    set_status,
    update_settings,
)

__all__ = [
    "SimEngineError",
    "SimAlreadyEndedError",
    "SimPausedError",
    "OwnedVarsOverlapError",
    "materialize_simulation",
    "create_simulation",
    "advance",
    "advance_lines",
    "fast_forward",
    "FastForwardResult",
    "apply_structural_change",
    "accept_suggested_causal_line",
    "reject_suggested_causal_line",
    "set_status",
    "set_pilot_config",
    "update_settings",
    "rename_simulation",
    "delete_simulation",
    "get_simulation",
    "list_simulations",
]
