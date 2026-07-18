# prompts/user/lightweight_extraction_request.md
#
# 发送给模型，请求对一段独立触发的候选窗口（不是完整的被压缩历史）做
# 结构化抽取（HistoryManager.maybe_trigger_extraction()，E1 §1.2.2）。
#
# 输出格式由 system/lightweight_extractor.md 规定。

Please carefully read through the conversation segment above, specifically
looking for trade-offs, entities, and factual statements, and fully populate
the `decisions`, `entities`, and `facts` arrays described in the system
prompt. This segment was flagged by a lightweight heuristic (connective-word
density or a minimum number of turns) as likely to contain content worth
extracting — but only extract what is actually there; do not fabricate
content to fill the arrays.

Leave `compact_summary` as the empty string `""` — this call does not need a
summary, only the structured arrays.

Remember: your entire response must be the single JSON object itself — no
markdown fences, no extra text.
