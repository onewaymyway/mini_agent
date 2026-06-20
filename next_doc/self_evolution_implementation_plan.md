# mini_agent 自我进化改造实施计划

> 本文档基于 `next_doc/self_evolution_design.md`（架构设计稿）与当前代码库实际状态核查后产出，
> 定位是**可执行的实施计划**，而非新的架构设计。所有结论均来自对源码的逐项核对，不依赖猜测。
>
> 核查时间：2026-06（对应代码快照见仓库当前 HEAD）。

---

## 一、现状核查结论

设计文档写于代码当前状态之前的某个时间点，部分内容已经实现，部分仍是空白。逐项核对如下：

| 文档项 | 状态 | 实际情况 |
|---|---|---|
| history `_type` 字段化 | ✅ 已实现 | `history/entry.py` 完整实现，含 `HType` 枚举、`is_real_user_input`/`is_tool_result`/`is_turn_boundary` 等判断函数、`to_llm_messages()` 剥离逻辑 |
| SubAgent 输出去截断 | ✅ 已实现（2026-06） | `get_task_status` 默认仍截断 3000 字符，`full=True` 可绕过；**新增**截断时主动通知（`truncated: true` + `full_length: N` + `hint`），见 Stage 0.3 |
| config.py 拆分 | ✅ 已实现（2026-06） | 已拆成 `config/` 包：`models.py`（14 个 dataclass）/ `loader.py` / `prompt_builder.py`，`config/__init__.py` 重导出保持外部 import 路径不变，见 Stage 0.4 |
| `task_manifest.json` / `plan_snapshot.json`（W1） | ✅ 已实现（2026-06） | `AgentPaths` 新增 `session_plan_snapshot`/`task_manifest`；`TaskRecord` 落 `manifest.json`（含 `update_task_progress` 工具主动写入）；`ExecutionPlan` 状态变更同步落 `plan_snapshot.json` 并支持 session 重启恢复，见 Stage 0.2 |
| Lesson Memory（`entry_type`/`trigger`/`confidence` 等字段） | ✅ 已实现（2026-06） | `MemoryEntry` 新增 8 个 lesson 专属字段；规则触发（连续失败/拒绝重试成功）、SessionEnd 反思、人类反馈纠正检测、`(e)dit` 接入四条写入路径均已落地，见 Stage 1 完成记录 |
| SessionEnd hook 真正触发 | ❌ 未实现 | `hooks/loader.py` 已声明事件名，但文档原话"预留未接"依然成立 |
| StateRepo / risk tier / evolve 分支 / worktree 副本化运行 | ❌ 未实现 | 全局零代码痕迹，是当前最大的空白区 |
| evolution-agent profile | ❌ 未实现 | 没有专属 profile，但 `AgentProfile`（含 `role_type`/`tools`/`tool_groups`/`inputs`）机制已就位，扩展成本低 |
| skill_propose / eval 反馈环 | ❌ 未实现 | 没有 `mini-agent eval` 命令，没有 lesson → test_cases 自动生成逻辑 |
| SubAgent 信息继承（active_skills 继承 / 共享缓存 / lesson 回流） | ❌ 未实现 | `ToolResultCache` 是单 session 内存缓存，无跨 SubAgent 共享或加锁机制 |
| Workdir / Global 知识层（Phase W 全部文件） | ❌ 未实现 | 没有 `project.json`/`timeline.jsonl`/`work_index.json`/`open_threads.json`/`knowledge.md`/`self_profile.json`/`projects_index.json`/`cross_project_index.json`/`activity_log.jsonl` 任何一个 |
| 观察性（tracing / `/diagnostics` / 异常检测） | ❌ 未实现 | 只有 `/status` 端点，没有 `traces.jsonl`、`/diagnostics` 端点、异常基线检测 |
| 事件因果链（`error_category`/`resolves_seq`） | ❌ 未实现 | `turn_id` 概念已存在（`permissions.py` 用于 HTTP 审批门控），但 `events.jsonl` 没有这些字段 |
| 文件变化影响推断（`FILE_CHANGE_EFFECTS` 映射表） | ❌ 未实现 | `file_watcher.py` 仍是纯被动通知，无规则映射 |
| 审批中插话（三选项 + `(e)dit`） | ⚠️ 部分实现 | `(e)dit` 选项已实现（比文档设想更早落地），但编辑内容未转化为 `_type="user_correction"` 喂给反思系统（因为反思系统本身不存在） |
| Skill 依赖图 / 置信度传递（`activation_conditions` 等 frontmatter 扩展） | ❌ 未实现 | 已有 `SkillUsageTracker` 做调用次数统计，但无 frontmatter 扩展字段 |
| `PlanTaskType`（`clarify`/`verify`） | ❌ 未实现 | `plan.py` 无此枚举扩展 |
| Phase H（daemon / `AgentSelfProfile` / Goal Backlog / 调度器） | ❌ 未实现 | `AgentBridge`/`InputQueue` 长驻进程雏形已存在（文档 7.8 节判断准确），其余全无 |

