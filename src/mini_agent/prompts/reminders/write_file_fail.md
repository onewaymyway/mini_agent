---
name: write_file_fail
trigger_event: tool_error
condition:
  tool_name: "write_file|create_file"
  error_pattern: ".*"
inject_as: user
priority: 75
enabled: true
---

**[Reminder] 写文件失败——建议改用分批写入：**

一次性写入大文件容易因特殊字符或输出长度限制而失败。请改用分批写入再合并：

```
# 1. 分批写入
write_file("<path>.part1", <前半段内容>)
write_file("<path>.part2", <后半段内容>)

# 2. 合并并清理
bash("cat <path>.part1 <path>.part2 > <path> && rm <path>.part1 <path>.part2")
```

- 每批建议不超过 150 行或 6000 字符
- 可以分更多批次（.part1 / .part2 / .part3 …）
- 合并后用 `bash("wc -l <path>")` 验证行数是否正确
