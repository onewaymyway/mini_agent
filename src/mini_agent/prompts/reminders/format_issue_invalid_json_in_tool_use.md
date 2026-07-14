---
name: invalid_json_in_tool_use
trigger_event: format_issue
condition:
  issue_type: "invalid_json_in_tool_use"
priority: 60
enabled: true
---

**[Reminder] `<tool_use>` 内部的 JSON 无法解析：**

标签本身是正常闭合的（`<tool_use>...</tool_use>` 配对完整），但中间的 JSON 解析失败（例如缺少引号、多余的逗号，或者字符串里有未转义的特殊字符）。

请重新发送一次完整、格式正确的工具调用：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。特别注意 JSON 字符串里的引号、换行符要正确转义。
