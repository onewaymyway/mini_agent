---
name: unclosed_tool_use
trigger_event: format_issue
condition:
  issue_type: "unclosed_tool_use"
priority: 60
enabled: true
---

**[Reminder] `<tool_use>` 标签没有正常闭合：**

看起来 `<tool_use>` 块开了头，但没有用 `</tool_use>` 正确闭合（或者标签在闭合前重复出现了），导致里面的 JSON 不完整。

请重新发送一次完整、格式正确的工具调用：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。

> 如果本次是因为要写入的内容本身很大导致输出被截断（例如正在用 `write_file` 写一个大文件），不要原样重发，请改为分片写入再合并——可参考 `write_file_truncated` 这条 reminder 的建议。
