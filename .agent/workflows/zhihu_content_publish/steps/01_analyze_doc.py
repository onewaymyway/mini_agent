"""
steps/01_analyze_doc.py — python_step：分析本地文档，产出摘要/主题/搜索关键词

对应 next_doc/workflow_python_step_and_zhihu_publish_plan.md §D。

入口约定：run(ctx: PyStepContext) -> dict，返回值会被 runner 写到
output_file（doc_analysis.json），下游 step 通过 {analyze_doc.output} 占位符
或 ctx.input_json("analyze_doc") 读取。
"""
from __future__ import annotations

from pathlib import Path


def run(ctx) -> dict:
    doc_path = ctx.params.get("doc_path")
    if not doc_path:
        raise ValueError(
            "缺少 doc_path 参数：运行本 workflow 时需要传 "
            'run_workflow(inputs={"doc_path": "<本地文档绝对路径>"})'
        )

    p = Path(doc_path)
    if not p.exists():
        raise FileNotFoundError(f"文档不存在：{doc_path}")

    text = p.read_text(encoding="utf-8", errors="ignore")
    # 单次 LLM 调用即可完成，不需要像过滤步骤那样批量——这里只有一份文档。
    prompt_tmpl = ctx.load_prompt_file("prompts/01_analyze_doc.md")
    result = ctx.llm.ask_json(
        prompt_tmpl.format(doc_text=text[:8000]),
        schema_hint='{"summary": "...", "topic": "...", "search_keywords": ["...", "..."]}',
        max_retries=3,
    )

    result.setdefault("summary", "")
    result.setdefault("topic", "")
    result.setdefault("search_keywords", [])
    result["source_doc_path"] = str(p.resolve())
    return result
