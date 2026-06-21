# 自我演化 lesson → skill 闭环（Stage 3.1 / Phase C）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 3.1，
> 设计依据 `next_doc/self_evolution_design.md` 第 6.1 节"角色分离"、
> 第 6.7 节"演化节奏治理"、第 4.4 节"分支与合并：evolve 分支取代 pending 目录"。

---

## 1. 这是什么

Stage 3.1 打通了"lesson → skill 提案"的完整闭环：

```
memory.jsonl 中的 lesson
      │
      │ /evolve review（扫描 + 阈值分组）
      ▼
evolution-agent（专职 sub-agent，只读 lesson + 调用 skill_propose）
      │
      │ skill_propose(name, content, source_lessons)
      ▼
evolve/<date>-skill-<name> 分支上的一个 commit（T1 校验通过才会产生）
      │
      │ 人工 /evolution show|diff 审核 → git merge（人工）/ /evolution revert（拒绝）
      ▼
合并后 skills/<name>/SKILL.md 在主分支生效，被 SkillLoader 发现
```

全程没有任何一步会让主分支（`main`/`master`）在"提案待审核"阶段出现新文件——
这是设计文档 4.4 节"evolve 分支取代 pending 目录"的直接体现：用 git 分支
天然具备的隔离性替代额外发明一套"待审目录"约定。

---

## 2. `skill_propose` 工具

```python
skill_propose(
    name="bash-rm-safety",
    content="---\nname: bash-rm-safety\ndescription: ...\n---\n正文...",
    source_lessons=["lesson_2026062001", "lesson_2026062003"],
    reason="repeated rm -rf incidents across multiple sessions",
)
```

### 2.1 行为

1. 生成分支名 `evolve/<今天日期>-skill-<name>`
2. 用 Stage 2.3 的 `EvolutionWorkspace.create()` 创建该分支对应的 `git worktree`
3. 在 worktree 内用 `StateRepo.apply()` 写入 `.claude/skills/<name>/SKILL.md`，
   tier **固定为 T1**（schema + 加载校验，复用 `validate_t1_load`——内容会
   真的过一遍 `SkillLoader._parse_skill`，保证"校验通过"等价于"运行时真的
   能加载"）
4. 校验通过 → 销毁 worktree，**保留分支**（这就是提案本身，等待审核）
5. 校验失败 → 销毁 worktree，**连分支一起删除**（没有产生有意义的内容，
   不留痕迹）

### 2.2 fresh-repo 修复

全新项目（`StateRepo` 刚 `git init`、还没有任何 commit）第一次触发自我演化时，
`git worktree add <path> -b <branch> HEAD` 会因为 `HEAD` 还不是有效引用而
失败（`fatal: invalid reference: HEAD`）——这正是 `next_doc/
self_evolution_implementation_plan.md` 中记录的 Stage 3.1 中断点。

修复方式：`StateRepo.ensure_initial_commit()` 在仓库没有任何 commit 时创建
一个空的初始 commit；`EvolutionWorkspace.create()` 在"基于默认 `base="HEAD"`
创建新分支"之前自动调用它兜底，调用方显式传入其他 `base` 时不做任何隐式
修复（按调用方意图失败更安全）。

### 2.3 返回值示例

```json
{
  "ok": true,
  "commit": "2f7ff162fda12c138340d1770372847584330784",
  "branch": "evolve/2026-06-21-skill-bash-rm-safety",
  "tier": "T1",
  "path": ".claude/skills/bash-rm-safety/SKILL.md",
  "message": "Skill 'bash-rm-safety' proposed on branch 'evolve/2026-06-21-skill-bash-rm-safety' (2f7ff162). This is NOT yet active — review with /evolution show 2f7ff162 or /evolution diff 2f7ff162, then merge the branch manually to apply it, or /evolution revert 2f7ff162 to discard."
}
```

校验失败时 `ok: false`，附 `validation_errors` 列表，不产生任何 commit。

### 2.4 权限与风险控制

- `skill_propose` 加入 `permissions.py` 的 `_RISKY_TOOLS`，`--sandbox` 模式下
  直接被拦截（与 `bash`/`write_file` 等写操作同等对待）
- `requires_approval=False`（不在每次调用时弹审批），真正的把关在三层：
  受保护路径清单（理论上不会命中，因为固定写 `skills/` 目录）、
  `StateRepo` 的 T1 校验流水线、提案天然落在独立分支等人工 merge
- `name` 参数做正则校验（小写字母数字连字符），防止路径穿越或非法文件名

---

## 3. `evolution-agent` profile

`.agent/agents/evolution-agent.md`，复用 Stage 1（自定义 sub-agent）已有的
`AgentProfile` 机制：

