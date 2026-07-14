---
name: tag_role_confusion
trigger_event: format_issue
condition:
  issue_type: "tag_role_confusion"
priority: 70
enabled: true
---

**[Reminder] 请求标签与结果标签用混了：**

看起来把请求标签 `<tool_use>` 和结果回填标签 `<tool_result>` 用混了——比如用其中一个开头，却用另一个收尾。`<tool_use>` 是**你**发起工具调用时用的标签；`<tool_result>` 只会由系统回填给你，你自己永远不需要输出它。

请重新发送一次完整、格式正确的工具调用：

```
<tool_use>
{"name": "<tool_name>", "input": {<参数 JSON 对象>}}
</tool_use>
```

要求：`<tool_use>` 单独占一行，JSON 紧跟下一行，`</tool_use>` 再单独占一行。只输出一个完整的工具调用，周围不要有其它格式错乱的内容。
