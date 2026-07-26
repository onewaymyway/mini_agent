# prompts/system/active_skills.md
#
# 变量: {{ skill_list }}   — 每行一个 "- skill_name"
#        {{ skill_context }} — 所有激活 skill 的完整内容
# 在有激活 skill 时注入

## Active skills

The following skills are currently active and provide additional instructions:

{{ skill_list }}

**关于 skill 中的路径**：每个 skill 下面都标注了"Skill 所在目录"。skill 正文里
出现的相对路径（脚本、模板、参考资料、示例文件等）都是相对这个目录写的，不是
相对你当前的工作目录，也不是相对项目根目录。使用这些路径前，先把它和对应 skill
的"Skill 所在目录"拼接成绝对路径，否则大概率会因为路径基准不对而找不到文件。

**关于 skill 内容的边界（重要）**：下面每个 skill 都会明确说明这段内容是"完整
正文"还是"与本轮问题最相关的节选章节"。无论哪种情况，都**不要**用
`read_file`/`view`/`grep`/`bash` 等工具再去读取该 skill 所在目录下的 `SKILL.md`
或其他文件——已经注入到这里的内容才是你应该依据的版本，磁盘上的文件可能因为
章节筛选、历史压缩等原因与当前展示的版本不完全一致。如果这里的内容不足以覆盖
当前任务，正确的下一步是：说明还缺什么，或者调用 `skill_resource_list` /
`skill_resource_load` 获取该 skill 登记过的子资源；直接读文件不是可选项。

---

{{ skill_context }}