```yaml
---
name: evolution-agent
tools: skill_propose, read_file, grep, list_dir
inputs:
  - name: lessons        # required，/evolve review 传入达标的 lesson 分组
  - name: existing_skills  # optional，用于去重检查
---
```

- **`role_type` 留空**：不是"每次输出后自动触发"的角色（不同于 `evaluator`/
  `coach`），只能被 `/evolve review` 显式 spawn
- **工具集刻意收窄**：只有 `skill_propose` + 三个只读检查工具，**没有**
  `write_file`/`bash`/`spawn_agent` ——所有写入必须经过 `skill_propose`
  进而经过 `StateRepo` 的校验流水线，不允许绕过
- 正文要求模型：先聚类、对照已有 skill 去重、判断证据是否充分（`human_feedback`
  权重高于 `self_reflection`）、不值得提案时明确说明理由而不是为了完成任务
  硬提案

---

## 4. lesson 阈值扫描（`perception/lesson_review.py`）

设计文档 6.7 节的证据门槛：

| Tier | 触发条件 |
|---|---|
| T0 | occurrence_count ≥ 1 即自动 apply（暂未在 Stage 3.1 实现自动 apply，仅扫描判定） |
| T1 | occurrence_count ≥ 3 且来自不止一个 session |
| T2/T3 | occurrence_count ≥ 5，且至少一条来源为 human_feedback |

### 4.1 实现取舍：关键词 Jaccard 相似度分组

`MemoryEntry` 目前没有跨条目的去重/聚类机制（设计文档 6.4 节，明确留给后续
Phase G 的后台循环）；每条 lesson 各自独立存储，`occurrence_count` 字段语义
是"同一 session 内连续失败次数"，不是"跨 session 重复出现次数"。

Stage 3.1 用一个轻量级、非语义的分组手段打通闭环：提取 trigger 文本的关键词
集合，按 Jaccard 相似度（阈值 0.3）贪心聚类。一个分组的"有效 occurrence_count"
= 组内各条目 occurrence_count 之和；"是否来自不止一个 session" = 组内
session_id 去重后数量 > 1。

```python
from mini_agent.perception.lesson_review import scan_for_proposals

groups = scan_for_proposals(memory_backend.all_entries(), tier="T1")
for g in groups:
    print(g.key, g.total_occurrence, g.session_ids, g.has_human_feedback)
```

这不是设计文档 6.4 节描述的完整语义聚类（例如 embedding 相似度），精度
留给后续迭代提升；Stage 3.1 的目标是打通闭环本身。

---

## 5. `/evolve` CLI 命令

```
/evolve list   [--global] [--tier T1|T2]   # 只扫描+展示，不消耗 LLM 调用
/evolve review [--global] [--tier T1|T2]   # 扫描后对达标分组 spawn evolution-agent
```

- `--global`：扫描 `~/.agent/memory.jsonl`（全局记忆）而非项目级 `memory.jsonl`
- `--tier`：默认 `T1`；传 `T2`/`T3` 时要求至少一条来源为 `human_feedback`
- 没有任何分组达标时只打印提示，不会无意义地 spawn 一个空转的 evolution-agent
- `evolution-agent` profile 缺失（例如项目还没创建 `.agent/agents/evolution-agent.md`）
  时给出明确错误，而不是静默失败

---

## 6. 测试

```bash
pytest tests/test_lesson_review.py -v            # 阈值扫描与分组逻辑
pytest tests/test_skill_propose.py -v             # skill_propose 工具 + fresh-repo 修复
pytest tests/test_evolve_cli.py -v                 # /evolve review|list 命令
pytest tests/test_evolution_agent_profile.py -v    # evolution-agent.md 自身结构校验
```

共新增/调整约 78 个测试用例，全部通过；全项目 934 passed，3 个与本阶段无关
的预先失败用例未变化，无回归。

---

## 7. 相关文档

- [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) — Stage 3.1 的完整需求背景
- [自我演化设计文档](../next_doc/self_evolution_design.md) — 第 6 节运营机制与节奏治理
- [Stage 2 安全网指南](self-evolution-stage2-guide.md) — `StateRepo`/`EvolutionWorkspace`/`/evolution` 命令组，本阶段直接复用
- [自定义子 Agent 指南](custom-sub-agents.md) — `AgentProfile` 机制，`evolution-agent.md` 遵循的约定
- [记忆管理指南](memory-management-guide.md) — `MemoryEntry`/`occurrence_count`/`source` 字段语义

---

*创建时间：2026-06（self_evolution_implementation_plan.md Stage 3.1）*
