"""world_simulator/anatomy_templates.py — 元素剖面的"槽位提示"模板（第二十四轮 A1）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.2.3。

## 这个模块做什么

按 `element_type` 给出"该关注哪些槽位"的**提示**：构成/指标/瓶颈/里程碑各自典型是什么类型的东西。
后续阶段用它做三件事：创建期拆解与联网研究的提示词（A2/A3）、深度调用提示词（A5）、界面默认展示哪些区块（A1 起）。

## 刻意不做

- **不带任何领域事实或默认数值**（计划 §3.2 第 3 条，延续 `tech_params`"占位值不是领域事实"的原则）：
  下表里全是"类型名称"，没有一个数字。
- **不限制 `element_type`**：它是自由文本（第二十三轮），不认识的类型一律用 `default` 模板。
- 模板不参与校验：`anatomy.normalize_anatomy` 不会因为某个槽位"不在模板里"而丢弃内容。

纯数据 + 查询函数，不读写磁盘、不调 LLM；不依赖其它模块（避免循环导入）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

DEFAULT_TEMPLATE = "default"

# 槽位顺序即界面默认展示顺序。
SLOTS: Tuple[str, ...] = (
    "components", "metrics", "bottlenecks", "approaches",
    "adoption_gates", "milestones", "assumptions", "signals",
)

SLOT_LABELS: Dict[str, str] = {
    "components": "构成 / 子系统",
    "metrics": "关键指标",
    "bottlenecks": "瓶颈",
    "approaches": "竞争路线",
    "adoption_gates": "采用门槛",
    "milestones": "里程碑",
    "assumptions": "关键假设",
    "signals": "先行信号",
}

# 每个模板：`label`（展示名）与四个"典型是什么"的提示（计划 §5.2.3 的表格）。
# 只有这四列随类型变化；`approaches`/`adoption_gates`/`assumptions`/`signals` 各类型通用。
_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "technology": {
        "label": "技术",
        "components": ["材料", "算法", "工艺", "依赖设施"],
        "metrics": ["性能指标", "成本", "良率", "寿命"],
        "bottlenecks": ["物理", "工艺", "供应链", "监管"],
        "milestones": ["实验室验证", "中试", "量产", "规模化"],
    },
    "project": {
        "label": "项目",
        "components": ["工作包", "团队", "交付物"],
        "metrics": ["进度", "预算消耗", "关键交付完成数"],
        "bottlenecks": ["资金", "人才", "审批", "外部依赖"],
        "milestones": ["各验收判据"],
    },
    "organization": {
        "label": "组织",
        "components": ["业务线", "产品", "团队"],
        "metrics": ["营收", "现金", "市占", "人员"],
        "bottlenecks": ["现金", "监管", "人才", "竞争"],
        "milestones": ["融资", "上市", "盈利"],
    },
    "person": {
        "label": "人物",
        "components": ["技能", "职位", "关系网", "资源"],
        "metrics": ["能力值", "影响力", "资源量"],
        "bottlenecks": ["时间", "健康", "机会", "约束"],
        "milestones": ["职业节点", "事件节点"],
    },
    "asset": {
        "label": "资产",
        "components": ["构成", "持有人结构"],
        "metrics": ["价格", "波动", "估值倍数", "供需"],
        "bottlenecks": ["流动性", "下行风险"],
        "milestones": ["关键价位", "事件"],
    },
    "policy": {
        "label": "政策",
        "components": ["条款", "执行机构"],
        "metrics": ["覆盖率", "强度", "合规率"],
        "bottlenecks": ["立法程序", "反对力量", "执行能力"],
        "milestones": ["提案", "审议", "颁布", "生效", "执行"],
    },
    "market": {
        "label": "市场",
        "components": ["细分", "玩家", "渠道"],
        "metrics": ["规模", "增速", "集中度", "价格"],
        "bottlenecks": ["准入", "需求", "供给"],
        "milestones": ["渗透率节点"],
    },
    "resource": {
        "label": "资源",
        "components": ["来源", "渠道"],
        "metrics": ["储量", "产能", "价格", "可得性"],
        "bottlenecks": ["开采", "运输", "地缘"],
        "milestones": ["产能节点"],
    },
    "event_series": {
        "label": "事件序列",
        "components": ["触发源", "传导渠道"],
        "metrics": ["频率", "强度分布"],
        "bottlenecks": [],
        "milestones": ["与事件先验（event_sampler）对接"],
    },
    DEFAULT_TEMPLATE: {
        "label": "通用",
        "components": ["构成"],
        "metrics": ["关键数值"],
        "bottlenecks": ["阻碍"],
        "milestones": ["阶段性节点"],
    },
}


def template_name(element_type: Any) -> str:
    """`element_type` → 模板名；大小写与首尾空白不敏感，不认识的返回 `default`。"""
    text = str(element_type or "").strip().lower()
    return text if text in _TEMPLATES and text != DEFAULT_TEMPLATE else DEFAULT_TEMPLATE


def get_template(element_type: Any) -> Dict[str, Any]:
    """返回模板的**拷贝**（调用方可随意改）。`name` 是实际命中的模板名。"""
    name = template_name(element_type)
    tpl = _TEMPLATES[name]
    out: Dict[str, Any] = {"name": name, "label": tpl["label"]}
    for key in ("components", "metrics", "bottlenecks", "milestones"):
        out[key] = list(tpl[key])
    return out


def known_templates() -> List[str]:
    """已有的模板名（含 `default`），顺序固定。"""
    return list(_TEMPLATES)


def slot_hint(element_type: Any) -> str:
    """一行槽位提示，给后续阶段的提示词用（A1 只提供，不接入任何提示词）。"""
    tpl = get_template(element_type)
    parts: List[str] = []
    for key in ("components", "metrics", "bottlenecks", "milestones"):
        items = tpl[key]
        if items:
            parts.append(f"{SLOT_LABELS[key]}（如：{'、'.join(items)}）")
    return f"{tpl['label']}类元素建议关注：" + "；".join(parts)
