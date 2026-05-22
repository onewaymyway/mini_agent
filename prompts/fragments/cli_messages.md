# prompts/fragments/cli_messages.md
#
# CLI / REPL 界面中的固定文本片段
# 格式: KEY: value（多行 value 用缩进表示）

BANNER: |
  ╔══════════════════════════════════════════╗
  ║        mini-claude-code  v0.1.0          ║
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
SKILL_ACTIVATED: Skill '{name}' activated.
SKILL_DEACTIVATED: Skill '{name}' deactivated.
SKILL_NOT_FOUND: Skill '{name}' not found or already in that state.
MODEL_SWITCHED: Model switched to: {model}
UNKNOWN_COMMAND: Unknown command: {cmd}. Type /help for available commands.
SKILL_CMD_USAGE: Usage: /skill on <name> | /skill off <name>
BYE_MSG: Bye!
NO_SKILLS_FOUND: (none found)

SLASH_COMMANDS_HEADER: Available slash commands:
SKILLS_LIST_HEADER: Available skills:
