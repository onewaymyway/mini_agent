# prompts/user/goal_spec_initial_request.md
#
# GoalSpecBuilder.build_initial() 发送的 user 消息
#
# 变量：
#   {{user_goal_text}}  用户输入的原始目标文本

用户的目标（原始描述，可能比较模糊，需要你加工，而不是照抄）：
{{user_goal_text}}

请按 system prompt 中的方法论，生成结构化、具体、可客观核查的验收标准 JSON。
再次提醒：acceptance_criteria 中的每一条都必须是对目标的加工结果（具体化 /
拆解 / 补充验证方式），不能是用户原句的复述或近义替换。