**核心判断**：项目当前已完成"文档设想的 Phase A + B 起点"全部地基工作——history `_type`、config 拆分、task manifest、lesson memory 四项均已落地（详见 Stage 0/1 完成记录）。但 F（安全网）这项被文档列为"后续一切的前提"的阶段，**仍是空白**。这意味着若不先补齐 F，C/D/E/G/H 都无法真正挂上去——直接跳过去做后面的阶段会建在不存在的地基上。

---

## 二、改造原则

1. **不重复造轮子**：跳过已确认完成的项（history `_type`）；对"部分完成"项（输出截断、`(e)dit` 选项）做增量补强，不推倒重写。
2. **遵循文档既定的依赖链**：A → B → F 是严格串行的前提关系——B 依赖 A 的 `_type` 精确截取用户意图轮次；F 的"按改动对象分级"依赖 B 把 lesson 数据结构定下来（commit message 需要真实的 `source_lessons`）。C/D/E 必须在 F 之后才有意义，否则 skill_propose 写到哪都谈不上"安全"。
3. **每个里程碑都可独立验证**：每步完成后必须能跑通一个具体场景，不允许"写了代码但不知道有没有用"。
4. **T3 红线最先画**：在写任何"自动修改代码"的能力之前，先把"受保护路径清单"这个治理机制本身钉死，避免出现"先有自动改代码能力、后补安全网"的风险窗口。

---

## 三、实施计划

### Stage 0（前置，约 0.5 人天）—— 补齐 Phase A 残留 + 画安全网红线 ✅ 已完成（2026-06）

不属于文档任何 Phase 编号，但是后续一切的地基，必须最先做、不能跳过。**内部 4 项互相独立，可四路并行**。

#### 0.1 受保护路径清单 ✅
- 新建独立文件（建议放仓库根目录或 `scripts/`，**明确不放在 `src/mini_agent/` 包内**，呼应文档"T3 判定逻辑本身要在 agent 可写范围之外"）
- 内容：硬编码的路径/正则集合，至少覆盖 `src/mini_agent/agent.py`、`src/mini_agent/permissions.py`、`src/mini_agent/hooks/`、以及该清单文件自身的路径
- 现在没有 StateRepo 也无妨——先把"清单"产物和存放位置确定，Stage 2 写 StateRepo 时直接 import
- **验证**：独立单测断言清单非空且包含上述关键文件
- **实际产出**：`scripts/protected_paths.py`（含为 Stage 2 `evolution/` 预留的正则规则）+ `tests/test_protected_paths.py`（10 个测试）；说明文档见 [受保护路径清单指南](../docs/protected-paths-guide.md)

