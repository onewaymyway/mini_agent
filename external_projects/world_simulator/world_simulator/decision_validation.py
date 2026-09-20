"""world_simulator/decision_validation.py — 引擎侧"决策校验层"入口
（阶段三十三 4.12 节第一步"校验前移"，`next_doc/
world_simulator_potential_causal_space_and_decision_engine_plan.md`）。

**背景**：4.3/4.5/4.9/4.11 落地时，"LLM 输出建议值 + engine 做一次
结构性校验/归一化"这个方向已经在多处分别实现——`risk_level`/
`urgency`/`action_type` 的归一化散落在 `state_model.py::ChoiceOption.
from_dict()` 里，`engine/option_heuristics.py` 单独放着"宏观事件
重合"/"指标调节语言"两条启发式警示。这些判断本身没有问题，只是
分散在不同文件，随着后续（4.12 第二、三步）继续加规则会越来越难
找。本模块不重写任何判断逻辑本身（避免无谓地引入行为差异/新
bug），只做**收敛**：

- `normalize_risk_level()` / `normalize_urgency()` / `normalize_
  action_type()`：把原来写在 `ChoiceOption.from_dict()` 里的三段
  内联归一化逻辑原样搬到这里，`state_model.py` 改为调用这三个
  函数——数据结构文件只管"字段是什么形状"，"怎么把一个不受信任的
  原始值归一化成合法取值"这件事收敛到这个校验层。
- `compute_option_warnings()`：直接复用 `engine/option_heuristics.
  py`（阶段三十三第三批已经实现的宏观重合/指标调节两条弱信号
  校验，第五轮方案 5.6 节新增的"高度相似选项"检测同样收敛在这里，
  不重复实现），不重复实现，只是把 `engine/advance.py` 调用的入口从
  `engine.option_heuristics` 改成这里——`engine/option_heuristics.
  py` 本身连同它的具体检测函数保持不变（`tests/test_option_
  heuristics.py` 仍直接测试它们的细节实现），本模块只是在它上面
  加一层"决策校验统一入口"的外壳，方便未来继续往这里加新规则时，
  `engine/advance.py` 只需要认识这一个模块。

**刻意不做的部分**：这一步不新增任何"新的校验规则"，也不改变任何
一条既有规则的判断结果——单纯是"同一批已经存在的逻辑，换一个更
集中的收纳位置"，属于纯重构，行为应该和重构前完全一致（由既有的
`tests/test_state_and_store.py`/`tests/test_option_heuristics.py`/
`tests/test_autopilot.py` 等测试兜底验证）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# 延迟到函数体内部导入 `engine.option_heuristics`（而不是模块顶层），
# 避免循环导入：`state_model.py` 在模块顶层导入本模块的归一化函数，
# 而 `option_heuristics.py` 又在模块顶层导入 `state_model.ChoiceOption`
# ——如果本模块在顶层导入 `option_heuristics`，会在 `state_model.py`
# 尚未初始化完成时触发 `state_model → decision_validation →
# option_heuristics → state_model` 的循环导入。

_VALID_RISK_LEVEL = ("low", "medium", "high")
_VALID_URGENCY = ("low", "medium", "high", "critical")
_VALID_ACTION_TYPE = ("single", "combo", "conditional")


def normalize_risk_level(raw: Any) -> Optional[str]:
    """归一化 `ChoiceOption.risk_level`：`None` 表示未声明（不伪造
    默认值），非空但不认识的取值统一退化为 `"medium"`（原逻辑，从
    `state_model.py::ChoiceOption.from_dict()` 搬迁而来，行为不变）。
    """
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value not in _VALID_RISK_LEVEL:
        value = "medium"
    return value


def normalize_urgency(raw: Any) -> Optional[str]:
    """归一化 `ChoiceOption.urgency`（4.3 节）：规则同
    `normalize_risk_level()`，取值集合多了 `"critical"`。`None` 表示
    未声明；不认识的非空取值退化为 `"medium"`（原逻辑搬迁，行为
    不变）。
    """
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value not in _VALID_URGENCY:
        value = "medium"
    return value


def normalize_action_type(raw: Any) -> str:
    """归一化 `ChoiceOption.action_type`（4.7 节）：没有"未声明"这个
    中间态，缺省或不认识的取值一律退化为 `"single"`（原逻辑搬迁，
    行为不变）。
    """
    value = str(raw or "single").strip().lower()
    if value not in _VALID_ACTION_TYPE:
        value = "single"
    return value


def compute_option_warnings(options: Sequence[Any], key_drivers: Sequence[str]) -> List[Dict[str, str]]:
    """引擎侧决策校验层的统一入口：`engine/advance.py` 从这里调用，
    而不是直接依赖 `engine/option_heuristics.py`（4.4/4.5 节的辅助
    校验），便于后续继续往"决策校验层"里加新规则时只改这一个模块的
    调用方。具体检测逻辑仍然在 `option_heuristics.py`，本函数只是
    转发，不重复实现。
    """
    from world_simulator.engine.option_heuristics import compute_option_warnings as _compute_option_warnings

    return _compute_option_warnings(options, key_drivers)
