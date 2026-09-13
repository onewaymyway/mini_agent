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
  
  ### `feedback` 字段里引用/示例 `<tool_use>` 内容时的强制标记（极其重要）
  
  如果 `feedback` 字符串内部需要引用/复述别处那段有问题的 `<tool_use>`
  内容，或者需要给出一段 `<tool_use>`/`<tool_result>` 格式示例（不管是
  原样引用还是你自己现写的修复模板），**必须**在这段内容前先原样写上这
  一整句标记：`【以下为工具格式示例并非实际工具调用】`，再紧跟着给出示例
  本身。这不是你自己发起的一次工具调用，只是 `feedback` 里的说明性文字，
  但下游有一套独立于本 JSON 输出格式的检测逻辑，专门扫描任意文本里是否
  出现 `<tool_use>`/`<tool_result>` 等协议关键字——如果没有这行标记，
  即使你已经把整个 JSON 写得完全正确、`status` 判定也完全正确，这段
  被引用的示例仍会被误判成"你自己这一轮想调用工具但格式写坏了"，导致你
  这次本已正确的判定结果被丢弃、被迫重新输出一轮。**这条规则和上面的
  JSON 格式规则同等优先级，忘记加这行标记等价于让你这一整轮的判定失效**，
  不要因为觉得"只是顺手引用一下例子"就省略它。
