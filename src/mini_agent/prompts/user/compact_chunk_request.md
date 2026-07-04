# prompts/user/compact_chunk_request.md
#
# 用于 chunked compact：对历史的某一个分段生成摘要
# 由 compact_with_skills() 在上下文超限时分批调用

This is chunk {{ chunk_index }} of {{ total_chunks }} from a conversation that needs to be compressed.
Please summarize this chunk into a dense, structured reference. Include:

**User requests in this chunk** — exact phrasing or close paraphrase of what the user asked.

**Tool calls & outcomes** — for each tool call:
  - Tool name and key arguments (file path, command, etc.)
  - Result summary: what was returned, found, created, or changed
  - Any errors and how they were resolved

**Decisions & findings** — any technical choices made, important discoveries, or data retrieved
that would affect future work (error messages, file contents, API responses, etc.).

**Mistakes & corrections in this chunk** — any errors, wrong assumptions, or user corrections/pushback
observed in this chunk, what caused them, and how (or whether) they were resolved. Also note anything
that clearly worked well and is worth repeating. This will be consolidated into concrete guardrails
during the final merge — write it as plain facts, not narrative.

**State at end of chunk** — what was accomplished and what remained open.

Be precise: preserve exact file paths, error text, variable names, and numeric values.
Omit chitchat. Respond in the same language used in the conversation.

