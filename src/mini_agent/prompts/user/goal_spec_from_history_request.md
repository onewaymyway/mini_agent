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

请先从这段记录中判断用户当前实际在做的任务/想要达成的目标是什么。记录里可能是
原始的多轮对话，也可能包含标注为「历史摘要（/compact 生成）」的结构化压缩摘要
（通常分 Goal / Work Completed / Current State / Pending 等小节）——如果出现
这种摘要，它是最可靠的信息来源，请重点看其中的 Current State（当前进展）和
Pending / Next Steps（待办事项）小节来判断"接下来还没做完的任务是什么"，
不要因为它是"已有的结构化摘要"就无视它、只依赖零散对话来猜测。

然后按 system prompt 中的方法论，生成结构化、具体、可客观核查的验收标准 JSON。

再次提醒：
- goal_text 必须是你对"当前尚未完成的任务是什么"的归纳复述，而不是对某一句
  用户闲聊或某个 Key Decisions 条目的原样照抄。如果记录中有结构化摘要的
  Pending/Current State 小节，直接依据其中列出的具体事实来写 goal_text 是
  合理的引用，不算"照抄"——"照抄"特指把用户随口的一句话原封不动当成目标或
  验收标准，不适用于对结构化摘要事实的合理引用。
- acceptance_criteria 中的每一条都必须是对目标的加工结果（具体化 / 拆解 /
  补充验证方式），不能是对话内容的直接复述或近义替换。
- 如果记录内容确实不足以判断出一个清晰的任务目标（比如全是闲聊、或刚开始还
  没展开、或摘要显示所有任务已经完成且没有 Pending 项），请把 goal_text 留空
  字符串，acceptance_criteria 留空数组，不要编造目标。
