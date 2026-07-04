# prompts/fragments/goal_mode.md
#
# Goal 模式相关的固定文本片段（role_agents/goal_judge.py, goal_mode/spec.py）
# 格式: KEY: value（多行 value 用缩进表示）

PRIOR_FEEDBACK_BLOCK: |
  **上一轮给出的反馈（用于判断本轮是否已解决）：**
  {feedback}