#### 0.2 `task_manifest.json` + `plan_snapshot.json`（对应设计文档 8.1 节 / W1）✅
- `AgentPaths` 新增方法：`session_plan_snapshot(sid)`、`task_manifest(sid, tid)`
- `Task`/`TaskRecord`（`orchestrator/task.py`）新增写入逻辑：任务创建时落初始 `manifest.json`（`id`/`name`/`initiator`/`goal` 由 `Task` 字段映射），任务结束时补写 `outcome` 块
- 新增工具 `update_task_progress(task_id, current_step, blockers, note)`，供 agent 在长任务中主动调用（文档强调这是"主动写入"，非被动推导）
- `ExecutionPlan`（`plan.py`）每次 `PlanTask` 状态变更时同步写 `plan_snapshot.json`；session 启动时检测文件存在则尝试恢复
- **验证**：跑一个多步骤任务，检查 `tasks/<id>/manifest.json` 与 `sessions/<sid>/plan_snapshot.json` 是否生成、字段是否符合设计文档 8.1 节 schema
- **实际产出**：`storage/paths.py`（新增 2 个路径方法）、`orchestrator/task.py`（`Task` 新增 `goal`/`initiator`/`acceptance_criteria`；`TaskRecord` 新增 `write_manifest`/`update_progress`）、`orchestrator/sub_agent.py`（创建/结束时落盘）、`orchestrator/plan.py`（`save_snapshot`/`load_snapshot`/`bind_plan_session`/`try_restore_plan`）、`agent.py`（`_bind_session_extras` 接入恢复逻辑）、`tools/orchestration.py`（新增 `update_task_progress` 工具）+ `tests/test_task_manifest_and_plan_snapshot.py`（10 个测试）；说明文档见 [Plan 与 Task 机制说明](../docs/plan-and-task-guide.md) 第 10 节、[存储设计](../docs/storage-design.md) 4.4 节

#### 0.3 SubAgent 输出截断收尾 ✅
- 现状已有 `full=True` 参数，缺的是"截断时主动通知"
- 在 `get_task_status` 截断分支加一行：真实截断发生时，返回 JSON 中加 `"truncated": true, "full_length": N`，提示主 agent 可用 `full=True` 重新取
- 工作量很小（约 10 行），顺手收尾遗留问题
- **验证**：构造一个输出 >3000 字符的 SubAgent 任务，检查返回值含 `truncated` 标记
- **实际产出**：`tools/orchestration.py`（`get_task_status` 新增 `truncated`/`full_length`/`hint` 字段）+ `tests/test_task_status_truncation.py`（4 个测试，覆盖超限/未超限/`full=True`/精确边界值）

#### 0.4 config.py 拆分 ✅
- 拆成 `config/` 包：
  - `config/models.py` —— 14 个 dataclass
  - `config/loader.py` —— `load_config`/`_load_config_file`/`_load_providers_config`/`_merge_providers_into_chain`
  - `config/prompt_builder.py` —— `build_system_prompt`/`_read_claude_md`/`_resolve_*_dir`
  - `config/__init__.py` —— 重导出，保持外部 `from mini_agent.config import AppConfig` 等现有 import 路径不变
- **验证**：跑现有全量测试套件（`tests/`），确保零行为变化
- **实际产出**：单文件 `config.py`（1140 行）拆分为 `config/{__init__,models,loader,prompt_builder}.py`；用 AST 比对确认拆分前后全部 25 个顶层定义内容逐字节一致（除良性末尾换行符差异）；全量测试套件 631 通过，2 个失败为拆分前已存在、与 config 无关的 `debug_logger.py` 边界值问题；说明文档见 [代码结构说明](../docs/code-structure-guide.md)、[配置系统指南](../docs/config-guide.md)

---

### Stage 1（核心，约 2-3 人天）—— Phase B：Lesson Memory ✅ 已完成（2026-06）

依赖 Stage 0（尤其 0.2 的路径方法）。**1.1 必须最先完成，1.2/1.3/1.4 三者互相独立、可三路并行**；1.5 依赖 1.4。

