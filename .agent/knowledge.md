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

| Area | Gotcha | Workaround |
|------|--------|------------|
| **Windows paths** | `read_file` fails on raw backslash paths | Use `bash("type file")` or `bash("cat file")` with forward slashes |
| **LLMHelper** | Forgetting to use it for side-calls | `grep -rn "LLMConfig.from_app_config" src/` — only 2 allowed hits |
| **Skill activation** | Skills not auto-loaded without trigger words | Call `skill_activate` explicitly or use `skill_resource_load` for sub-resources |
| **Compact triggers** | Multiple triggers fire, only highest priority wins | Check `compact_event.trigger_reason` in raw_history for audit |
| **Wiki dual-write** | `wiki_paths` was added to `LibraryIndex` but never passed from `memory_factory` | Fixed via `MemoryConfig.wiki_enabled=True` (now default) |
| **AffordanceMap** | Only works in multi-user daemon mode | Single-user CLI doesn't build it — known gap |
| **Cognitive anchor** | Ctrl-C in daemon connected REPL doesn't save anchor | Not yet hooked — known gap |
| **update_work_thread** | Requires Agent session context (project_root provider) | Can't call from standalone script — must run inside Agent session |
| **knowledge.md** | Missing baseline file blocks `update_knowledge` tool | Create baseline `knowledge.md` first (this file!) |
| **Protected paths** | Editing `agent/` or `evolution/` forces T3 review | Use `/evolution` commands for skill proposals, not direct edits |
| **RPM limiter** | Default 0 = unlimited, easy to hit provider limits | Set `--rpm 30` or config `rpm_limit` for production |
| **Token threshold compact** | Hard constraint, ignores cooldown | `TokenThresholdTrigger` always fires when context > threshold |
| **Decision confidence** | Fixed 0.5 — don't treat as high-confidence knowledge | Use `recall_related_decisions()` before proposing changes |
| **Goal mode** | Fine-grained executor not implemented | Use coarse-grained; each step = full turn |
| **Cron jobs** | `enabled` state from `DigestAdvisorConfig` at first inject | Check `cron_jobs.json` for current state |
| **Persona allowed_tools** | Empty = unrestricted, not "no tools" | Explicitly list tools to restrict |
| **Hook blocking** | Only `UserPromptSubmit`, `PreToolUse`, `PreCompact` can block | Other hooks are notification-only |
| **Memory aging** | Only affects lessons, not other entry types | Non-lesson entries use static scoring |
| **Entity dedup** | `find_similar_page` uses rule scoring + optional LLM for borderline | Embedding path requires explicit `embed_call` arg |
| **Compact chunked** | On `LLMContextWindowError`, falls back to string summary per chunk | Chunk boundary = turn boundary, max 50% context per chunk |

---

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
