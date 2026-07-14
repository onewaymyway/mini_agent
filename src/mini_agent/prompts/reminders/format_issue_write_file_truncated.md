---
name: write_file_truncated
trigger_event: format_issue
condition:
  issue_type: "write_file_truncated"
priority: 80
enabled: true
---

**[Reminder] 写文件调用疑似因内容过大被截断：**

检测到一次 `write_file` / `create_file` 的 `<tool_use>` 调用在内容写到一半时就结束了，没有正常闭合——大概率是要写入的内容太大，一次性输出超出了限制。

请**不要**尝试把完整内容重新原样输出一次（大概率还是会在同样的位置被截断，陷入死循环）。改为**分片写入再合并**：

1. 将目标内容按语义边界（段落 / 代码块）拆成若干片段，每片建议不超过约 1500～2000 字符；
2. 依次调用 `write_file`，把每一片分别写入 `<path>.part1`、`<path>.part2` … （每次只输出一个完整、可解析的 `<tool_use>`，不要在一次输出里塞入过多内容）；
3. 全部分片写完后，用一次 `bash` 调用合并并清理：
   `cat <path>.part1 <path>.part2 ... > <path> && rm <path>.part1 <path>.part2 ...`
4. 合并后可用 `bash("wc -c <path>")` 核对内容长度是否符合预期。
