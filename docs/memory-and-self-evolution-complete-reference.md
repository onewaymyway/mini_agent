# mini_agent 记忆机制、自我进化机制与具身智能机制 完整技术文档

> 本文档系统整理 `mini_agent` 项目中所有与「记忆」（Memory）、「自我进化」
> （Self-Evolution）、「具身智能」（Embodied Agent）相关的机制，覆盖数据结构、
> 存储、检索、触发链路、安全治理、命令入口。适合作为架构级参考文档使用。
>
> 涉及源码目录：`src/mini_agent/perception/`（记忆存储/检索/图书馆式索引/具身感知模块）、
> `src/mini_agent/evolution/`（自我进化全流程）、`src/mini_agent/tools/evolution.py`
> （自我进化工具）、`src/mini_agent/cli/commands/{evolve,evolution}.py`（命令入口）、
> `src/mini_agent/reminders/`、`src/mini_agent/workflow/`（具身改进关联模块）。

---

# 第一部分：记忆机制（Memory）

## 1. 总体架构

```
Agent / 各感知模块
      │ add() / search() / all_entries()
      ▼
MemoryBackend（抽象接口，perception/memory_base.py）
      │
      ├── MemoryStore（内置实现，perception/memory_store.py）
      │       JSONL 文件 + TF-IDF/关键词检索 + 时间衰减
      │       ├── LibraryIndex（可选挂载，perception/library_index.py）
      │       │       ├── ClassificationTree（分类树/书架，classification.py）
      │       │       ├── EntityStore（实体/著者目录，entity_index.py）
      │       │       └── CategoryCatalog（分类指针 + 知识编年，catalog.py）
      │
      └── （扩展点，未内置）ChromaMemoryBackend / RedisMemoryBackend / SQLiteMemoryBackend
```

**接入新后端的方式**（`memory_base.py` 头部注释明确写明）：继承 `MemoryBackend`，
实现 `add()` / `search()` / `search_by_tag()`，在 `memory_factory.py` 的 `_REGISTRY`
中注册即可，Agent 与 ContextBuilder 只依赖抽象接口，不感知具体实现。

## 2. MemoryEntry 数据结构

`perception/memory_store.py::MemoryEntry`，一个 dataclass，字段按用途分为四组：

### 2.1 基础字段
| 字段 | 说明 |
|---|---|
| `session_id` | 产生该条记忆的 session |
| `summary` | session 摘要 |
| `key_outcomes` | 关键结论列表 |
| `tags` | 自动提取的标签 |
| `model` | 使用的模型 |
| `created_at` | 创建时间戳 |
| `entry_id` | 12 位 uuid hex，自动生成 |
| `scope` | `"project"`（项目特有）\| `"global"`（跨项目通用，写入 `~/.agent/memory.jsonl`）|

### 2.2 Lesson Memory 扩展字段（Stage 1）
| 字段 | 说明 |
|---|---|
| `entry_type` | `"summary"` \| `"lesson"` \| `"capability_map"` |
| `trigger` | 触发场景描述 |
| `outcome` | 实际发生了什么 |
| `root_cause` | 根因 |
| `suggested_action` | 下次该怎么做 |
| `confidence` | 0~1 可信度 |
| `occurrence_count` | 同类 lesson 重复出现次数 |
| `source` | `"self_reflection"` \| `"human_feedback"` \| `"revert_record"` |

### 2.3 图书馆式索引扩展字段
| 字段 | 说明 |
|---|---|
| `category` | 分类树节点号，如 `"000.003"`（未归类为 `"000"`），由 `LibraryIndex.on_new_entry()` 在 `add()` 时自动填充 |
| `entity_ids` | 关联的实体 ID（`entity_index.py`） |

### 2.4 派生方法
- `to_search_text()`：检索文本拼接。`summary` 型只拼 `summary + key_outcomes + tags`；
  `lesson` 型额外纳入 `trigger/outcome/root_cause/suggested_action`，否则这些字段
  内容无法被检索到。
- `age_days`：`(now - created_at) / 86400`。

## 3. MemoryStore：检索与持久化

### 3.1 检索算法演进（v2 修复记录，直接写在模块头部注释里）
1. **中文分词粒度**：改用双字/三字 n-gram 替代逐字切分，提升"数据库连接"这类
   复合词的 TF-IDF 召回率（`_tokenize()`）。
2. **时间衰减**：搜索评分乘以指数衰减因子 `exp(-λ * days_ago)`，防止旧记忆
   持续干扰当前上下文检索。
3. **条目上限**：超过 `max_entries`（默认 500）时自动淘汰最旧条目。
4. **持久化改写**：淘汰后 `_rewrite_disk()` 重写整个文件（而非只追加），
   保持磁盘与内存一致。

### 3.2 具身改进 v3 C2：时间加权记忆激活（`evolution/memory_aging.py`）
- 普通条目（`summary` 等）仍用全局统一半衰期 `_DECAY_HALF_LIFE_DAYS = 30.0` 天。
- **lesson 型条目不再用全局半衰期**，而是按 `source + occurrence_count` 计算
  专属半衰期：`compute_half_life_days(entry)`。
  - 被人类亲自纠正的（`source == "human_feedback"`）、被反复印证的
    （`occurrence_count` 高）经验衰减更慢。
  - 一次性的回退记录（`source == "revert_record"`）衰减更快。
- `compute_decay_factor(entry)`：结合上述专属半衰期计算最终衰减系数，
  接入 `MemoryStore._score_all()` 的排序逻辑。

### 3.3 MemoryStore 主要方法
| 方法 | 说明 |
|---|---|
| `add(entry)` | 持久化一条记忆，超限自动淘汰最旧条目 |
| `delete_by_session(session_id)` | 按 session 删除 |
| `upsert(entry)` | 按 `session_id` 更新（默认实现：先删后加），用于同一 session 多次刷新摘要 |
| `reload()` | 从磁盘重新加载 |
| `search(query, k=3)` | TF-IDF + 时间衰减排序，返回 top-k |
| `search_by_tag(tag)` | 精确标签匹配 |
| `rank_subset(query, subset, k=3)` | 只在给定子集内重排（供 `LibraryIndex.shelf_search()` 两步检索的第二步复用） |
| `rewrite_categories(updates)` | 巩固循环 知识巩固时批量改写分类号 |
| `library` | 属性，返回挂载的 `LibraryIndex` 实例（若有） |
| `count` / `all_entries()` | 条目总数 / 全量条目列表 |

## 4. 图书馆式索引子系统（`perception/library_index.py` 等四个模块）

### 4.1 设计动机
不是"关键词 → 文档"的扁平倒排索引，而是先有一套**分类体系**（书架），新记忆
先"上架"到某个分类节点，检索时**先定位书架、再在架内精细检索**——这是本项目
用来替代传统扁平 TF-IDF 检索的核心改进（见项目历史：*"设计并实现库风格的知识
索引系统，用自增长分类树取代扁平 TF-IDF 检索"*）。

### 4.2 四个子模块的职责划分
| 模块 | 职责 |
|---|---|
| `classification.py` | 分类树（书架结构）本体：节点表、规则匹配、LLM 兜底分类、巩固循环 批量新增节点 |
| `entity_index.py` | 实体/著者目录：记忆条目关联的实体 ID |
| `catalog.py` | 分类指针索引 + 知识编年目录（`CategoryCatalog`） |
| `library_index.py` | 组合外观（Facade），把前三者串成统一对外接口 |

### 4.3 分类树的生长规则（`classification.py`）
分类树**完全由系统运行时自动归纳生长**，不预置人工分类表：
1. 冷启动时只有一个根节点 `"000 未分类"`（`ROOT_CODE`）。
2. 新记忆生成时，先用当前树节点关键词做规则匹配（`classify_by_rule`），
   命中阈值 `_MIN_RULE_SCORE = 2`。
