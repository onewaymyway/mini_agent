# prompts/fragments/judge_tool_protocol_reference.md
#
# TurnJudge / GoalJudge 判定"这次停止是不是因为 tool_use 格式写坏了"时用的
# 权威格式参考。摘自 prompts/system/tool_call_protocol.md（主 Agent 实际
# 遵循的协议原文，不含工具列表部分——判官不需要知道有哪些工具，只需要知道
# 正确的调用语法长什么样）。
#
# [背景] 此前 turn_judge.md / goal_judge.md 只是抽象地要求判官识别"工具调用
# 标签未闭合、JSON 截断、协议关键字混用"这类问题，却从未告诉判官正确格式
# 本身是什么样——判官只能凭自己对"工具调用大概长什么样"的泛泛猜测去做判断，
# 这会导致两类问题：(1) 遇到不熟悉的畸形变体时，判官可能没能准确识别出这
# 就是"格式问题"导致的停止；(2) 需要在 feedback 里给出修复示例时，判官自己
# 写出的"正确格式"也可能不准确，反而误导主 Agent。
#
# 通过 pm.fragment("judge_tool_protocol_reference", "TOOL_PROTOCOL_REFERENCE")
# 无需任何变量，直接拼进两个判官的 system prompt。

TOOL_PROTOCOL_REFERENCE: |
  ## 正确的工具调用格式（权威参考，判断是否为格式问题时以此为准）

  主 AI 助手实际遵循的协议要求：工具调用必须是且只能是这个形状——

  <tool_use>
  {"name": "<tool_name>", "input": {<parameters as JSON object>}}
  </tool_use>

  硬性规则：
  1. `<tool_use>` 独占一行，JSON 紧跟在下一行，`</tool_use>` 独占一行且在 JSON 之后
  2. JSON 必须合法：双引号 key、无尾随逗号、字符串正确转义
  3. 一次回复只能发起一次工具调用；必须等到工具结果返回才能发起下一次
  4. 不能在同一次回复里既调用工具又给出最终答案
  5. 收到工具结果后才能继续推理、再次调用工具或给出最终答案
  6. 绝不能凭空编造工具结果

  常见畸形变体（凡是输出偏离上面的形状，都应视为"格式问题导致停止"，而不是
  "助手主动结束等用户输入"）：
  - `<tool_use>` 或 `</tool_use>` 标签缺失/未闭合，导致内容被截断在半当中
  - 标签内不是合法 JSON（多余逗号、未转义引号、被截断的字符串）
  - 混用了其它协议关键字或起了别的标签名（比如 `<tool_call>`、
    ```json 代码块包裹、把 `<tool_result>` 当成自己要输出的内容）
  - 一次回复里出现了不止一个 `<tool_use>` 块
  - 内容看起来像是"正准备调用工具"但半途只写了自然语言描述，没有真正落地
    成上面这个格式

  如果需要在 feedback 里给出修复示例，必须严格照抄上面这个格式（标签、字段名
  `name`/`input`、JSON 结构），不要自己发明变体格式。
