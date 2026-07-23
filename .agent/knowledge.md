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
