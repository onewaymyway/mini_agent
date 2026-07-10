# prompts/fragments/cli_messages.md
#
# CLI / REPL 界面中的固定文本片段
# 格式: KEY: value（多行 value 用缩进表示）

BANNER: |
  ╔══════════════════════════════════════════╗
  ║        mini-agent  v{version}                ║
  ║  Type /help for commands, exit to quit   ║
  ╚══════════════════════════════════════════╝

REPL_STARTUP_MODEL: Model: {model}
REPL_STARTUP_PROJECT: Project: {project_root}
REPL_STARTUP_SKILLS: Skills available: {skill_count}
REPL_SANDBOX_WARNING: SANDBOX mode — destructive operations are blocked.

INTERRUPT_MSG: ⚡ Interrupted (Ctrl-C). Type 'exit' to quit.
COMPACT_START: Compacting history… (sending summary request)
COMPACT_SUCCESS: History compacted.
COMPACT_EMPTY: History is empty.
HISTORY_CLEARED: Conversation history cleared.
VERBOSE_ON: Verbose mode: ON
VERBOSE_OFF: Verbose mode: OFF
RAW_OUTPUT_ON: Raw output mode: ON (showing all model output, including <tool_use> blocks)
TURN_JUDGE_ON: TurnJudge: ON (轮次结束前将自动核查是否需要真人介入)
TURN_JUDGE_OFF: TurnJudge: OFF (轮次结束将直接等待真人输入，不做自动核查)
RAW_OUTPUT_OFF: Raw output mode: OFF (<tool_use> blocks hidden again)
REASONING_ON: Reasoning display: ON (model's thinking/reasoning process will be shown)
REASONING_OFF: Reasoning display: OFF (model's thinking/reasoning process will be hidden)
SKILL_ACTIVATED: Skill '{name}' activated.
SKILL_DEACTIVATED: Skill '{name}' deactivated.
SKILL_NOT_FOUND: Skill '{name}' not found. Use /skills to see available skill names.
SKILL_ALREADY_ACTIVE: Skill '{name}' is already active.
SKILL_NOT_ACTIVE: Skill '{name}' is not currently active.
MODEL_SWITCHED: Model switched to: {model}
UNKNOWN_COMMAND: Unknown command: {cmd}. Type /help for available commands.
SKILL_CMD_USAGE: Usage: /skill on <name> [name2 ...] | /skill off <name> [name2 ...] | /skill info <name> | /skill reset
BYE_MSG: Bye!
NO_SKILLS_FOUND: (none found)

SLASH_COMMANDS_HEADER: Available slash commands:
SKILLS_LIST_HEADER: Available skills:
DEBUG_LLM_ON: LLM debug logging: ON → {log_file}
DEBUG_LLM_OFF: LLM debug logging: OFF
SYSTEM_TOOL_CALL_ON: Tool call mode: system-prompt (max compatibility)
SYSTEM_TOOL_CALL_OFF: Tool call mode: SDK native
TASKS_HEADER: Sub-agent tasks:
TASKS_NONE: No tasks submitted yet.
TASK_MANAGER_READY: Task manager ready ({workers} workers max)
