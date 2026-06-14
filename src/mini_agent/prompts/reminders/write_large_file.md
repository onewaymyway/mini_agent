---
name: write_large_file
trigger_event: user_intent
condition:
  intent_pattern: "写.*文件|生成.*文件|创建.*文件|write.*file|create.*file"
inject_as: user
priority: 50
enabled: true
---

**[Reminder] 写入文件注意事项：**

1. 优先使用 `str_replace` 修改已存在的文件（比全量覆写更安全）
2. 使用 `create_file` 创建新文件前，先确认路径不存在，避免报错
3. 大型文件（>200行）建议分模块拆分，保持每个文件职责单一
4. 写入前检查目标目录是否存在，必要时先 `mkdir -p`
