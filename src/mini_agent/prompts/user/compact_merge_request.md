# prompts/user/compact_merge_request.md
#
# 用于 chunked compact 的最终合并步骤：把多个 chunk 摘要合并为一个统一摘要

Below are {{ total_chunks }} sequential chunk summaries from a long conversation.
Please merge them into a single coherent summary with the following structure:

## Goal
The overall objective(s) the user was pursuing.

## Key Decisions
Technical choices and reasoning (consolidated across all chunks, no duplicates).

## Work Completed
All significant work done, in chronological order:
- Files created / modified / deleted (exact paths + one-line description)
- Commands run and their key outcomes
- Tool call results that matter for future work

## Critical Findings
Important facts discovered: errors and resolutions, data retrieved, test results,
API responses, file contents — anything that affects what comes next.

## Current State
Where things stand after everything in the chunks above.

## Lessons & Guardrails
Consolidate every "Mistakes & corrections" note across all chunks into a short list of concrete,
actionable rules for continuing the work:
- Mistakes / failures and their root cause — state plainly what to avoid repeating
- User corrections or pushback — treat as hard constraints going forward, not just history
- Approaches that worked well and are worth repeating
- Any remaining open risks that could resurface

Deduplicate across chunks and phrase each item as a short imperative rule (e.g. "Always X",
"Never Y without Z"). If nothing notable occurred across any chunk, write "None noted."

## Pending / Next Steps
Open items, blockers, or next actions discussed or implied.

---
Preserve exact file paths, error messages, variable names, and numeric values.
Consolidate redundant information. Omit pleasantries and meta-commentary.
Respond in the same language used in the chunks.

--- CHUNK SUMMARIES ---
{{ chunk_summaries }}

