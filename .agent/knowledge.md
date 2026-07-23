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

## Key Architectural Decisions

### 1. Agent Core: Mixin-Based Composition (Stage 12+)

**Decision**: Split `agent.py` (single file) into 9 mixin files + `core.py` skeleton, assembled via multiple inheritance.

**Rationale**:
- Single file grew to 5000+ lines, hard to navigate
- Mixins map to clear responsibilities: `lifecycle.py`, `reflection.py`, `profile.py`, `llm_control.py`, `turn_loop.py`, `role_judge.py`, `reminders_correction.py`, `compaction.py`, `snapshot.py`
- External import path `from mini_agent.agent import Agent` unchanged

**Tradeoff**: Multiple inheritance adds complexity; `_helpers.py` holds shared module-level functions to avoid diamond inheritance issues.

---

### 2. LLMHelper: Unified Side-Call Entry Point (2026-07)

**Decision**: All LLM calls outside the main conversation loop MUST go through `LLMHelper` (`src/mini_agent/llm/service.py`).

**Rationale**: Before this, side-calls (judge, ensemble, goal decomposition, memory summarization, routing) each wrote their own `LLMConfig.from_app_config(cfg)` + `create_client()` boilerplate. Problems:
- No retry by default
- Didn't follow `/model` switches at runtime (used static startup config)
- Some passed unsupported `max_tokens=` to `chat()` silently ignored
- Inconsistent `max_retries` across call sites

**Enforcement**: `grep -rn "LLMConfig.from_app_config" src/` should only return `sub_agent.py` and `agent/core.py` (explicit exceptions).

**Usage**:
- With Agent ref: `agent.llm_helper.ask(prompt)` or `agent.llm_helper.chat(messages, ...)`
- Without Agent: `LLMHelper.from_config(app_cfg).ask(prompt)`
- Override model for judge: `agent.llm_helper.ask(prompt, override_model="claude-opus-4")`

---

### 3. Client Pool: Multi-Key Rotation + Multi-Config Fallback

**Decision**: Two-layer resilience in `src/mini_agent/llm/client_pool.py`:
1. **ApiKeyPool**: Multiple keys for same provider, strategies `passive` (switch on error) / `round_robin`
2. **LLMClientPool**: Ordered fallback chain of ProviderEntry (each with own key pool)

**Config**: `providers.json` (priority) > `agent_config.json.llm_fallback_chain`

**Key insight**: Auth failures (`LLMConfigError`) do NOT trigger fallback — changing config won't fix code bugs.

---

### 4. History: Raw JSONL Immediate Flush + Selective Compression

**Decision**: `RawHistory.append()` writes JSONL + `fsync` immediately. Compression uses `SelectiveStrategy` (default) with type-weighted scoring.

**Rationale**:
- Crash-safe: even `kill -9` preserves raw history
- Selective compression preserves user/assistant turns (weight 1.0/0.9) over tool results (0.4)
- Position weighting: recent 25% gets +0.2 boost
- Minimum user turns preserved (`selective_min_user_turns=3`)
- Orphan fixing: tool_results must pair with assistant_reply

**Trigger system** (`history/triggers.py`): CompositeTrigger with 5 independent triggers (token threshold, turn count, tool call count, redundancy ratio, topic shift) — OR combined, highest priority wins. Cooldown 3 turns between non-hard triggers.

---

### 5. Memory Layers: W2 (Workdir) + W3 (Global) + Wiki

**Decision**: Three parallel knowledge systems:
- **W2** (`.agent/`): `project.json`, `timeline.jsonl`, `work_index.json`, `open_threads.json`, `knowledge.md`
- **W3** (`~/.agent/`): `self_profile.json`, `projects_index.json`, `cross_project_index.json`, `activity_log.jsonl`
- **Wiki** (`.agent/wiki/`): Markdown pages with frontmatter + `[[link]]`, `_index/` derived indexes

**Key insight**: Wiki is now **primary retrieval path** (`MemoryConfig.library_wiki_search_primary=True` default). Library index (`shelf_search`) is fallback. Both coexist during transition.

**Wiki page types**: `entity`, `decision`, `experience`, `process`, `topic` — each with frontmatter schema in `_templates/`.

---

### 6. Self-Evolution Safety Net: StateRepo + Tiered Validation

