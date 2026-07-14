---
name: orphan_close_tag
trigger_event: format_issue
condition:
  issue_type: "orphan_close_tag"
priority: 55
enabled: true
---

**[Reminder] 出现了孤立的闭合标签：**

检测到一个 `</tool_use>`（或类似的闭合标签），但前面没有与之匹配的合法开标签。通常是开标签用了非标准名字（如 `<tool_call>`），或者开标签被意外漏写了。

请重新发送一次完整、格式正确的工具调用：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：开标签必须是 `<tool_use>`（而不是 `<tool_call>` 等别名），单独占一行；JSON 紧跟下一行；`</tool_use>` 再单独占一行。
