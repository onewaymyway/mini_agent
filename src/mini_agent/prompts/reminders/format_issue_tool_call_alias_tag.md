---
name: tool_call_alias_tag
trigger_event: format_issue
condition:
  issue_type: "tool_call_alias_tag"
priority: 55
enabled: true
---

**[Reminder] 用了非标准的标签名：**

检测到用了 `<tool_call>` 或 `<tool_invoke>` 等非标准标签变体，而不是 `<tool_use>`。系统只能识别 `<tool_use>`，请用正确的标签名重新发送一次：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。
