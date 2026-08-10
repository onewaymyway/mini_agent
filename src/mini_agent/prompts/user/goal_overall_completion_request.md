# prompts/user/goal_overall_completion_request.md
#
# GoalExecutionSpecBuilder.evaluate_overall_completion() 发送的 user 消息
#
# 变量：
#   {{goal_title}}         Goal 标题
#   {{goal_description}}   Goal 描述
#   {{criteria_lines}}     overall_completion_criteria 逐条列出的文本（已编号）
#   {{children_lines}}     全部子 Objective 的 "标题（状态）" 列表（已编号）
#   {{manifest_block}}     该 Goal 历史全部轮次的产出摘要拼接文本

Goal 标题：{{goal_title}}
Goal 描述：{{goal_description}}

整体完成标准 overall_completion_criteria：
{{criteria_lines}}

全部子 Objective 及其终态：
{{children_lines}}

历史全部轮次的实际产出：
{{manifest_block}}

请依据 system prompt 中的判定原则，逐条核查以上标准是否已被满足，并输出
判定结果 JSON。