3. 规则不中时，允许兜底调用一次 LLM，但 LLM 只被要求"从现有节点里选一个
   最接近的，或明确回答 NONE"——**不会凭一次判断就新建分类节点**，避免树被
   单条易变的记忆污染。
4. 真正的"新增分类节点"**只在 巩固循环 巡检时批量发生**：未分类候选积累到
   `_MIN_CLUSTER_SIZE = 5` 条、且彼此关键词高度重合时，才聚类归纳出一个新
   节点（`grow_from_candidates`）——对应图书馆"新学科出现才增设类目"的稳态性。
5. 候选队列上限 `_MAX_CANDIDATES_KEPT = 500`，超出淘汰最旧。

持久化：`classification_tree.json`（节点表）+ `unclassified_candidates.jsonl`
（候选队列，巩固循环 处理后清空/归档）。两者都是可重建的观察性数据，不经
`StateRepo`（不属于"自我修改"，不需要 git 版本化）。

### 4.4 LibraryIndex 对外接口
| 方法 | 说明 |
|---|---|
| `on_new_entry(entry, llm_call=None)` | 新记忆写入 `MemoryStore` 时调用：分类 → 挂实体 → 更新目录 → 记编年事件 |
| `shelf_search(store, query, k, llm_call=None)` | 两步检索：先定位书架，再只在书架范围内精排；书架内容太少时回退全库检索 |
| `record_retrieval_feedback(query, useful, llm_call=None)` | 检索命中质量的自我反馈——命中书架后续被验证有效/无效时调用，累积调整该书架 `feedback_score`，让分类器越用越准 |
| `mark_stale_from_correction(store, injected_entry_ids, correction_text)` | 人类纠正 → 定位刚被检索命中、可能已过时的旧知识 → 标记冲突/推翻，而不是任由新旧知识并存靠时间衰减慢慢盖过去 |
| `consolidate(store, llm_call=None, min_cluster_size=5, summary_threshold=3)` | 巩固循环 巡检调用：批量处理未分类候选（新增/合并分类节点）、批量重写攒够证据的实体摘要（含冲突检测）、实体去噪与近重复合并 |

## 5. Lesson Memory 的产生机制

Lesson 条目共有**四种来源**（`MemoryEntry.source` 字段），对应四条完全不同的
写入路径（第三点 `experiment_confirmed` 与具身智能 ExplorationSandbox 相关，
详见第三部分第 6 节）：

### 5.1 `self_reflection`：SessionEnd 自我反思
Agent 在 session 结束时对本次 session 的表现做反思，生成的 lesson。

### 5.2 `human_feedback`：两条独立触发路径
**路径一（规则触发引擎，`perception/lesson_rules.py::LessonRuleEngine`）**：
不依赖 LLM，纯规则触发，在每次工具调用结果产生后由调用方（`ToolExecutor`
挂载点）调用 `observe(tool_name, tool_input, allowed, result_str, is_error)`。
内置两条规则：

- **规则一：连续失败**（`_check_consecutive_failure`）——同一工具连续失败达到
  `fail_threshold`（默认 3）次，生成一条 lesson，并用 `_fail_lesson_emitted`
  避免同一失败区间内重复生成。
- **规则二：拒绝后重试成功**（`_check_denial_retry_success`）——用户曾经拒绝
  某个工具调用（`allowed=False`，记录为 `_PendingDenial`），之后同一工具被
  允许执行且成功，说明用户当时的拒绝是"有意义的介入"，生成一条
  `source="human_feedback"` 的 lesson。

**路径二（直接纠正短语检测，`perception/correction_detector.py`，具身改进
A2）**：检测用户消息中的直接纠正短语（"不对"、"应该用 xxx"、"下次记得..."
等），命中即立即生成 `entry_type="lesson"`、`source="human_feedback"` 的
条目，不需要等到工具调用层面的连续失败/拒绝重试才触发。

`human_feedback` 是四种来源里被认为"用户亲自纠正过"、`memory_aging.py`
计算半衰期时**衰减最慢**（90 天）的来源。

### 5.3 `experiment_confirmed`：ExplorationSandbox 验证通过
由具身智能 B4/SoftGoalDeriver 关联的 `ExplorationSandbox` 验证通过后生成
（详见第三部分第 6 节），半衰期 60 天。

### 5.4 `revert_record`：`/evolution revert` 联动
`cli/commands/evolution.py::_record_revert_lesson()`：每次执行 `/evolution
revert <commit>`，自动生成一条 `source="revert_record"` 的 lesson，记录
"曾提案改动 X，已被判定不应保留、撤销"——**回退记录反哺 lesson 库**
（设计文档 4.3 节），半衰期最快（14 天，一次性事件，不代表持续性问题）。

四种来源的完整半衰期对照表见第三部分第 5 节（C2 时间加权记忆激活）。

## 6. workdir_knowledge：open_threads（未完成线索）

`perception/workdir_knowledge.py::load_open_threads()`——独立于 `MemoryEntry`
体系的另一种项目级知识，记录"当前项目里已知但尚未解决的问题/线索"
（`OpenThread`：`title` / `status`（open/closed）/ `priority`（high/medium/low）/
`type`（bug/blocker/...））。这是 `AffordanceAnalyzer`（见具身智能相关文档）
交叉分析的三路输入之一。

## 7. 隐私边界（`perception/privacy_guard.py`）

Agent 自身记忆体系与用户行为感知层（`perception/behavior/`）是完全独立的
两套系统，其中 `PrivacyGuard` 负责流式 token 缓冲下的占位符替换/脱敏，
保证敏感信息不会未经处理地进入 system prompt 或落盘记忆。

## 8. 多用户 / 多 Scope（`perception/memory_factory.py`）

| 函数 | 说明 |
|---|---|
| `create_memory_backend(cfg, user_id=None)` | 项目级（`scope="project"`）后端，多用户 daemon 下按 `user_id` 隔离 |
| `create_global_memory_backend(cfg, user_id=None)` | 跨项目通用（`scope="global"`，写入 `~/.agent/memory.jsonl`） |
| `create_both_memory_backends(cfg, ...)` | 同时创建 project + global 两个后端 |
| `merge_search(...)` | 跨两个后端的合并检索 |
| `register_memory_backend(...)` / `list_memory_backends()` | 后端注册表（供扩展 Chroma/Redis 等） |
| `set_llm_classify_call` / `build_llm_call` | 给 `LibraryIndex` 注入分类用的 LLM 调用（可选） |

**跨项目知识聚合**（`perception/global_knowledge.py`）：`cross_project_index.json`
做**跨项目**（不是跨用户）的模式/能力地图聚合。**目前没有跨用户聚合机制**——
多用户 daemon 下，owner 和 family/colleague 各自的 lesson memory、
capability_map 完全隔离（详见 `docs/multi-user-guide.md`）。

## 9. 记忆相关命令

| 命令 | 说明 |
|---|---|
| `/memory` | 查看/管理记忆（具体子命令见 `docs/commands-and-tools-reference.md`） |
| `/evolution lessons-to-reminders` | 把符合条件的 lesson 转成动态 reminder（`evolution/lesson_to_reminder.py`） |

---

# 第二部分：自我进化机制（Self-Evolution）

## 1. 总体架构与 Stage 划分

自我进化系统按照实现顺序分 Stage 0~9 逐步搭建，各 Stage 职责：

