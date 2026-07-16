# prompts/fragments/compress.md
#
# Compact 机制主动化改进计划（compact_mechanism_improvement_plan.md）
# P0-A / P0-B 用到的固定文本片段。
# 格式: KEY: value（多行 value 用缩进表示）

# ── P0-A：目标相关性动态权重 ──────────────────────────────────────────────
# 由 goal_mode/runner.py::_do_compact() 在 cfg.compress.goal_aware_weighting_enabled
# 为 True 且存在未通过的验收标准时构建并传给 compact_with_skills(goal_hint=...)。
# compact_with_skills() 内部只是把这段文本追加到 compact_prompt 末尾，不改变
# 摘要本身的结构（仍然是 user/compact_history.md 定义的 markdown 分节），
# 只是提醒模型在压缩时对这些尚未达成的点保留更多细节，而不是一视同仁地压缩。
GOAL_AWARE_COMPACT_HINT_BLOCK: |
  ---
  [Goal-aware guidance] The following acceptance criteria are NOT YET satisfied.
  When writing the summary above (especially "Work Completed" / "Critical Findings" /
  "Current State"), keep enough concrete detail about anything related to these items
  — exact file paths, exact error messages, exact commands tried and their results —
  even if you would otherwise compress it away. Content clearly unrelated to these
  items can still be compressed normally.

  Unmet criteria:
  {unmet_criteria_lines}

# ── P0-B：Compact 兼做经验沉淀检查点 ──────────────────────────────────────
# 由 agent/compaction.py::compact_with_skills() 在
# cfg.compress.decision_extraction_on_compact_with_skills_enabled 为 True 时
# 追加到 compact_prompt 末尾，要求模型在正常摘要之后再附一段结构化 JSON。
# 复用 history/decision_extraction.py::parse_decision_response 的解析口径
# （字段名保持一致：topic / options_considered / chosen / rejected_because /
# related_entities），不新造一套格式。
DECISION_EXTRACTION_APPEND_BLOCK: |
  ---
  After the summary above, on a new line, append ONE more block delimited exactly like
  this (do not use markdown code fences, use these literal delimiters):

  ===DECISIONS_JSON===
  {{"decisions": [{{"topic": "...", "options_considered": ["..."], "chosen": "...", "rejected_because": {{"...": "..."}}, "related_entities": ["..."]}}]}}
  ===END_DECISIONS_JSON===

  `decisions` must be an empty array `[]` when this conversation segment does not
  contain a genuine trade-off between multiple concrete options — do not invent
  decisions just to fill the array. Only include an entry when the conversation
  actually weighed multiple approaches and settled on one. This JSON block will be
  stripped out programmatically and is NOT part of the visible summary — keep the
  summary above completely unaffected by this instruction.
