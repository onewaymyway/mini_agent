# prompts/user/goal_execution_spec_revise_request.md
#
# GoalExecutionSpecBuilder.revise() 发送的 user 消息
#
# 变量：
#   {{prior_version}}  上一版版本号
#   {{prior_summary}}  上一版规范的可读展示（GoalExecutionSpec.render_summary_for_user()）
#   {{user_feedback}}  用户对上一版的修改意见
#   {{locked_block}}   哪些字段被锁定、要求原样保留的说明文字

这是当前的执行规范草案（第 {{prior_version}} 版）：
{{prior_summary}}

{{locked_block}}

用户对这版草案的修改意见：
{{user_feedback}}

请基于以上反馈生成修订后的新版本 JSON。注意：
1. 已锁定的字段必须原样保留（原样复制其内容到输出 JSON 对应字段），不要
   因为反馈涉及其他字段就顺带改动它们
2. 只调整用户反馈涉及的部分，其余未锁定字段也尽量保持稳定，避免无关改动
3. 新增/修改的内容同样要遵守 system prompt 中的方法论——具体、可核查