| Stage | 内容 | 关键模块 |
|---|---|---|
| Stage 1 | Lesson Memory（见第一部分第 5 节） | `perception/lesson_rules.py` |
| Stage 2 | 安全网三件套：Risk Tier（T0~T3）+ StateRepo + 验证流水线 | `evolution/state_repo.py`、`evolution/validators.py` |
| Stage 2.3 | EvolutionWorkspace（git worktree 隔离） | `evolution/workspace.py` |
| Stage 2.4 | `/evolution` 命令组（log/show/diff/revert） | `cli/commands/evolution.py` |
| Stage 3.1 | Lesson → Skill 提案闭环 | `perception/lesson_review.py`、`tools/evolution.py::skill_propose` |
| Stage 3.2 | eval 反馈环 | `evolution/eval_runner.py`、`mini-agent eval` |
| Stage 8 | 巩固循环 后台循环（剪枝/能力地图/晋升/知识巩固） | `evolution/consolidation.py` |
| Stage 9 | 自治运行时：GoalBacklog / AutonomousLoop / ResourceArbiter / CronScheduler | `perception/goal_backlog.py`、`evolution/autonomous_loop.py`、`evolution/resource_arbiter.py`、`evolution/cron_scheduler.py` |
| [新增] | 效果回填（用户真实反馈闭环） | `evolution/outcome_tracker.py` |

## 2. 安全网三件套（Stage 2）

### 2.1 Risk Tier（风险分级）
```
T0 < T1 < T2 < T3
```
`evolution/state_repo.py::StateRepo.resolve_tier()`：

- **T3 是治理红线**：风险分级逻辑、merge 门槛判定、worktree 隔离逻辑本身
  都属于 T3——即使调用方传入 T0/T1/T2，只要改动命中受保护路径清单
  （`is_protected_path()`），一律**强制升级为 T3**，且**只升不降**。
- **initiator 上浮规则**（Stage 9 §9.2）：当 `initiator` 为 `"autonomous"`
  或 `"scheduled"` 且 `effective_tier == "T0"` 时，自动上浮为 T1——
  "用户主动要求的 T0 改动可以直接 apply，但自主发起的同样改动需要多一层
  验证"。只处理 T0→T1 这一档上浮，T1/T2/T3 不因 initiator 改变。

### 2.2 验证流水线（随 tier 升级，`evolution/validators.py`）
| Tier | 验证内容 |
|---|---|
| T0 | schema 校验 |
| T1 | schema/加载校验 + eval 场景对比（tool 失败率/turns/token） |
| T2 | lint + 类型检查 → 现有单测全过 → 副本进程 smoke boot → eval 场景对比 |
| T3 | 同 T2，且 diff 必须显式标红展示，强制人审 |

所有校验函数统一签名 `(root: Path, changes: ChangeSet) -> ValidationResult`，
可直接作为 `StateRepo.apply()` 的 `validators` 参数传入；校验失败必须返回
明确原因（`ValidationResult.failure(reason)`），不允许静默失败。

### 2.3 StateRepo：所有自我修改的唯一入口
`evolution/state_repo.py::StateRepo.apply(changes, message, meta, tier, validators, initiator=...)`：
1. 计算实际生效 tier（受保护路径强制升级）
2. 按 `effective_tier` 自动选取对应校验函数（避免"请求 T0 但被升级为 T3，
   调用方却仍只传 T0 校验函数"的不一致）
3. 校验通过后才真正 commit，写入 `[Tn][来源] message` 格式的 commit message
4. `revert(commit)`：生成 revert commit（不是 `git reset`，保留完整历史）

### 2.4 分支隔离：evolve 分支取代 pending 目录（4.4 节设计）
`evolution/workspace.py::EvolutionWorkspace`：一次"进化尝试" = 创建分支
`evolve/<date>-skill-<name>` + 对应 worktree，在该隔离环境里 `apply()`，
**不直接写当前 checkout 的分支**（通常是 main/master）：
- 审核 = `git diff main..evolve/xxx`（即 `/evolution diff`）
- 批准 = merge（**人工操作**，工具层不提供"自动合并到 main"的能力）
- 拒绝 = 删分支（`/evolution revert` 或直接删除分支）

worktree 内的 commit 通过共享的 git 对象库，从主仓库天然可见，不需要
额外同步机制。

## 3. Lesson → Skill 提案闭环（Stage 3.1）

### 3.1 阈值扫描（`perception/lesson_review.py`）
| Tier | 触发条件 |
|---|---|
| T0 | `occurrence_count ≥ 1` 即自动 apply |
| T1 | `occurrence_count ≥ 3` 且来自不止一个 session（`T1_MIN_OCCURRENCE=3`，`T1_MIN_SESSIONS=2`） |
| T2/T3 | `occurrence_count ≥ 5`，且至少一条来源为 `human_feedback`（`T2_T3_MIN_OCCURRENCE=5`） |

**分组机制**（`LessonGroup`）：用 trigger 文本的归一化形式（小写、去标点、
提取关键词）做轻量级、非语义的分组 key（`group_lessons()`），"有效
occurrence_count" = 组内各条目 `occurrence_count` 之和；"是否来自不止一个
session" = 组内 `session_id` 去重后数量 > 1。**这不是完整语义聚类**，是
Stage 3.1 范围内明确的简化取舍，聚类精度留给后续迭代提升。

### 3.2 提案生成：evolution-agent
`/evolve review` 扫描达标分组后，spawn 一个 `evolution-agent`
（`.agent/agents/evolution-agent.md` profile），把 `lessons_payload =
[g.to_dict() for g in groups]` 和 `existing_skills` 作为输入，由该 sub-agent
生成提案内容（不是本项目主流程内嵌逻辑，由 profile 驱动）。

### 3.3 提案落地：`skill_propose` 工具（`tools/evolution.py`）
```python
skill_propose(name, content, source_lessons, reason="")
```
- **tier 固定为 T1**，不接受调用方传入的 tier 参数——"skill 提案"这个动作
  本身的风险等级是确定的，不应该被 prompt injection 或模型的自由发挥改变。
  若新 skill 路径命中受保护路径，`StateRepo.apply()` 仍会强制升级到 T3。
- 写入路径固定为 `.claude/skills/<name>/SKILL.md`。
- 内部：`EvolutionWorkspace.create()` → `StateRepo.apply()`（tier=T1，
  validators=T1 校验集）→ 成功则 `ws.destroy(delete_branch=False)`
  保留分支供人工审核；失败（校验不过）则 `ws.destroy(delete_branch=True)`
  清理，不留垃圾分支。
- **[新增] 效果回填基线记录**：成功后对 `source_lessons` 中每个 lesson
  group id 调用 `outcome_tracker.record_commit_baseline()`（见第 8 节）。

### 3.4 命令
```bash
/evolve review [--global] [--tier T1|T2]   # 扫描 + spawn evolution-agent
/evolve list [--global] [--tier T1|T2]     # 只扫描列出，不 spawn（不耗 LLM 调用）
```

## 4. eval 反馈环（Stage 3.2）

`evolution/eval_runner.py` + `mini-agent eval` 子命令（独立进程入口，不是
REPL slash 命令）：

```bash
mini-agent eval --scenario test_cases/ --skill docx
mini-agent eval --scenario test_cases/ --skill docx --output /tmp/report.json
mini-agent eval --scenario test_cases/                # 不传 --skill，跑 baseline 冒烟测试
```

对比某个 skill 开启/排除前后的 turns/token/tool 失败率，是 T1/T2/T3
验证流水线里"eval 场景对比"这一项的核心引擎，同时也可独立于 CLI 被其他
代码调用。

## 5. 巩固循环 后台循环（Stage 8）

`evolution/consolidation.py::run_consolidation()`，触发方式：`/evolve consolidate` 手动触发，
或 SessionEnd 时间门控检查（超过 `interval_hours` 阈值自动触发，
`should_run_consolidation()`）。一次运行按顺序做以下几件事（每步独立
try/except，失败静默降级，不阻断后续步骤）：

