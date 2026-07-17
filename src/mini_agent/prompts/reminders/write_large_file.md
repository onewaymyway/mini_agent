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

1. **用户未指定路径时，一律创建到 `./temp/` 目录下**，不要在项目根目录或 `src/` 等源码目录落地临时产物；文件名要能反映用途，如 `temp/parse_log.py`、`temp/result.json`
2. 优先使用 `patch_file` 修改已存在的文件（比全量覆写更安全）
3. 使用 `create_file` 创建新文件前，先确认路径不存在，避免报错
4. 大型文件（>200行）建议分模块拆分，保持每个文件职责单一
5. 写入前检查目标目录是否存在，必要时先 `bash("mkdir -p ./temp")`
