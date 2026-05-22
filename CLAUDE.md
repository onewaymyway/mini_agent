# mini-claude-code

A simplified Claude Code implementation in Python with skill support.

## Project structure

- `main.py` — CLI entry point and REPL
- `agent.py` — Agent loop (conversation, tool dispatch, streaming)
- `config.py` — Configuration loading and system prompt building
- `permissions.py` — Permission guard for tool calls
- `renderer.py` — Rich terminal output
- `tools/__init__.py` — Tool registry and @tool decorator
- `tools/builtin.py` — Built-in tools (bash, file I/O, search)
- `skills/__init__.py` — Skill discovery and loading

## Development conventions

- Each tool is a plain Python function decorated with `@tool()`
- Tools must return `str` (or something str()-able)
- New tools go in `tools/builtin.py` or a new file in `tools/`
- Skills live in `.claude/skills/<name>/SKILL.md`
- Always prefer `patch_file` over `write_file` for edits

## Running

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python main.py
```
