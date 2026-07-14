# prompts/fragments/goal_mode.md
#
# Goal 模式相关的固定文本片段（role_agents/goal_judge.py, goal_mode/spec.py）
# 格式: KEY: value（多行 value 用缩进表示）

PRIOR_FEEDBACK_BLOCK: |
  **上一轮给出的反馈（用于判断本轮是否已解决）：**
  {feedback}

# [goal_mode_completion_improvement_plan 改造项一] GoalJudge 扩展输出指令：
# 在标准的 status/feedback 之外，额外要求输出 progress / progress_reason /
# checklist 三个字段，供 GoalRunner 做"是否有实质进展"的判断（替代纯文本
# 相似度规则）与逐条验收标准状态追踪。只有 cfg.goal_mode.progress_judge_mode
# == "llm" 时才会拼进 system prompt；解析失败时 GoalRunner 自动回退到规则算法，
# 不影响 status/feedback 的解析（两者互相独立）。
GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS: |
  ## 额外字段（用于进展判断与逐条验收标准追踪，同一个 JSON 对象里一并输出）

  除了 `status` 和 `feedback`，请在同一个 JSON 对象中额外包含：

  - `"progress"`：字符串，只能是以下之一：
    - `"SUBSTANTIVE_ADVANCE"` —— 相比上一轮有实质推进（哪怕验收标准仍未
      全部通过），例如：修复了新的问题、通过了新的验收条目、错误信息发生
      了实质性变化（说明排查方向前进了）
    - `"SAME_APPROACH_NO_GAIN"` —— 本轮和上一轮本质上是同一个策略/卡在
      同一个错误上，没有新进展（哪怕措辞、汇报方式不同，只要实质内容没变
      就属于这一类，不要被"表述不同"误导）
    - `"REGRESSED"` —— 本轮反而比上一轮更差（引入了新错误、破坏了已经
      通过的验收标准），请在 progress_reason 中明确指出具体退步点
    这是本轮判断的核心：请重点对比本轮产出与"上一轮反馈"中提到的失败点/
    错误信息是否发生了实质变化，而不是单纯看文字像不像。
  - `"progress_reason"`：字符串，用一两句话说明为什么判断为以上某一种
    progress（必须给出具体依据，不能只写"没有进展"这种空话）
  - `"checklist"`：数组，对每一条验收标准给出 `{{"index": 序号(从1开始),
    "passed": true/false, "evidence": "依据"}}`。**重要**：如果你在
    "上一轮各条标准状态"中看到某条已经是 passed: true，除非本轮有明确的
    相反证据（比如新改动破坏了它），否则请保持 passed: true，不要仅因为
    这一轮没有重新提到就改判为 false。

  示例：

  ```
  {"status": "CONTINUE", "feedback": "...", "progress": "SUBSTANTIVE_ADVANCE",
    "progress_reason": "测试 A 已从失败变为通过；测试 B 报错从 NPE 变为断言失败，说明修复方向正确但未完成",
    "checklist": [{"index": 1, "passed": true, "evidence": "pytest 全部通过"},
                  {"index": 2, "passed": false, "evidence": "lint 仍有 3 处报错"}]}
  ```

PRIOR_CHECKLIST_BLOCK: |
  **上一轮各条验收标准的通过情况（除非本轮有明确相反证据，否则请保持一致，不要无理由回退）：**
  {checklist_lines}

STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK: |
  以下是最近几轮已经验证过、没有取得实质进展的方向，请不要重复：
  {attempted_paths_lines}
  请基于以上信息，明确选择一个不同于以上的新方向，并说明为什么这次会不同，
  而不是换个说法继续同一个思路。