**Decision**: All self-modifying operations go through `StateRepo.apply()` (`evolution/state_repo.py`) with tiered validation:
- **T0**: Schema validation only (fast, auto-apply for trivial changes)
- **T1**: Schema + load validation (skill loads, agent starts) — used for skill proposals
- **T2**: Full eval suite (skill vs no-skill comparison) — used for code changes

**Key invariant**: `initiator` field (`user`/`autonomous`/`cron`) propagates through `_TurnCommand` → `enqueue()` → `TurnInfo` → `StateRepo.apply()`. Autonomous-initiated T0 changes auto-promote to T1.

**Protected paths** (`scripts/protected_paths.py`): `agent/`, `permissions.py`, `hooks/`, `evolution/` — any change hitting these is forced to T3 (human review required).

---

### 7. Lesson Memory: Four Independent Write Paths

**Decision**: Lessons written immediately on trigger, not batched at session end. Four paths:
1. **Rule trigger** (`lesson_rules.py`): ≥3 consecutive failures OR permission denied then retry success → `confidence=0.6`, `source=self_reflection`
2. **SessionEnd reflection** (`agent/reflection.py`): Lightweight LLM call on exit → `source=self_reflection`
3. **Human feedback detection** (`correction_detector.py`): ~30 rule patterns (CN/EN) → `confidence=0.85`, `source=human_feedback`
4. **Edit approval** (`permissions.py` + `tool_executor.py`): `(e)dit` approval → `HType.USER_CORRECTION` + lesson

**MemoryEntry fields**: `entry_type`, `trigger`, `outcome`, `root_cause`, `suggested_action`, `confidence`, `occurrence_count`, `source`

---

### 8. AutonomousLoop: Three Tiers, Physical Method Boundaries

**Decision** (`evolution/autonomous_loop.py`): Three tiers with **method-level isolation** (not just config flags):
- **passive**: `CronScheduler.tick()` only — system maintenance jobs
- **maintenance**: + `ObjectiveExecutor` — executes accepted Goals/Objectives
- **autonomous**: + `SoftGoalDeriver` + `ExplorationSandbox` — proposes & validates new goals

**Rationale**: Prevents accidental capability leakage. Each tier is a separate method (`_tick_passive`, `_tick_maintenance`, `_tick_autonomous`).

**CronScheduler** (`evolution/cron_scheduler.py`): Interval/cron dual format. Built-in jobs: `sys:daily_digest`, `sys:next_action_digest`, `sys:decision_profile_update`, `sys:wiki_gap_scan`, `sys:wiki_fallback_cleanup`, `sys:self_maintain`, `sys:workdir_sync`, `sys:self_eval`, `sys:goal_review`, `sys:digest_trim`, `sys:consolidation`.

---

### 9. Goal Mode: Coarse-Grained Executor + Pinned Context

**Decision** (`goal_mode/`): `CoarseStepExecutor` runs one full `run_turn` per goal step. Goal context pinned via `HType.GOAL_CONTEXT` message type — re-attached after every turn AND after every compact.

**Safety valves**: `max_rounds`, `max_total_compacts`, consecutive similar feedback detection (difflib).

**Recovery**: `GoalState` atomic write to `.agent/sessions/<sid>/goal_state.json` at turn boundaries only. `/goal resume` picks up.

**Limitation**: Fine-grained executor (`GoalStepExecutor` interface reserved) not yet implemented — would judge after each tool call, not each turn.

---

### 10. Embodied Agent: Proprioception + AffordanceMap + AgentSelfModel

**Decision**: Explicit self-modeling modules (12 items across A/B/C/D phases):
- **ProprioceptionModule** (`perception/proprioception.py`): O(1) per-turn snapshot (cognitive load, uncertainty, risk, budget, frustration). Frustration spike + consecutive failures → metacognitive hint injection.
- **AffordanceMap** (`perception/affordance_analyzer.py`): Cross-references open_threads, capability_map, lesson memory → risk/opportunity summary. `high_risk_zones` persisted to `affordance_snapshot.json` (60min TTL). **Only active in multi-user daemon path** (gap).
- **AgentSelfModel** (`perception/self_model.py`): Aggregates SelfAssessment (slow) + capability_map + Proprioception (fast) + AffordanceMap (fast). Distinct from UserProfile/RoleProfileManager/AgentProfile.
- **Memory aging** (`evolution/memory_aging.py`): Lesson decay by source (human_feedback 90d half-life → revert_record 14d) × occurrence_count (max 4x slower).
- **Cognitive anchor** (`agent/lifecycle.py`): Ctrl-C → LLM generates 4-section "mind rebuild guide" → injected as `system_extra` next session. **Not yet hooked in daemon connected REPL**.
- **SelfMaintenanceModule** (`evolution/self_maintenance.py`): Three health checks (stale_tools from traces, stale_skills from tracker, conflicting_lessons from clustering) → writes `activity_digest.jsonl` type=health_report. SessionEnd time-gated + `sys:self_maintain` cron.

