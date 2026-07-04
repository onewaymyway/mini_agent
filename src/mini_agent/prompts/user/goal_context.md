# prompts/user/goal_context.md
#
# GoalSpec.render_context_block() 使用的模板 —— 作为"钉住"消息重新附加到
# 主 Agent 的对话历史中（goal_mode/runner.py::_pin_goal_context），
# 每次 compact 后都要重新附加，防止目标信息被摘要策略稀释或丢弃。
#
# 变量：
#   {{goal_text}}       目标描述
#   {{criteria_lines}}  编号后的验收标准清单（每行一条）

[Goal 模式 — 目标与验收标准（此消息会在每次压缩历史后重新附加，请始终以此为准）]
目标：{{goal_text}}

验收标准：
{{criteria_lines}}

请持续朝这个目标推进，直到所有验收标准都满足为止。
