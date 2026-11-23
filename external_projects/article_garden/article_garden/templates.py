"""templates.py — 新建文章库时可选的章程模板。

模板只提供章程的起点，随后由你在设置页修改；其中的 scope.in 是“方向骨架”，不满意可全部改写。
任何模板都不会带来自动化放权（自动化开关在 settings.py，新库一律保守默认）。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "blank": {
        "label": "空白",
        "description": "只有保守默认值，范围、读者、术语都自己填。",
        "charter": {},
    },
    "general_tech": {
        "label": "通用技术",
        "description": "面向开发者的技术类文章：重概念讲解、对比与教程。",
        "charter": {
            "scope": {"in": [], "out": []},
            "audience": {"primary": "有一定编程基础的开发者", "levels": ["入门", "进阶", "专题"]},
            "style": {"tone": "准确、简洁", "length": [1500, 4000], "must_have": ["摘要", "小结", "延伸阅读"]},
            "sources": {"prefer": ["官方文档", "开源仓库 README 与 release notes", "论文"],
                        "avoid": ["营销内容"], "min_credibility": "B"},
        },
    },
    "ai": {
        "label": "人工智能（预置方向骨架）",
        "description": "中文 AI 领域：预置 10 个方向骨架与常见中英术语别名；方向可在设置页增删改名。",
        "charter": {
            "domain": "人工智能",
            "scope": {
                "in": ["基础概念与机器学习", "大语言模型", "提示词与上下文工程", "智能体（Agent）",
                       "RAG 与知识库", "训练与微调", "评测与基准", "工程落地与部署", "安全与治理", "行业应用"],
                "out": [],
            },
            "audience": {"primary": "对 AI 感兴趣的中文读者与开发者", "levels": ["入门", "进阶", "专题"]},
            "style": {"tone": "平实克制，不夸大", "length": [1500, 4000],
                      "must_have": ["摘要", "小结", "延伸阅读"], "ai_disclosure": True},
            "terms": {
                "智能体": ["Agent", "AI Agent"],
                "检索增强生成": ["RAG"],
                "大语言模型": ["LLM", "大模型"],
                "微调": ["fine-tuning", "Fine-tuning"],
                "提示词": ["Prompt", "prompt"],
                "上下文工程": ["Context Engineering"],
            },
            # 偏好顺序：论文与官方文档(A) > 开源仓库与技术博客(B) > 媒体(C) > 营销号(D，不用)
            "sources": {"prefer": ["论文", "官方文档", "开源仓库", "官方技术博客"],
                        "avoid": ["营销号", "未标注来源的转载"], "min_credibility": "B"},
            "risk": {"disclaimer": "", "forbidden_claims": ["已完全解决", "一定会取代"]},
        },
    },
}


def list_templates() -> List[Dict[str, str]]:
    return [{"id": k, "label": v["label"], "description": v["description"]} for k, v in TEMPLATES.items()]


def template_charter(template_id: str) -> Dict[str, Any]:
    if template_id not in TEMPLATES:
        raise KeyError(f"未知模板：{template_id}（可选：{', '.join(TEMPLATES)}）")
    return copy.deepcopy(TEMPLATES[template_id]["charter"])
