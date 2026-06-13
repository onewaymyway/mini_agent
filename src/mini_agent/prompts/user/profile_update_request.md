# prompts/user/profile_update_request.md
#
# 发送给模型，要求基于历史会话摘要生成/刷新用户画像
# 变量：{{memory_text}} — 最近的长期记忆摘要列表（每行一条）

Below are summaries of this user's recent sessions, most recent last.
Based on these, build (or update) a profile of the user: their typical
goals, technical background/stack, and recurring habits or preferences.

Recent session summaries:
{{memory_text}}

Respond with only the JSON object described in the system prompt.
