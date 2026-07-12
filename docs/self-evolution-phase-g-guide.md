# Phase G 后台循环指南（Stage 8）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 8，
> 设计依据 `next_doc/self_evolution_design.md` 第 6.4/6.5/6.6/6.7 节。

---

## 1. 这是什么

Phase G 是 mini_agent 自我演化系统的**后台循环**：定期扫描已有数据，主动识别"可以改进的机会"。它由四个扫描任务组成，通过一个时间门控机制触发，**不需要常驻进程**。

```
触发方式（二选一）：
  A) 手动：/evolve phase-g
  B) 自动：任意 session 结束时检查"距上次运行是否超过 24h"

每次运行执行三个扫描（按顺序）：
  8.2  剪枝候选  — "哪些 skill 占 token 高但长期没用？"
  8.3  能力地图  — "我擅长什么？不擅长什么？"
  8.4  晋升候选  — "哪些模式在多个项目里反复出现，值得提炼为 global skill？"

节奏治理（8.5）：
  同一个 skill / 模式，7 天内只提一次建议
```

---

## 2. 手动触发：/evolve phase-g

```bash
/evolve phase-g            # 标准运行（受时间门控保护）
/evolve phase-g --force    # 忽略 24h 限制，强制运行
/evolve phase-g --dry-run  # 只展示报告，不写入节奏治理记录
```

**示例输出**：

```
[phase-g] 开始扫描…

⚠  剪枝候选
──────────────────────────────────────────────────────
  Skill              Reason                            Last Used (days)
  python-legacy      token_cost=3200 > 2000, last_used=18d ago  18d
  old-git-helper     conflicts_with=['git-workflow']   45d

📊 能力地图（已写入 memory）
──────────────────────────────────────────────────────
  Domain        Confidence         ✓ / ✗
  python        ▓▓▓▓▓▓▓▓▓░ 90%    45/5
  testing       ▓▓▓▓▓▓▓░░░ 70%    21/9
  devops        ▓▓▓▓▓░░░░░ 50%    8/8

🚀 跨项目晋升候选
──────────────────────────────────────────────────────
  Pattern                        Projects  Confidence  Suggested Skill
  always use bash -e for scripts  3        85%         always_use_bash_e_for

  提示：用 /evolve review 触发 evolution-agent 将候选转为 skill 提案

Phase G 完成，共发现 2 个剪枝候选、1 个晋升候选
```

---

## 3. 自动触发（时间门控）

每次 `trigger_session_end()` 时，`_maybe_run_phase_g()` 检查 `.agent/phase_g_rhythm.json` 里的 `_last_run_at`：

```
elapsed = now - last_run_at
if elapsed >= 24h:
    run_phase_g()  # 后台运行，有发现时打印单行摘要
    # "[phase-g] 发现 2 个剪枝候选，用 /evolve phase-g 查看详情。"
```

若无发现则静默退出，不打印任何内容。

---

## 4. 三个扫描任务详解

### 4.1 8.2 剪枝候选扫描

**目标**：识别"占 token 多但近期没被用到"的 skill，以及存在冲突声明的互斥 skill 组合。

**判定规则**（满足其一即列为候选）：

| 规则 | 说明 |
|------|------|
| 高成本未使用 | `token_cost > 2000` 且 `last_used > 14d` |
| 冲突检测 | skill 的 `conflicts_with` 字段里有其他**已激活** skill |

> **不自动删除**：Phase G 只输出候选列表，不执行任何 skill 的停用操作。需要人工确认后通过 `/skill deactivate <name>` 下线。

**冷却期**：同一个 skill 7 天内只提一次剪枝建议（受节奏治理约束）。

**冲突声明示例**（在 SKILL.md frontmatter 里）：

```yaml
---
name: old-git-helper
description: 旧版 git 工作流辅助
conflicts_with:
  - git-workflow    # 与新版 skill 互斥，不应同时激活
---
```

### 4.2 8.3 能力地图生成

**目标**：统计 agent 在各类任务上的成功率，自动写入 `memory.jsonl`（`entry_type="capability_map"`）。

**数据来源**：扫描 `.agent/sessions/*/tasks/*/manifest.json`，读取 `outcome.status`（done/failed/cancelled）和 `goal` 字段。

**domain 推断规则**（按优先级匹配，第一条命中为准）：

| domain | 触发关键词 |
|--------|-----------|
| `refactor` | refactor、重构、clean up、整理代码 |
| `testing` | test、测试、pytest、assert |
| `bug_fix` | bug、fix、修复、debug |
| `documentation` | document、docs、readme、注释 |
| `devops` | docker、container、k8s |
| `bash_scripting` | bash、shell、脚本、.sh |
| `api_dev` | api、endpoint、route、graphql |
| `git` | git、commit、branch、merge |
| `database` | sql、database、db、query |
| `frontend` | 前端、css、html、react、vue |
| `python` | django、flask、fastapi |
| `general` | 其他（兜底） |

**能力地图 memory 条目格式**：

```
能力地图（Phase G 自动更新）

python:      ▓▓▓▓▓▓▓▓▓░ 90% (success=45 fail=5)
testing:     ▓▓▓▓▓▓▓░░░ 70% (success=21 fail=9)
devops:      ▓▓▓▓▓░░░░░ 50% (success=8 fail=8)
general:     ▓▓▓░░░░░░░ 30% (success=3 fail=7)
```

这条 memory 条目会被 Stage 8.4（Scope 晋升）和 `/diagnostics` 端点消费，也会出现在 memory 检索结果里，帮助 agent 在分配子任务时有数据依据。

### 4.3 8.4 Scope 晋升候选

