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

REMOVE

## Gotchas & Common Pitfalls

23 common gotchas documented: Windows paths (use bash with forward slashes), LLMHelper side-call enforcement (grep for LLMConfig.from_app_config), skill activation requires explicit trigger or skill_activate, compact triggers highest priority wins, wiki dual-write fixed via wiki_enabled=True, AffordanceMap daemon-only, cognitive anchor not hooked in daemon REPL, update_work_thread requires Agent session context, knowledge.md baseline required for update_knowledge, protected paths force T3 review, RPM limiter default 0=unlimited, token threshold compact ignores cooldown, decision confidence fixed 0.5, goal mode fine-grained executor not implemented, cron jobs enabled state from DigestAdvisorConfig, persona allowed_tools empty=unrestricted, hook blocking only UserPromptSubmit/PreToolUse/PreCompact, memory aging only affects lessons, entity dedup uses rule scoring + optional LLM, compact chunked falls back to string summary per chunk.

## Conventions & Standards

File organization: skills in .claude/skills/, custom agents in .agent/agents/, personas in .agent/personas/, workflows in .agent/workflows/, reminders in src/mini_agent/prompts/reminders/, wiki pages in .agent/wiki/{entities,decisions,experiences,processes,topics}/, tests mirror src/ structure. Configuration priority: CLI args > config file > defaults. Tool registration via @tool() decorator in tools/builtin.py or new files under tools/. Prompt management: all LLM prompts as .md files under src/mini_agent/prompts/ loaded via PromptManager. Documentation: every major module has guide in docs/*.md. Git hygiene: providers.json auto-gitignored, self-evolution via StateRepo.apply() -> evolve/<date>-<type>-<name> branch -> human review -> merge, protected paths block direct commits.

## Open Questions & Future Work

10 open questions: 1) Fine-grained GoalExecutor (judge after each tool call), 2) AffordanceMap in single-user CLI (currently daemon-only), 3) Cognitive anchor in daemon REPL (Ctrl-C not hooked), 4) Wiki decommission execution (read-only plan exists, needs human), 5) Positive retrieval feedback loop (API exists, no auto-trigger), 6) Cross-project capability map aggregation (W3 scope, needs data), 7) Skill auto-proposal from exploration (ExplorationSandbox -> skill_propose not wired), 8) Decision confidence calibration (fixed 0.5, could be dynamic), 9) Persona system in multi-user (role/profile interaction needs clarification), 10) Behavior perception system (all switches default OFF, mobile collectors template-only).

## Key Files Quick Reference

24 key files mapped to purpose: Agent entry (core.py), LLM side-calls (service.py LLMHelper), Client pool (client_pool.py), History manager (history_manager.py), Compact triggers (triggers.py), Memory factory (memory_factory.py), Workdir knowledge (workdir_knowledge.py), Global knowledge (global_knowledge.py), Wiki indexer (indexer.py), Wiki search (search.py), StateRepo (state_repo.py), AutonomousLoop (autonomous_loop.py), Goal runner (runner.py), Proprioception (proprioception.py), AffordanceMap (affordance_analyzer.py), SelfMaintenance (self_maintenance.py), Workflow runner (runner.py), Daemon (daemon.py), Session pool (session_pool.py), Protected paths (protected_paths.py), Config loader (loader.py).

## Test Section

REMOVE

## Test2

REMOVE