#### 1.1 数据结构扩展 ✅
- `MemoryEntry`（`memory_store.py`）新增字段：`entry_type: str = "summary"`、`trigger`、`outcome`、`root_cause`、`suggested_action`、`confidence: float = 0.5`、`occurrence_count: int = 1`、`source: str = "self_reflection"`
- 全部带默认值，保证现有 `summary` 型条目零迁移成本继续工作
- `MemoryStore.search()` 的检索文本拼接（`to_search_text`）需把新字段纳入，否则 lesson 类条目无法被检索到
- **实际产出**：`perception/memory_store.py`（`MemoryEntry` 新增 8 个字段 + `to_search_text` 对 lesson 型条目额外拼接 trigger/outcome/root_cause/suggested_action）；`tests/test_lesson_memory_entry.py`（8 个测试，含磁盘 roundtrip 与新旧格式混存验证）

#### 1.2 规则触发（先做这个——文档明确建议"先做规则触发"，成本低、无需等 SessionEnd）✅
- 定位现有"连续失败计数"逻辑（若没有则在 `agent.py`/`permissions.py` 周边新增一个轻量计数器）
- 规则一：同一工具连续失败 ≥ N 次（建议 N=3，可配置）→ 模板直接生成 `entry_type="lesson"`、`source="self_reflection"` 条目，不调用 LLM
- 规则二：识别"权限拒绝后重试成功"模式（结合 `permissions.py` 的拒绝记录 + 紧接着的成功调用）→ 生成轻量 lesson
- **实际产出**：新建 `perception/lesson_rules.py`（`LessonRuleEngine` 类，两条规则均为纯规则判断不调用 LLM；同时把 `agent.py` 原有的 `_is_tool_error` 迁移至此处的 `is_tool_error`，供 `tool_executor.py` 共享避免循环依赖）；`tool_executor.py` 接入 `lesson_engine`/`memory_sink` 参数，每次工具调用后观察并写入；`config/models.py` 新增 `lesson_rules_enabled`/`lesson_fail_threshold` 配置项；`tests/test_lesson_rules.py`（13 个测试）

#### 1.3 SessionEnd hook 真正接入（文档点名的"预留未接"项）✅
- 在 `hooks/loader.py` 已声明的 `SessionEnd` 事件基础上，找到 session 真正结束的代码路径（REPL 退出 / `/exit` / 进程终止），补上 `mgr.run("SessionEnd", {...})` 调用
- payload 至少包含 `tool_stats`、最后 N 轮 history（用 `is_turn_boundary` 精确截取——这正是文档强调"依赖 Phase A 的 `_type` 字段"之处，现在 A 已就位可直接复用）
- 接一个轻量 LLM 调用生成结构化 lesson 候选（prompt：给定 `tool_stats` + history 摘要，输出 JSON 数组的 lesson 候选）
- **实际产出**：`agent.py` 新增 `trigger_session_end()`（触发 hook + 反思）、`_reflect_and_save_lessons()`（LLM 调用 + 解析 + 写入）、`_parse_lesson_candidates()`/`_clamp_confidence()` 辅助函数；`cli/repl.py` 两处真实退出点（`EOFError`、`exit/quit`）接入；新建 prompt 模板 `prompts/system/session_reflection.md` + `prompts/user/session_reflection_request.md`；`tests/test_session_end_reflection.py`（10 个测试，覆盖 hook 调用、反思失败降级、候选解析、`max_lessons` 限制）