**目标**：从 `~/.agent/cross_project_index.json` 里找出达到晋升门槛的跨项目模式，建议提炼为 global skill。

**晋升门槛**（全部满足）：

| 条件 | 默认值 | 说明 |
|------|--------|------|
| `observed_in_projects` | ≥ 2 | 在至少 2 个项目里出现 |
| `confidence` | ≥ 0.70 | 置信度（保守，可按实际情况调高）|
| `global_skill_candidate` | `true` | 在 cross_project_index 里标记为候选 |
| 冷却期 | ≥ 7d | 同一模式 7 天内不重复提案 |

**晋升不自动发生**：Phase G 只输出候选列表，真正的 skill 提案需要手动运行 `/evolve review` 触发 evolution-agent 来完成。这遵循"高风险操作需人工确认"的一贯原则（T1 级别）。

---

## 5. 节奏治理（8.5）

### 5.1 冷却期机制

Phase G 使用 `.agent/phase_g_rhythm.json` 记录提案历史：

```json
{
  "_last_run_at": 1720000000.0,
  "prune:python-legacy": 1720000000.0,
  "promote:cxp-001": 1719913600.0
}
```

每次 Phase G 运行后，对**已生成**的候选记录时间戳。下次运行时，如果某条候选的上次提案时间距今不足 7 天，则跳过（不再出现在报告里）。

### 5.2 观察期（T1 安全网）

设计文档第 18 节开放问题 1 的回答：**T1 级别的自动合并需要先观察 5 个 session**（`observation_window_sessions=5`，可配置）。这个参数当前记录在晋升候选的元数据里，evolution-agent 在决定是否自动提案时会读取，不在 Phase G 本身做阻塞。

---

## 6. 与其他系统的联动

```
Stage 4 W2 (work_index / open_threads)
  └─ 提供 manifest.json 数据 ──→ Phase G 8.3 能力地图

Stage 5 W3 (cross_project_index)
  └─ 提供跨项目模式 ────────→ Phase G 8.4 晋升候选

Stage 6 (observability / traces.jsonl)
  └─ 提供 context_breakdown ──→ Phase G 8.2 token 成本估算（未来扩展点）

Stage 6 (activity_log / session_metrics)
  └─ 提供基线数据 ──────────→ Stage 6.3 异常检测

Stage 3.1 (/evolve review / skill_propose)
  └─ 晋升候选触发 ──────────→ evolution-agent 提案 ──→ evolve/* 分支
```

---

## 7. 代码入口速查

| 功能 | 位置 |
|------|------|
| 整体模块 | `src/mini_agent/evolution/phase_g.py` |
| `run_phase_g()` | 整体运行入口 |
| `should_run_phase_g()` | 时间门控检查（24h）|
| `prune_skills()` | 8.2 剪枝候选扫描 |
| `build_capability_map()` | 8.3 能力地图生成 |
| `check_scope_promotion()` | 8.4 晋升候选扫描 |
| `rhythm_is_allowed()` / `record_proposal()` | 8.5 节奏治理 |
| `_infer_domain()` | goal 文本 → domain 标签推断 |
| 时间门控接入点 | `agent.py → _maybe_run_phase_g()` |
| CLI 命令 | `src/mini_agent/cli/commands/evolve.py → _handle_phase_g()` |
| 节奏治理状态文件 | `.agent/phase_g_rhythm.json` |

---

## 8. 测试

```bash
python -m pytest tests/test_phase_g.py -v
```

覆盖：节奏治理（7 个）、domain 推断（8 个）、能力地图（5 个）、晋升候选（6 个）、整体入口（7 个）。

---

## 9. 常见问题

### Q: `/evolve phase-g` 什么都没输出？

时间门控在起作用——24h 内已经运行过了。用 `--force` 强制运行，或等到下次 session 结束时自动触发。

### Q: 剪枝候选里的 skill，删除它安全吗？

Phase G 只给**建议**，不执行操作。用 `/skill info <name>` 看详情，确认无误后用 `/skill deactivate <name>` 停用（不删除文件）。

### Q: 晋升候选如何变成真正的 global skill？

目前不会自动变成 skill。你可以：
1. 查看晋升候选的描述和 `suggested_skill_name`
2. 手动写一个对应的 SKILL.md，放在 `~/.claude/skills/` 或调用 `/evolve review` 让 evolution-agent 起草

### Q: cross_project_index 为空，Phase G 没发现晋升候选？

`cross_project_index.json` 由 Stage 5.4 的 `scan_cross_project_patterns()` 生成——需要在至少 2 个不同项目里活跃过，才会有跨项目模式数据。可以手动调用：

```python
from mini_agent.perception.global_knowledge import scan_cross_project_patterns, merge_cross_project_patterns
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(your_project_root)
patterns = scan_cross_project_patterns(paths)
merge_cross_project_patterns(paths, patterns)
```

---

## 10. 相关文档

- [自我演化 Stage 4 & 5 指南](self-evolution-stage4-5-guide.md) — W2/W3 知识层数据来源
- [观察性系统指南](observability-guide.md) — traces.jsonl + 异常检测
- [自我演化 lesson → skill 闭环（Stage 3.1）](self-evolution-stage3-1-guide.md) — 晋升候选的后续处理
- [Skill 系统指南](skill-system-guide.md) — `conflicts_with` / `confidence_score` 字段说明
- [命令与工具参考](commands-and-tools-reference.md) — `/evolve phase-g` 命令参数
- [四项优先改进指南](four-priority-improvements-guide.md) — Affordance 权重闭环校准（`calibrate()`，Phase G 新增步骤）
