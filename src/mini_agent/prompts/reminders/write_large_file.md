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

1. **用户未指定路径时，一律使用 system prompt 里给出的本 session Temp dir /
   Output dir（`Workspace Hygiene` 一节里的绝对路径）**，不要自己另外
   `mkdir` 一个 `./temp/`、`./output/` 或其它项目根目录下的临时目录——
   [历史教训] 这条 reminder 之前写的是硬编码的 `./temp/`，和 session 的
   规范工作目录脱节，导致项目根目录下出现过不受管理的游离 `./temp/`
   目录。文件名要能反映用途，如 `<temp_dir>/parse_log.py`、
   `<temp_dir>/result.json`
2. 优先使用 `patch_file` 修改已存在的文件（比全量覆写更安全）
3. 使用 `create_file` 创建新文件前，先确认路径不存在，避免报错
4. 大型文件（>200行）建议分模块拆分，保持每个文件职责单一
5. Temp dir/Output dir 在 session 初始化时已经自动创建好，正常情况下不需要
   `mkdir`；如果写入时报目录不存在，先确认自己用的是不是 system prompt
   里给出的那个路径，而不是凭印象拼一个相对路径
