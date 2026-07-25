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
   如果不确定改动是否正确，可先用 `test_workflow_step(name=..., step_id=..., mock_step_results=...)`
   沙箱验证一下（不落盘、不影响正式执行记录），确认没问题再正式续跑。
4. 改好后（或判断是瞬时故障不需要改）：调用
   `resume_workflow_run(workflow_session_id=..., force_rerun_from="<失败的step_id>")`
   只重跑这一步及其下游，已经成功、消耗过 token 的前序步骤不会重来。
   如果只是想临时调整一下执行参数（比如把 timeout 调大试试）而不想改动正式定义，
   可以改用 `resume_workflow_run(..., step_overrides='{"<step_id>": {"timeout": 120}}')`，
   这个只影响本次续跑、不会写回工作流定义。
