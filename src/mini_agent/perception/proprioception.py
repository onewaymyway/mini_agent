"""
perception/proprioception.py — 本体感知模块（具身改进 v3 B1）

具身来源：本体感知（proprioception）——生物体不靠"看"就能感受自身姿态、
肌肉张力、疲劳程度。当前 Agent 对自身状态的认知完全是外部的、被动的：
token 超阈值才压缩，max_turns 到了才停，连续失败了也没有内部信号——
直到某个外部规则真正触发才"知道"。

ProprioceptionModule 提供一个轻量、零 LLM 调用的快照接口：每轮调用一次
sense()，得到当前的认知负荷 / 不确定性 / 风险感知 / 剩余预算 / 挫败感，
供 agent.py 决定是否需要调整行为（比如主动压缩历史、注入元认知提示、
或者在连续失败时停下来汇报困境而不是盲目重试）。

设计取舍：
  - 每个 Agent / SessionAgent 实例持有独立的 ProprioceptionModule（不是
    全局单例），因为多用户架构下每个 session 的"内部状态"是互相独立的。
  - sense() 是纯函数式快照（O(1)，不做 LLM 调用、不读盘），调用方负责
    把当前 turn 已经算好的数据（token 占比、最近工具名、assistant 文本）
    传进来，模块本身不反射 Agent 内部属性——这样既避免了对 Agent 内部
    结构的强耦合，也让单元测试不需要构造一个真实 Agent。
  - frustration 是模块内部唯一需要跨调用累积的状态，用简单的指数衰减
    （失败 +0.2，封顶 1.0；成功 ×0.5）而不是滑动窗口，足够表达"挫败感
    会累积、但一次成功就能明显缓解"这个直觉，且实现和理解成本最低。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

# 涉及写操作 / 不可逆操作的工具集合，用于估算 risk_perception。
# 与 tool_executor.py 的 _DEDUP_TOOLS（幂等只读工具）互补但不复用同一常量，
# 因为两者语义不同（"可去重的只读工具" vs "风险工具"），合并会引入隐性耦合。
_RISKY_TOOLS = frozenset({
    "write_file", "create_file", "patch_file", "delete_file",
    "str_replace_editor", "str_replace", "bash",
})

# 不确定性词频检测（中英文常见的犹豫/猜测表达）
_UNCERTAINTY_WORDS = (
    "不确定", "可能", "也许", "应该是", "我猜", "猜测", "不太清楚",
    "unclear", "might", "maybe", "possibly", "uncertain", "i think",
    "not sure", "i guess",
)

# 单次失败对 frustration 的增量；成功后的衰减系数
_FRUSTRATION_INCREMENT = 0.2
_FRUSTRATION_DECAY_ON_SUCCESS = 0.5


@dataclass(frozen=True)
class AgentInternalState:
    """某一时刻的本体感知快照。所有字段归一化到 [0, 1]。"""

    cognitive_load: float = 0.0        # 当前 context 占用比例（含压缩压力）
    uncertainty: float = 0.0           # 基于最近 assistant 文本的不确定性词频
    risk_perception: float = 0.0       # 最近工具调用中"危险/不可逆操作"的比例
    energy_budget_ratio: float = 1.0   # 剩余 turn 预算比例（1.0 = 刚开始，0.0 = 用尽）
    frustration: float = 0.0           # 连续失败的指数衰减累积

    def to_dict(self) -> dict:
        return {
            "cognitive_load": round(self.cognitive_load, 3),
            "uncertainty": round(self.uncertainty, 3),
            "risk_perception": round(self.risk_perception, 3),
            "energy_budget_ratio": round(self.energy_budget_ratio, 3),
            "frustration": round(self.frustration, 3),
        }


@dataclass
class ProprioceptionModule:
    """
    每个 Agent/SessionAgent 实例持有一个独立实例。

    用法（典型挂载点：agent.py 的 _agentic_loop()）：
        state = self._proprioception.sense(
            cognitive_load_ratio=_budget_pct,
            recent_tool_names=[tc.name for tc in response.tool_calls],
            assistant_text=response.text,
            turns_used=loop_count,
            max_turns=self.cfg.max_turns,
        )
        ... 工具执行后 ...
        for ok in tool_outcomes:
            self._proprioception.record_tool_outcome(ok)
    """

    _frustration: float = field(default=0.0, init=False)
    _consecutive_failures: int = field(default=0, init=False)

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def sense(
        self,
        *,
        cognitive_load_ratio: float = 0.0,
        recent_tool_names: Optional[Sequence[str]] = None,
        assistant_text: str = "",
        turns_used: int = 0,
        max_turns: int = 1,
    ) -> AgentInternalState:
        """O(1) 快照，不做 LLM 调用。每轮调用一次。"""
        return AgentInternalState(
            cognitive_load=_clamp(cognitive_load_ratio),
            uncertainty=self._calc_uncertainty(assistant_text),
            risk_perception=self._calc_risk(recent_tool_names or []),
            energy_budget_ratio=_clamp(1.0 - turns_used / max(max_turns, 1)),
            frustration=self._frustration,
        )

    def record_tool_outcome(self, success: bool) -> None:
        """工具执行后调用，更新内部挫败感状态。"""
        if success:
            self._consecutive_failures = 0
            self._frustration *= _FRUSTRATION_DECAY_ON_SUCCESS
        else:
            self._consecutive_failures += 1
            self._frustration = min(1.0, self._frustration + _FRUSTRATION_INCREMENT)

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset(self) -> None:
        """新 session / 显式重置时调用，清空挫败感累积。"""
        self._frustration = 0.0
        self._consecutive_failures = 0

    # ── 内部计算 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_uncertainty(text: str) -> float:
        if not text:
            return 0.0
        lowered = text.lower()
        count = sum(1 for w in _UNCERTAINTY_WORDS if w in lowered)
        return _clamp(count * 0.15)

    @staticmethod
    def _calc_risk(recent_tool_names: Sequence[str]) -> float:
        if not recent_tool_names:
            return 0.0
        risky = sum(1 for t in recent_tool_names if t in _RISKY_TOOLS)
        return _clamp(risky * 0.25)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
