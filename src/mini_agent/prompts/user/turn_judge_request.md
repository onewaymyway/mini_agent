# prompts/user/turn_judge_request.md
#
# TurnJudge 每次核查时发送的 user 消息（role_agents/turn_judge.py::build_turn_judge_prompt）
#
# 变量：
#   {{auto_round_no}}       当前是第几次连续自动核查（1 表示本轮是真人交互后的第一次）
#   {{max_auto_rounds}}     允许连续自动处理的最大次数（超过后必须交还用户）
#   {{hit_max_turns_line}}  是否撞到了 max_turns 硬顶的提示行（未撞到时为空字符串）
#   {{assistant_output}}    主助手本轮最终产出的文本（可能为空，若为空说明本轮没有产出最终文本）
#   {{recent_history}}      最近若干条历史消息（角色 + 内容摘要），供你判断上下文

请核查：主 AI 助手刚刚结束的这一轮，是「真的需要人类用户输入」，还是「遇到了技术性问题，
应该由系统自动介入让它继续」。这是本次真人交互后的第 {{auto_round_no}}/{{max_auto_rounds}} 次
自动核查（超过上限后无论如何都会强制交还给真实用户，防止死循环）。
{{hit_max_turns_line}}

**主助手本轮的最终产出：**
{{assistant_output}}

**最近的对话历史（供参考上下文）：**
{{recent_history}}

请严格按照你的核查原则和输出格式进行判定。
