# prompts/system/available_subagents.md
#
# 变量: {{ agent_list }} — 每行一条 "- `name`: description (inputs: ...)"
# 在存在自定义子 agent profile 时注入

## Custom Sub-Agents

The following predefined specialized sub-agents are available via `spawn_named_agent`.
Use `list_agent_profiles` to see full details (input schema) for any of them.

{{ agent_list }}

> Prefer `spawn_named_agent` over `spawn_agent` when one of these profiles matches the
> task — it comes with a tuned system prompt, an appropriate model, and (optionally) a
> restricted tool set. Pass `inputs` matching the profile's declared parameters, and use
> `context` for any free-form background info (file excerpts, prior findings, etc.).
