---
name: tool_result_used_as_request
trigger_event: format_issue
condition:
  issue_type: "tool_result_used_as_request"
priority: 70
enabled: true
---

**[Reminder] 把"发起调用"误写成了"回填结果"：**

看起来用 `<tool_result>` 包了一个工具**请求**（一个带 `name` 和 `input` 字段的 JSON 对象），但这应该用 `<tool_use>`。`<tool_result>` 只保留给系统用来把执行结果回填给你；当**你**想发起一次工具调用时，请使用 `<tool_use>`。

请重新发送一次完整、格式正确的工具调用：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。