| 步骤 | 函数 | 说明 |
|---|---|---|
| 8.2 剪枝候选 | `prune_skills()` | 找出长期未使用/token 成本高的 skill，生成 `PruneCandidate`；`record_proposal()` 记录冷却时间避免重复提案 |
| 8.3 能力地图 | `build_capability_map()` | 扫描历史数据生成 `CapabilityMapEntry`（domain + confidence），写回 memory（`_write_capability_map_to_memory`，`entry_type="capability_map"`） |
| 8.4 Scope 晋升候选 | `check_scope_promotion()` | project → global 的知识晋升候选（`min_projects`/`min_confidence`/`min_interval_days` 门槛） |
| 8.6 知识巩固 | `library.consolidate()` | 仅当 `memory_backend` 带 `library`（`LibraryIndex`）时生效；批量处理未分类候选、重写实体摘要、去重合并 |
| [新增] 效果回填判定 | `outcome_tracker.tick()` | 见第 8 节 |

`observation_window_sessions` 参数（8.5 节"T1 自动合并前先观察 N 个
session"）：当前版本只记录，实际"等待 N 个 session"逻辑由晋升提案的
消费方（evolution-agent）从提案元数据里读取，不在 `run_consolidation()` 内阻塞。

**演化节奏治理**（8.5 节，`_rhythm_path` / `rhythm_is_allowed` /
`record_proposal`）：状态文件 `consolidation_rhythm.json`，防止同一类提案
（prune/promote）过于频繁地重复提出。

## 6. Stage 9：自治运行时

### 6.1 GoalBacklog（`perception/goal_backlog.py`）
跨会话的目标队列，用户可通过 `/goal` 显式添加目标，也接受
`SoftGoalDeriver` 推导出的候选目标（需 `/goals accept`/`reject` 显式处理，
不自动执行）。

