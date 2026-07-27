"""
steps/01_analyze_doc.py — python_step：分析本地文档，产出摘要/主题/搜索关键词

对应 next_doc/workflow_python_step_and_zhihu_publish_plan.md §D。

入口约定：run(ctx: PyStepContext) -> dict，返回值会被 runner 写到
output_file（doc_analysis.json），下游 step 通过 {analyze_doc.output} 占位符
或 ctx.input_json("analyze_doc") 读取。

[修复记录] doc_path 通过上游 `intake`（type: human_input, input_key:
doc_path）step 传入，而不是 step.params——python_step 的 params 字段是纯
字面量透传，workflow.yaml 里写 `params: {doc_path: "{doc_path}"}` 不会做
占位符替换，脚本会拿到字面量字符串 "{doc_path}" 而不是真实路径。

[优化] 除了在 doc_analysis.json 里返回 search_keywords 列表本身，这里额外
用 ctx.write_output() 把它单独落一份 search_keywords.json（纯 JSON 数组，
zhihu_search_with_login.py --keywords-file 要求的格式），并把这个文件的
绝对路径放进返回值的 keywords_file 字段。这样下游 search_zhihu 步骤可以
直接把 {analyze_doc.output} 里的 keywords_file 路径原样传给
--keywords-file，不用再让 agent 自己现算一遍 JSON 数组、手工写一份关键词
文件——省掉一次没必要的"agent 对话轮里现场造文件"操作，也避免它抄错/漏抄
关键词。
"""
from __future__ import annotations

from pathlib import Path


def run(ctx) -> dict:
    doc_path = ctx.input_output("intake", "").strip()
    if not doc_path:
        raise ValueError(
            "缺少 doc_path 参数：运行本 workflow 时需要传 "
            'run_workflow(inputs={"doc_path": "<本地文档绝对路径>"})，'
            "该值由上游 intake（human_input, input_key=doc_path）step 接收后"
            "透传给本步骤"
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

    # 单独落一份 zhihu_search_with_login.py --keywords-file 要求的纯 JSON
    # 数组文件，下游 search_zhihu 步骤直接引用这个路径，不需要自己再拼一份。
    keywords_path = ctx.write_output("search_keywords.json", result["search_keywords"])
    result["keywords_file"] = str(keywords_path)

    return result
