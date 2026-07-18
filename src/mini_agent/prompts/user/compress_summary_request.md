# prompts/user/compress_summary_request.md
#
# 发送给模型，要求对将被压缩的历史对话生成摘要 + 提炼决策（LLMSummaryStrategy）
#
# 输出格式由 system prompt 规定（单个 JSON 对象，{compact_summary, decisions[]}）。
# 本文件只描述 compact_summary 字段本身应当包含的内容，decisions[] 的字段含义
# 见 system prompt。

Please first completely identify and populate the `decisions`, `entities`,
and `facts` arrays described in the system prompt — read through the
conversation specifically looking for trade-offs, entities, and factual
statements before you start writing the summary. Only after those three
arrays are fully populated should you write the `compact_summary` field.

Then create a concise but complete summary of the conversation above, to
be placed in the `compact_summary` field of the JSON object described in
the system prompt. The summary will replace the full conversation history,
so it must preserve all actionable detail:

1. **Goal** — The user's overall objective
2. **Work completed** — Files created/modified/deleted (exact paths), commands run and their results,
   tool call outcomes (what was found, fixed, or produced — preserve exact error messages, paths, values)
3. **Key decisions** — Technical choices made and reasoning (a short recap here is fine even though the
   same decisions may also appear structured in the `decisions` array — the array is for durable
   knowledge-base storage, this section is for restoring conversational context)
4. **Critical findings** — Important data retrieved, errors and resolutions, test results, API responses
5. **Current state** — What works, what is broken, what is partially done
6. **Lessons & Guardrails** — Mistakes or failures made and their root cause, user corrections or
   pushback (treat as hard constraints going forward), and approaches that worked well and are worth
   repeating. Phrase each as a short, imperative rule (e.g. "Always X", "Never Y without Z first").
   Write "None noted" if nothing notable occurred.
7. **Pending / next steps** — Open items discussed or implied

Be factual and precise. Preserve exact file paths, command names, error messages, and variable names.
Write the summary text in the same language used in the conversation.
Do NOT include meta-commentary like "Here is a summary:" — just the summary text as the JSON field value.

Separately, populate the `decisions` array (can be empty) with any genuine trade-offs the conversation
settled — i.e. places where multiple concrete approaches were weighed and one was chosen over the others.
Do not fabricate decisions that were not actually discussed.

Also populate the `entities` and `facts` arrays (both can be empty) with general world knowledge from the
conversation — this is separate from `decisions` and from the "Lessons & Guardrails" section above, which
are error/correction-focused. `entities`/`facts` should capture what the conversation established about the
world: people, projects, modules, tools, concepts, or external systems discussed, and factual statements
about them — even when nothing went wrong and nothing was decided. Do not fabricate content to fill these
arrays; leave them empty when the segment has nothing beyond what the summary already says.

Remember: your entire response must be the single JSON object itself — no markdown fences, no extra text.
