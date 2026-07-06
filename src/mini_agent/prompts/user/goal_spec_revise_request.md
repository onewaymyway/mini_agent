# prompts/user/goal_spec_revise_request.md
#
# GoalSpecBuilder.revise() 发送的 user 消息
#
# 变量：
#   {{prior_version}}   上一版版本号
#   {{prior_summary}}   上一版验收标准的可读展示（GoalSpec.render_summary_for_user()）
#   {{user_feedback}}   用户对上一版的修改意见

这是当前的验收标准草案（第 {{prior_version}} 版）：
{{prior_summary}}

用户对这版草案的修改意见：
{{user_feedback}}

请基于以上反馈生成修订后的新版本 JSON。注意：
1. 只调整用户反馈涉及的部分，其余保持稳定，避免无关改动
2. 新增/修改的标准同样要遵守 system prompt 中的方法论——具体、可客观核查、
   分维度，不能把用户反馈原句直接当成一条标准
