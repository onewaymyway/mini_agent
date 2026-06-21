# prompts/user/timeline_reflection_request.md
#
# 发送给模型，要求基于最后若干轮用户意图轮次生成 {theme, key_outcomes} 概览
# 变量：
#   {{turns_text}} — 最后若干轮用户意图轮次（已用 is_turn_boundary 精确截取）

Summarize this session for the project timeline (see system prompt for the
required JSON format: {"theme": ..., "key_outcomes": [...]}).

Last few user-intent turns in this session:
{{turns_text}}
