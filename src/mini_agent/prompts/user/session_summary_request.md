# prompts/user/session_summary_request.md
#
# 发送给模型，要求生成本次 session 的简短摘要（用于长期记忆）
# 变量：{{turns_text}} — 用户消息列表（每行一条，已截断）

Summarize this conversation in 2-3 sentences.
Focus on: what was accomplished, key decisions made, and important outcomes.
Be concise. Respond with only the summary, in the same language as the user
messages below.

User messages:
{{turns_text}}
