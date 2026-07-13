# prompts/fragments/judge_json_output.md
#
# 判官类 Agent（GoalJudge / TurnJudge 等需要状态机驱动的判官）统一的
# 结构化 JSON 输出指令片段，配合 role_agents/verdict.py::parse_judge_verdict
# 使用。通过 pm.fragment("judge_json_output", "JSON_OUTPUT_INSTRUCTIONS",
# valid_statuses="DONE | CONTINUE | NEED_COMPACT", status_field_hint="...")
# 渲染后拼进各自的 system prompt 末尾，替换掉此前"输出格式"里那段人肉约定
# 的 Markdown 格式说明。

JSON_OUTPUT_INSTRUCTIONS: |
  ## 输出格式（必须严格遵守）
  
  你的回复**必须是且只能是**一个 JSON 对象，不要有任何 JSON 之外的文字、
  不要用 ```json 代码块包裹、不要在前后添加任何说明——直接输出 JSON 本身。
  
  JSON 对象必须包含且仅包含以下字段：
  
  - `"status"`：字符串，只能是以下之一：{valid_statuses}
  - `"feedback"`：字符串，人类可读的核查依据/理由/下一步指令（{feedback_hint}）
  
  示例（仅供参考格式，具体内容请根据实际核查结果填写）：
  
  ```
  {{"status": "{example_status}", "feedback": "{example_feedback}"}}
  ```
  
  再次强调：绝对不要输出 JSON 之外的任何字符（包括代码块围栏、前后缀说明文字），
  否则你的判定会被视为解析失败，系统将保守地按最安全的状态处理。
