---
name: workflow_run_failed
trigger_event: post_tool
condition:
  tool_name: "run_workflow|resume_workflow_run|get_workflow_run_status"
  output_pattern: "❌|状态：failed|状态：needs_fix|状态：gate_failed|状态：timeout|needs_fix"
inject_as: user
priority: 70
enabled: true
---

**[Reminder] 该工作流执行出现了失败/需要修复的步骤：**

不要凭空猜测原因或直接重跑了事，建议按以下顺序处理：

1. 调用 `get_workflow_run_status(workflow_session_id=..., verbose=true)` 查看具体是哪个
   step 失败、`error_type` 是什么、（verbose 模式下）traceback 与上下文细节。
2. 判断失败类型：
   - 如果状态是 `needs_fix`，或错误信息里提示"定义/配置问题"，说明重跑无效，必须先修改工作流定义。
   - 如果是网络超时等瞬时错误（`failed` 且没有 `needs_fix` 提示），可以直接续跑。
3. 需要改定义时：调用 `patch_workflow_step(name=..., step_id=..., patch='{"prompt": "..."}')`
   只修改出错的那个 step 的相关字段，不要重贴整份 YAML。
4. 改好后（或判断是瞬时故障不需要改）：调用
   `resume_workflow_run(workflow_session_id=..., force_rerun_from="<失败的step_id>")`
   只重跑这一步及其下游，已经成功、消耗过 token 的前序步骤不会重来。
