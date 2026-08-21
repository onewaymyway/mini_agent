"""
doc-template-generation / members / standard_report / script.py

统一接口: run(input: dict) -> dict
input 约定: {
    "text": "...",
    "target": {"template_name": "standard_report"},
    "content": {
        "title": "报告标题",
        "body_sections": [{"heading": "小节标题", "text": "小节正文"}, ...],
    },
}
返回: {"status": "success"|"fail",
       "data": {"document": {"format": "markdown", "sections": [...]}} | None,
       "error": str | None}

与 browser-site-scraper 的 baidu/zhihu member 不同，本 member 是纯逻辑实现，
不依赖任何底层原语 skill（不需要 browser-core 或类似的东西）——用来验证方案
文档第 5 节"member 统一接口规范"本身并不强制要求 member 依赖某个外部能力，
探索蒸馏出的 member 依赖 tool_runtime 只是 browser-site-scraper 这一个领域
的特点，不是引擎的通用要求。
"""

from __future__ import annotations


def run(input: dict) -> dict:
    content = input.get("content")
    if not isinstance(content, dict):
        return {"status": "fail", "data": None, "error": "缺少 content 参数（需要 dict）"}

    title = content.get("title")
    body_sections = content.get("body_sections")
    if not title:
        return {"status": "fail", "data": None, "error": "content.title 不能为空"}
    if not isinstance(body_sections, list) or not body_sections:
        return {"status": "fail", "data": None, "error": "content.body_sections 需要是非空数组"}

    sections = [{"heading": "标题", "text": title}]
    for i, sec in enumerate(body_sections):
        if not isinstance(sec, dict) or not sec.get("heading"):
            return {
                "status": "fail",
                "data": None,
                "error": f"content.body_sections[{i}] 缺少 heading 字段",
            }
        sections.append({"heading": sec["heading"], "text": sec.get("text", "")})

    return {
        "status": "success",
        "data": {"document": {"format": "markdown", "sections": sections}},
        "error": None,
    }
