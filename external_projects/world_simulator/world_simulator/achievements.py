"""world_simulator/achievements.py — 游戏化视图的成就徽章计算（阶段七）。

设计依据：`world_simulator_external_project_plan.md` 第 5 节页面 6
「游戏化视图」——"把同一份 `state_history` 按'章节'渲染……纯前端呈现层，
不需要新的数据结构"。本模块同样不引入新的持久化结构，只是从既有的
`SimManifest` + `SimState` 历史列表里*派生*出一组"成就是否已解锁"的
只读判断，供 `app.py` 的游戏化视图渲染成徽章；不落盘、不影响引擎语义，
纯粹是展示层的计算逻辑，因此单独成一个无状态的小模块，便于不依赖
Streamlit 单独做单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from world_simulator.state_model import SimManifest, SimState


@dataclass
class Achievement:
    id: str
    label: str
    description: str
    unlocked: bool


def compute_achievements(manifest: SimManifest, history: List[SimState]) -> List[Achievement]:
    """根据实例 manifest + 主时间线历史，计算一组成就徽章。

    刻意只用"已经存在的字段"（`step`/`chosen_by`/`major_decision`/
    `manifest.status`/`manifest.pilot_mode`）做判断，不要求调用方传入
    分支列表等额外信息——分支相关的成就（"开启平行时空"）由 `app.py`
    在调用处按需追加 `extra` 判断位，见 `unlocked_forked` 参数。
    """

    steps_reached = history[-1].step if history else 0
    autopilot_used = any(s.chosen_by == "autopilot" for s in history)
    faced_major_decision = any(s.major_decision for s in history)

    return [
        Achievement(
            id="first_step",
            label="启程",
            description="完成第一步推进，故事正式展开。",
            unlocked=steps_reached >= 1,
        ),
        Achievement(
            id="five_steps",
            label="崭露头绪",
            description="推进到第 5 步，时间线开始有了脉络。",
            unlocked=steps_reached >= 5,
        ),
        Achievement(
            id="ten_steps",
            label="长篇在望",
            description="推进到第 10 步，这已经是一段完整的故事了。",
            unlocked=steps_reached >= 10,
        ),
        Achievement(
            id="major_decision",
            label="命运转折",
            description="经历过一次被判定为「重大决策」的节点。",
            unlocked=faced_major_decision,
        ),
        Achievement(
            id="autopilot",
            label="放手托管",
            description="至少有一步是交给自动挡代理决定的。",
            unlocked=autopilot_used,
        ),
        Achievement(
            id="ended",
            label="落幕",
            description="这段模拟已经被标记为「已结束」。",
            unlocked=manifest.status == "ended",
        ),
    ]


def achievement_progress(achievements: List[Achievement]) -> Dict[str, Any]:
    unlocked = [a for a in achievements if a.unlocked]
    return {
        "unlocked_count": len(unlocked),
        "total_count": len(achievements),
        "ratio": (len(unlocked) / len(achievements)) if achievements else 0.0,
    }