#### 1.4 人类反馈通道（设计文档 6.2 节，标注"第一批补充机制"，优先级高、成本低）✅
- 纠正检测：规则式识别"不对/不要/应该/下次记住"等短语（中英文均需覆盖），命中时将该 user 消息转为 `entry_type="lesson"`、`source="human_feedback"`、较高 `confidence`（建议 0.7）
- 该逻辑挂在 history 写入路径上（`make_user_input` 之后增加检测钩子），与 1.2/1.3 完全独立
- **实际产出**：新建 `perception/correction_detector.py`（`detect_correction()` 规则式短语检测，中英文各 ~15 条模式，收紧"应该"类规则避免误判普通陈述句；`make_correction_lesson_fields()` 生成 `confidence=0.85`，高于规则触发的 0.6）；`agent.py` 新增 `_detect_and_record_correction()`，挂在 `run_turn()` 的 `append_user` 之后；`config/models.py` 新增 `correction_detection_enabled` 配置项；`tests/test_correction_detector.py`（37 个测试，含误判率验证）

#### 1.5 `(e)dit` 审批选项接入纠正信号（设计文档 16.1 节收尾，框架已具备 `(e)dit`，只差最后一步）✅
- `permissions.py` 中 `(e)dit` 修改后的内容，目前只注入下一轮 LLM 调用；现追加：同时调用 1.4 的"转 lesson"逻辑，标记 `source="human_feedback"`
- 依赖 1.4 先完成
- **实际产出**：`history/entry.py` 新增 `HType.USER_CORRECTION` 枚举值 + `make_user_correction()` 构造函数（`is_real_user_input`/`is_turn_boundary` 均纳入此类型）；`permissions.py` 新增 `last_edit`/`pop_last_edit()`/`_edit_repr()`，三处 `(e)dit` 分支（CLI 简单版、HTTP 双路版的 CLI 端、HTTP 端）统一记录编辑事件；`tool_executor.py` 新增 `on_edit_detected` 回调参数，`check()` 调用后检测并触发；`agent.py` 新增 `_on_edit_detected()`，写入 `user_correction` 消息 + 生成 `source="human_feedback"` lesson；`tests/test_edit_approval_integration.py`（11 个测试）+ `tests/test_session_end_reflection.py` 中补充的 Agent 集成测试

**验证标准**：
- 人为制造 3 次连续 bash 失败 → 检查 `memory.jsonl` 出现 `entry_type=lesson` 条目 ✅（`test_lesson_rules.py::test_consecutive_failure_triggers_at_threshold`）
- 对话中说"不对，应该用 patch_file" → 检查同样生成 lesson 且 `source=human_feedback` ✅（`test_session_end_reflection.py::test_detect_and_record_correction_writes_human_feedback_lesson`）
- 正常跑完一个 session 后退出 → 检查 SessionEnd 反思 lesson 出现 ✅（`test_session_end_reflection.py::test_reflect_and_save_lessons_writes_entries`）
- 全量回归：719 通过（631 Stage 0 基线 + 88 Stage 1 新增），2 个失败为 Stage 0 收尾时已确认的、与本阶段无关的 `debug_logger.py` 边界值问题

---

### Stage 2（核心，约 3-4 人天）—— Phase F：安全网

依赖 Stage 1（commit message 规范需要真实的 lesson id）。**2.1 必须先做；2.2 与 2.3 可并行；2.4 依赖 2.1 + Stage 1**。

#### 2.1 `StateRepo` 类
- 新文件 `src/mini_agent/evolution/state_repo.py`
- 按设计文档 4.2 节签名实现 `apply()`/`log()`/`diff()`/`revert()`/`checkout_file()`
- `apply()` 内部：写文件前检查改动路径是否落在 0.1 的"受保护路径清单"内 → 命中则强制 `tier="T3"`，即使调用方传了别的 tier
- commit message 使用设计文档 4.2 节的结构化格式（`[Tn][来源] 标题` + 正文 `source_lessons`/`session_id`/`confidence`/`occurrence_count`/`proposed_by`）
- 用 `subprocess` 调用 `git`，不引入额外依赖

