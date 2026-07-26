"""
steps/03_filter.py — python_step：批量 LLM 判断哪些候选问题符合发布要求

对应 next_doc/workflow_python_step_and_zhihu_publish_plan.md §C
（批量过滤：一次 LLM 调用处理多条数据）。

设计要点（都是这次改进要求里明确提到的）：
  - 不用规则筛选，判断标准完全交给 prompts/03_filter_batch.md 里描述的
    LLM 判断（相关性/是否值得回答/是否有效提问）。
  - 效率：按 BATCH_SIZE 分批，一次 ask_json 调用处理一批（而不是每条
    问题单独调一次 LLM），显著减少调用次数和重复的 system prompt token。
  - 漏判保护：如果某一批返回的 decisions 数量明显少于输入数量（模型输出
    被截断/遗漏），把这一批打散成更小的子批重试，而不是直接丢弃漏判的
    问题、也不是简单粗暴地整体重试一次了事。
"""
from __future__ import annotations

import json

BATCH_SIZE = 15
# 一批 decisions 数量少于 (1 - MISS_RATIO_THRESHOLD) * 输入数量时，
# 判定为"漏判过多"，触发子批重试而不是直接采信这批结果。
MISS_RATIO_THRESHOLD = 0.2
MIN_SUB_BATCH = 3


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _judge_batch(ctx, prompt_tmpl, doc_summary_json, batch: list) -> dict:
    """对一批候选问题调用一次 ask_json，返回 {id: decision_dict}。"""
    prompt = prompt_tmpl.format(
        doc_summary=doc_summary_json,
        questions_json=json.dumps(batch, ensure_ascii=False, indent=2),
        batch_size=len(batch),
    )
    result = ctx.llm.ask_json(
        prompt,
        schema_hint='{"decisions": [{"id": "...", "keep": true, "reason": "..."}]}',
        max_retries=3,
    )
    decisions = result.get("decisions", [])
    return {d.get("id"): d for d in decisions if d.get("id")}


def _judge_with_retry(ctx, prompt_tmpl, doc_summary_json, batch: list) -> dict:
    """对一批做判断，如果漏判过多就拆成更小的子批重试，避免因为模型
    输出被截断而把没判到的问题误当成"不符合要求"直接丢弃。"""
    decisions = _judge_batch(ctx, prompt_tmpl, doc_summary_json, batch)
    missing = [q for q in batch if q.get("id") not in decisions]
    if not missing:
        return decisions
    miss_ratio = len(missing) / max(1, len(batch))
    if miss_ratio <= MISS_RATIO_THRESHOLD or len(batch) <= MIN_SUB_BATCH:
        # 漏判比例可接受，或者已经小到不能再拆了，就此打住（缺失的问题
        # 会在调用方那里被视为"信息不足，按不符合要求处理"，符合
        # prompts/03_filter_batch.md 里"宁可漏选不要武断选入"的判断标准）。
        return decisions
    sub_size = max(MIN_SUB_BATCH, len(missing) // 2)
    for sub_batch in _chunk(missing, sub_size):
        decisions.update(_judge_with_retry(ctx, prompt_tmpl, doc_summary_json, sub_batch))
    return decisions


def run(ctx) -> dict:
    doc_analysis = ctx.input_json("analyze_doc", {})
    search_result = ctx.input_json("search_zhihu", {})
    candidates = search_result.get("questions", [])

    if not candidates:
        return {"kept_questions": [], "total_input": 0, "total_kept": 0, "note": "search_zhihu 没有产出候选问题"}

    prompt_tmpl = ctx.load_prompt_file("prompts/03_filter_batch.md")
    doc_summary_json = json.dumps(
        {"summary": doc_analysis.get("summary", ""), "topic": doc_analysis.get("topic", "")},
        ensure_ascii=False,
    )

    all_decisions: dict = {}
    for batch in _chunk(candidates, BATCH_SIZE):
        all_decisions.update(_judge_with_retry(ctx, prompt_tmpl, doc_summary_json, batch))

    kept = []
    for q in candidates:
        d = all_decisions.get(q.get("id"))
        if d and d.get("keep"):
            kept.append({**q, "filter_reason": d.get("reason", "")})

    result = {
        "kept_questions": kept,
        "total_input": len(candidates),
        "total_kept": len(kept),
        "total_judged": len(all_decisions),
    }
    # 中间产物（含每条的 keep/reason，包括被过滤掉的）单独落一份，便于排查
    # 筛选结果是否合理，不占用主输出 output_file 的篇幅。
    ctx.write_output("filter_decisions_debug.json", all_decisions)
    return result