### 6.2 AutonomousLoop：三档位自治（`evolution/autonomous_loop.py`）
按 `autonomy_level` 分三档，tick 间隔逐级变长：
1. **被动执行**：只响应用户显式指令
2. **maintenance**：定期维护（清理、健康检查等）+ 自动给缺 Objective 的
   Goal 补 Objective（`_ensure_goal_objectives()`，可配置开关/上限，见
   [Stage 9 指南 3.3 节](self-evolution-stage9-guide.md#33-goal--objective-自动拆解)）
   + ObjectiveExecutor 推进活跃 Objective
3. **autonomous**：maintenance + 软目标推导（`_tick_autonomous()` 调用
   `SoftGoalDeriver`）

### 6.3 SoftGoalDeriver（`evolution/soft_goal_deriver.py`）
从三路候选推导可能值得做的目标：`capability`（能力盲区）、`workthread`
（陈旧的未完成线索）、`lesson`（高频 lesson）。**只有 capability 类候选
经过 `ExplorationSandbox` 验证**，workthread/lesson 类候选直接写入
`GoalBacklog`，正确性依赖后续 GoalJudge 事后把关（见改进方向分析文档，
这是当前已识别但尚未修复的不对称）。

### 6.4 ResourceArbiter（`evolution/resource_arbiter.py`）
预算/并发仲裁：`used_today_goals` / `used_today_exploration` 两个独立计数器；
`objective_executor.py` 限制最多同时跑 `autonomy.max_concurrent_objectives_cap`
个 Objective（默认 2，可通过 agent_config.json 配置或看板热改，没有硬
天花板）；`append_activity_digest()` / `read_activity_digest()` /
`build_digest_summary()` 支撑 `/digest` 命令展示近期自主活动摘要。
`can_run_autonomous()` 还会读取 B1 落盘的 `proprioception_snapshot.json`，
`frustration` 达阈值时跳过本次 tick（第 5 节 B1 有详细说明）。

### 6.5 CronScheduler（`evolution/cron_scheduler.py`）
daemon 模式下的周期性任务调度，支持固定间隔和类 cron 表达式
（`compute_next_run()`），命令：`/cron list|status|enable|disable|run|add|
remove|set-schedule`。

### 6.6 命令
```bash
mini-agent daemon start|stop|status     # 守护进程管理
mini-agent self status                  # AutonomousLoop/goals/近期活动/session pool 总览（owner-only）
/agent daemon status                    # REPL 内查看
/goal / /goals                          # 目标管理
/digest                                 # 近期自主活动摘要
/cron ...                               # 定时任务管理
```

## 7. Self-Maintenance（自维护，`evolution/self_maintenance.py`）

具身智能 C4 阶段能力，定期审视 Agent 自身状态并做出调整；与 巩固循环 的
剪枝/能力地图/晋升逻辑在语义上有一定职责重叠（见改进方向分析：两者边界
尚未做过正式梳理，是潜在的未来重复建设风险点）。

## 8. 【新增】效果回填：用户真实反馈闭环（`evolution/outcome_tracker.py`）

> 完整用户文档：`docs/self-evolution-outcome-tracking-guide.md`

### 8.1 解决的问题
T0~T3 验证流水线 + eval 反馈环回答的都是"这次自我修改有没有引入技术性
回归"，没有任何指标回答"这次修改是否真的解决了它声称要解决的问题"。
本模块补上这条正交信号：**触发这次 `skill_propose` 的 lesson group，
commit 落地之后是否真的不再高频出现**。

### 8.2 数据结构
`TrackedCommit`：`commit_id` / `trigger_lesson_group_id` / `committed_at` /
`baseline_trigger_count` / `observation_window_days`（默认 14 天）/
`observation_deadline` / `status`（`observing`→`resolved`）/
`post_trigger_count` / `verdict`。持久化在
`<project_root>/.agent/outcome_tracking.json`（与 `consolidation_rhythm.json`
同级、同样的 tmp+`os.replace` 原子写）。

### 8.3 判定规则
| verdict | 条件 |
|---|---|
| `improved` | 触发次数下降 ≥ 50%，或降为 0 |
| `worsened` | 触发次数上升 ≥ 20% |
| `no_change` | 变化幅度介于两者之间 |
| `insufficient_data` | 基线触发次数 < 3（样本太小，不参与 revert 建议） |
| `reverted_by_user` | 观察期内已被 `/evolution revert` 撤销，提前结束观察 |

触发次数统计**复用**（不重新实现）`perception/lesson_review.py::group_lessons()`
的分组聚合逻辑（按 `lesson_group_id` 精确匹配 `LessonGroup.key`）。

### 8.4 接入点
| 接入点 | 调用 |
|---|---|
| `tools/evolution.py::skill_propose` 成功后 | `record_commit_baseline()` 记录基线 |
| `evolution/consolidation.py::run_consolidation()` | `tick()` 周期性判定，结果写入 `ConsolidationReport.outcome_tracking_resolved` |
| `cli/commands/evolution.py::_handle_revert` | `mark_reverted()`，观察期内被撤销则提前结束 |
| `cli/commands/evolution.py::_handle_outcomes` | `/evolution outcomes [--worsened]` 命令，展示记录 + `worsened` 复核提示 |

### 8.5 设计哲学：只建议，不自动执行
`verdict == "worsened"` 只触发**提示**（"建议复核：`/evolution show`
/ `/evolution revert`"），**不会自动 revert**——与 `SoftGoalDeriver`
推导出的 Goal 需要 `/goals accept`/`reject` 显式处理是同一套设计哲学：
自动化到"提出建议"为止，最终决策权留给用户。

## 9. 自我进化相关命令汇总

| 命令 | 说明 |
|---|---|
| `/evolution log [N]` | 展示最近 N 条自我修改 commit |
| `/evolution show <commit>` | 展示单条 commit 完整信息 + diff |
| `/evolution diff <commit>` | 展示某次 commit 的改动 diff |
| `/evolution revert <commit>` | 生成 revert commit + 记录 `revert_record` lesson + 结束效果回填观察 |
| `/evolution outcomes [--worsened]` | **新增**：查看效果回填记录 |
| `/evolution lessons-to-reminders` | lesson → 动态 reminder |
| `/evolve review [--global] [--tier T1\|T2]` | 扫描达标 lesson 分组，spawn evolution-agent |
| `/evolve list [--global] [--tier T1\|T2]` | 只扫描列出，不 spawn |
| `/evolve consolidate [--force] [--dry-run]` | 手动触发 巩固循环 |
| `mini-agent eval --scenario DIR [--skill NAME]` | eval 反馈环，独立进程子命令 |
| `mini-agent daemon start\|stop\|status` | 守护进程管理（Stage 9） |
| `mini-agent self status` | 自治状态总览（owner-only） |
| `/goal` / `/goals` | 目标管理 |
| `/digest` | 近期自主活动摘要 |
| `/cron ...` | 定时任务管理 |

---

# 第三部分：具身智能机制（Embodied Agent）

> 完整用户文档：`docs/embodied-agent-guide.md`（设计依据：
> `next_doc/embodied_agent_design.md`；改进计划：
> `next_doc/embodied_agent_improvement_plan_v3.md`）

## 1. 解决什么问题

传统 Agent 循环是"感知-决策-执行"的单向管道：读取用户输入 → 决定调用什么
工具 → 执行 → 把结果原样塞回上下文。Agent 对自己"现在处于什么状态"没有
显式建模——不知道自己是不是在反复失败、不知道当前工作目录哪些方向还有
未完成的线索、不知道自己的工具或经验是不是已经过时、被打断后也不会主动
留下"当时在想什么"的痕迹。

这组改进借用具身认知（embodied cognition）里几个朴素类比——本体感知
（proprioception）、余裕感知（affordance）、工具透明性（tool
transparency）、自创生（autopoiesis）——落地成十二个具体、可独立开关的
模块，按 A/B/C 三个优先级阶段 + 阶段 D 收尾实现，**全部已实现**：

| 项目 | 模块 | 优先级 | 状态 |
|------|------|-------|------|
| A1. Connected REPL 完整命令对等 | `cli/daemon.py` | P1 | ✅ |
| A2. Lesson source 区分（human_feedback） | `perception/correction_detector.py` | P1 | ✅ |
| A3. Reminder pre_tool 触发时机 | `reminders/manager.py`/`matcher.py` | P1 | ✅ |
| B1. 本体感知模块（ProprioceptionModule） | `perception/proprioception.py` | P2 | ✅ |
| B2. Lesson → Reminder 自动闭环 | `evolution/lesson_to_reminder.py` | P2 | ✅ |
| B3. Workflow 并发执行 | `workflow/runner.py` | P2 | ✅ |
| B4. 余裕感知层（AffordanceMap） | `perception/affordance_analyzer.py` | P2 | ✅ |
| 工具透明性（IntentActionMapper） | `perception/intent_action_mapper.py` | P2 | ✅ |
| C1. AgentSelfModel | `perception/self_model.py` | P3 | ✅ |
| C2. 时间加权记忆激活 | `evolution/memory_aging.py` | P3 | ✅ |
| C3. 认知锚点文件 | `agent.py` + `storage/paths.py` | P3 | ✅ |
| C4. 自维护模块（SelfMaintenanceModule） | `evolution/self_maintenance.py` | P3 | ✅ |

**与本文档前两部分的关系**：具身智能层大量复用记忆机制（lesson memory、
capability_map）和自我进化机制（巩固循环、ExplorationSandbox）已有的基础
设施，不是第三套独立系统——具体耦合关系见第四部分。

## 2. A1. Connected REPL 完整命令对等

**问题**：daemon 模式下 CLI 以"连接模式"接入，但早期实现里 `DaemonClient`
只透传聊天消息，本地模式下能用的 `/skills`、`/memory`、`/evolve` 等 slash
命令在连接模式下不可用。

**实现**：`cli/daemon.py::DaemonClient` 扩展命令分发，把连接模式下输入的
slash 命令路由到对应的 HTTP API 端点（而不是原样当作聊天消息发给 agent），
达到"连接模式与本地模式命令对等"。不依赖新协议，全部复用现有 HTTP API。

## 3. A2. Lesson source 区分（human_feedback）

`perception/correction_detector.py` 检测用户消息中的直接纠正短语（"不
对"、"应该用 xxx"、"下次记得..."等），立即生成 `entry_type="lesson"`、
`source="human_feedback"` 的条目。四种 source（详见第一部分第 5 节）被
C2（时间加权记忆激活）和 B2（Lesson → Reminder 自动闭环）真正利用：
human_feedback 衰减最慢、激活所需样本量最低。

## 4. A3. Reminder pre_tool 触发时机（前馈控制）

**问题**：原有 Reminder 系统只在工具出错后（`tool_error`）或工具调用后
（`post_tool`）触发，属于"事后补救"；具身认知里前馈控制（feedforward
control）的价值在于"在动作发生前，根据已知的危险模式提前提醒"。

**实现**：`reminders/loader.py` 新增 `TRIGGER_PRE_TOOL = "pre_tool"` 触发
类型，`reminders/manager.py::check_pre_tool()` 在工具真正执行前调用
`matcher.py::match_pre_tool()` 做匹配，命中则在工具调用前注入提醒（例如
"上次用 `bash rm -rf` 忘了先确认路径，这次执行前建议先 `ls` 确认"）。

## 5. B1. 本体感知模块（ProprioceptionModule）

**模块**：`perception/proprioception.py`（配置：`ProprioceptionConfig`，
`cfg.proprioception`，默认 `enabled=True`）

Agent 对自身状态的**轮间快照**——不调用 LLM，是 O(1) 纯计算：

- **认知负荷**（context 占用比例）
- **不确定性**（估算，基于回复中的迟疑措辞密度）
- **风险感知**（基于本轮工具名的敏感度）
- **剩余预算**（`max_turns` 消耗比例）
- **挫败感（frustration）**：连续工具调用失败会累积，成功一次会快速衰减

当 `frustration` 超过 `frustration_threshold`（默认 0.5）且连续失败次数
达到 `consecutive_failure_threshold`（默认 3）时，向模型注入一条元认知
提示——建议它停下来向用户汇报困境，而不是盲目重试同一种方法。

每轮快照可选写入 `traces.jsonl`（`trace_enabled`，默认开启），供后续
巩固循环 分析趋势；C1（AgentSelfModel）也读取最新一次快照作为"此刻内部
感受"维度。

**→ Stage 9 信号桥接**：`frustration` 有意义变化时落盘到
`AgentPaths.proprioception_snapshot`（`.agent/proprioception_snapshot.json`），
`evolution/resource_arbiter.py::ResourceArbiter.can_run_autonomous()`
读取该快照，`frustration` 达到阈值时跳过本次 tick 的自主任务提交——
避免一个正在反复受挫的 Agent 同时还在后台跑高置信度要求的自主探索。
快照缺失/超过 10 分钟未更新时不阻塞。测试：`tests/test_proprioception.py`

## 6. B2. Lesson → Reminder 自动闭环

**模块**：`evolution/lesson_to_reminder.py`

**问题**：Lesson Memory 本身只是被动等待检索命中；没有机制把"反复出现的
教训"主动转化为会在恰当时机触发的 Reminder。

**实现**：`LessonToReminderBridge` 按 `trigger` 文本聚类同类 lesson——
`source="human_feedback"` 的经验只需 1 次即可直接激活写入 reminder 目录
（`enabled: true`）；`source="self_reflection"` 等来源需要达到 T1 门槛
（同类 lesson 出现次数）才生成，且先落在 `drafts/` 子目录
（`enabled: false`），需要 `promote_draft()` 手动提升为正式生效。反引号
包裹的工具名会被自动提取为 `condition.tool_name`。命令：
`/evolution lessons-to-reminders`。测试：`tests/test_lesson_to_reminder.py`

## 7. B3. Workflow 并发执行（depends_on 拓扑分析）

**模块**：`workflow/runner.py`

`WorkflowRunner._compute_parallel_batches()` 对 `depends_on` 做拓扑排序，
把无依赖关系的步骤分到同一批次并发执行，有依赖的步骤放到下一批次串行
等待；`evaluator` 类步骤天然依赖被评估步骤，会落在不同批次，不会被误
并行。测试：`tests/test_workflow_parallel.py`

## 8. B4. 余裕感知层（AffordanceMap）

**模块**：`perception/affordance_analyzer.py`（配置：`AffordanceConfig`，
`cfg.affordance`）

**具身来源**：affordance（余裕/行动可能性）——环境不是中性的信息集合，
而是"对当前主体呈现出一组行动可能性"。`AffordanceAnalyzer` 在 session
开始时构建一次（不是每轮 turn），交叉分析 `open_threads.json`（第一部分
第 6 节）、`capability_map`（巩固循环 能力地图，第二部分第 5 节）、
lesson memory（第一部分第 5 节），生成"当前环境对我意味着哪些行动机会"
的简短文本块，拼进 `system_extra`。纯只读分析，不调用 LLM，不写入任何
文件，失败静默跳过不阻断 session 创建。

**接入点**：唯一实现入口 `inject_affordance_map()`，daemon 多用户路径
（`api/session_pool.py::SessionAgentPool._create_entry()`）与本地单
Agent 路径（`cli/app.py`，Agent 构造完成后立即调用）共用同一份逻辑。

**与用户行为感知层的可选交叉分析**（默认关闭）：`AffordanceConfig.
use_behavior_context` 与 `perception/behavior/` 的总开关 `enabled` 同时
为 `True` 时，额外只读查询最近 30 分钟的 `BehaviorEventStore`（用户前台
窗口/Git 活动/终端命令等采集事件），压缩为 `BehaviorContext`，追加成
1-2 条"用户近期活动提示"。任一开关为 `False` 时该输入源视为缺失。这份
`BehaviorContext` 同时会写回 `AgentSelfModel.user_presence`（见下方
C1），供下游程序化读取，不必解析 system prompt 文本。
测试：`tests/test_affordance_analyzer.py`

## 9. 工具透明性（IntentActionMapper）

**模块**：`perception/intent_action_mapper.py`

**具身来源**：盲人手杖用熟了之后，使用者感知到的是"路面在这里有个坑"，
而不是"手杖碰到了什么"——手杖本身从意识中"消失"，感知对象前移到手杖
末端接触的世界。工具调用同理：`read_file` ×3 + `patch_file` ×2 这类原始
流水账，不如"做了一次代码重构"这种意图层面的总结有用。

**实现**：`IntentActionMapper.group_calls()` 纯规则匹配（不调用 LLM），
按"工具名所属意图类别"做连续游程分组——`exploration`（探索/检索）、
`code_edit`（代码编辑）、`test_run`/`env_setup`/`vcs_op`（`bash` 命令按
内容关键词细分）、`research`（`web_search`）、`other`。接入 `agent.py`
主循环的 `execute_tools` span：分组结果作为 `action_events` 字段写入
`traces.jsonl`，不改变 history 本身，只在可观测性侧补充语义标注。
测试：`tests/test_intent_action_mapper.py`（17 个用例）

## 10. C1. AgentSelfModel——三个 Profile 概念的语义澄清与聚合

**模块**：`perception/self_model.py`

**问题**：代码库里有三个命名相近但职责完全不同的"profile"概念——
`UserProfile`（用户跨项目技术栈画像）、`RoleProfileManager`（多用户
角色/信任等级）、`AgentProfile`（SubAgent 角色定义模板）；此外
`global_knowledge.SelfProfile`/`SelfAssessment` 只反映跨 session 的慢
变化历史评估，没有"这一轮我现在感觉如何"的实时维度。

**实现**：不做破坏性重命名，新增 `AgentSelfModel` 作为聚合视图，session
级构建一次（`AgentSelfModelBuilder`），之后每轮 turn 只更新
`internal_state` 这一个快变量：

- **慢变量**（session 级，构建一次）：来自 `SelfAssessment`（跨 session
  历史评估摘要引用）+ `capability_map`（当前 workdir 技术领域置信度）+
  `user_presence`（B4 交叉分析出的 `BehaviorContext`，通过
  `is_user_actively_engaged()` 访问，行为感知未启用时为 `None`）
- **快变量**（每轮更新）：来自 ProprioceptionModule 最新 `sense()` 快照
  （B1）+ AffordanceMap（B4，当前 session 的余裕地图）

通过 `ContextBuilder` 新增的 `self_model_getter` callable 注入，与已有的
`profile_text_getter` 同构。测试：`tests/test_self_model.py`

## 11. C2. 时间加权记忆激活

**模块**：`evolution/memory_aging.py`（与第一部分第 3.2 节相互引用）

**实现取舍**：原计划设想"巩固循环 tick 时批量预计算 `temporal_weight`
缓存字段"，但核对 `memory_store.py` 后发现时间衰减本来就是按
`entry.age_days`（属性，非缓存字段）在每次 `search()` 时实时计算——没有
"缓存过期"问题，批量预计算反而多一份一致性维护成本。改为新增纯函数
`compute_decay_factor(entry)`，由 `MemoryStore._score_all()` 直接调用
替换原有的全局 `self._decay_lambda`：

| lesson source | 半衰期基准 |
|---|---|
| `human_feedback` | 90 天（最慢——用户亲自纠正的价值不因时间快速贬值）|
| `experiment_confirmed` | 60 天 |
| `self_reflection` | 30 天（默认）|
| `revert_record` | 14 天（最快——具体操作被回退的记录，环境变化后很可能不再适用）|

`occurrence_count`（同类经验重复出现次数）每 +1，半衰期额外延长 30%，
封顶 4 倍——被反复印证的知识更"抗遗忘"。非 lesson 条目（summary 等）
沿用构造时传入的全局半衰期配置，行为不变。测试：`tests/test_memory_aging.py`
（10 个用例，含 MemoryStore 端到端排序验证：相同 age 下 human_feedback
lesson 排序应该高于 revert_record）

## 12. C3. 认知锚点文件——思维状态重建指南

**模块**：`agent/lifecycle.py::_save_cognitive_anchor()` /
`_maybe_load_cognitive_anchor(session_id)` / `_cognitive_anchor_path(session_id)`
（`<sessions_dir>/<session_id>/cognitive_anchor.md`，**session 级存储**，
详见 [具身智能改进指南 §12](embodied-agent-guide.md#12-c3-认知锚点文件思维状态重建指南)
的完整变更说明）

**具身来源**：与自创生（autopoiesis）呼应——生物体被打断后不会丢失
"当时在想什么"，只是需要一点提示就能快速恢复状态。Agent 被 Ctrl-C 打断
当前任务时，history 本身已经记录了"做了什么"，但没有记录"当时在想
什么、为什么这么做、下一步的直觉、还有哪些疑问没解决"——这些是恢复
思路时最难从原始 history 重建的部分。

**存储粒度**：锚点属于具体某一个 session，因此按 session 存储而非
workdir 级单文件（旧版实现，已废弃）——避免"session-A 留下的锚点被
后续任意一个不相关的 session 读到"这种串味问题。

**触发**：本地纯 REPL 模式下用户在 REPL 里 Ctrl-C 打断当前任务
（`cli/repl.py::run_repl()` 的 `KeyboardInterrupt` 处理分支），直接调用
`agent._save_cognitive_anchor()`（写入**当前** session 自己的目录）。
daemon-connected 模式下（`cli/daemon.py`）客户端进程不直接持有 Agent
实例，改为 best-effort POST `/v1/sessions/{session_id}/save_anchor`，
服务端代为调用同一个方法（详见
[HTTP API 指南](http-api-guide.md#stage-9-daemon-模式说明)）。基于最近 12
轮 history，用 LLM 生成固定四段式格式的锚点内容：

```markdown
## 当时在想什么
## 为什么这么做
## 下一步的直觉
## 未解决的疑问
```

**恢复**：resume 一个已有 session 时（`agent/lifecycle.py::load_session()`），
若该 session 自己目录下存在锚点文件则读取并注入 `system_extra`，随后
立即归档（重命名为带时间戳后缀的文件），避免同一份锚点被无限期重复
注入。**不**在新建全新 session 时检查（新 session 不可能有自己的锚点）。

**开关**：`AppConfig.cognitive_anchor_enabled`（默认 `True`），本地与
daemon-connected 两条触发路径共用同一开关。测试：`tests/test_cognitive_anchor.py`
（15 个用例，含 2 条 session 隔离专项验证）


## 13. C4. 自维护模块（SelfMaintenanceModule）

**模块**：`evolution/self_maintenance.py`

**具身来源**：自创生（autopoiesis）——生物体不只是被动响应环境扰动，
还主动维持自身边界和内部一致性。Agent 原来对自身健康状况是纯被动的：
工具失败了才知道工具可能坏了，skill 内容过时了要等产生错误建议才会被
发现，记忆库里出现自相矛盾的经验也不会被主动揪出来。

**四项检查**（`SelfMaintenanceModule.health_check()`）：

1. **stale_tools**（可能失效的工具）：扫描最近 20 个 session 的
   `traces.jsonl` 里 `phase="tool_call"` 记录，统计每个工具近期失败率——
   样本量 ≥3 且失败率 ≥60% 判定为"可能失效，建议排查 API/参数是否变更"。
2. **stale_skills**（长期未用的 skill）：复用 `consolidation.py::prune_skills()`
   同款 `skill_loader.tracker` 基础设施（角度不同：巩固循环 是"高成本 +
   未使用 → 建议剪枝"，这里是"长期未使用 → 可能过时，建议复核"）。
3. **conflicting_lessons**（可能矛盾的经验）：复用
   `lesson_review.py::group_lessons()` 聚类结果，同一聚类内若同时出现
   正面关键词（成功/应该/建议/可以/有效/推荐）和负面关键词（失败/不行/
   不应该/出错/无效/不要/避免）信号，标记"可能矛盾，建议人工判断保留
   哪条"。这是启发式而非精确判断。
4. **skill_effectiveness**（skill 结果有效性，`自诊断闭环深化` P4）：与
   stale_skills 的"多久没用"新鲜度视角不同，回答"用了之后任务是否顺利"——
   读取各 session `meta.json` 里已持久化的 `skill_activations`/
   `tool_stats`（`agent/lifecycle.py::save_session()` 写入，不新增埋点），
   按"是否激活了该 skill"把最近 30 个 session 分成激活组/对照组，比较两组
   整体工具失败率之差（≥0.15 判定 `low_effectiveness`/`effective`，两组
   样本量都需 ≥3 才下结论），标记"该 skill 激活后任务失败率明显更高，
   建议复核内容或使用场景是否合适"。

**只产出建议，不自动修复**——与自我进化侧的效果回填（第二部分第 8 节）
同一套"保留人类控制权"原则：结果写入 `activity_digest.jsonl`
（`type="health_report"`），下次 `/digest` 或连接时的晨报里展示。

**触发方式**：与 巩固循环 同款"时间门控"模式（独立状态文件
`self_maintenance_state.json`，默认 24h 间隔）：
- `agent.py::_maybe_run_self_maintenance()`——SessionEnd 时检查
- 内置 cron job `sys:self_maintain`（`evolution/cron_scheduler.py`，
  `interval:86400`），daemon 模式下按计划触发

测试：`tests/test_self_maintenance.py`（27 个用例）

**下游消费**：`evolution/improvement_backlog_merge.py`（自诊断闭环深化
P1）读取最近一条 `health_report`，把其中的 `skill_effectiveness` ==
`low_effectiveness` 的条目也计入排序候选（`effective`/`inconclusive`
不构成"待处理问题"，不计入）。

## 14. ExplorationSandbox：具身智能与自我进化的直接接口

`SoftGoalDeriver`（第二部分第 6.3 节）对 `capability` 类候选目标，会先
经过 `ExplorationSandbox` 做隔离验证，验证通过后生成一条
`source="experiment_confirmed"` 的 lesson（第一部分第 5.3 节），半衰期
60 天——这是具身智能层（自主目标推导）直接写入记忆体系的路径，绕开了
`lesson_rules.py`/`correction_detector.py` 那两条常规触发路径。

## 15. 配置一览

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `cfg.proprioception.enabled` | `True` | B1 本体感知开关 |
| `cfg.proprioception.frustration_threshold` | `0.5` | 触发元认知提示的挫败感阈值 |
| `cfg.proprioception.consecutive_failure_threshold` | `3` | 连续失败次数阈值 |
| `cfg.affordance.enabled` | `True` | B4 余裕感知开关（daemon 与本地路径均生效）|
| `cfg.affordance.use_capability_map` | `True` | 是否纳入 巩固循环 能力地图数据 |
| `cfg.affordance.use_behavior_context` | `False` | 是否交叉分析用户行为感知层（双重开关，默认关闭）|
| `cfg.affordance.risk_gating_enabled` | `True` | [方案一] 高风险域接入自主探索门控总开关 |
| `cfg.affordance.risk_downweight_factor` | `0.4` | [方案一] 高风险域候选的 urgency 降权系数 |
| `cfg.autonomy.behavior_gating_enabled` | `False` | [方案二] BehaviorContext 接入自主任务调度门控总开关 |
| `cfg.autonomy.behavior_gating_switch_threshold` | `3` | [方案二] 视为"用户明显忙碌切换"的应用切换次数阈值 |
| `cfg.proprioception.uncertainty_threshold` | `0.45` | [方案三] 触发 uncertainty 事件发布的单轮阈值 |
| `cfg.proprioception.uncertainty_streak_required` | `3` | [方案三] 连续超阈值轮次要求（限流发布） |
| `cfg.cognitive_anchor_enabled` | `True` | C3 认知锚点开关 |
| `evolution/memory_aging.py` 半衰期表 | 见上表 | C2，不走配置文件，代码内常量 |
| 自维护间隔 | `24h` | C4，`should_run_self_maintenance(interval_hours=24.0)` |

## 16. 已知的架构不对称与后续方向

参考 `next_doc/priority_improvements_implementation_plan.md`：

- **AffordanceMap 本地路径接入**（已修复）：此前只在多用户 daemon 路径
  生效，现已统一为 `inject_affordance_map()` 共享实现。
- **打通具身自我感知与用户行为感知两层**（已部分实现）：`use_behavior_context`
  双开关桥接，默认关闭、一次性只读快照，不做逐 turn 实时联合决策。
- **SoftGoalDeriver 验证不对称**（已缓解，见下方跨系统事件总线
  第6.4节）：workthread/lesson 类候选此前直接写入 `GoalBacklog`、
  完全不经过验证；现在打 `needs_review` 标签 + 事件驱动的轻量一致性
  复核（重新核对触发信号是否仍成立），不是完整 `ExplorationSandbox`
  验证，但不再是"完全不验证"。
- **自维护模块与 巩固循环 职责边界**（未梳理）：两者在"定期审视自身状态
  并调整"的语义上有重叠，尚未做过正式边界梳理。
- **跨系统事件总线**（已实现，见 `docs/system-events-bus-guide.md`）：
  四条链路全部打通——proprioception → 提前自维护、记忆检索稀疏 → 探索
  novelty 加权、outcome 负面判定 → 回写 lesson、workthread/lesson 候选
  的轻量一致性复核（缓解上面 SoftGoalDeriver 验证不对称问题）。过程中
  额外发现并修复了 `soft_goal_deriver.py`/`goal_backlog.py`/
  `lesson_review.py` 里六个独立的既有 bug（字段名/方法签名对不上，
  详见 `system-events-bus-guide.md` 第7节）——最严重的一处是
  `commit_goals()` 调用 `add_goal(description=...)` 时该参数根本不存在，
  导致"软目标自动推导"此前从未真正提交成功过一个目标节点。
- **`_from_capability_map` 死代码 bug**（已修复，见
  `docs/system-events-bus-guide.md` 第7节）：该方法此前缺少独立 `def` 头，
  被误拼接进 `_recently_explored_domains()` 的不可达死代码区，
  `derive_candidates()` 调用它必然 `AttributeError`（被外层 `except`
  静默吞掉），导致"软目标自动推导"信号1（低置信度能力域）从未真正
  产出过候选；同时补上了它依赖的、此前完全不存在的
  `consolidation.load_capability_map()`。
- **AffordanceMap 高风险域接入自主探索门控**（已实现，方案一，见
  `docs/embodied-agent-guide.md` 8 节）：`high_risk_zones` 落盘 +
  `SoftGoalDeriver`/`ExplorationSandbox` 只读消费，候选降权 + token
  上限收紧，双开关（`risk_gating_enabled`）默认开启。
- **BehaviorContext 接入自主任务调度门控**（已实现，方案二，见
  `docs/embodied-agent-guide.md` 8.1 节；呼应第 11 条此前"当前尚未接入"
  的描述，现已接入）：`ResourceArbiter.can_run_autonomous()` 新增第五条
  规则，用户明显活跃切换时暂缓自主任务，默认关闭
  （`behavior_gating_enabled=False`）。
- **ProprioceptionModule.uncertainty 接入事件总线**（已实现，方案三，
  见 `docs/system-events-bus-guide.md` 6.6 节）：连续多轮超阈值限流
  发布 `proprioception.uncertainty_sustained`，与既有的
  `memory.sparse_region_detected` 信号一起为未探索能力候选加权。
- **AgentSelfModel 接入 SoftGoalDeriver 候选打分**（已实现单场景验证，
  方案四，见 `docs/embodied-agent-guide.md` 5.1 节）：负面回填域
  （`outcome_tracker.get_revert_candidates()`）强降权，验证一个具体、
  影响面可控的场景，暂不做通用聚合接入。

---



# 第四部分：三个机制的交汇点

记忆、自我进化、具身智能三者并非独立系统，而是紧密耦合：

**记忆 ↔ 自我进化**

1. **Lesson Memory 是自我进化的唯一触发源**——`skill_propose` 的
   `source_lessons` 参数、`/evolve review` 扫描的对象，都来自
   `perception/lesson_review.py` 对 `MemoryStore` 中 `entry_type="lesson"`
   条目的分组统计。
2. **能力地图是自我进化的产物，又反哺记忆体系**——`consolidation.py::
   build_capability_map()` 扫描历史数据生成后，通过
   `_write_capability_map_to_memory()` 写回 `MemoryStore`（`entry_type=
   "capability_map"`），供后续 `AffordanceAnalyzer` 等模块检索使用。
3. **巩固循环 既维护记忆索引，也维护进化节奏**——`run_consolidation()` 一次运行里，
   `library.consolidate()`（记忆图书馆知识巩固）与 `prune_skills()` /
   `check_scope_promotion()`（自我进化候选生成）在同一个函数里顺序执行，
   共享同一套"演化节奏治理"（`rhythm_is_allowed`）冷却机制。
4. **效果回填复用记忆检索能力，且现在会反向写回**——`outcome_tracker` 判定
   verdict 时，直接复用 `lesson_review.group_lessons()` 对当前记忆状态重新
   统计，没有另建一套独立的计数体系；判定为 `worsened` 时还会反向写入一条
   `source="eval_failure"` 的新 lesson（见 `docs/system-events-bus-guide.md`
   第6.3节），使负面判定本身也能被后续检索到，不只是单向消费记忆。
5. **revert 操作反哺记忆，同时终结效果回填观察**——`/evolution revert`
   一次操作同时触发两件事：写入 `revert_record` lesson（记忆侧）+
   `outcome_tracker.mark_reverted()`（自我进化侧），两条链路在同一个
   命令处理函数里被同时驱动。

**具身智能 ↔ 记忆**

6. **AffordanceMap 是记忆的消费方**——`AffordanceAnalyzer.analyze()` 的
   三路核心输入（`open_threads`/`lesson_entries`/`capability_entries`）
   全部来自记忆体系；`AgentSelfModel`（C1）的慢变量同样引用
   `capability_map` 摘要，不重复注入全文。
7. **correction_detector 是记忆的生产方**——具身改进 A2 直接扩展了
   `MemoryEntry.source` 的语义利用范围，是 lesson memory 四种来源之一。
8. **memory_aging 按具身语义（source）分层衰减**——C2 时间加权记忆激活
   本质上是把"具身认知里经验的可信度应该因来源不同而不同"这一具身理念，
   直接实现为记忆检索排序算法的一部分。

**具身智能 ↔ 自我进化**

9. **ExplorationSandbox 是两者的直接接口**——`SoftGoalDeriver`（自我进化
   Stage 9）对 capability 类候选目标的验证，通过后生成
   `source="experiment_confirmed"` 的 lesson，这条 lesson 又会被
   `AffordanceAnalyzer`（具身 B4）的高风险区域分析读取，形成
   "自我进化验证结果 → 记忆 → 具身感知"的完整闭环。
10. **自维护模块与效果回填是同一治理哲学的两个实例**——C4
    `SelfMaintenanceModule`（具身）与 `outcome_tracker`（自我进化）都遵循
    "只产出建议，不自动执行"的设计原则，且都挂载在 巩固循环/SessionEnd
    同款"时间门控"触发节奏上。
11. **AffordanceMap 与用户行为感知层的桥接，具身×自治已初步融合**——
    `use_behavior_context` 开关此前只服务于 session 级的一次性感知，
    现在 `BehaviorContext` 数据结构已被 `ResourceArbiter.
    can_run_autonomous()`（方案二）真正复用，作为自主任务调度是否要
    考虑用户当前在场/繁忙的输入源（`behavior_gating_enabled`，默认
    关闭，见第三部分第 16 节）。

---



# 附录：相关文档索引

| 文档 | 覆盖范围 |
|---|---|
| `docs/self-evolution-stage2-guide.md` | 安全网三件套（T0~T3、StateRepo） |
| `docs/self-evolution-stage3-1-guide.md` | Lesson → Skill 闭环 |
| `docs/self-evolution-stage3-2-guide.md` | eval 反馈环 |
| `docs/self-evolution-consolidation-guide.md` | 巩固循环 后台循环 |
| `docs/self-evolution-stage9-guide.md` | 自治运行时（GoalBacklog/AutonomousLoop/ResourceArbiter/CronScheduler） |
| `docs/self-evolution-outcome-tracking-guide.md` | 效果回填闭环 |
| `docs/embodied-agent-guide.md` | 具身智能 12 项能力（含 AffordanceAnalyzer 对 lesson memory 的交叉分析） |
| `docs/behavior-perception-guide.md` | 用户行为感知层（AffordanceMap 可选交叉分析的数据来源） |
| `docs/library-index-guide.md` | 图书馆式记忆索引（分类树/实体/编年目录） |
| `docs/memory-management-guide.md` | 记忆管理命令与配置 |
| `docs/multi-user-guide.md` | 多用户场景下记忆/自我进化的隔离边界 |
| `docs/system-events-bus-guide.md` | 跨子系统事件总线（记忆/自我进化/具身感知之间的信号桥接） |
| `docs/commands-and-tools-reference.md` | 全部命令速查表 |
| `next_doc/priority_improvements_implementation_plan.md` | 已修复/待修复的架构不对称设计方案 |
