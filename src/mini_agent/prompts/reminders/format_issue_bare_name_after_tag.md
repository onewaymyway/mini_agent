---
name: bare_name_after_tag
trigger_event: format_issue
condition:
  issue_type: "bare_name_after_tag"
priority: 70
enabled: true
---

**[Reminder] 工具名不应该写在标签外面：**

看起来把工具/函数名当作纯文本写在了开标签后面单独一行，参数 JSON 里却没有 `name` 字段，例如 `<tool_call>some_name\n{...}\n</tool_call>` 这种写法。这不是期望的格式。

正确格式：函数名必须是 JSON 对象里的 `"name"` 字段，标签必须是 `<tool_use>`，开标签这一行不要有任何其它内容。请重新发送一次：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。
