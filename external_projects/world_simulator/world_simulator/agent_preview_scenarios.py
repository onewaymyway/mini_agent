"""world_simulator/agent_preview_scenarios.py — Agent Preview 用的内置
通用测试情境（4.5 节第二批，`next_doc/world_simulator_agent_preview_
and_adaptive_policy_plan.md` 4.2 节）。

固定维护，不做成用户可编辑的配置——测试情境需要覆盖"可逆 vs 不可逆"
"高风险 vs 低风险"等有代表性的维度组合，让画像编辑者随便乱改测试
情境反而会削弱这个功能的参照价值（详见子方案 4.2 节）。

每个情境与具体模拟实例、具体模板无关，选项本身带 4.1 落地的
`risk_level`/`reversibility` 字段，这样情境化策略（4.5 节第一步，
已在 `autopilot.conditional_policies` 落地）才有实际的匹配对象可以
在 Preview 里验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class PreviewOption:
    id: str
    label: str
    description: str
    risk_level: str  # low / medium / high
    reversibility: str  # reversible / hard_to_reverse / irreversible


@dataclass
class PreviewScenario:
    id: str
    title: str
    description: str
    options: List[PreviewOption] = field(default_factory=list)

    def to_prompt_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "options": [
                {
                    "id": o.id,
                    "label": o.label,
                    "description": o.description,
                    "risk_level": o.risk_level,
                    "reversibility": o.reversibility,
                }
                for o in self.options
            ],
        }


BUILTIN_SCENARIOS: List[PreviewScenario] = [
    PreviewScenario(
        id="sudden_unemployment",
        title="突然失业",
        description=(
            "你突然失业，有一笔够撑 3 个月生活的存款。面前有两个选项。"
        ),
        options=[
            PreviewOption(
                id="quick_job", label="立刻找一份不太满意但能马上上岗的工作",
                description="现金流马上稳定，但工作内容和长期发展方向不一定合适。",
                risk_level="low", reversibility="reversible",
            ),
            PreviewOption(
                id="wait_for_fit", label="花 1 个月准备再找更合适的工作",
                description="短期内没有收入，但更可能找到长期匹配度更高的工作。",
                risk_level="medium", reversibility="reversible",
            ),
        ],
    ),
    PreviewScenario(
        id="startup_equity_offer",
        title="创业公司股权邀约",
        description=(
            "你在一家稳定的大公司工作，一个朋友邀请你加入他刚起步的创业"
            "公司，用一部分股权代替一部分现金薪资。"
        ),
        options=[
            PreviewOption(
                id="stay", label="留在原公司，拒绝邀约",
                description="收入和职业路径都保持稳定、可预期。",
                risk_level="low", reversibility="reversible",
            ),
            PreviewOption(
                id="join_full", label="辞职全职加入，接受较低的现金薪资换股权",
                description=(
                    "潜在收益更高，但公司可能失败，且辞职后短期内很难无成本"
                    "回到原来的工作轨道。"
                ),
                risk_level="high", reversibility="hard_to_reverse",
            ),
        ],
    ),
    PreviewScenario(
        id="health_symptom_ignored",
        title="持续但不严重的身体不适",
        description=(
            "你最近一直有些轻微但持续的身体不适，去做一次全面检查需要"
            "花费不少时间和金钱，但目前症状还不影响正常生活。"
        ),
        options=[
            PreviewOption(
                id="check_now", label="现在就花时间和钱做全面检查",
                description="能尽早排除潜在问题，但当下会打乱工作和生活安排。",
                risk_level="low", reversibility="reversible",
            ),
            PreviewOption(
                id="wait_and_see", label="先观察一段时间，症状加重再说",
                description="节省当下的时间和金钱，但如果情况恶化，处理成本会更高。",
                risk_level="medium", reversibility="irreversible",
            ),
        ],
    ),
    PreviewScenario(
        id="major_purchase_on_credit",
        title="大额消费决策",
        description=(
            "你想买一件明显超出当前预算、需要分期付款很多期的大件物品，"
            "不是生活必需，但你确实很想要。"
        ),
        options=[
            PreviewOption(
                id="buy_now", label="现在就用分期付款买下来",
                description="立刻获得满足感，但会长期占用一部分现金流。",
                risk_level="high", reversibility="hard_to_reverse",
            ),
            PreviewOption(
                id="save_first", label="先存钱，攒够全款或大部分再买",
                description="财务上更稳健，但要等待更久才能获得这件东西。",
                risk_level="low", reversibility="reversible",
            ),
        ],
    ),
]
