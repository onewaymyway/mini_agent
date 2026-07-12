# prompts/user/goal_spec_from_history_request.md
#
# GoalSpecBuilder.build_from_history() 发送的 user 消息
#
# 变量：
#   {{history_transcript}}  从当前 session 历史中提取的对话摘录（user/assistant 轮次）
#   {{truncated_note}}      若历史被截断，说明文字；否则为空字符串

以下是当前会话到目前为止的对话记录（可能只截取了最近的部分）：

--- 对话记录开始 ---
{{history_transcript}}
--- 对话记录结束 ---
{{truncated_note}}

请先从这段对话中判断用户当前实际在做的任务/想要达成的目标是什么（可能分散在
多轮对话里，需要你自己归纳，而不是随便摘抄某一句话），然后按 system prompt
中的方法论，生成结构化、具体、可客观核查的验收标准 JSON。

再次提醒：
- goal_text 必须是你对"这段对话在做什么"的归纳复述，不能是对话中某一句话的
  原样照抄。
- acceptance_criteria 中的每一条都必须是对目标的加工结果（具体化 / 拆解 /
  补充验证方式），不能是对话内容的直接复述或近义替换。
- 如果对话内容确实不足以判断出一个清晰的任务目标（比如全是闲聊、或刚开始还
  没展开），请把 goal_text 留空字符串，acceptance_criteria 留空数组，不要
  编造目标。
