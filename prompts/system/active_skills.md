# prompts/system/active_skills.md
#
# 变量: {{ skill_list }}   — 每行一个 "- skill_name"
#        {{ skill_context }} — 所有激活 skill 的完整内容
# 在有激活 skill 时注入

## Active skills

The following skills are currently active and provide additional instructions:

{{ skill_list }}

---

{{ skill_context }}
