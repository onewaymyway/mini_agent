# prompts/user/session_to_workflow_summary_request.md
#
# 变量：
#   {{timeline_text}} — 2.3 节格式的执行时间线（用户轮次 + ActionEvent 摘要 + assistant 阶段性文本交替）

Summarize this session into the following JSON structure:

{
  "goal": "这次 session 要完成的总体目标，一句话",
  "final_outcome": "最终实际交付/达成的是什么",
  "stages": [
    {"id": "简短英文标识，如 analyze/fix/verify", "purpose": "这个阶段要达成什么",
     "approach": "做法摘要，意图层面的描述，不是工具调用流水账",
     "depends_on_stage_ids": ["依赖的前置阶段 id"], "had_retries": false,
     "retry_note": "如果 had_retries=true，一句话说明失败原因和最终怎么解决的",
     "gate_candidate": false}
  ],
  "candidate_parameters": [
    {"name": "建议的参数名", "example_value": "这次实际用的值", "source": "取值来源，如'首个用户输入'"}
  ],
  "repeated_pattern": null
}

Session timeline:
{{timeline_text}}
