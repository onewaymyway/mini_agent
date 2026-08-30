# prompts/fragments/turn_judge.md
#
# TurnJudge 专属的可选 prompt 片段。目前只有一个：分级响应所需的
# confidence 字段指令，见
# next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
# 方案 C。只在 cfg.turn_judge.auto_continue_with_note_enabled=True 时才会
# 被拼进 system prompt（见 role_agents/turn_judge.py::run_turn_judge）；
# 关闭时 {{confidence_instructions}} 渲染为空字符串，行为与升级前完全一致。

CONFIDENCE_INSTRUCTIONS: |
  ## 置信度字段（仅当判定为 AUTO_CONTINUE 时需要）

  如果你的判定是 `"AUTO_CONTINUE"`，请额外输出一个 `"confidence"` 字段，
  表示你对"这确实是技术性问题、不需要真人介入"这个判断的把握程度，取值
  范围 0 到 1 之间的小数：

  - 接近 1（如 0.9）：证据非常明确（比如工具调用标签明显未闭合、清楚地
    撞到了 max_turns 硬顶），几乎不可能是真人需要介入的场景
  - 接近 0.5：有一定依据，但也存在"其实用户希望在这里看一眼"的可能性
  - 接近 0（如 0.2）：证据比较间接，更多是推测

  这个字段只是给系统一个"要不要更谨慎对待这次自动继续"的参考信号，不会
  改变你的 status 判定本身——该判 AUTO_CONTINUE 就判 AUTO_CONTINUE，只是
  如实反映你的把握程度，不要为了让流程更顺畅而虚报高置信度。

  示例：

  ```
  {"status": "AUTO_CONTINUE", "feedback": "...", "confidence": 0.85}
  ```
