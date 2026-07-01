# prompts/user/cognitive_anchor_request.md
#
# 发送给模型，要求基于最后若干轮 history 生成认知锚点文件内容
# 变量：
#   {{turns_text}} — 最后若干轮对话内容（用户消息 + 助手文本，已截断）

Your work on this task was just interrupted. Based on the recent turns
below, write the cognitive anchor (see system prompt for the required
format).

Recent turns in this session:
{{turns_text}}
