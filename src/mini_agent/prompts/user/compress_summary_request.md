# prompts/user/compress_summary_request.md
#
# 发送给模型，要求对将被压缩的历史对话生成摘要（LLMSummaryStrategy）

Please create a concise but complete summary of the conversation above.
The summary will replace the full conversation history, so it must preserve all actionable detail:

1. **Goal** — The user's overall objective
2. **Work completed** — Files created/modified/deleted (exact paths), commands run and their results,
   tool call outcomes (what was found, fixed, or produced — preserve exact error messages, paths, values)
3. **Key decisions** — Technical choices made and reasoning
4. **Critical findings** — Important data retrieved, errors and resolutions, test results, API responses
5. **Current state** — What works, what is broken, what is partially done
6. **Lessons & Guardrails** — Mistakes or failures made and their root cause, user corrections or
   pushback (treat as hard constraints going forward), and approaches that worked well and are worth
   repeating. Phrase each as a short, imperative rule (e.g. "Always X", "Never Y without Z first").
   Write "None noted" if nothing notable occurred.
7. **Pending / next steps** — Open items discussed or implied

Be factual and precise. Preserve exact file paths, command names, error messages, and variable names.
Respond in the same language used in the conversation.
Do NOT include meta-commentary like "Here is a summary:" — just the summary text.

