# prompts/user/compact_history.md
#
# 发送给模型，要求压缩对话历史（由 compact_with_skills() 通过 run_turn 发送）

Please summarize our conversation so far into a compact but complete reference.
Structure your summary using the following sections — omit any section that has no content:

## Goal
What the user is trying to accomplish (overall objective and any sub-goals).

## Key Decisions
Technical choices made and the reasoning behind them (architecture, tools selected, approaches rejected and why).

## Work Completed
For each significant piece of work done:
- Files created / modified / deleted — include exact paths and a one-line description of the change
- Commands run — include the command and the essential result (exit code, output highlights)
- Tool call outcomes — summarize what was found, fixed, or produced

## Critical Findings
Important discoveries from tool calls: errors encountered and how they were resolved, data retrieved
that will affect future steps, API responses, test results, file contents that matter.

## Current State
Where things stand right now: what works, what is broken, what is partially done.

## Lessons & Guardrails
Extract concrete, actionable rules for continuing this work, based on what actually happened in
this conversation:
- **Mistakes / failures** — anything that went wrong (wrong assumption, wrong flag, tool misuse,
  misread requirement) and its root cause. State plainly what to avoid repeating.
- **User corrections** — anything the user explicitly corrected, pushed back on, or had to repeat.
  Treat these as hard constraints for the rest of the work, not just history.
- **What worked well** — approaches, commands, or sequences that succeeded and are worth repeating.
- **Open risks** — anything still fragile, unverified, or likely to bite again if not handled carefully.

Phrase each item as a short, imperative rule (e.g. "Always verify X before Y", "Never assume Z —
check W first"), not as a narrative retelling. If truly nothing notable occurred, write "None noted."

## Pending / Next Steps
Open items, blockers, or next actions that were discussed or implied.

---
Be factual and precise. Preserve exact file paths, command names, error messages, and variable names
— these cannot be reconstructed from a vague summary. Omit pleasantries and meta-commentary.
This summary will replace the full conversation history, so completeness of actionable detail matters more than brevity.
Respond in the same language used in the conversation.

