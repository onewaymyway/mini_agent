---
name: file_not_found
trigger_event: tool_error
condition:
  error_pattern: "No such file or directory|FileNotFoundError|ENOENT|not found|does not exist"
inject_as: user
priority: 75
enabled: true
---

**[Reminder] 文件/路径不存在错误处理建议：**

1. 确认当前工作目录：`pwd`
2. 列出目录内容确认路径：`ls -la <parent_dir>`
3. 使用 `find . -name "<filename>"` 搜索文件实际位置
4. 注意路径是绝对路径还是相对路径，避免混淆
5. 若目录不存在需要先创建：`mkdir -p <dir_path>`