#### 2.2 验证流水线（设计文档 4.6 节）
- 按 tier 实现分层校验函数：
  - T0：仅 JSON schema 校验
  - T1：加"加载校验"（例如改了 SKILL.md 要能被 SkillLoader 正常解析）
  - T2/T3：第一版先做最小集（lint + 现有单测跑通），smoke boot 和 eval 对比留到 Stage 3 接入，避免本阶段战线过长
- 校验失败需返回明确原因，不允许静默失败

#### 2.3 `EvolutionWorkspace`（设计文档 4.5 节，进程级隔离）
- 封装 `git worktree add` + 可选 venv 创建 + subprocess 拉起子进程
- 第一版可不做"自动跑 eval 场景"，只做"创建/销毁 worktree"骨架，验证"改动在隔离环境里能正常加载"这一最低要求
- `--sandbox-permissions strict` 模式直接复用现有 `permissions.py` 的 `--sandbox` flag，无需新发明

#### 2.4 CLI 接入：`/evolution log` / `/evolution revert <commit>`
- REPL 斜杠命令体系新增这两个子命令，直接调用 `StateRepo.log()`/`StateRepo.revert()`
- `revert` 触发后按设计文档 4.3 节自动生成一条 `source="revert_record"` 的 lesson（依赖 Stage 1 的 lesson 写入接口）

**验证标准**：
- 手动构造一次 T1 级改动（如新增一个 SKILL.md）→ 走 `StateRepo.apply()` → 检查 commit message 格式正确、`git log` 可见
- 故意改动 `agent.py`（受保护路径内）→ 检查被强制标记为 T3
- `/evolution revert` 后检查对应的 revert 记录 lesson 是否生成

---

### Stage 3（视进度选择性推进，约 3-5 人天）—— C/D/E 三路并行

Stage 1+2 完成后，C/D/E 之间**互相独立**，可按资源拆给不同批次并行推进。

#### 3.1 Phase C：`skill_propose` + evolution-agent profile
- 新建 `.agent/agents/evolution-agent.md`，复用现有 `AgentProfile` 机制（`role_type` 留空或新增 `"evolution"`），`tools` 字段限制为一组新工具
- 新增工具 `skill_propose(name, content, source_lessons)`——内部调用 `StateRepo.apply()` 在 evolve 分支上写 `skills/<name>/SKILL.md`，tier 固定 T1
- 触发条件先实现最简单版本：`/evolve review` 手动命令，扫描 `memory.jsonl` 中 `occurrence_count` 超阈值（设计文档 6.7 节：T1 阈值 3）的 lesson，spawn evolution-agent 处理
- 依赖 Stage 2 的 evolve 分支机制（`git branch` 实现，无需额外代码，2.1 已具备）

#### 3.2 Phase D：`mini-agent eval` 命令
- CLI 新增子命令 `eval --scenario <dir> --with-skill/--without-skill`
- 实现：跑 `test_cases/` 下场景，对比开关某个 skill 前后的 tool 失败率/turns/token 消耗，输出 JSON 报告
- 可与 3.1 完全并行；唯一共享依赖是 Stage 2 的 worktree（用于副本对比），时间紧可先在主进程内跑（牺牲隔离性换开发速度），后续再接入 worktree

#### 3.3 Phase E：SubAgent 信息继承
- `Task`（`orchestrator/task.py`）新增字段 `active_skills: list[str]`，`spawn_agent` 工具透传主 agent 当前激活的 skill 列表
- SubAgent 启动时按名称激活这些 skill（复用现有 `SkillLoader`，传参即可）
- `ToolResultCache` 改造为跨 agent 共享：最简方案是 `TaskManager` 持有全局共享实例并加 `threading.Lock`，而非每个 SubAgent 各自新建一份
- SubAgent 结束时把规则型 lesson（1.2 节产物）汇总写回主 agent 的 `memory.jsonl`，而非只留在 `TaskRecord.log_lines`
- 该项对 Stage 2 依赖最弱（不涉及 StateRepo），**可提前于 3.1/3.2 启动，紧跟 Stage 1 完成后即可开始**

