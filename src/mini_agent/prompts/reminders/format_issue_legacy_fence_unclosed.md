---
name: legacy_fence_unclosed
trigger_event: format_issue
condition:
  issue_type: "legacy_fence_unclosed"
priority: 55
enabled: true
---

**[Reminder] 旧版 ```tool_call 围栏没有闭合：**

看起来用了旧版的 ```tool_call 代码围栏格式，但没有匹配的收尾 ``` ，导致工具调用无法解析。

请改用当前支持的标准格式重新发送一次：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。
