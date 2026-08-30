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

# [goal_mode_stuck_compact_plan.md §2.1] 过程判断 / 结果判断分离：单独要求
# 判官额外输出 process_flags 字段，标记"表面结果满足但达成方式有问题"的情况
# （例如把测试断言改成恒真、删掉失败用例、绕过检查等）。process_flags 是否
# 请求由 cfg.goal_mode.process_integrity_check_enabled 独立控制，和
# progress/checklist（GOAL_JUDGE_EXTENDED_OUTPUT_INSTRUCTIONS）是两个独立开关，
# 可以任意组合开关。
PROCESS_INTEGRITY_INSTRUCTIONS: |
  ## 过程正当性判断（独立于结果判断，同一个 JSON 对象里一并输出）

  除了核对每条验收标准是否"通过"（结果判断），还请额外核查达成方式是否正当
  （过程判断）——即使 checklist 全部 passed: true，也不代表可以直接判定为
  DONE，如果达成方式本身有问题，请通过 `"process_flags"` 字段明确指出。

  请在同一个 JSON 对象中额外包含：

  - `"process_flags"`：数组，默认应该是空数组 `[]`（表示过程判断无异议）。
    只有在你发现存在以下这类投机行为的**具体证据**时才添加条目，不要无依据
    臆测：
    - 测试被弱化（比如断言被改成恒真、关键校验被注释掉或删除）
    - 检查被绕过（比如本该跑的验证步骤被跳过、用 mock/stub 替代了本该真实
      验证的部分而不是覆盖本身就要求 mock）
    - 结果被人为伪造（比如手写"预期输出"直接覆盖实际运行结果、编造不存在
      的运行记录）
    - 验收标准范围被悄悄缩小以规避真正的困难点（比如标准要求"所有测试通过"，
      实现却只是删除了会失败的那部分测试）
    每条格式为 `{"concern": "简短类别标签", "detail": "具体证据和位置"}`。

  **`process_flags` 非空时，即使所有 checklist 都 passed: true，也绝不能判定
  为 DONE**——请判定为 CONTINUE，并在 feedback 中明确指出"结果表面达标但
  存在过程问题，需要恢复真实的验证方式后重做"，具体说明是哪一条、问题在哪。

  示例（发现问题时）：
  ```
  {"status": "CONTINUE", "feedback": "标准1对应的测试断言被改成了 assert True，
    这不是真正的验证，需要恢复原有的实质性断言后重新验证。",
    "process_flags": [{"concern": "test_weakened",
      "detail": "test_foo.py 第 42 行的 assert result == expected 被改成了 assert True"}]}
  ```

PRIOR_CHECKLIST_BLOCK: |
  **上一轮各条验收标准的通过情况（除非本轮有明确相反证据，否则请保持一致，不要无理由回退）：**
  {checklist_lines}

# [goal_mode_stuck_compact_plan.md §2.2] 自验证优先：GoalRunner 在送进 GoalJudge
# 之前，如果 GoalSpec.verification_command 非空，会程序化地（不依赖任何 LLM）
# 执行一次该命令，把客观结果拼进这个块传给判官，作为不依赖主 Agent 自述的
# 硬证据。auto_verify_enabled=False 或 GoalSpec 未设置 verification_command 时
# 不会生成这个块。
VERIFICATION_RESULT_BLOCK: |
  **系统自动执行验证命令的客观结果（不依赖 AI 助手自述，请优先参考这里的结果）：**
  验证命令：{verification_command}
  退出码：{returncode}
  标准输出（尾部）：
  {stdout_tail}
  标准错误（尾部）：
  {stderr_tail}

STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK: |
  以下是最近几轮已经验证过、没有取得实质进展的方向，请不要重复：
  {attempted_paths_lines}
  请基于以上信息，明确选择一个不同于以上的新方向，并说明为什么这次会不同，
  而不是换个说法继续同一个思路。

# [goal_mode_stuck_compact_plan.md §5] Goal 重规划提议：只在"即将耗尽卡住
# 恢复额度的最后一次机会"这一轮拼进提示，只出现一次，不是每轮都问。
# 只是征求提议，不代表验收标准或目标会被自动放宽——是否采纳由
# cfg.goal_mode.replan_proposal_mode 决定（"confirm" 需要用户后续手动
# /goal revise 采纳；"auto" 会在解析到非空提议后自动应用一次）。
REPLAN_PROPOSAL_REQUEST_BLOCK: |
  这是本次自动恢复的最后一次机会。如果你认为反复卡住的根本原因是目标定义
  本身有问题（比如某条验收标准依赖了不存在的前提、目标范围过大难以一次性
  完成、验证方式设定不合理等），请在本轮回复的**末尾**额外输出一个
  ```replan_proposal 代码块（只有真的认为目标定义有问题时才输出，如果你
  认为只是需要换个方法继续尝试，不要输出这个块），格式如下：

  ```replan_proposal
  {{"suggested_split": ["子目标1", "子目标2"], "suggested_criteria_changes": ["建议把标准3放宽为...，理由：..."], "reason": "反复卡住的根本原因简述"}}
  ```

  这只是一份供参考的提议，不会自动生效改写验收标准——请继续按原目标正常
  完成本轮任务，提议只是附加在回复末尾的额外信息。

# [next_doc/autonomous_execution_stability_and_self_learning_integration_plan.md
# 方案 A] 卡住归因分类：只在本轮 progress 判定为 SAME_APPROACH_NO_GAIN /
# REGRESSED（即"没有实质进展"）时才需要额外输出 stuck_category，帮助
# GoalRunner 区分"卡住的真实原因"，从而分流出不同的恢复策略（而不是所有
# 原因都走同一条 compact + 换角度提示的路径）。仅在
# cfg.goal_mode.stuck_attribution_enabled=True 时拼进 system prompt；
# 字段缺省/解析失败时 GoalRunner 自动按 "unknown" 处理，完全退化为升级前
# 的通用恢复逻辑。
STUCK_ATTRIBUTION_INSTRUCTIONS: |
  ## 卡住归因分类（仅当 progress 为 SAME_APPROACH_NO_GAIN 或 REGRESSED 时需要）

  如果你判断本轮相比上一轮没有实质进展或有所退步，请额外输出
  `"stuck_category"` 字段，说明卡住的**真实原因类别**（只能是以下之一）：

  - `"env_blocked"` —— 环境/依赖/权限问题（比如缺少某个命令、模块装不上、
    进程起不来、没有必要的访问权限），不是方法或方向的问题
  - `"goal_ambiguous"` —— 目标描述或验收标准本身有歧义/互相矛盾/依赖了
    不存在的前提，导致无论怎么做都难以被判定为"通过"
  - `"tool_format_error"` —— 反复卡在工具调用格式/协议问题上，不是任务
    本身的语义困难
  - `"genuine_difficulty"` —— 方向大体正确，但任务本身确实复杂，需要更多
    轮次才能推进，不属于以上几类
  - `"unknown"` —— 无法明确归类到以上任何一类

  请给出你能给出的最具体的分类，不要在能明确归类时选择 `"unknown"`。

  示例：

  ```
  {"status": "CONTINUE", "feedback": "...", "progress": "SAME_APPROACH_NO_GAIN",
    "progress_reason": "连续两轮都因为 pip 安装依赖失败而无法运行测试",
    "stuck_category": "env_blocked", "checklist": [...]}
  ```