**验证标准**：
- 3.1：跑通后能看到真实的 evolve 分支被创建，`git diff main..evolve/xxx` 可见新 SKILL.md
- 3.2：跑通后 `mini-agent eval` 输出有意义的对比数字
- 3.3：spawn 一个 SubAgent，确认它确实带着主 agent 当前激活的 skill 启动

---

### 暂不启动的部分

- **Phase G（后台循环）、Phase H（daemon / 自主运行时）**：设计文档自己强调"H 是需要显式决策的方向选择"，且强依赖 Phase W（知识层）全部就位。当前 W 完全空白，跳过 C/D/E/F 直接做 H 没有地基。本轮改造不涉及，待 Stage 1-3 稳定运行、知识层确有需求时再单独立项。
- **第 9/10/11/12/13/14 章的横向加固**（tracing、环境漂移检测、能力匹配调度、knowledge_index 等）：设计文档自身定位为"横向加固，可在任意阶段穿插"，非阻塞项。建议作为机会性任务——例如 Stage 1 做 SessionEnd hook 时，可顺手把 tracing 打点接上（同一处代码改动，增量成本低），但不单独立 Stage。

---

## 四、依赖关系图

```
Stage 0（4 项并行）✅ 已完成
  0.1 保护路径清单 ─┐
  0.2 task_manifest ─┼─→ Stage 1（B：lesson memory）✅ 已完成
  0.3 输出截断收尾  ─┤        1.1 数据结构（必须先做）
  0.4 config 拆分   ─┘          ├─ 1.2 规则触发 ─┐
                                  ├─ 1.3 SessionEnd ─┼─→ Stage 2（F：安全网）
                                  └─ 1.4 人类反馈   ─┘     2.1 StateRepo（必须先做）
                                        └─ 1.5 edit接入         ├─ 2.2 验证流水线
                                                                 └─ 2.3 EvolutionWorkspace
                                                                       └─ 2.4 CLI(/evolution)
                                                                             └─→ Stage 3（C/D/E 并行）
                                                                                   3.1 skill_propose
                                                                                   3.2 eval 命令
                                                                                   3.3 SubAgent 继承（可提前到 Stage 1 后启动）
```

---

## 五、时间与人力估算

| Stage | 工作量估计 | 并行度 | 状态 |
|---|---|---|---|
| Stage 0 | 0.5 人天 | 4 路并行，1 人天内可完成 | ✅ 已完成（2026-06） |
| Stage 1 | 2-3 人天 | 1.1 单线，1.2/1.3/1.4 三路并行 | ✅ 已完成（2026-06） |
| Stage 2 | 3-4 人天 | 2.1 单线，2.2/2.3 两路并行 | 待开始 |
| Stage 3 | 3-5 人天 | 3.1/3.2/3.3 三路独立，3.3 可提前 | 待开始 |

总计约 **9-13 人天**可以把 A（补完）+ B + F + C/D/E 这条设计文档推荐的"建议起步顺序"主线跑通，建立起真正的"经验沉淀 → 安全验证 → 受控应用"闭环骨架。过程中每个 Stage 结束都有可验证、可演示的成果，避免"写了很多代码但拼不起来"的风险。

---

## 六、单人执行时的串行顺序建议（备选）

若实际只有单人执行，无法并行，建议按以下严格顺序推进（同一 Stage 内原本可并行的子项改为依次完成，编号即推荐顺序）：

1. ~~0.1 → 0.2 → 0.3 → 0.4~~ ✅ 已完成（2026-06）
2. ~~1.1 → 1.2 → 1.4 → 1.5 → 1.3~~ ✅ 已完成（2026-06）
3. **下一步 →** 2.1 → 2.2 → 2.3 → 2.4
4. 3.3（依赖最弱，优先验证）→ 3.1 → 3.2
