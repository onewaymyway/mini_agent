# prompts/user/compact_history.md
#
# 发送给模型，要求压缩对话历史（由 compact_with_skills() 通过 run_turn 发送）

Please summarize our conversation so far into a compact but complete reference.
Structure your summary using the following sections. Every section below is
required — do NOT omit any of them, even if it means writing one short line.

## Environment & Session Anchors
This section is the single most important part of the summary and must NEVER be
omitted, shortened away, or left implicit. Scan the ENTIRE conversation (not just
the most recent turns) and extract, verbatim, every concrete environment anchor
the user or a tool call established, including but not limited to:
- The working directory / project root the user is operating in or referred to
  (e.g. "cd into X", "my project is at Y") — use the exact absolute path if one
  was ever given; if only a relative path was given, state it exactly as given
  AND note what it is relative to (the anchor established at that point).
- Any output/input directory the user explicitly named for this task (e.g.
  "输出到 <path>", "save results to <path>", "--output-dir <path>").
- Absolute paths of key input/output files or directories produced or consumed
  during the work (audio/video/data files, generated artifacts, config files).
- Environment names, interpreters, or toolchains pinned for this task (e.g. a
  specific conda/venv environment, a fixed ffmpeg/binary path, an API key that
  was confirmed to be set).
If the user or a tool ever changed one of these (e.g. moved to a new directory,
picked a different output path), record the LATEST value, not the first — but do
not drop the earlier one silently if it's still relevant context (e.g. multiple
active output directories for different stages of one pipeline).
Never re-derive or guess a path — only report a path that literally appeared in
the conversation or tool output. If truly no environment anchors were ever
established, write "None stated — no directory/path/environment was fixed in
this conversation" (do not just omit the section).

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
Before finishing, double-check the "Environment & Session Anchors" section specifically: if the
original conversation named a working directory, output directory, or any absolute path and it is
not verbatim present in that section, add it now — this is the detail most likely to silently break
the next turn if lost.

