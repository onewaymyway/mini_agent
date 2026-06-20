# prompts/user/session_reflection_request.md
#
# 发送给模型，要求基于 tool_stats + 最后若干轮 history 生成结构化 lesson 候选
# 变量：
#   {{tool_stats_text}} — 工具调用统计摘要（每行一条）
#   {{turns_text}}       — 最后若干轮用户意图轮次（已用 is_turn_boundary 精确截取）

Review this session and extract any genuinely useful lessons (see system
prompt for the required JSON format). If nothing notable happened, return [].

Tool call statistics for this session:
{{tool_stats_text}}

Last few user-intent turns in this session:
{{turns_text}}