**Integration with autonomy** (4 schemes):
1. AffordanceMap high-risk zones → ExplorationSandbox token limit tightening
2. BehaviorContext (shared by AffordanceMap + ResourceArbiter) → user presence gating for autonomous tasks
3. Proprioception.uncertainty sustained → `proprioception.uncertainty_sustained` event → ResourceArbiter attention
4. AgentSelfModel.recent_negative_outcome_domains() → SoftGoalDeriver candidate downweighting (single-scenario validated)

---

### 11. Wiki Knowledge Base: Three-Stage Retrieval + Lifecycle State Machine

**Decision** (`wiki/`):
1. **Rule coarse filter**: Tag/keyword + O1 `grounded_hit_count` confidence weighting + O4 `lifecycle_discount` (optional)
2. **Graph expansion**: `GraphIndex.expand(strong_only=True, max_hops=2, decay=0.7)` — multi-hop with decay
3. **LLM rerank**: Requires "基于页面:" citation in response → parsed to `grounded_page_ids`

**Lifecycle (O4)**: `knowledge_state` ∈ {`fresh`, `stale`, `superseded`} + `last_validated_at` + `validated_by`. `mark_page_state()` unified entry. `stale_candidate_scan()` for `/wiki lifecycle-scan`.

**Topic re-consolidation (O3)**: Existing topic pages absorb new qualifying pages (tag cluster + strong link density ≥0.5, ≥4 pages) instead of creating new topics. Logged to `_index/topics_reconsolidation_log.jsonl`.

**Decommission (next phase)**: `wiki/decommission.py::check_and_plan()` — read-only evaluation against promotion criteria (content ratio, validation rate, A/B hit rate). Outputs 3-step checklist: disable legacy index → observe ≥2 weeks → remove old files. **No auto-delete**.

---

### 12. Decision/Tradeoff Extraction: Compact-Phase Piggyback + Batch Consolidation

**Decision**: Decision candidates extracted during compact (reuse summary LLM call), output structured JSON `{compact_summary, decisions[]}`. Only queued to `.agent/decision_candidates_pending.jsonl` — **not written immediately**.

**Consolidation** (`wiki/decision_writer.py::consolidate_pending()`): Runs in consolidation loop. Merges same-batch candidates (topic slug overlap or entity intersection). Three-way:
- Hit existing decision, same choice → update
- Hit existing, choice changed → old `status=overturned`, new `supersedes`/`superseded_by` chain
- Miss → new `status=settled` (subject to 1-day rhythm gate)

**Confidence fixed at 0.5** (lower than lesson 0.6, human_feedback 0.7) — decisions are agent's retrospective reconstruction, higher confabulation risk.

**Pre-proposal recall** (`evolution/decision_recall.py`): `/evolve review` auto-queries wiki for `type=decision` pages before spawning evolution-agent. Injected into task context.

---

### 13. Workflow: Directory Mode + Defaults + Includes + Token Guardrails

**Decision** (`.agent/workflows/<name>/workflow.yaml`):
- **Directory mode**: Private `agents/`, `skills/`, `prompts/` per workflow
- **Defaults**: Top-level `defaults:` block inherited by all steps
- **Includes**: Reusable step fragments via `include:`
- **Token guardrail**: `max_total_tokens` per workflow run
- **Plugin steps**: Custom step types via plugin system

**Execution**: `run_workflow` tool, background mode for approval steps (`require_approval: true`).

---

### 14. Multi-User Daemon: Session Pool + Dual-Gate Bridge

**Decision** (`api/`):
- `SessionAgentPool`: Per `(user_id, session_id)` isolated Agent + AgentBridge
- Idle timeout 30min, max concurrent 20, crash recovery
- `HttpPermissionGate` + `HttpInteractionGate` (unified `InputQueue.enqueue(initiator, meta)`)
- `SelfMessageBus` for inter-session communication
- Roles: owner/family/colleague/agent/public → tool permissions + resource quotas

---

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
