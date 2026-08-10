# prompts/user/goal_execution_spec_initial_request.md
#
# GoalExecutionSpecBuilder.build_draft() 发送的 user 消息
#
# 变量：
#   {{goal_title}}        Goal 标题
#   {{goal_description}}  Goal 描述
#   {{schedule}}          调度信息（周期性 Goal 才有，否则"（未设置，非周期性 Goal）"）
#   {{task_template}}     每轮任务内容模板（周期性 Goal 才有）
#   {{template_block}}    可选：从模板起步时拼进来的骨架参考（可能为空字符串）
#   {{history_block}}     可选：该 Goal 过去若干轮的实际产出摘要（可能为空字符串）

Goal 标题：{{goal_title}}
Goal 描述：{{goal_description}}
调度信息：{{schedule}}
每轮任务内容：{{task_template}}

{{template_block}}

{{history_block}}

请按 system prompt 中的方法论，把这个 Goal 具体化成一份结构化的执行规范
JSON。如果提供了模板骨架，可以在此基础上增删改，不要机械照搬；如果提供了
历史实际产出，请参考它们校验规范是否贴近实际情况。
