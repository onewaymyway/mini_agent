# Project Knowledge Base: mini_agent

*Accumulated architectural decisions, gotchas, tradeoffs, and conventions for the mini_agent project.*

---

## Project Overview

**mini_agent** is a simplified Python implementation of a Claude Code-like agent with:
- **Skill mechanism**: Modular, loadable skills (`.claude/skills/<name>/SKILL.md`)
- **Self-evolution infrastructure**: Lesson memory, skill proposal, eval feedback loop, consolidation loop
- **Multi-layer memory**: Workdir (W2), Global (W3), Wiki-style knowledge base
- **Autonomous runtime (Stage 9)**: Daemon process, Goal Backlog, three-tier AutonomousLoop
- **Embodied agent improvements**: Proprioception, AffordanceMap, AgentSelfModel, SelfMaintenanceModule
- **Wiki-style knowledge base**: Markdown pages with explicit graph, three-stage retrieval
- **Goal mode**: Natural language goals → structured acceptance criteria → auto-execution
- **Workflow orchestration**: Multi-step automation with DAG scheduling
- **HTTP API + Multi-user daemon**: Session isolation, role-based permissions

**Key architectural principle**: Everything is modular and composable. Core agent loop (`src/mini_agent/agent/`) is split into 9 mixins assembled via multiple inheritance. Skills, agents, personas, workflows are all file-based and hot-reloadable.

---

## Key Architectural Decisions

14 key architectural decisions covering: mixin-based agent composition, LLMHelper unified side-call entry, client pool multi-key rotation, history raw JSONL + selective compression, three memory layers (W2/W3/Wiki), self-evolution safety net with tiered validation, lesson memory four write paths, autonomous loop three tiers, goal mode coarse-grained executor, embodied agent (proprioception/affordance/self-model), wiki three-stage retrieval + lifecycle, decision extraction compact-phase piggyback, workflow directory mode with defaults/includes, multi-user daemon session pool.

## Gotchas & Common Pitfalls

23 common gotchas documented: Windows paths (use bash with forward slashes), LLMHelper side-call enforcement (grep for LLMConfig.from_app_config), skill activation requires explicit trigger or skill_activate, compact triggers highest priority wins, wiki dual-write fixed via wiki_enabled=True, AffordanceMap daemon-only, cognitive anchor not hooked in daemon REPL, update_work_thread requires Agent session context, knowledge.md baseline required for update_knowledge, protected paths force T3 review, RPM limiter default 0=unlimited, token threshold compact ignores cooldown, decision confidence fixed 0.5, goal mode fine-grained executor not implemented, cron jobs enabled state from DigestAdvisorConfig, persona allowed_tools empty=unrestricted, hook blocking only UserPromptSubmit/PreToolUse/PreCompact, memory aging only affects lessons, entity dedup uses rule scoring + optional LLM, compact chunked falls back to string summary per chunk.

## Conventions & Standards

### File Organization
- **Skills**: `.claude/skills/<name>/SKILL.md` + optional `references/<sub>.md` (lazy-loaded via `skill_resource_load`)
- **Custom agents**: `.agent/agents/<name>.md` (project) or `~/.agent/agents/<name>.md` (global)
- **Personas**: `.agent/personas/<name>.md` or `~/.agent/personas/<name>.md`
- **Workflows**: `.agent/workflows/<name>/workflow.yaml` (+ private agents/skills/prompts)
- **Reminders**: `src/mini_agent/prompts/reminders/*.md` (system) + `--reminders-dir` (user)
- **Wiki pages**: `.agent/wiki/{entities,decisions,experiences,processes,topics}/*.md`
- **Tests**: `tests/test_<module>.py` mirroring `src/mini_agent/` structure
- **Test cases**: `test_cases/inputs/*.txt` + `test_cases/*.md` for integration scenarios

### Configuration Priority
**CLI args > config file > defaults**. Previously config file won; fixed.

### Tool Registration
All tools via `@tool()` decorator in `src/mini_agent/tools/builtin.py` or new files under `tools/`. Return `str`.

### Prompt Management
All LLM prompts saved as `.md` files under `src/mini_agent/prompts/`, loaded via `PromptManager`.

### Documentation
Every major module has a guide in `docs/*.md` (see CLAUDE.md "文档索引" section).

### Git Hygiene
- `providers.json` (API keys) auto-gitignored
- Self-evolution changes go through `StateRepo.apply()` → dedicated `evolve/<date>-<type>-<name>` branch → human review → merge
- Protected paths (`scripts/protected_paths.py`) block direct commits

---

## Open Questions & Future Work

1. **Fine-grained GoalExecutor**: Judge after each tool call, not each turn
2. **AffordanceMap in single-user CLI**: Currently daemon-only
3. **Cognitive anchor in daemon REPL**: Ctrl-C not hooked
4. **Wiki decommission execution**: Read-only plan exists, actual transition needs human
5. **Positive retrieval feedback loop**: API exists (`record_retrieval_feedback`), no auto-trigger
6. **Cross-project capability map aggregation**: W3 scope, needs data accumulation
7. **Skill auto-proposal from exploration**: ExplorationSandbox success → `skill_propose` not yet wired
8. **Decision confidence calibration**: Fixed 0.5 — could be dynamic based on evidence count
9. **Persona system in multi-user**: Role/profile interaction needs clarification
10. **Behavior perception system**: All switches default OFF, mobile collectors template-only

---

## Key Files Quick Reference

| Purpose | Path |
|---------|------|
| Agent entry | `src/mini_agent/agent/core.py` |
| LLM side-calls | `src/mini_agent/llm/service.py` (`LLMHelper`)
| Client pool | `src/mini_agent/llm/client_pool.py`
| History manager | `src/mini_agent/history_manager.py`
| Compact triggers | `src/mini_agent/history/triggers.py`
| Memory factory | `src/mini_agent/perception/memory_factory.py`
| Workdir knowledge | `src/mini_agent/perception/workdir_knowledge.py`
| Global knowledge | `src/mini_agent/perception/global_knowledge.py`
| Wiki indexer | `src/mini_agent/wiki/indexer.py`
| Wiki search | `src/mini_agent/wiki/search.py`
| StateRepo | `src/mini_agent/evolution/state_repo.py`
| AutonomousLoop | `src/mini_agent/evolution/autonomous_loop.py`
| Goal runner | `src/mini_agent/goal_mode/runner.py`
| Proprioception | `src/mini_agent/perception/proprioception.py`
| AffordanceMap | `src/mini_agent/perception/affordance_analyzer.py`
| SelfMaintenance | `src/mini_agent/evolution/self_maintenance.py`
| Workflow runner | `src/mini_agent/workflow/runner.py`
| Daemon | `src/mini_agent/cli/daemon.py`
| Session pool | `src/mini_agent/api/session_pool.py`
| Protected paths | `scripts/protected_paths.py`
| Config loader | `src/mini_agent/config/loader.py`

---

*Last updated: 2026-07-23 (Session ec36ed2e maintenance)*
*This file is the baseline for `update_knowledge` tool — new sections appended, existing sections replaced by heading match.*

## Test Section

Test content

## Test2

Test content 2
