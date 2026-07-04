# prompts/user/goal_judge_request.md
#
# GoalJudge 每轮核查时发送的 user 消息（role_agents/goal_judge.py::build_goal_judge_prompt）
#
# 变量：
#   {{round_no}}            当前第几轮核查
#   {{goal_text}}            目标描述
#   {{criteria_lines}}       编号后的验收标准清单（每行一条）
#   {{agent_output}}         AI 助手本轮的产出
#   {{prior_feedback_block}} 上一轮反馈块（无上一轮反馈时为空字符串）

请核查 AI 助手是否已经达成以下目标。这是第 {{round_no}} 轮核查。

**目标：**
{{goal_text}}

**验收标准清单：**
{{criteria_lines}}

**AI 助手本轮的产出（含过程与最终回复）：**
{{agent_output}}
{{prior_feedback_block}}

请严格按照你的核查原则和输出格式进行判定。
