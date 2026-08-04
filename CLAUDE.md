# mini-agent

一个用 Python 实现的简化版 Claude Code，支持技能机制。

## 项目理念与长期规划

**在做任何架构决策、评估新功能优先级、或起草改进计划之前，先阅读** [mini_agent 核心理念与长期规划](docs/mini_agent_核心理念与长期规划.md)——这是本项目所有长期方向决策的参照标准，包含：

- 终极目标（能力持续增强的超级 AI 系统）与个人代理场景的关系：个人代理不是过渡产物，而是能力增强过程获得可验证目标函数和反馈信号的唯一可靠来源；"系统自主生成目标"明确排除在近期/中期规划外
- "自我进化"的正确理解：不是 AI 产生自己的意志，而是用户建模精度、任务自主执行程度、自我改进能力持续提高；唯一可操作的衡量标准是用户需要显式交代的比例是否持续下降
- 长期规划应遵循的五条理念：以"减少用户认知负担"而非功能列表为北极星、记忆/知识系统是地基而非特性、自我进化需先自我诊断再自我提案（阶段 A→D）、主动性要晚于可靠性、警惕数据采集先于数据消费
- 具身智能模块（AffordanceMap/AgentSelfModel/ProprioceptionModule）的定位：服务于自我诊断→自我改进这条主线的能力自我认知地基，而非独立的通用具身智能研究方向
- 当前重点方向与优先级（P0/P1）及明确暂不推进的方向

新增子系统或撰写 `next_doc/` 下的规划文档时，应对照该文档的理念自查，尤其是"这让用户下次少解释了什么"这一测试标准。

## 项目结构

- `src/mini_agent/agent/` — Agent 主类（对话循环与编排）。Stage 12 起由单文件拆分为包：`core.py`（`Agent` 类骨架 + `__init__`）+ 9 个按职责拆分的 Mixin 文件（`lifecycle.py`/`reflection.py`/`profile.py`/`llm_control.py`/`turn_loop.py`/`role_judge.py`/`reminders_correction.py`/`compaction.py`/`snapshot.py`）+ `_helpers.py`（共享辅助函数），通过多重继承组装回同一个类；对外 `from mini_agent.agent import Agent` 路径不变
- `src/mini_agent/context_builder.py` — System prompt 构建（skill/memory/project 注入）
- `src/mini_agent/tool_executor.py` — 工具执行（权限检查 + 调用 + 截断 + 缓存）
- `src/mini_agent/history_manager.py` — 历史管理（追加 + 压缩 + 快照恢复）
- `src/mini_agent/config/` — 配置管理包（`models.py`/`loader.py`/`prompt_builder.py`，含 providers.json 加载、llm_fallback_chain、退避策略参数；对外 `from mini_agent.config import ...` 路径不变）
- `src/mini_agent/permissions.py` — 工具调用的权限守卫
- `src/mini_agent/interaction.py` — 通用交互式提问的双路（本地终端 + HTTP）适配层：ask_user 系列工具 / `/goal` 协商 / daemon connected 模式下任意 slash 命令内 `prompt_user()` 调用的统一入口，与 `permissions.py` 的 HTTP 双路审批机制对称
- `src/mini_agent/session.py` — 会话管理
- `src/mini_agent/tools/__init__.py` — 工具注册表和 `@tool` 装饰器
- `src/mini_agent/tools/builtin.py` — 内置工具（bash、文件 I/O、web_search 等）
- `src/mini_agent/tools/orchestration.py` — 并发编排工具（含 `update_task_progress` 任务进度叙事写入）
- `src/mini_agent/tools/skill_manager.py` — 技能管理工具
- `src/mini_agent/tools/plan.py` — 规划工具
- `src/mini_agent/tools/notepad.py` — 记事本工具（`notepad_add`/`update`/`remove`/`list`/`summarize`），常驻 system prompt、不受 compact 影响，详见 [记事本机制说明](docs/notepad-guide.md)
- `src/mini_agent/tools/user_input.py` — 用户输入工具
- `src/mini_agent/mcp/` — MCP（Model Context Protocol）支持
- `src/mini_agent/skills/` — 技能发现和加载
- `src/mini_agent/cli/app.py` — CLI 应用入口
- `src/mini_agent/cli/parser.py` — 参数解析
- `src/mini_agent/cli/repl.py` — REPL 交互循环
- `src/mini_agent/cli/commands/` — REPL 命令处理器（concurrency, plans, notepad, sessions, skills, tasks, agents, hooks, goal_mode_cmd 等）
- `src/mini_agent/llm/` — LLM 抽象层
- `src/mini_agent/orchestrator/` — 并发编排（含 `plan.py` 的 `plan_snapshot.json` 持久化、`task.py` 的 `manifest.json` 写入）
- `src/mini_agent/hooks/` — hooks 机制（关键事件自动执行命令）
- `src/mini_agent/perception/` — 感知与记忆子系统（含具身改进：`proprioception.py` 本体感知/`affordance_analyzer.py` 余裕感知（`high_risk_zones` 落盘只读消费）/`self_model.py` AgentSelfModel 聚合（含负面回填域桥接）/`intent_action_mapper.py` 工具调用意图分组/`system_events.py` 跨子系统事件总线）
- `src/mini_agent/ui/` — 终端交互（terminal.py, renderer.py, repl_input.py）
- `src/mini_agent/api/` — HTTP API 服务
- `src/mini_agent/history/` — 历史管理（压缩算法 + RawHistory 即时落盘 + 条目类型定义）
- `src/mini_agent/goal_mode/` — Goal 模式：`spec.py`（`GoalSpec`/`GoalSpecBuilder`，自然语言目标→结构化验收标准，多轮协商）/`executor.py`（`GoalStepExecutor` 接口 + `CoarseStepExecutor`，为细粒度版本预留扩展点）/`state.py`（`GoalState`/`GoalStateStore` 原子落盘 + `find_resumable_session`）/`runner.py`（`GoalRunner` 外层驱动循环：判定/反馈注入/compact整合/安全阀）
- `src/mini_agent/prompts/` — Prompt 管理
- `src/mini_agent/storage/` — 存储层（`paths.py` 含 `session_plan_snapshot`/`session_notepad`/`task_manifest`/`workdir_xxx`/`global_xxx` 等路径方法）
- `src/mini_agent/env_info/` — 环境信息采集与注入（Provider 抽象基类 + 注册表 + 内置 Provider）
- `src/mini_agent/evolution/` — 自我演化机制：`state_repo.py`（唯一写入入口，Stage 9 加 `initiator` T0→T1 上浮）/`validators.py`（分级校验）/`workspace.py`（worktree 隔离）/`eval_runner.py`（eval 反馈环）/`consolidation.py`（Stage 8 后台循环：剪枝/能力地图/Scope 晋升/节奏治理）/`autonomous_loop.py`（Stage 9 三档位 tick + ExplorationSandbox + SoftGoalDeriver 接入；`_tick_passive()` 内新增注意力错配 daemon 主动推送检查，`cfg.digest_advisor.next_action_push_enabled` 门控，复用 `InputQueue.enqueue(initiator="scheduled")` 通道）/`resource_arbiter.py`（Stage 9 资源仲裁 + activity_digest.jsonl + 六分组 build_digest_summary；五条仲裁规则，第五条 `_check_user_presence()` 为具身×自治方案二新增）/`cron_scheduler.py`（Stage 9 定时任务：interval/cron 双格式，内置系统 job 含 `sys:daily_digest`/`sys:next_action_digest`/`sys:decision_profile_update` 三个（主动推荐与数字分身机制），首次注入时的 `enabled` 状态由 `DigestAdvisorConfig` 决定，以及 `sys:wiki_gap_scan`/`sys:wiki_fallback_cleanup`）/`step_runner.py`（[下一阶段新增] `run_step()` 巩固子步骤限时执行包装器，线程+轮询超时，超时跳过不阻塞不重试）/`objective_executor.py`（Stage 9 Objective 多步持续执行引擎）/`soft_goal_deriver.py`（Stage 9 autonomous 档位软目标 derive：三路信号 + ExplorationSandbox 验证；另接入高风险域降权/uncertainty域加权/负面回填域降权三个具身×自治信号）/`outcome_tracker.py`（效果回填：baseline/post 触发次数对比判定 verdict，`worsened` 时回写 `eval_failure` lesson + 发布 `evolution.outcome_negative` 事件，供 `AgentSelfModel.recent_negative_outcome_domains()` 桥接消费）/`memory_aging.py`（具身改进 C2，lesson 按 source + occurrence_count 计算专属时间衰减半衰期）/`self_maintenance.py`（具身改进 C4，SelfMaintenanceModule：stale_tools/stale_skills/conflicting_lessons 健康检查，SessionEnd 时间门控 + `sys:self_maintain` cron job）/`decision_recall.py`（决策/取舍知识提炼计划"提案前主动召回"：`recall_related_decisions()` 复用 wiki 三段式检索限定 `type=decision`，接入 `/evolve review` 的 `_spawn_evolution_agent()`）/`daily_digest.py`（主动推荐与数字分身机制方案阶段一：合并行为分布+目标进展+git提交为 `.agent/daily_reports/<日期>.json/.md`，`/digest daily` 命令）/`next_action_advisor.py`（阶段二：`soft_goal_deriver` 的排序层而非候选发现层，停滞目标/注意力错配规则候选 + 可选 LLM 排序，`_apply_profile_weighting()` 接入 decision_profile 同类别内加权，`check_persistent_attention_mismatch()` 供 autonomous_loop 做持续超时推送判断，阈值均可由 `DigestAdvisorConfig` 覆盖模块常量）/`decision_profile_builder.py`（阶段三：从历史决策记录归纳需 ≥N 条独立证据的价值取向模式，矛盾不覆盖只记录 `contradicted_by`，产出 `.agent/wiki/user_value_profile.md`，`/decision_profile` 命令）
- `scripts/protected_paths.py` — 受保护路径清单（T3 治理红线，独立于 `src/mini_agent/` 包，自我演化相关安全机制使用）
- `weixin_bot.py` — 微信端接入入口（与 `main.py` 同级，内嵌 `mini_agent`，每个 openid 独立 Agent 实例，权限审批走微信消息 + `threading.Event` 而非终端阻塞）；`Agent()` 首次构造必须经由 `loop.run_in_executor()` 丢进线程池，不能在 `on_text` 协程里同步调用，否则 `MCPManager.register_all()` 内部的 `run_coroutine_threadsafe(...).result()` 会在事件循环自身线程里死锁（详见 [微信接入指南](docs/weixin-bot-guide.md) 第 3 节）
- `apps/weixin_plugin/weixin/` — 微信网关 SDK（`bot.py`/`types.py`/`login.py`），供 `weixin_bot.py` 使用
- `src/mini_agent/hybrid_exec/` — 脚本/LLM/Agent 混合执行系统（独立于 `workflow` 之外的顶层包，2026-08 新增，P1-P4 已完成）：`spec.py`（`TaskSpec`/`ExecutionResult`/`ExecutionTier`）/`repository.py`（`ScriptRepository` 脚本版本+统计+退役）/`runner.py`（`ScriptRunner` 复用 `py_step_runner.py` 协议）/`explorer.py`（`LLMExplorer`/`AgentExplorer`）/`repairer.py`（`LLMRepairer`/`AgentRepairer`）/`fallback.py`（`FallbackExecutor`）/`executor.py`（`HybridExecutor` 顶层编排器 + `default_executor()` 工厂）/`recorder.py`（`RunRecorder` run 记录落盘）/`policy.py`（`ReexplorePolicy` 跨 run 主动重探索）/`kanban_summary.py`（看板只读汇总）/`workflow_integration.py`（`hybrid_step` 接入 workflow，`register_step_executor()` 扩展点）；详见 [脚本/LLM/Agent 混合执行系统指南](docs/hybrid-exec-guide.md)
- `myplugins/hybrid_step.py` — 薄插件文件：注册 `hybrid_step` workflow 类型，删除即等效禁用

## 开发规范

- 每个工具用 `@tool()` 装饰器注册，返回 `str` 类型
- 新工具放在 `src/mini_agent/tools/builtin.py` 或 `tools/` 目录下的新文件
- 技能文件放在 `.claude/skills/<name>/SKILL.md`
- 编辑文件时优先使用 `patch_file` 而非 `write_file`
- 核心代码放在 `src/mini_agent/` 目录下，使用包导入方式
- 所有与 LLM 的交互通过 `llm.LLMClient` 接口，切换 provider 只需修改配置
- **主对话循环之外**的 LLM 调用（judge / ensemble / 目标拆解 / 摘要重写 / 路由判定等旁路场景）一律通过 `LLMHelper`（`agent.llm_helper` 或 `LLMHelper.from_config(cfg)`），**禁止**再手写 `LLMConfig.from_app_config(cfg)` + `create_client()` 的重复组合；详见"LLMHelper：旁路 LLM 调用统一入口"章节与 [LLMHelper 使用指南](docs/llm-helper-guide.md)
- **新增 `agent_config.json` 配置字段一律走 `config/param_registry.py` 的统一 nested block 注册机制**（多数情况下只需要在 `config/models.py` 里加一个 dataclass 字段，不需要碰 `loader.py`）；确需 CLI 覆盖的全新参数用同文件的 `ParamSpec` 机制；**禁止**在 `config/loader.py` 里新写手动的 `XxxConfig(field=int(_x.get(...)), ...)` 构造代码或新的 `_f`/`_fb`/`_fn` 调用点。决策树、示例代码见 [参数系统指南](docs/param-system-guide.md)
- 所有系统或者模块都应该在/docs 目录下有对应的设计与功能说明
- 未来规划相关的文档放在/next_doc 目录下
- 关键功能都应该在/tests 下有对应的单元测试
- 系统性的测试案例放在 /test_cases 下
- 所有涉及调用大模型的 prompt，必须保存到 src/mini_agent/prompts 目录下，然后通过 PromptManager 来获取
- 增加/命令 时，需要在 src/mini_agent/ui/terminal.py 的 _COMMANDS里配置对应的提示信息

## 运行

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key linux
export ANTHROPIC_API_KEY=sk-...
export AGNES_API_KEY=agnes-xxx            # ask_image 使用 Agnes 视觉模型

# 配置 API Key win
$env:ANTHROPIC_API_KEY=sk-...
$env:AGNES_API_KEY="agnes-xxx"             # ask_image 使用 Agnes 视觉模型

# 交互式模式
python -m mini_agent

# 或单次模式
python -m mini_agent "写一个质数筛法的 Python 脚本"

# 使用指定模型
python -m mini_agent --model claude-haiku-4-5

# 更多参数
python main.py --provider nvidia --model qwen/qwen3.5-122b-a10b --system-tool-call --system-msg-format system_role

# **注意**：命令行参数优先级高于配置文件参数

# 启动 HTTP API 服务（单用户模式）
python -m mini_agent --http

# 启动多用户 daemon（后台常驻，推荐）
mini-agent daemon start --http --http-multi-user --detach

# 多用户管理
mini-agent user list                                    # 查看所有用户
mini-agent user add --name "小明" --role colleague      # 添加用户（返回 token）
mini-agent user add --name "小红" --role family --trust 8
mini-agent user remove u_a1b2c3d4                      # 删除用户
mini-agent user role u_a1b2c3d4 family                 # 修改角色
mini-agent user token u_a1b2c3d4                       # 重新生成 token
```

## 模块说明

### LLM 层 (`src/mini_agent/llm/`)

- `base.py` — LLM 客户端基础接口
- `factory.py` — Provider 工厂，根据配置创建对应客户端
- `retry.py` — 重试策略（退避策略 + 条件框架）：
  - `BackoffStrategy` 抽象基类，`delay_for(attempt)` 计算等待时长
  - 内置策略：`FixedBackoff`（固定等待）、`LinearBackoff`（线性递增）、`ExponentialBackoff`（指数递增）
  - `parse_backoff()` 从字符串模式名构造策略，方便 CLI / 配置文件使用
  - `RetryPolicy` 持有退避策略 + 重试条件，执行带重试的 LLM 调用
- `client_pool.py` — 多配置故障转移 & 多 API Key 轮转：
  - `ApiKeyPool` — 同一 provider 的多 API Key 管理，支持 passive（遇错切换）和 round_robin（主动轮询）两种轮转策略
  - `LLMClientPool` — 多套 LLM 配置的故障转移链，当前配置全部失败后自动切换到下一条
  - `ProviderEntry` — fallback chain 中的单条配置，持有 client + key pool
  - 触发条件可配置：`key_switch_on`（key 切换触发）、`fallback_on`（配置切换触发）
- `system_tool_call.py` — 系统工具调用格式转换
- `providers/` — 各 LLM 提供商实现（anthropic, openai, ollama, nvidia）
- `debug_logger.py` — LLM 调试日志记录
- `service.py` — `LLMHelper`：**主对话循环之外**场景（judge / ensemble / 目标拆解 / 摘要重写 / 路由判定……）统一的轻量 LLM 调用入口，详见下方"LLMHelper：旁路 LLM 调用统一入口"与 [LLMHelper 使用指南](docs/llm-helper-guide.md)

### Agent 核心 (`src/mini_agent/`)

- `agent/` — Agent 主类，对话循环与编排（Stage 12 起拆分为包，见上方项目结构小节详细文件列表）
  - `core.py` — `Agent` 类骨架 + `__init__`
  - `lifecycle.py` — 会话生命周期（初始化/加载/新建/保存/关闭）
  - `reflection.py` — 会话结束反思流水线（lesson/timeline/workdir 知识/巩固/可观测性）
  - `profile.py` — 用户画像读取/刷新/摘要生成
  - `llm_control.py` — LLM 客户端与 Provider/模型切换
  - `turn_loop.py` — 对话主循环（`run_turn`/`_agentic_loop` 等）；撞到 `cfg.max_turns` 硬顶时的行为由 `cfg.max_turns_on_limit`（`"stop"` 默认 / `"continue"` / `"compact_continue"`）+ `cfg.max_turns_hard_limit`（续跑总轮次兜底，默认 `max_turns * 5`）控制，详见 [配置系统指南](docs/config-guide.md#max_turns_on_limit--max_turns_hard_limitappconfig-直接字段)
  - `role_judge.py` — 角色 Agent 联动与轮次质量判定
  - `reminders_correction.py` — 情境提醒注入与人类反馈纠正检测
  - `compaction.py` — 历史压缩（skill compact/分块压缩/自动触发）
  - `snapshot.py` — 轮次快照/重试/回滚
  - `_helpers.py` — 模块级共享辅助函数
- `context_builder.py` — System prompt 构建（skill/memory/project 注入）
- `tool_executor.py` — 工具执行（权限检查 + 调用 + 截断 + 缓存）
- `history_manager.py` — 历史管理（追加 + 压缩 + 快照恢复）
- `config/` — 配置管理包：`models.py`（14 个配置 dataclass + AppConfig）/ `loader.py`（`load_config` 及 providers.json 加载、llm_fallback_chain、退避策略参数）/ `param_registry.py`（统一参数注册与解析机制：nested block 通用加载 `NESTED_CONFIG_BLOCKS`/`load_all_nested_blocks()` + flat CLI 参数 `ParamSpec`，新增配置字段的标准入口，见 [参数系统指南](docs/param-system-guide.md)）/ `config_catalog.py`（看板配置 UI 字段目录，与 `param_registry.py` 做一致性校验）/ `prompt_builder.py`（`build_system_prompt`）；`__init__.py` 重导出，对外 import 路径不变
- `permissions.py` — 工具调用的权限守卫
- `interaction.py` — 通用交互式提问的双路适配层（详见上方项目结构小节）
- `session.py` — 会话管理

### 工具系统 (`src/mini_agent/tools/`)

- `__init__.py` — 工具注册表，`@tool` 装饰器
- `builtin.py` — 内置工具（读/写文件、bash、grep、glob 等）
- `orchestration.py` — 并发编排工具（spawn_agent, task 管理, `update_task_progress` 任务进度叙事写入）
- `skill_manager.py` — 技能管理工具（skill_list, skill_activate 等）
- `plan.py` — 规划工具
- `notepad.py` — 记事本工具：`notepad_add`/`notepad_update`/`notepad_remove`/`notepad_list`/`notepad_summarize`，session 级持久化到 `notepad.json`，内容常驻 system prompt 固定位置（`prompts/system/notepad.md`），不受 history compact 影响。总开关 `cfg.notepad_enabled`（默认 `True`），provider 用 `threading.local()` 存储（与 evolution.py/workdir_knowledge.py 同款写法，避免多 Agent 并发串扰）
- `user_input.py` — 用户输入工具
- `workdir_knowledge.py` — Workdir 知识层工具（Stage 4 + 检索侧补全）：`add_open_thread`/`update_work_thread`/`update_knowledge`/`search_knowledge`，thread-local provider 机制与 `orchestration.py` 同构

### MCP 支持 (`src/mini_agent/mcp/`)

- `__init__.py` — 公开接口导出
- `config.py` — MCPConfig / MCPServerConfig 数据类
- `transport.py` — BaseTransport / StdioTransport / SSETransport
- `manager.py` — MCPManager（连接、注册、调用路由）

### 并行编排 (`src/mini_agent/orchestrator/`)

- `task.py` — 任务定义（`Task`/`TaskRecord`；`TaskRecord` 新增 `manifest.json` 写入：`write_manifest`/`update_progress`，任务创建时落初始版本、结束时补写 `outcome`；`Task.fallback_profiles`/`demotion_scope` 字段支撑 Stage 7 降级重试链）
- `orchestrator/task_manager.py` — 任务调度（依赖解析、SubAgent 管理）；`_try_demotion()`/`_resubmit_demoted()`（Stage 7，13.2+15.3）两阶段降级：先按 `fallback_profiles` 切换 agent profile，全部失败后按 `demotion_scope` 缩小目标范围重试，复用原 task_id
- `sub_agent.py` — 子 Agent 实现（线程包装、自动重试、输出捕获、`manifest.json` 创建/收尾）
- `concurrency.py` — 并发控制（TaskSemaphore + LLMSemaphore + RateLimiter RPM 限速 + StreamTokenState 流式 token 计数 + RetryCountdownState 重试倒计时）
- `status_bar.py` — 状态栏显示（含 Task Tab 栏、流式 token 计数、重试倒计时进度条、RPM 限速状态）
- `plan.py` — 执行计划数据模型（`ExecutionPlan`/`PlanTask`；新增 `plan_snapshot.json` 持久化：状态变更自动落盘，session 启动时 `try_restore_plan` 自动恢复）
- `plan_display.py` — 计划 UI 渲染
- `task_display.py` — 任务状态显示
- `agent_profiles.py` — 自定义子 agent profile（预设角色）

### 感知与记忆 (`src/mini_agent/perception/`)

- `project_scanner.py` — 项目结构扫描
- `file_watcher.py` — 文件变化监听
- `tool_cache.py` — 工具结果缓存
- `memory_store.py` — 跨 session 长期记忆（`MemoryEntry` 含 Lesson Memory 扩展字段，`entry_type="capability_map"` 由 Stage 8 消费）
- `memory_base.py` — 记忆后端抽象
- `memory_factory.py` — 记忆工厂
- `lesson_rules.py` — 规则触发引擎（连续失败计数 / 权限拒绝后重试成功检测，不调用 LLM）
- `correction_detector.py` — 人类反馈纠正检测（规则式短语匹配，中英文约 30 条模式）
- `token_counter.py` — Token 预估
- `workdir_knowledge.py` — Workdir 知识层（W2，Stage 4）：`project.json`/`timeline.jsonl`/`work_index.json`/`open_threads.json`/`knowledge.md` 五个文件的数据模型与读写，含 `capture_environment_fingerprint`/`detect_environment_drift`/`KnowledgeIndexEntry`
- `global_knowledge.py` — Global 知识层（W3，Stage 5）：`self_profile.json`/`projects_index.json`/`cross_project_index.json`/`activity_log.jsonl` 数据模型与读写，含跨项目模式聚合 `scan_cross_project_patterns`/`merge_cross_project_patterns`
- `observability.py` — 观察性（第 9 章，Stage 6）：`SessionTracer`（`traces.jsonl` 打点）、`classify_error()`（14 种 `error_category` 分类）、`detect_anomalies()`（k-σ 异常检测）
- `lesson_review.py` — lesson 阈值扫描（Stage 3.1），`/evolve review` 的扫描逻辑
- `goal_backlog.py` — 跨会话目标层级（Stage 9）：`GoalNode`（Goal/Objective 统一节点）、`GoalBacklog`（持久化 `.agent/goals.json`）；`has_actionable_work()` 和 `active_objectives()` 是 AutonomousLoop/ObjectiveExecutor 的核心调用接口
- `exploration_sandbox.py` — 探索实验沙盒（Stage 9 Phase 3）：包装 Stage 2 `EvolutionWorkspace` 加预算门控，`ExplorationReport` 结果写入 `activity_digest.jsonl`；`_tick_autonomous()` 对 capability 类软目标候选调用此沙盒做轻量验证，成功才写 GoalBacklog + 触发 `skill_propose`；高风险域探索额外收紧 token 上限（`_risk_adjusted_token_limit()`，具身×自治方案一），超限抛 `ExplorationTokenLimitExceeded` 走既有异常收尾路径
- `system_events.py` — 跨子系统事件总线：轻量 `publish()`/`poll_since()`，事件落盘 `events.jsonl`（滚动归档），按 `consumer_name` 独立游标（同一消费者名同时订阅多种 `event_type` 会共享游标推进，需用独立消费者名区分）；已接入 `frustration_spike`/`memory.sparse_region_detected`/`evolution.outcome_negative`/`goal.candidate_unvalidated`/`proprioception.uncertainty_sustained` 五类事件，详见 [跨子系统事件总线指南](docs/system-events-bus-guide.md)
- `proprioception.py` — 本体感知模块 `ProprioceptionModule`：认知负荷/不确定性/风险感知/剩余预算/frustration 轮间快照（O(1) 纯计算，不调用 LLM）；`frustration` 落盘供 `ResourceArbiter` 消费，`uncertainty` 连续超阈值时限流发布 `proprioception.uncertainty_sustained` 事件（具身×自治方案三，`agent/reflection.py::_maybe_publish_uncertainty_signal()`）
- `affordance_analyzer.py` — 余裕感知层 `AffordanceMap`：交叉分析 open_threads/capability_map/lesson memory 生成风险/机会提示；`high_risk_zones` 落盘到 `<workdir>/affordance_snapshot.json`（`persist_affordance_map()`/`load_recent_high_risk_zones()`，60 分钟过期），供 `SoftGoalDeriver`/`ExplorationSandbox` 只读消费做候选降权/token 上限收紧（具身×自治方案一）；`load_behavior_context()`（原私有 `_load_behavior_context`，已提升为公共函数）供 `AffordanceAnalyzer` 与 `ResourceArbiter._check_user_presence()`（具身×自治方案二）共用
- `self_model.py` — `AgentSelfModel` 聚合视图：澄清与 UserProfile/RoleProfileManager/AgentProfile 三个既有 profile 概念的语义边界；新增 `recent_negative_outcome_domains()` 桥接 `outcome_tracker.get_revert_candidates()`，供 `SoftGoalDeriver.derive_candidates()` 对负面回填域候选强降权（具身×自治方案四，单场景验证，暂不做通用聚合接入）
- `classification.py` — 图书馆式分类树（书架结构）：`ClassificationTree` 冷启动只有根节点，运行时自动生长（规则关键词匹配 + LLM 兜底只能入座已有节点，新节点只在 巩固循环 批量聚类时诞生）；`merge_similar_nodes()` 按 Jaccard 相似度收敛重复书架，`feedback_score` 累积检索反馈调整打分权重
- `entity_index.py` — 实体目录（图书馆"著者目录"对应物）：`EntityStore` 管理模块/bug模式/概念等实体卡片，`link_entry()` 挂载记忆、`rewrite_summary()` 攒够证据才批量重写摘要（含冲突检测，矛盾时标注 `⚠矛盾已更新：` 并归档 `superseded_notes`）、`consolidate_entities()` 做去噪+近重复合并
- `catalog.py` — 分类目录（分类号 → entry_id 指针索引，可从 `memory.jsonl` 重建）+ 知识生命周期编年目录（`knowledge_timeline.jsonl` + 侧车索引 `knowledge_timeline_index.json` 支持按实体/分类过滤查询）
- `library_index.py` — 图书馆式索引组合外观 `LibraryIndex`：`on_new_entry()`（写入上架）/`shelf_search()`（两步检索：先定位书架再精排，不足回退全库）/`record_retrieval_feedback()`/`mark_stale_from_correction()`（人类纠正 → 定位刚检索到的旧知识 → 标记过时闭环）/`timeline_for()`/`consolidate()`（巩固循环 巡检：分类生长+合并、实体摘要重写、实体巩固、wiki 镜像/索引重建/专题页生成），可选参数 `wiki_paths` 开启后同时暴露 `wiki_search()`（三段式检索，见下方 wiki/ 条目），详见 [图书馆式知识索引指南](docs/library-index-guide.md)
- `behavior/` — 用户行为感知系统（配置文件 `<project_root>/behavior_config.json`，跟 `agent_config.json` 同级目录，独立于 `AppConfig` 加载流程；采集到的原始事件/分析摘要仍落盘在 `~/.agent/behavior/`，总开关+全部子开关默认关闭）：`config.py`（`BehaviorConfig`）、`events.py`（`ActivityEvent`/`BehaviorEventStore` JSONL 存储）、`manager.py`（`BehaviorPerceptionManager` 单例，启停采集器+外部上报门禁）、`analyzer.py`（把原始事件聚合为工作/生活画像日报）、`mobile_setup.py`（Android/iOS 接入模板生成）、`collectors/`（`active_window`/`idle`/`now_playing`/`app_lifecycle` 本机线程采集器 + `cdp_browser`/`browser_launcher` CDP 专用浏览器方案 + `external_hooks` git/终端 hook 生成器），详见 [用户行为感知系统指南](docs/behavior-perception-guide.md)

### Wiki 式知识库 (`src/mini_agent/wiki/`)

> 图书馆式索引（`perception/classification.py`/`entity_index.py`/`catalog.py`/`library_index.py`）之外的平行实现，用 md 页面 + 显式关系图代替分类树 + 滚动覆盖摘要，解决"关系表达能力不足"与"知识不可直接阅读"两个结构性局限。两套系统过渡期并存，`LibraryIndex.wiki_paths` 非 `None` 时才启用双写/双检索，默认由 `MemoryConfig.wiki_enabled=True` 控制；`MemoryConfig.library_wiki_search_primary=True`（默认）时 wiki 检索是优先路径，未命中才回退 `shelf_search`。经 O1-O4/E1-E3 改进计划（`next_doc/wiki知识库提取与组织层改进计划.md`，全部已完成）与下一阶段改进计划（`next_doc/wiki_next_phase_improvement_plan.md`：双轨制退出评估/陈旧专题页标注/巩固分步超时熔断/世界知识独立触发信号/知识缺口主动扫描/daemon 定时任务，全部已完成）两轮深化。详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md)

- `parser.py` — 解析单个 md 页面：frontmatter（`id`/`type`/`tags`/`status`/`links`/`source_entries`/`grounded_hit_count`（O1）/`knowledge_state`/`last_validated_at`/`validated_by`（O4）等）+ 正文 + 正文内 `[[page-id]]` 弱引用（自动记为 `relation: mentions`）；`WikiPage.strong_links()`/`weak_links()` 按 `source` 字段（`"frontmatter"`|`"body"`）区分
- `graph.py` — `GraphIndex`：内存图结构（正向边+反向边 dict，不引入 networkx），`expand(page_ids, strong_only=, max_hops=, decay=)`（O2：多跳衰减扩展，同节点多路径取最大权重）供检索"图扩展"阶段与 `validator.py` 死链检测复用，`expand_legacy()` 保留原一跳签名兼容既有调用
- `indexer.py` — `discover_pages()`/`build_index()`：遍历 `wiki/` 生成 `_index/` 下 `graph.json`/`tags.json`/`backlinks.json`/`search_index.json` 四个可随时删除重建的派生索引，`incremental=True`（默认）时用 `_manifest.json`（mtime+hash）跳过未改动文件
- `index_reader.py` — **[O1 新增]** 读取 `_index/` 派生索引供 `search.py`/`dedup.py` 复用，避免每次调用都全量 `parse_page` 扫描 `wiki/`；索引缺失/过期时透明退回全量扫描
- `writer.py` — `write_page()`/`set_status()`/`append_section()`：原子写（tmp+fsync+`os.replace`），`append_section()` 用于双写路径的"追加历史沿革"场景；`increment_grounded_hit_count()`（O1）/`update_lifecycle_fields()`/`replace_body()`（O4）
- `validator.py` — `validate_pages()`：死链（`links.target` 指向不存在页面）、id 冲突、孤儿页面（无入边无出边）、`supersedes`/`superseded_by` 成对性（决策提炼）四类跨页面问题
- `migration.py` — `migrate_entity_store()` 一次性把 `EntityStore` 现有实体导出成 `entities/*.md`（可重复运行，增量迁移）；`mirror_entity()` 单个实体的增量镜像，`load_entity_map()` 供 `library_index.py::on_new_entry()`/`consolidate()`/`mark_stale_from_correction()`（O4）共用；`_migration_map.json` 维护 `entity_id → page_id` 映射（不属于可随时删除的 `_index/`，是双写路径依赖的持久状态）
- `entity_digest.py` — **[E3 新增]** `build_entity_digest()` 生成极简实体索引（`id + entity_type + 一句话描述`）反哺抽取 prompt，让模型识别实体时能复用已有 id 而不是裸命名；`world_writer.py` 用 `dedup.py` 分数对模型自报的 `reused_existing_id` 做二次校验
- `dedup.py` — `find_similar_page()`：页面相似度判断，默认规则打分（tag重合度+关键词Jaccard加权）+ 不确定区间只对 top-1 候选问一次 LLM 确认，`find_similar_page_embedding()` 保留为需显式传 `embed_call` 才启用的可选路径，两者互斥；O1 后优先复用 `index_reader.py` 派生索引
- `search.py` — `wiki_shelf_search()`：三段式检索（规则粗筛（含 O1 信度加权 + O4 `lifecycle_discount_enabled` 折扣开关，默认关闭）→ `GraphIndex.expand(strong_only=True, max_hops=)` 图扩展（O2 多跳）→ LLM 精排，精排要求回答后标注"基于页面:"解析进 `grounded_page_ids`），`LibraryIndex.wiki_search()` 暴露给外部，`stage_reached` 标注实际走到哪一段
- `topics.py` — `consolidate_topics()`：按 tag 聚类且组内 frontmatter 强链接密度达标（默认页面数≥4、密度≥0.5）与 LLM 直接聚类（P3）两条候选路径并存生成新专题页；O3 新增再巩固扫描 `_find_topic_reconsolidation_candidates()`/`append_to_topic_page()`，达标新页面追加进已有 topic 而非参与新聚类（`TopicConfig.reconsolidation_interval_runs` 控制频率，`_index/topics_reconsolidation_log.jsonl` 记事件，累计追加超软上限标记 `needs_review`），接入 `LibraryIndex.consolidate()` 步骤 7
- `world_writer.py` — 世界模型候选（`entities[]`/`facts[]`）批量落盘；O4 新增 `_next_fact_anchor()`/`_fact_content_with_anchor()`，fact 以正文内锚点注释（`<page-id>#fact-N; knowledge_state: ...`）实现独立于页面级 frontmatter 的状态标记
- `lifecycle.py` — **[O4 新增]** `mark_page_state(paths, page_id, *, confidence, anchor=None)` 跨页面类型（entity/decision/experience/...）统一状态标记入口（`confidence` 取值 `fresh`/`stale`/`superseded`，写入独立字段 `knowledge_state`）；`touch_validated()` 隐式验证回升（`stale→fresh`，`superseded` 不回升）；`stale_candidate_scan()` 巡检久未验证页面，供 `/wiki lifecycle-scan` 调用
- `stats.py` — `compute_stats()`（`by_type`/`by_entity_type`/`by_source_kind`/`by_knowledge_state`（O4））+ `compute_extraction_stats()`（E2 方案B：`avg_entities_per_extraction`/`avg_facts_per_extraction` 抽取充分性观测），供 `/wiki stats` 展示
- `decision_writer.py` — 决策/取舍知识提炼落盘：`queue_candidates()` 供 compact 阶段把决策候选 append 到 `.agent/decision_candidates_pending.jsonl`（不落盘）；`consolidate_pending()` 供巩固循环批量消费——合并同批次指向同一件事的候选后调用 `process_candidates()`（命中已有决策页且方案一致→更新；方案变了→旧页 `status=overturned`、新建页用 `supersedes`/`superseded_by` 双向串联；未命中→新建 `status=settled`），"新建"动作套 `evolution/consolidation.py::rhythm_is_allowed()` 冷却
- `promotion.py` — wiki 转正为主索引的三项标准量化（P4）：内容占比/校验通过率/检索 A/B 命中率，`/wiki promotion` 只读展示
- `decommission.py` — **[下一阶段新增]** 图书馆式索引下线评估：`check_and_plan()` 只读复用 `promotion.py` 三项标准，达标给出「关闭 `legacy_index_enabled` → 观察 ≥2周 → 移除旧索引文件」三步执行清单（不自动删代码/不自动切开关）；`check_ready_transition()` 检测"未就绪→就绪"翻转，挂载于 `evolution/autonomous_loop.py` 巩固循环收尾与 `cli/commands/evolve.py::_handle_consolidation()`（两处均为直接同步调用 `run_consolidation()` 的位置），翻转瞬间各写一条 digest 记录/打印一行提示，不重复打扰
- `gap_scanner.py` — **[下一阶段新增]** `scan_gaps()`：规则扫描浅层实体（强链接 ≤1）/孤儿页面/陈旧专题页三类知识缺口，零 LLM 成本；`mark_stale_topics()`：topic 页面 `absorbs` 链接成员中 `knowledge_state != fresh` 占比超阈值（默认 0.6）时复用 O4 `lifecycle.py::mark_page_state()` 标注该 topic 过时，供 `/wiki gap-scan` 与 cron job `sys:wiki_gap_scan` 调用
- `fallback_cleanup.py` — **[下一阶段新增]** `cleanup_fallback_pages()`：对创建超过 N 天（默认 30）且从未被判重合并过的 `entities/session-facts-<date>.md` 兜底页重新跑一次 `dedup.py::find_similar_page()`，命中合并、未命中标 `stale`（页面级粒度，非逐条 fact_id），供 `/wiki fallback-cleanup` 与 cron job `sys:wiki_fallback_cleanup` 调用
- `_templates/` — `entity.md`/`decision.md`/`process.md`/`experience.md`/`topic.md` 五种页面类型的 frontmatter 骨架

`evolution/step_runner.py` — **[下一阶段新增，不在 `wiki/` 目录下]** `run_step(name, fn, *, timeout_seconds, on_timeout="skip")`：线程+轮询（0.5s 间隔）实现的限时执行包装器，替换 `consolidate()` 内部裸 `try/except`；超时即跳过本步骤结果（原线程不强杀，在后台跑完但结果丢弃），下一轮巩固自然覆盖；`evolution/consolidation.py::run_consolidation()`/`perception/library_index.py::consolidate()` 的全部子步骤已接入，`ConsolidationReport`/巩固返回值新增 `step_timings` 字段记录每步耗时与状态（ok/error/timeout/skipped）

`history/extraction_trigger.py::scan_for_extraction_window()` — **[E1 新增，不在 `wiki/` 目录下]** 独立于 compact 的候选窗口探测器（连接词密度+轮次兜底，零 LLM 成本），命中后 `history_manager.py::maybe_trigger_extraction()` 异步排队"仅抽取"LLM 调用，游标持久化在 `extraction_cursor.json`，`CompressConfig.extraction_trigger_enabled`/`extraction_trigger_dispatch_enabled` 两级开关；**[下一阶段新增]** `trigger_reason="entity_density"` 与既有 `connective_density` 并行的第二种触发信号——正则扫描新增条目里的候选专有名词/路径/配置项，统计不在 `known_entity_names`（复用 `entity_digest.py::build_entity_digest()`）里的"新词"数量达标即触发，覆盖 `connective_density` 抓不住的纯描述性内容（不含"因为/所以"类决策语境词）

### HTTP API (`src/mini_agent/api/`)

- `server.py` — FastAPI app 工厂 + AgentRunner 后台线程（Stage 9：内嵌 AutonomousLoop tick，`_build_autonomous_loop()`）+ 输出钩子；`app.state.http_server = self` 供 routes 查询 AutonomousLoop 状态
- `routes.py` — HTTP 路由定义（对话/SSE/事件/权限/交互/文件系统/`GET /v1/diagnostics` 系统健康检查 Stage 6.2）；`/v1/status` Stage 9 新增 `autonomy_level`/`last_autonomous_tick_at`/`tick_count`/`subscribers` 字段；新增 `/v1/interactions/pending`、`/v1/interactions/{req_id}`（通用交互式提问，daemon 适配）
- `bridge.py` — 解耦桥梁（RingBuffer/OutputBroadcaster/InputQueue/PermissionGate/InteractionGate）；`InputQueue.enqueue()` Stage 9 新增 `initiator`/`meta` 参数；`HttpInteractionGate` 是 `HttpPermissionGate` 的通用化版本，供 ask_user 系列工具/`/goal` 协商/远程 slash 命令复用
- `models.py` — Pydantic 请求/响应模型 + AgentEvent；`TurnInfo` Stage 9 新增 `initiator`；`StatusResponse` Stage 9 新增 daemon 状态字段；新增 `INTERACTION_REQ`/`INTERACTION_DONE` 事件类型 + `InteractionRequestBody`/`InteractionResponse`
- `auth.py` — Bearer Token 认证中间件（单用户模式）
- `multi_auth.py` — 多用户认证中间件（`MultiUserAuthMiddleware`）；与 `auth.py` 互斥，由 `create_app()` 按 `http_multi_user_enabled` 二选一挂载；认证成功后在 `request.state.user_ctx` 注入 `UserContext`
- `user_store.py` — 用户注册表（`UserStore`）与角色体系；五种角色（owner/family/colleague/agent/public）对应不同工具权限和资源配额；token 明文存 `.agent/users/tokens/*.key`（0600），hash 存 `users.json`；`RoleProfileManager` 管理每用户社交画像（`profile.json`）
- `session_pool.py` — 多用户 Session 池（`SessionAgentPool`）；每个 `(user_id, session_id)` 对应独立 Agent 实例和 AgentBridge；含 idle 超时自动挂起（默认 30 分钟）、崩溃恢复、最大并发限制（默认 20）；`SelfMessageBus` 实现 Self 与 SessionAgent 之间的内部消息；`find_by_interaction_req()` 与 `find_by_permission_req()` 对称，定位通用交互请求归属的 SessionEntry
- `fs_helper.py` — 文件系统操作封装

### CLI (`src/mini_agent/cli/`)

- `app.py` — 应用启动装配（解析参数、初始化组件、启动 REPL）；含 `daemon` 子命令短路、`--daemon-mode` 持续驻留模式（Stage 9）
- `parser.py` — CLI 参数定义；含 `--daemon-mode` / `--no-daemon` 标志（Stage 9）
- `repl.py` — REPL 循环和斜杠命令处理；退出时自动打印 resume 提示（`_print_resume_hint()`）；含 `/agent` / `/goals` / `/digest`（自主活动摘要）/`/digest daily`（融合日报）/`/next`（主动推荐）/`/decision_profile`（决策画像）路由（Stage 9 + 主动推荐与数字分身机制设计方案）；启动时 `_print_startup_digest_and_advisor()` 打印未展示过的日报/推荐摘要各一行，分别受 `cfg.digest_advisor.daily_digest_startup_print_enabled`/`next_action_startup_print_enabled` 门控
- `daemon.py` — 守护进程管理：`cmd_daemon_start/stop/status`、PID 文件管理（`.agent/daemon.pid` + `.agent/daemon_info.json`）、`DaemonClient`（HTTP 连接模式 CLI，含 `respond_permission`/`respond_interaction` 等）、`run_connected_repl`（Stage 9）；`_handle_connected_permission`/`_handle_connected_interaction` 分别渲染权限审批 / 通用交互式提问（ask_user 系列工具、`/goal` 协商、远程 slash 命令输入），两者机制对称
- `commands/` — REPL 命令处理器（concurrency, plans, sessions, skills, tasks, agents, hooks, providers, evolution, evolve, eval_cmd, wiki, digest_cmd（`/digest daily`）, next_action_cmd（`/next`）, profile_cmd（`/decision_profile`））
- `commands/wiki.py` — `/wiki <page-id>|list|search [--deep]|rebuild|stats|promotion|lifecycle-scan|gap-scan|fallback-cleanup`：浏览 wiki 式知识库页面/backlinks、三段式检索（`--deep` 强制多跳图扩展，O2）、手动索引重建、内容来源与生命周期状态分布（O4）、转正评估（P4）、生命周期巡检（O4）、**[下一阶段新增]** 知识缺口主动扫描（`gap-scan [--max-results N] [--dispatch]`，规则扫描零 LLM 成本，`--dispatch` 依赖 daemon `InputQueue`，交互式 CLI 会提示不可用）/兜底页归并清理（`fallback-cleanup [--days N]`），详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md)；`/wiki` 顶级命令及全部子命令/选项已注册进 `ui/terminal.py::_COMMANDS`（此前曾遗漏，交互式终端敲 `/wiki ` 现在能弹出 Tab 补全提示，回归测试见 `tests/test_wiki_slash_completer.py`）
- `commands/debug_cmd.py` — `/debug system|history|all|save`：打印/导出当前 system prompt 与 history（含 `_type`/估算 token），便于分析调试；补全表 `_COMMANDS`（`ui/terminal.py`）同步注册
- `commands/user_cmd.py` — `mini-agent user` 子命令（多用户架构）；通过 HTTP 调用 `/v1/users` 端点管理用户，不直接读写文件；支持 list / add / remove / role / token 子命令；需要 daemon 以 `--http-multi-user` 启动
  - `goals.py` — `/agent goals` 全部子命令（add/obj/done/abandon/accept/reject/pause/progress/status），`/goals`/`/digest` 快捷命令，`/goals accept|reject` 含 `SoftGoalDeriver.record_rejected()` 30天去重（Stage 9）
  - `cron.py` — `/cron` 全部子命令（list/status/enable/disable/run/add/remove/set-schedule），daemon 模式专属（Stage 9 Phase 1）

### hooks (`src/mini_agent/hooks/`)

- `__init__.py` — 公开接口导出
- `loader.py` — HookManager（加载、执行、动态注册）
- `runner.py` — HookResult 定义

### 终端交互 (`src/mini_agent/ui/`)

- `terminal.py` — 统一终端 I/O 管理器，支持命令行输入补全（slash 命令/文件路径/历史建议）、Task 焦点控制（方向键切换日志）
- `renderer.py` — Rich 终端输出渲染
- `repl_input.py` — REPL 输入处理
- `raw_key_listener.py` — 跨平台方向键监听（Unix: `/dev/tty` + `termios` / Windows: `msvcrt`），支持运行时切换 Task 日志视图

### 历史管理 (`src/mini_agent/history/`)

- `__init__.py` — 公开接口导出
- `compression.py` — 历史压缩算法（turn_aligned / sliding_window / llm_summary / **selective**）；`LLMSummaryStrategy` 顺带请求结构化 `{compact_summary, decisions[]}` JSON，`decisions` 只 `wiki/decision_writer.py::queue_candidates()` 入队，不在此处落盘（`CompressConfig.extract_decisions`）
- `decision_extraction.py` — **[2026-07 新增]** `parse_decision_response()` 解析 compact 阶段 LLM 输出的决策候选 JSON（容错：解析失败退化为纯摘要），`DecisionCandidate.to_dict()`/`from_dict()` 供 pending 队列 JSONL 序列化
- `triggers.py` — **[2026-07 新增]** Compact 触发器框架（`CompactTrigger` / `CompositeTrigger`），决定"何时"触发压缩，与 `compression.py` 决定"怎么压缩"分离
- `raw_history.py` — Raw history 管理器（JSONL 即时落盘，每次 append() 立即写文件 + fsync，防崩溃丢失）
- `entry.py` — 历史条目类型定义（HType 枚举）、构造辅助函数、时间戳生成（本地时间 + 时区偏移）

#### History 即时落盘机制

- RawHistory 从批量写 JSON 改为 JSONL 追加写 + fsync
- 每次 `append()` 立即写一行 JSON 并 `fsync`，不等 `save_session()`
- 即使 agent 崩溃或被强杀，raw history 仍然完整
- 存储路径：`.agent/sessions/<id>/raw_history.jsonl`
- Session 恢复时优先加载 `.jsonl`，回退兼容旧格式 `.json`
- 时间戳使用本地时间 + 时区偏移（如 `2026-06-18T16:30:00.123+08:00`），对人类更直观

#### Selective 压缩策略

- 新增 `selective` 压缩策略（`CompressConfig.strategy = "selective"`）
- 按 `_type` 差异化权重评分：user_input(1.0) > assistant_reply(0.9) > tool_result(0.4) > reminder(0.2)
- 位置加权：最近 25% 的消息额外 +0.2
- 保证最少用户轮数（`selective_min_user_turns`，默认 3）
- `_fix_orphans()` 保证 turn 结构完整（tool_result 必须配对 assistant_reply）
- 去重：skill_context / reminder / hook_context 只保留最新一条
- 配置：`compress.selective_weights`（自定义权重）、`compress.selective_min_user_turns`

#### Compact 触发器体系（2026-07 新增）

- `history/triggers.py::CompositeTrigger` 在 `_agentic_loop()` 每轮开头检查一组触发器，OR 组合，取 priority 最高的命中结果
- 内置触发器（均带独立开关，默认关闭；`TokenThresholdTrigger` 由已有的 `compress.enabled` 控制）：
  - `TokenThresholdTrigger`（现有逻辑，硬约束，无视冷却期）
  - `TurnCountTrigger` — `turn_count_trigger_enabled` / `max_turns_before_compact`（默认 20）
  - `ToolCallCountTrigger` — `tool_call_count_trigger_enabled` / `max_tool_calls_before_compact`（默认 50）
  - `RedundancyTrigger` — `redundancy_detection_enabled` / `redundancy_tool_result_ratio`（默认 0.6）
  - `TopicShiftTrigger` — `topic_shift_detection`（`"off"`/`"heuristic"`/`"llm"`），heuristic 用关键词重合度+切换语关键词，llm 档追加一次小模型二次确认；两档均内置续接短语白名单（"继续"/"continue"等整句匹配）+ 短文本豁免（当前消息关键词数 <2 跳过重合度判断），防止短回复误判为话题切换
- 每个触发器可给出 `suggested_strategy`（如话题切换建议 `llm_summary`，轮次/工具调用/冗余建议 `selective`），`_auto_compress_history()` 临时切换 `cfg.compress.strategy` 执行后再恢复
- `compact_cooldown_turns`（默认 3）：compact 后这么多轮内，非硬约束触发器不生效
- `require_confirmation`（默认 `False`）：触发后是否需要 `term.confirm()` 询问用户 y/n
- **修复**：`_auto_compress_history()` 原来是独立于 `CompressionStrategy` 注册表之外的硬编码切割实现，配置的 `compress.strategy` 从未真正生效；现已改为委托给 `HistoryManager.auto_compress()`
- `compact_event` 新增 `trigger_reason` 字段（写入 raw_history，供审计/统计各触发器命中效果）
- 详见 [compact 设计文档](docs/compact-design.md)

#### 分批摘要（Chunked Compact）

- `compact_with_skills()` 内部自动选路：正常路径用 `run_turn`；若触发 `LLMContextWindowError`
  则切换到 `_compact_chunked()`，完全绕开 `run_turn`，用 `_llm.chat_with_retry` 分批处理
- 切分规则：按 turn 边界分 chunk，每 chunk ≤ 模型上下文 50%；chunk 数 > 1 时再做一次合并调用
- 单 chunk 失败时降级为字符串摘要，不中断整体流程；合并调用失败时字符串拼接
- 所有 compact prompt 统一要求保留：工具调用结果摘要、精确文件路径、错误信息、关键发现
- 新增 prompt 文件：`compact_chunk_request.md`、`compact_merge_request.md`
- 详见 [compact 设计文档](docs/compact-design.md)

### 存储层 (`src/mini_agent/storage/`)

- `__init__.py` — 公开接口导出
- `paths.py` — 路径管理（`AgentPaths`，含 `session_plan_snapshot(sid)`/`task_manifest(sid, tid)`（Stage 0.2）、`workdir_project_meta()`/`workdir_timeline()`/`workdir_work_index()`/`workdir_open_threads()`/`workdir_knowledge_md()`/`workdir_knowledge_index()`（Stage 4，W2）、`global_self_profile()`/`global_projects_index()`/`global_cross_project_index()`/`global_activity_log()`（Stage 5，W3）等路径方法）

### 环境信息采集 (`src/mini_agent/env_info/`)

- `__init__.py` — 公开接口导出（EnvInfoProvider、EnvInfoRegistry、build_env_block）
- `base.py` — EnvInfoProvider 抽象基类（name/enabled/collect/safe_collect）
- `registry.py` — EnvInfoRegistry（注册、采集、格式化、from_config 工厂方法）
- `providers/system.py` — SystemInfoProvider（OS、Arch、Hostname*、User*）
- `providers/runtime.py` — RuntimeInfoProvider（Python、Venv、CWD）
- `providers/locale.py` — LocaleInfoProvider（Timezone、Locale）

### Reminder 系统 (`src/mini_agent/reminders/`)

- `loader.py` — Reminder 加载器，扫描目录解析 `.md` 文件
- `matcher.py` — 条件匹配引擎，根据事件/正则匹配触发条件
- `manager.py` — ReminderManager，Agent 主流程集成入口
- `generator.py` — Reminder 生成工具，用于从对话提取经验

### 自定义子 Agent

- profile 文件位置：`.agent/agents/*.md`（项目级）或 `~/.agent/agents/*.md`（全局级）
- 文件格式：YAML frontmatter（name/description/inputs/tools） + system prompt 模板
- 支持占位符：`{参数名}` 和 `{context}` 自动填充
- CLI 命令：`/agents list|show <name>|reload`
- 工具：`list_agent_profiles`、`spawn_named_agent`

### 角色扮演（Persona）系统

- 定位：作用于**主 agent 自身**的人格切换，跨轮持续生效直到显式退出；与"自定义子 Agent"（一次性任务型，独立 context）是两套独立机制，仅共享 frontmatter + Markdown 正文的解析风格
- persona 文件位置：`.agent/personas/*.md`（项目级）或 `~/.agent/personas/*.md`（全局级），加载逻辑见 `src/mini_agent/orchestrator/persona_profiles.py`
- frontmatter 字段：`name`/`display_name`/`description`/`tone`/`break_character_policy`（soft|strict）/`allowed_tools`（可选工具白名单）
- 状态：`Agent.active_persona`（内存态，随 session `meta.json` 持久化，`new_session()` 时重置为 `None`，不跨 session 继承）
- system prompt 注入：`ContextBuilder.build()` 单独成段注入渲染结果，不与 skill/tool 使用规范混排；渲染结果由 `render_persona_prompt()` 强制追加安全边界声明（代码写死，不受角色文件内容影响，无法被覆盖）
- `allowed_tools` 强制拦截：`ToolExecutor.execute_all()` 在 `PreToolUse` hook 之后、`guard.check()` 之前检查，非白名单工具直接拒绝，不进入常规审批流程；空 `allowed_tools` = 不限制
- 使用统计：`~/.agent/persona_usage.jsonl`（全局、跨项目累计），`/role use` 时自动记录，`/role stats` 查看
- 内置默认角色：`senior-swe-mentor`、`jarvis`、`socratic-tutor`、`storyteller-narrator`、`rem`
- CLI 命令：`/role list|use <name>|show <name>|exit|status|stats|reload`
- 生成角色的 skill：`persona-generator`（`.claude/skills/persona-generator/SKILL.md`）
- 详细设计见 `next_doc/roleplay_persona_design.md`

### Hooks 机制

- 配置文件：`.agent/hooks.json`（项目级）或 `~/.agent/hooks.json`（全局级）
- 支持事件（15 个，按生命周期）：
  - **Session**：`SessionStart`、`SessionEnd`
  - **Prompt**：`UserPromptSubmit`
  - **Tool**：`PreToolUse`（可阻止）、`PostToolUse`、`PostToolUseFailure`（工具抛异常时）、`PostToolBatch`（一批工具全部结束后）
  - **Subagent**：`SubagentStart`、`SubagentStop`
  - **Task**：`TaskCreated`、`TaskCompleted`
  - **Stop**：`Stop`（LLM 无工具调用、准备结束本轮时；context 可注入）
  - **Compact**：`PreCompact`（可阻止）、`PostCompact`
  - **mini_agent 扩展**：`TurnEnd`（一轮结束后，可注入 `user_input` 接管下一轮）
- Hook 通过 stdin 接收 JSON payload，通过 stdout 返回决策（allow/block/context/input/user_input）
- 可阻止的事件：`UserPromptSubmit`、`PreToolUse`、`PreCompact`
- CLI 命令：`/hooks list|reload`

### Reminder 机制

- Reminder 目录：`src/mini_agent/prompts/reminders/`（系统默认）+ `--reminders-dir` 指定（用户自定义）
- 文件格式：YAML frontmatter（trigger_event/condition/priority 等）+ 正文提示内容
- 触发事件：`tool_error`、`post_tool`、`user_intent`、`pattern`
- **去重守卫**：同一 turn 内同名 reminder 只注入一次（`_reminder_already_in_turn()`），避免历史堆积重复噪音
- 压缩去重：SelectiveStrategy 压缩时，skill_context / reminder / hook_context 只保留最新一条
- CLI 参数：`--reminders-dir`、`--no-reminders`、`--reminder-verbose`
- 技能：`reminder-generator` 从对话提取经验生成 reminder

### Role Agent

- 预设角色子 Agent 模板，位于 `src/mini_agent/orchestrator/agent_profiles.py`
- 支持结构化参数注入、工具/模型限制
- CLI 命令：`/agents list|show <name>|reload`

### Goal 模式

- 设定一个目标，Agent 自动多轮尝试直至达成或触发安全阀，位于 `src/mini_agent/goal_mode/`
- `GoalSpecBuilder`：自然语言目标 → 结构化验收标准，支持多轮对话式修订+版本 diff，确认前不占用主 Agent 上下文；system prompt 禁止照抄用户原话、要求具体化/分维度拆解，代码层面对照抄结果自动带纠正提示重试一次（`_looks_like_verbatim_echo`）；2026-07 起改为直接调用 `LLMHelper.ask()`（不再构造受限 Agent 走工具循环，避免了 MCP 连接+工具幻觉导致轮次耗尽产不出 JSON 的问题），可注入调用方已有的 `llm_helper` 复用其 `LLMClientPool`
- `GoalJudge`（`role_agents/goal_judge.py`）：对照验收标准逐条核查，输出 `GOAL_STATUS: DONE/CONTINUE/NEED_COMPACT`；不经过 `RoleAgentDispatcher`，由 `GoalRunner` 直接调用；`judge_tools_enabled` 开关控制是否挂只读工具自己验证
- `GoalRunner`：外层驱动循环（跨多次 `run_turn`，与 Role Agent 的单次 `run_turn` 内修订循环不同）；粗粒度 `CoarseStepExecutor` 每步跑一次完整 `run_turn`，`GoalStepExecutor` 接口为未来细粒度版本预留
- 安全阀：`max_rounds`、`max_total_compacts`、连续雷同反馈检测（`difflib.SequenceMatcher`）提前终止
- 异常中断恢复：`GoalState` 原子落盘到 `.agent/sessions/<sid>/goal_state.json`，只在轮次边界写入；复用既有 session 持久化存对话历史，不重复保存；`/goal resume` 续跑；`/goal list`（`list_resumable_sessions`）列出所有可恢复目标，避免多进程各自设定目标都被杀死后只显示最近一个
- 目标上下文用 `HType.GOAL_CONTEXT` 类型消息"钉住"，每轮结束和每次 compact 后都重新附加，防止被压缩策略稀释
- CLI 命令：`/goal <文本>`、`/goal resume [sid]`、`/goal list`、`/goal status`、`/goal cancel`；需 `goal_mode.enabled: true`（默认关闭）；协商子对话（`_negotiate_loop`）通过 `interaction.py` 双路适配，daemon connected 模式下正常可用，不再依赖 daemon 进程本身的本地终端
- 详见 [Goal 模式指南](docs/goal-mode-guide.md)

### Workflow

- 工作流编排机制，支持多步骤自动化任务执行
- 参见 [Workflow 指南](docs/workflow-guide.md)

### 混合执行系统（hybrid_exec，2026-08 新增）

- 模块位置：`src/mini_agent/hybrid_exec/`（独立于 `workflow` 包之外的顶层包，P1-P4 均已完成）
- 核心决策逻辑：探索优先 agent/llm（`LLMExplorer` → `AgentExplorer`）、执行优先脚本（`ScriptRunner` 复用 `py_step_runner.py` 协议）、脚本故障时优先修复脚本（`LLMRepairer` → `AgentRepairer`）、修复不了才降级到 `FallbackExecutor`（LLM/Agent 直接给答案，不产脚本）
- 顶层编排器 `HybridExecutor`，便捷工厂 `default_executor(project_root)`；`TaskSpec`/`ExecutionResult`/`ExecutionTier` 三元组对外接口，可完全脱离 workflow 独立 `import` 调用
- `ScriptRepository` 管理脚本版本（`.agent/hybrid_exec/scripts/<task_id>/`），含成功率统计 + 连续失败自动退役
- 接入 workflow 新 step 类型 `hybrid_step`：**未修改 workflow 包任何源码**，通过 `workflow/executors.py::register_step_executor()` 公开扩展点 + 薄插件文件 `myplugins/hybrid_step.py` 注册，删除插件文件即禁用（与 `python_step_enabled` 配置开关模式不同）
- `RunRecorder` 落盘每次 run 决策轨迹到 `.agent/hybrid_exec/runs/<task_id>/`（`summary.json` 滚动聚合），独立调用与 workflow 场景共享同一份统计口径；`ReexplorePolicy` 基于累计成功率的跨 run 主动重探索（默认不启用）
- 只读端点 `GET /v1/hybrid_exec/summary`（`kanban_summary.py::build_kanban_summary()`）+ 看板 "🧪 混合执行" Tab
- 参见 [脚本/LLM/Agent 混合执行系统指南](docs/hybrid-exec-guide.md)

### Env Info（环境信息采集）

- 模块位置：`src/mini_agent/env_info/`
- 抽象基类：`EnvInfoProvider`（name/enabled/collect/safe_collect）
- 注册表：`EnvInfoRegistry`（注册、采集、格式化、from_config 工厂方法）
- 内置 Provider：`builtin.system`、`builtin.runtime`、`builtin.locale`
- 自定义 Provider：实现 `EnvInfoProvider` 子类，在 `agent_config.json` 的 `env_info.providers` 中注册完整类路径
- 配置示例：`{"env_info": {"enabled": true, "providers": ["builtin.system", "myplugins.git_info.GitInfoProvider"]}}`
- 参见 [Env Info 指南](docs/env-info-guide.md)

### LLM 故障转移 & 多 Key 轮转

- 核心模块：`src/mini_agent/llm/client_pool.py`
- **ApiKeyPool**：同一 provider 的多 API Key 管理
  - 轮转策略：`passive`（遇错切换，默认）和 `round_robin`（每次请求轮转）
  - 切换触发条件（`key_switch_on`）：默认 `["LLMRateLimitError"]`，可扩展为认证失败等
  - 冷却机制：key 被切换后进入冷却期（`key_cooldown`，默认 60s）
- **LLMClientPool**：多套 LLM 配置的故障转移链
  - `llm_fallback_chain`：有序配置列表，第一条为主配置
  - fallback 触发条件（`fallback_on`）：默认 `["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"]`
  - 认证失败（`LLMConfigError`）不触发 fallback（换配置不会解决代码配置问题）
- **配置来源**：`providers.json`（优先）> `agent_config.json` 中的 `llm_fallback_chain`
- **providers.json**：独立存放含 API key 的敏感配置，已自动加入 `.gitignore`
- **providers 块合并**：`providers` 字段的全局设置（api_keys、key_rotation 等）自动合并到 chain 每条条目中
- 参见 [LLM 故障转移指南](docs/llm-failover-guide.md)

### LLMHelper：旁路 LLM 调用统一入口（2026-07 新增）

- 核心模块：`src/mini_agent/llm/service.py`（`LLMHelper`）
- **解决的问题**：主对话循环之外的 LLM 调用（judge 评审 / ensemble 候选生成 / 目标自动拆解 / 记忆摘要重写 / 路由判定……）此前各处各写各的：有的裸调 `client.chat()` 无重试，有的每次 `LLMConfig.from_app_config(cfg)` 重新读一份**启动时的静态配置**（不跟随会话中 `/model` 切换），甚至有两处传了 `chat()` 不支持的 `max_tokens=` 参数被静默吞掉异常（`objective_executor.py` / `goal_backlog.py` 的历史 bug，均已修复）。
- **获取方式**：
  - 有 `Agent` 引用 → `agent.llm_helper`（懒加载属性，每次访问基于当前 `_client_pool`，跟随 `/model` 切换）
  - 无 `Agent` 引用（独立工具函数/后台任务） → `LLMHelper.from_config(app_cfg)`
  - 只有单个 `client` 没有 `client_pool`（如 `memory_factory.py`） → 直接用 `LLMClient.chat_with_retry()`，不必强套 `LLMHelper`
- **两个入口**：`ask(prompt, ...) -> str`（单轮文本，最常用）、`chat(messages, system, tools, ...) -> LLMResponse`（完整能力）
- **默认路径**：走 `client_pool.call_with_pool`，复用多 key 轮转 + 多配置 fallback
- **override 逃生舱**：`override_model` / `override_provider` / `override_temperature` 任一传入时，一次性构造独立 client（不进 fallback chain），仍套用同一个 `RetryPolicy`——用于 judge 想固定用更强模型评审这类场景
- **`max_retries` 按场景区别对待**，无统一默认值套用所有场景：目标拆解/judge 用默认 3，decision 路由判定用 2，ensemble 候选生成用 1（不重试，交给"多候选"机制兜底整体成功率）
- **工具函数入口**（`tools/orchestration.py` 的 `run_ensemble_llm`/`run_ensemble_subagents`）通过 thread-local provider 机制（`set_current_llm_helper_provider`，与 `active_skills` 同款模式）拿到当前 agent 的 `llm_helper`，未绑定 agent 的线程自动降级为 `LLMHelper.from_config(cfg)`
- **例外（不接入）**：`orchestrator/sub_agent.py`（独立隔离对话，自带 `LLMClientPool` + 外层重试，设计上不迁移）、`agent/llm_control.py` 的 `/model` 探测调用（故意不重试，避免掩盖配置错误）
- **新增旁路 LLM 调用的硬性要求**：一律通过 `LLMHelper`（或明确记录例外原因），**禁止**再手写 `LLMConfig.from_app_config(cfg)` + `create_client()` 的组合；自检命令：`grep -rn "LLMConfig.from_app_config" src/`，预期只剩 `sub_agent.py` 与 `agent/core.py` 两处
- 参见 [LLMHelper 使用指南](docs/llm-helper-guide.md)、`next_doc/llm_helper_unification_plan.md`（完整改造计划）

### 重试退避策略

- 核心模块：`src/mini_agent/llm/retry.py`
- **BackoffStrategy** 抽象基类：`delay_for(attempt)` 返回第 N 次重试前等待秒数
- 内置策略：
  - `FixedBackoff(delay)` — 每次等待固定秒数（默认）
  - `LinearBackoff(initial, step, max_delay)` — 线性递增：initial, initial+step, initial+2*step, …
  - `ExponentialBackoff(initial, multiplier, max_delay)` — 指数递增：initial, initial×m, initial×m², …
- `parse_backoff(mode, initial, step_or_multiplier, max_delay)` — 从字符串模式名构造策略
- **配置方式**：
  - CLI：`--retry-backoff fixed|linear|exponential --retry-backoff-step N --retry-backoff-max S`
  - 配置文件：`llm_retry_backoff_mode`、`llm_retry_backoff_step`、`llm_retry_backoff_max_delay`
- **兼容旧接口**：`retry_delay=N` 等价于 `backoff=FixedBackoff(N)`
- 参见 [重试退避指南](docs/retry-backoff-guide.md)

### RPM 限速

- 核心模块：`src/mini_agent/orchestrator/concurrency.py`（`RateLimiter`）
- 滑动窗口频率限制器，限制每分钟最多发出 max_rpm 次 LLM 请求
- 超出时阻塞等待，直到窗口内请求数低于上限
- CLI：`--rpm N`（0 = 不限速，默认 0）
- 状态栏显示 RPM 使用率进度条（接近上限时高亮警告）

### 自我演化基础设施（Stage 0）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 0，为后续 lesson memory / 自动改代码能力打地基

- **受保护路径清单**：`scripts/protected_paths.py`，独立于 `src/mini_agent/` 包外，不 import 任何 mini_agent 模块；`is_protected_path(path)` 命中即强制判定为 T3 风险等级。当前覆盖 `agent/`（整个目录）/`agent.py`（历史单文件路径，防御性保留）/`permissions.py`/`hooks/`/清单自身，并为 Stage 2 的 `evolution/` 包预留正则规则
- **任务进度叙事**：`orchestrator/task.py` 的 `TaskRecord.write_manifest()`，任务创建时落初始 `manifest.json`，agent 可调用 `update_task_progress` 工具主动更新 `progress`/`decision_log`，任务结束时补写 `outcome`
- **计划持久化与恢复**：`orchestrator/plan.py` 的 `ExecutionPlan` 每次状态变更自动写 `plan_snapshot.json`；session 启动/续接时通过 `try_restore_plan()` 自动恢复，中断时仍 `RUNNING` 的任务状态被忠实保留
- 详见 [受保护路径清单指南](docs/protected-paths-guide.md)、[Plan 与 Task 机制说明](docs/plan-and-task-guide.md) 第 10 节、[存储设计](docs/storage-design.md) 4.4 节

### Lesson Memory（Stage 1）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 1（Phase B），四条独立写入路径，任意一条触发都立即写入，不等 session 结束

- **数据结构**：`MemoryEntry`（`perception/memory_store.py`）新增 `entry_type`/`trigger`/`outcome`/`root_cause`/`suggested_action`/`confidence`/`occurrence_count`/`source` 8 个字段，全部带默认值，summary 型条目零迁移成本
- **规则触发**（不调用 LLM）：`perception/lesson_rules.py` 的 `LessonRuleEngine`，连续失败 ≥ N 次（默认 3）或权限拒绝后重试成功，`confidence=0.6`、`source="self_reflection"`；接入 `tool_executor.py`
- **SessionEnd 反思**：`agent.trigger_session_end()`，在 REPL 真正退出（`EOFError`/`exit`/`quit`）时同步触发 `SessionEnd` hook + 一次轻量 LLM 反思调用，基于 `tool_stats` + `is_turn_boundary()` 截取的最后若干轮用户意图生成结构化 lesson 候选
- **人类反馈纠正检测**：`perception/correction_detector.py` 的 `detect_correction()`，规则式短语匹配（中英文约 30 条，刻意排除高误报模式），挂在 `run_turn()` 的 `append_user` 之后，`confidence=0.85`、`source="human_feedback"`
- **`(e)dit` 审批编辑接入**：`permissions.py` 新增 `last_edit`/`pop_last_edit()`，`(e)dit` 编辑发生时通过 `tool_executor.py` 的 `on_edit_detected` 回调转交 `agent._on_edit_detected()`，写入 `HType.USER_CORRECTION` 类型的 history 消息 + 生成 `human_feedback` lesson
- 详见 [记忆管理指南](docs/memory-management-guide.md#lesson-memory)、[history 类型化设计](docs/history-typed-design.md)、[权限管理指南](docs/permission-guide.md)、[hooks 指南](docs/hooks.md)

### 自我演化安全网（Stage 2）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 2（Phase F），自我修改的唯一写入入口 + 分级校验 + 进程级隔离

- **`StateRepo`**（`evolution/state_repo.py`）：`apply()` 是所有自我修改的唯一写入入口，原子化（先算受保护路径强制升级后的生效 tier → 跑校验 → 全部通过才落盘 + `git commit`），commit message 结构化携带 `source_lessons`/`session_id`/`confidence`/`occurrence_count`/`proposed_by`；改动路径命中 `scripts/protected_paths.py` 清单时强制升级为 T3，只升不降
- **验证流水线**（`evolution/validators.py`）：T0 schema 校验 / T1 加载校验（真用 `SkillLoader`/`AgentProfile` 解析一遍）/ T2 lint + 现有单测 / T3 同 T2 + 强制人审
- **`EvolutionWorkspace`**（`evolution/workspace.py`）：基于 `git worktree` 的进程级隔离，`smoke_boot()` 验证改动在隔离环境里能正常加载，复用现有 `--sandbox` flag
- **`/evolution log|show|diff|revert`** CLI 命令组：`revert` 触发后自动生成一条 `source="revert_record"` 的 lesson，反哺记忆系统
- 详见 [自我演化安全网指南（Stage 2）](docs/self-evolution-stage2-guide.md)

### lesson → skill 闭环（Stage 3.1）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 3.1（Phase C），`memory.jsonl` 中沉淀的经验自动提炼为可复用的 skill

- **`skill_propose` 工具**：内部调用 `StateRepo.apply()` 在 `evolve/<date>-skill-<name>` 分支上写 `skills/<name>/SKILL.md`，tier 固定 T1；含 fresh-repo（全新项目首次触发演化、`git worktree add ... HEAD` 因无提交而失败）修复
- **`evolution-agent` profile**（`.agent/agents/evolution-agent.md`）：复用现有 `AgentProfile` 机制的专职 sub-agent，只读 lesson + 调用 `skill_propose`，不直接改动主分支
- **lesson 阈值扫描**（`perception/lesson_review.py`）：`/evolve review` 扫描 `occurrence_count` 超阈值（T1 默认 3）的 lesson，按关键词 Jaccard 相似度分组后 spawn `evolution-agent` 处理
- 详见 [自我演化 lesson → skill 闭环指南（Stage 3.1）](docs/self-evolution-stage3-1-guide.md)

### eval 反馈环（Stage 3.2）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 3.2（Phase D），验证某个 skill 开启前后的真实效果差异

- **`mini-agent eval --scenario DIR [--skill NAME]`**：复用 `test_cases/*.txt` 既有格式作为回归集，对同一批场景分别跑 with/without-skill 两遍，对比 turns/token/tool 失败率，输出 JSON 报告
- **核心引擎**（`evolution/eval_runner.py`）：场景加载、单场景执行（真实 LLM 调用）、对比报告生成，与 CLI 解耦，可被未来的 `evolution-agent` 直接调用
- **`SkillLoader.exclude(name)`**：与 `deactivate()` 的区别是把 skill 从 `_all` 中整体移除，保证不会被 `auto_activate()` 关键词命中重新拉起，是 `--without-skill` 严格排除的关键
- 详见 [自我演化 eval 反馈环指南（Stage 3.2）](docs/self-evolution-stage3-2-guide.md)

### SubAgent 信息继承（Stage 3.3）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 3.3（Phase E），主 agent 的运行期状态向 SubAgent 继承/共享

- **Skill 继承**：`Task.active_skills` 字段 + `tools/orchestration.py` 的 thread-local provider 机制，SubAgent 启动时按名称自动激活主 agent 当前激活的 skill；用独立 `ToolRegistry` 副本（`filtered()`）规避全局单例重复注册崩溃 / 闭包跨实例串台
- **`ToolResultCache` 跨 SubAgent 共享**：`tool_cache_enabled` 开启时 `TaskManager` 持有唯一加锁实例（`threading.Lock` 保护 `OrderedDict` 操作），避免并发 SubAgent 重复读取同一份文件
- **lesson 回流**：SubAgent 与主 agent 共享同一个 `memory.jsonl` 磁盘路径，SubAgent 进入终态（`DONE`/`FAILED`/`CANCELLED`）时触发主 agent 已注册 memory backend 的 `reload()`
- 详见 [自我演化 SubAgent 信息继承指南（Stage 3.3）](docs/self-evolution-stage3-3-guide.md)

### Workdir 知识层（Stage 4 / Phase W2）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 4，设计依据 `next_doc/self_evolution_design.md` 第 8.2 节。在 `.agent/` 下新增五个文件，是项目级"软知识"的沉淀层

- **五个文件**：`project.json`（项目身份证，含 `name`/`root_language`/`key_modules`/`environment_fingerprint`）、`timeline.jsonl`（session 时序骨架，独立轻量反思生成 `theme`/`key_outcomes`）、`work_index.json`（`WorkThread` 聚合，跨 session 累积 `cumulative_progress`/`next_suggested`）、`open_threads.json`（跨 session 待处理线索池）、`knowledge.md`（项目软知识，T1，走 `StateRepo.apply()`）
- **核心模块**：`perception/workdir_knowledge.py`（数据模型 + 读写 + 检索：`search_knowledge_index()`/`read_knowledge_section()`）、`tools/workdir_knowledge.py`（`add_open_thread`/`update_work_thread`/`update_knowledge`/`search_knowledge` 四个工具）
- **维护机制**：SessionStart 时 `agent/lifecycle.py` 的 `_maybe_ensure_project_meta()` 创建/更新 `project.json`（含 12.2 横向加固 `environment_fingerprint` 漂移检测）；SessionEnd 时 `agent/reflection.py::_update_workdir_knowledge_on_session_end()` 追加 `timeline.jsonl`、关联 `work_index.json`、回收 `task_manifest.outcome.unresolved` 进 `open_threads.json`
- **context 注入**：`context_builder.py` always-on 注入 `project.json` 身份信息 + 活跃 WorkThread 进度 + 高优先级 open_threads（数量上限 `WorkdirKnowledgeConfig.open_threads_inject_limit`，默认 5）。`knowledge.md` **不**走 always-on——按设计文档 8.4 节"按意图检索注入"，agent 需要主动调用 `search_knowledge` 工具
- **横向加固顺带完成**：14.1 `knowledge_index.json`（`update_knowledge()` 写入时同步生成结构化索引）+ 检索侧补全（`search_knowledge` 工具：此前索引建了但从未被读出来用过，TF-IDF 关键词检索，复用 `memory_store.py` 的中英混合分词器）
- **配置**：`WorkdirKnowledgeConfig`（默认 `enabled=True`），`work_thread_relation_days`（默认 7 天，关联启发式窗口）
- 详见 [Workdir 知识层与 Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md)

### Global 知识层（Stage 5 / Phase W3）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 5，设计依据 `next_doc/self_evolution_design.md` 第 8.3 节。`~/.agent/` 下新增四个文件，scope 从单项目升级为跨项目

- **四个文件**：`self_profile.json`（agent 自我模型，`AgentSelfProfile` 落地版，`autonomy_level` 默认 `"passive"`）、`projects_index.json`（workdir 注册表，30 天无活动自动标记 `dormant`）、`cross_project_index.json`（跨项目模式聚合，`observed_in_projects`/`confidence`/`global_skill_candidate`）、`activity_log.jsonl`（全局活动时序，与 `timeline.jsonl` 同一处代码路径写入）
- **核心模块**：`perception/global_knowledge.py`（数据模型 + 读写 + `scan_cross_project_patterns()`/`merge_cross_project_patterns()` 跨项目聚合）
- **维护机制**：session 启动时 `_maybe_register_global_project()` 注册/更新 `projects_index.json` 并做 dormant 巡检；SessionEnd 时复用 Stage 4 的 theme/duration 计算结果追加 `activity_log.jsonl` 并更新 `self_profile.json`
- **本 Stage 范围**：5.4 节"跨项目模式扫描"只实现扫描聚合函数本身（`scan_cross_project_patterns`），**不接调度自动触发**——触发时机（"该不该自动晋升"）留给 Stage 8 巩固循环
- **context 注入**：`_build_global_knowledge_block()` always-on 注入 `self_assessment` 精简版 + `pending_evolve_branches`；workdir 变化时注入 `projects_index`/`activity_log` 最近若干条（`GlobalKnowledgeConfig.activity_log_inject_limit`，默认 5）
- **配置**：`GlobalKnowledgeConfig`（默认 `enabled=True`），`dormant_after_days`（默认 30）
- 详见 [Workdir 知识层与 Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md)

### 观察性（Stage 6 / 第 9 章）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 6，设计依据 `next_doc/self_evolution_design.md` 第 9/10/11 章。是 巩固循环 剪枝判断和异常检测的数据基础

- **6.1 时序性能追踪**：`SessionTracer` + `span()` context manager，在 `_agentic_loop()` 的 `call_llm`/`execute_tools`/`build_system` 三处打点，写入 `session_dir/traces.jsonl`，含 `context_breakdown`（`system_base`/`history`/`total` token 占比）字段——是 Stage 8 剪枝判断的直接数据来源
- **6.2 系统健康检查**：`GET /v1/diagnostics` 端点，五个分组：`performance`（traces 聚合）/`memory`（条目统计）/`skills`（激活列表）/`evolution`（演化状态）/`anomaly_flags`（异常标记），直接聚合底层数据，不反向依赖 `self_profile.json`
- **6.3 异常行为检测**：`detect_anomalies()` k-σ 算法（默认 k=3.0），检测 `tool_call_spike`/`token_spike`/`session_duration_spike` 三类异常，依赖 `activity_log.jsonl` 中的 `session_metrics` 行（至少 `anomaly_min_samples` 条历史记录，默认 10，才启用）
- **6.4 工具调用因果链**：`classify_error()` 基于正则规则做 14 种 `error_category` 分类（复用 `lesson_rules.py` 的异常类名模式）；`traces.jsonl` 的 tool_call 记录含 `sequence_in_turn`/`error_category`/`resolves_seq` 字段
- **核心模块**：`perception/observability.py` + `api/routes.py`（`/diagnostics` 端点）
- **配置**：`ObservabilityConfig`（`enabled`/`tracing_enabled`/`anomaly_k_sigma`/`anomaly_min_samples`），便捷属性 `cfg.observability_enabled`/`cfg.tracing_enabled`
- 详见 [观察性系统指南（Stage 6）](docs/observability-guide.md)

### 横向加固任务池（Stage 7）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 7，延续设计文档"可在任意阶段穿插"的定位，挂在 Stage 4-8 的具体改动点上顺带完成，非独立排期

- **13.2 + 15.3（SubAgent 降级重试链 + 任务降级策略）**：`TaskManager._try_demotion()`/`_resubmit_demoted()`，两阶段降级——阶段一按 `Task.fallback_profiles` 列表顺序切换 agent profile；阶段二（全部 profile 试过后）若设置了 `Task.demotion_scope` 则缩小目标范围重试一次，复用原 `task_id`，下次 tick 自动调度。是 Phase H 自主运行时"没有用户在场纠正"场景的硬性技术前提
- **15.2（错误分类驱动恢复）**：`reminders/matcher.py` 的 `condition.error_category` 字段，基于 Stage 6.4 的分类结果精确路由，无需正则
- **14.1/14.2/14.3（knowledge_index / Skill 依赖冲突图 / 知识可信度传递）**：分别在 Stage 4 的 `update_knowledge()` 与 Stage 3.2 的 `SkillLoader.activate()` 改动中顺手完成（`conflicts_with`/`activation_conditions` 约束检查、`confidence_score` 注入 context 时调整语气）。14.1 的检索侧（设计文档 8.4 节"按意图检索注入"，此前只完成了写入侧）后续补全为 `search_knowledge` 工具 + `search_knowledge_index()`/`read_knowledge_section()`
- **12.2（环境漂移检测）**：`detect_environment_drift()` + `_maybe_ensure_project_meta()`，在 Stage 4 顺手完成
- **暂缓/留待 Stage 9 的条目**：12.1（`FILE_CHANGE_EFFECTS`）、12.3（inbound webhook）、13.1（能力匹配调度）、13.3（中间结果流）、15.1（元认知 checkpoint）、16.2（隐式反馈捕捉）、16.3（澄清优先分支）、17.2（Prompt 工程版本化）——详见计划文档 Stage 7 表格

### 巩固循环 后台循环（Stage 8）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 8，设计依据 `next_doc/self_evolution_design.md` 第 6.4/6.5/6.6/6.7 节。不依赖常驻进程的"时间门控"调度，是 Phase H 自主运行时的前置数据沉淀

- **8.1 调度骨架**：`consolidation_rhythm.json` 的 `_last_run_at` 字段替代 cron，`should_run_consolidation()` 在每次 `trigger_session_end()` 时检查（默认 24h 间隔）；手动触发入口 `/evolve consolidate [--force] [--dry-run]`
- **8.2 剪枝候选**：`prune_skills()`，规则 A（高 token 成本 + 未使用）+ 规则 B（冲突检测），输出 `PruneCandidate` 列表，不自动执行下线
- **8.3 能力地图**：`build_capability_map()` 扫描 `tasks/*/manifest.json`，按 `_infer_domain()` 规则式推断任务类型，聚合成功率，写入 `entry_type="capability_map"` 的 memory 条目——终于激活了早期设计就预留的这个枚举值。Global scope 汇总留待数据积累后扩展
- **8.4 Scope 晋升**：`check_scope_promotion()` 读 `cross_project_index.json`，判据 `observed_in_projects ≥ 2` 且 `confidence ≥ 0.70` 且 `global_skill_candidate=true`，当前只输出候选列表（`PromotionCandidate`），不直接调用 `skill_propose`
- **8.5 节奏治理**：`rhythm_is_allowed()`/`record_proposal()`，7 天冷却期，可对任意 `(proposal_type, key)` 限流——回应设计文档开放问题 1（T1 自动合并的观察期）
- **8.6 知识巩固（图书馆式索引）**：`run_consolidation()` 顺带调用 `LibraryIndex.consolidate()`——未分类候选批量聚类生长分类节点、分类树按关键词 Jaccard 相似度合并收敛、攒够证据的实体摘要批量重写（含冲突检测）、实体去噪+近重复合并；结果并入 `ConsolidationReport.knowledge_consolidation`，`/evolve consolidate` 报告展示；新增 `/evolve timeline --entity <id>|--category <code>` 查询知识生命周期编年目录
- **决策候选批量落盘（对应决策/取舍知识提炼计划）**：`run_consolidation()` 顺带调用 `wiki/decision_writer.py::consolidate_pending()`——批量读取 compact 阶段入队的 `.agent/decision_candidates_pending.jsonl`，合并同批次指向同一件事的候选后落盘（更新/推翻新建/新建三分支），"新建"套本节 8.5 节奏治理冷却；结果并入 `ConsolidationReport.decision_consolidation`，`/evolve consolidate` 报告展示；详见 [巩固循环 后台循环指南](docs/self-evolution-consolidation-guide.md) 4.4 节
- **核心模块**：`evolution/consolidation.py`（`run_consolidation()` 整体入口）+ `cli/commands/evolve.py`（`_handle_consolidation()`）+ `agent/reflection.py`（`_maybe_run_consolidation()`，SessionEnd 时间门控接入点）
- 详见 [巩固循环 后台循环指南（Stage 8）](docs/self-evolution-consolidation-guide.md)、[图书馆式知识索引指南](docs/library-index-guide.md)

### Wiki 式知识库

> 对应项目根目录《wiki式知识库重构计划.md》。图书馆式索引（分类树+实体索引）的平行新实现，核心动机是分类树"每条知识只有一个最合适位置"的单一归属假设与软件工程知识天然网状的结构不匹配

- **阶段一（基础设施）**：新增 `src/mini_agent/wiki/` 包，页面用 md（frontmatter + 正文 + `[[link]]`）存储，`_index/` 下 `graph.json`/`tags.json`/`backlinks.json`/`search_index.json` 四个索引全部可随时删除、随时从 md 重新生成
- **阶段二（迁移与双写）**：`migration.py::mirror_entity()` 把 `EntityStore` 实体镜像进 `wiki/entities/*.md`，接入 `LibraryIndex.on_new_entry()`/`consolidate()`；`dedup.py::find_similar_page()` 判重默认规则打分（tag重合度+关键词Jaccard）+ 不确定区间才问一次 LLM，embedding 保留为显式可选路径
- **阶段三（检索切换）**：`search.py::wiki_shelf_search()` 三段式检索——规则粗筛 → `GraphIndex.expand(strong_only=True)` 图扩展 → LLM 精排（标注"基于页面:"依据），`LibraryIndex.wiki_search()` 暴露，与 `shelf_search()` 完全并存不替换；`consolidate()` 新增步骤 6 自动触发增量索引重建
- **阶段四（专题页与收尾）**：`topics.py::consolidate_topics()` 按 tag 聚类且强链接密度达标（默认页面数≥4、密度≥0.5）时 LLM 综合聚合成 `topics/*.md`，接入 `consolidate()` 步骤 7；新增 `/wiki <page-id>|list|search|rebuild` 命令
- **接线修复**：`wiki_paths` 参数虽在阶段二就加入 `LibraryIndex.__init__`，但 `memory_factory.py` 此前从未真正传递过，双写路径在真实 agent 运行中从未被触发；本次补上 `MemoryConfig.wiki_enabled`（默认开启）完成接线
- **P4 转正 + 实际切换**：`wiki/promotion.py` 把"内容占比/校验通过率/检索 A/B 命中率"三项转正标准量化为可持续观测的指标（`/wiki promotion` 只读展示）；`context_builder.py` 现在默认（`MemoryConfig.library_wiki_search_primary=True`）优先尝试 `wiki_search`，未命中才回退 `shelf_search`——这次切换是在没有完整 P4 观测数据支撑下执行的，`library_wiki_search_primary` 设为 `False` 可随时完全退回旧路径
- **O1-O4、E1-E3 提取层与组织层改进计划**（`next_doc/wiki知识库提取与组织层改进计划.md`，全部已完成）：O1 索引复用（`index_reader.py`）+ 信度分层（`grounded_hit_count`）；E3 实体摘要反哺抽取（`entity_digest.py`）；E1 抽取与 compact 解耦（`history/extraction_trigger.py`，独立触发窗口）；E2 抽取任务拆分（JSON schema 字段重排到 decisions/entities/facts 优先于 compact_summary）；O2 多跳衰减图扩展（`GraphIndex.expand(max_hops=, decay=)`）；O3 topic 再巩固（追加进已有 topic 而非只生成新的）；O4 统一知识生命周期状态机（`wiki/lifecycle.py`，`knowledge_state`/`last_validated_at`/`validated_by` 三个新 frontmatter 字段，`/wiki lifecycle-scan` 命令）。详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) §十 与各项独立实施记录（`next_doc/wiki提取层改进计划_*.md`）
- **下一阶段改进计划**（`next_doc/wiki_next_phase_improvement_plan.md`，全部已完成）：双轨制退出评估（`wiki/decommission.py::check_and_plan()`，只读评估+三步执行清单，不自动删代码，挂载于巩固循环收尾）；陈旧专题页标注（`wiki/gap_scanner.py::mark_stale_topics()`，复用 O4 `knowledge_state`）；`consolidate()` 分步超时熔断（`evolution/step_runner.py`，每子步骤独立超时预算，`step_timings` 记录）；世界知识独立触发信号（`history/extraction_trigger.py` 新增 `trigger_reason="entity_density"`）；知识缺口主动扫描 + 兜底页归并清理（`wiki/gap_scanner.py::scan_gaps()`/`wiki/fallback_cleanup.py`，新增命令 `/wiki gap-scan`/`/wiki fallback-cleanup`）；daemon 新增 2 个内置 cron job（`sys:wiki_gap_scan`/`sys:wiki_fallback_cleanup`）；补上此前遗漏的 `/wiki` 命令行 Tab 补全注册（`ui/terminal.py::_COMMANDS`）。详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) 十一·5 节、`next_doc/wiki_next_phase_improvement_plan.md`、`next_doc/wiki_next_phase_implementation_record.md`

### 决策/取舍知识提炼

> 对应《决策/取舍知识提炼计划》，Wiki 式知识库 `decision` 页面类型之上的独立提炼线，捕捉 lesson（规则触发）和 correction（人类显式纠正）都覆盖不到的场景——正常推进、没报错也没人纠正时做出的工程决策取舍

- **提取**：`history/compression.py::LLMSummaryStrategy` 复用 compact 阶段本就要发的摘要 LLM 调用，输出改成 `{compact_summary, decisions[]}` 结构化 JSON；`history/decision_extraction.py::parse_decision_response()` 容错解析（失败退化为纯摘要，不阻断 compact）；`CompressConfig.extract_decisions` 默认开启
- **批量节流落盘**：compact 阶段只调用 `wiki/decision_writer.py::queue_candidates()` 把候选 append 到 `.agent/decision_candidates_pending.jsonl`，不立即落盘；巩固循环（`run_consolidation()`）批量调用 `consolidate_pending()`——先合并同批次里指向同一件事的多条候选（topic slug 相同或 `related_entities` 有交集，只留最新一条 `chosen`），再走 `process_candidates()` 三分支（命中且方案一致→只更新；命中但方案变了→旧页 `status=overturned` + 新建页 `supersedes`/`superseded_by` 双向串联沿革链；未命中→新建 `status=settled`），"新建"动作套 8.5 节奏治理冷却（`CompressConfig.decision_batch_min_interval_days`，默认 1 天）
- **置信度与状态**：决策页 `confidence` 固定 `0.5`（低于 lesson 的 0.6、human correction 的 0.7，因为决策复盘是 agent 对自身历史行为的二次解读，主观重构风险更高）；`parser.py::STATUS_VALUES` 新增 `settled`/`overturned`，`validator.py` 新增 supersedes/superseded_by 成对性校验
- **提案前主动召回**：`evolution/decision_recall.py::recall_related_decisions()` 复用 `wiki_shelf_search()` 三段式检索、限定 `type=decision`，按 `settled`/`overturned` 分别渲染提醒文字；已接入 `cli/commands/evolve.py::_spawn_evolution_agent()`——`/evolve review` spawn evolution-agent 前自动查一遍相关历史决策，把提醒前置注入 task context，异常静默降级不影响主流程
- 详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) 九·2 节、[巩固循环 后台循环指南](docs/self-evolution-consolidation-guide.md) 4.4 节

### 自主运行时（Stage 9 / Phase H）

> 对应 `next_doc/self_evolution_stage9_plan.md`。在全部 Stage 0-8 基础设施之上引入常驻守护进程、跨会话目标层级和三档位自主调度

- **进程模型升级**：daemon 进程常驻（`cli/daemon.py`），AgentRunner 线程内嵌 `AutonomousLoop` tick 调度；CLI 进入"连接模式"（通过现有 HTTP API 接入，不新增协议）；`--no-daemon` 可回退到传统模式
- **守护进程管理**（`cli/daemon.py`）：`mini-agent daemon start [--detach]|stop|status`；PID 文件写入 `.agent/daemon.pid` + `.agent/daemon_info.json`
- **Goal Backlog**（`perception/goal_backlog.py`）：`GoalNode`（Goal/Objective 统一节点），持久化到 `.agent/goals.json`；Objective 可通过 `work_thread_ref` 关联已有 WorkThread（复用 Stage 4 进展文本）；`has_actionable_work()` / `active_objectives()` 是 ObjectiveExecutor 调用的核心接口
- **AutonomousLoop**（`evolution/autonomous_loop.py`）：三档位（passive/maintenance/autonomous），边界用方法边界物理隔离；`passive` 档位调用 `CronScheduler.tick()`；`maintenance` 档位起调用 `ObjectiveExecutor`；`autonomous` 档位加入 `SoftGoalDeriver` + `ExplorationSandbox`
- **CronScheduler**（`evolution/cron_scheduler.py`）：interval/cron 双格式，内置系统 job（consolidation/wiki_gap_scan/wiki_fallback_cleanup/workdir_sync/self_eval/goal_review/digest_trim/self_maintain，后两个 wiki job 为下一阶段新增；另有 `daily_digest`/`next_action_digest`/`decision_profile_update` 三个属于主动推荐与数字分身机制设计方案，首次注入时的 `enabled` 由 `AppConfig.digest_advisor` 决定）；触发的 job 通过 `InputQueue.enqueue(initiator="cron")` 提交
- **ObjectiveExecutor**（`evolution/objective_executor.py`）：Objective 拆解为 3-8 个 Step，每步完成后 `on_turn_done()` 回调推进；SSE 推送 `OBJECTIVE_PROGRESS` 事件；同时最多 2 个 Objective 并行，单步最多重试 2 次
- **SoftGoalDeriver**（`evolution/soft_goal_deriver.py`）：三路信号（capability_map 低置信度 / WorkThread 积压 / 高频 Lesson）；`derive_candidates()` 分 capability 类和其他类；capability 类经 ExplorationSandbox 验证后才写 GoalBacklog
- **资源仲裁**（`evolution/resource_arbiter.py`）：用户优先 / 路径冲突检测 / 预算硬限制三条规则；`activity_digest.jsonl` 自主行为粗粒度日志；`build_digest_summary()` 六分组渲染（Objective进展/Cron执行/探索结果/Agent建议/进化提案/其他）
- **initiator 字段贯穿**：`_TurnCommand`/`enqueue()`/`TurnInfo`/`StateRepo.apply()` 均加入 `initiator` 参数（`"user"`/`"autonomous"`/`"cron"`）；自主发起的 T0 改动自动上浮为 T1
- **新 API 端点**：`/v1/autonomous/status`、`/v1/goals` CRUD、`/v1/cron/jobs` CRUD，共 8 个新端点；另有主动推荐与数字分身机制设计方案新增的 3 个只读端点 `/v1/digest/daily`、`/v1/next_actions`、`/v1/decision_profile`（均直接读已落盘文件，不重复触发生成）
- **CLI 命令**：`/goals`（含 accept/reject）、`/cron`（含所有子命令）、`/digest`（六分组摘要）、`/digest daily`（融合日报）、`/next`（主动推荐）、`/decision_profile`（决策画像，注意与既有 `/profile` 强制刷新用户画像命令无关）、`mini-agent daemon start|stop|status`
- 详见 [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md)

### 具身智能改进（Embodied Agent）

> 对应 `next_doc/embodied_agent_design.md`（设计依据）与
> `next_doc/embodied_agent_improvement_plan_v3.md`（改进计划 + 逐项实现取舍
> 说明）。借用本体感知/余裕感知/工具透明性/自创生等具身认知类比，给
> Agent 补上"对自身状态的显式建模"，全部 A/B/C 三阶段 + 阶段 D 收尾共 12
> 项均已实现

- **A1 Connected REPL 完整命令对等**：`cli/daemon.py::DaemonClient` 把连接
  模式下的 slash 命令路由到对应 HTTP API 端点，与本地模式命令对等
- **A2 Lesson source 区分**：`perception/correction_detector.py` 检测用户
  直接纠正短语，立即生成 `source="human_feedback"` 的 lesson（区别于
  `self_reflection`/`experiment_confirmed`/`revert_record`），供 B2/C2 差异化对待
- **A3 Reminder pre_tool 触发**：`reminders/manager.py::check_pre_tool()`
  在工具执行前做前馈匹配，命中则提前注入提醒，而非等出错后补救
- **B1 本体感知模块**：`perception/proprioception.py`（`ProprioceptionConfig`），
  O(1) 纯计算的轮间快照（认知负荷/不确定性/风险感知/剩余预算/frustration），
  frustration 超阈值 + 连续失败达标时注入元认知提示
- **B2 Lesson → Reminder 自动闭环**：`evolution/lesson_to_reminder.py`，
  human_feedback 来源 1 次即激活，其余来源需达 T1 门槛且先落草稿；
  `/evolution lessons-to-reminders` 命令
- **B3 Workflow 并发执行**：`workflow/runner.py::_compute_parallel_batches()`
  对 `depends_on` 拓扑排序，无依赖步骤并发执行
- **B4 余裕感知层（AffordanceMap）**：`perception/affordance_analyzer.py`
  （`AffordanceConfig`），session 级构建，交叉分析 open_threads/capability_map/
  lesson memory 生成行动机会摘要，接入 `api/session_pool.py`（当前仅多用户路径生效）
- **工具透明性（IntentActionMapper）**：`perception/intent_action_mapper.py`，
  纯规则匹配把工具调用按意图分组（exploration/code_edit/test_run/env_setup/
  vcs_op/research/other），写入 `traces.jsonl` 的 `action_events` 字段，不
  改变 history 本身
- **C1 AgentSelfModel**：`perception/self_model.py`，聚合 SelfAssessment（慢
  变量）+ capability_map + ProprioceptionModule 快照 + AffordanceMap（快变量），
  澄清与 UserProfile/RoleProfileManager/AgentProfile 三个既有 profile 概念的语义边界
- **C2 时间加权记忆激活**：`evolution/memory_aging.py::compute_decay_factor()`，
  lesson 按 source 区分半衰期基准（human_feedback 90d 最慢 → revert_record
  14d 最快），occurrence_count 越高衰减越慢（封顶 4 倍），接入
  `memory_store.py::_score_all()`；非 lesson 条目行为不变
- **C3 认知锚点文件**：`agent/lifecycle.py::_save_cognitive_anchor()`/
  `_maybe_load_cognitive_anchor()` + `AgentPaths.workdir_cognitive_anchor`，
  Ctrl-C 打断时 LLM 生成四段式"思维状态重建指南"（`prompts/system/
  cognitive_anchor.md`），下次 session 启动时注入 `system_extra` 并归档；
  daemon connected REPL 的 Ctrl-C 暂未接入，留作后续
- **C4 自维护模块（SelfMaintenanceModule）**：`evolution/self_maintenance.py`，
  三项检查（`traces.jsonl` 失败率推断 stale_tools / skill tracker 推断
  stale_skills / lesson 聚类正负信号推断 conflicting_lessons），只产出建议
  写入 `activity_digest.jsonl`（`type="health_report"`），不自动修复；
  SessionEnd 时间门控 + 内置 cron job `sys:self_maintain`
- 详见 [具身智能改进指南](docs/embodied-agent-guide.md)

### 参数优先级

**命令行参数 > 配置文件参数**。之前配置文件优先级更高，已修正。

## 文档索引

- [系统概览](docs/system-overview.md) — 整体架构与模块介绍
- [记忆管理指南](docs/memory-management-guide.md) — 长期记忆系统，含 Lesson Memory（规则触发/SessionEnd 反思/人类反馈纠正检测）
- [history 类型化设计](docs/history-typed-design.md) — `_type` 字段化设计，含 `user_correction` 类型
- [权限管理指南](docs/permission-guide.md) — 权限守卫、白名单，`(e)dit` 接入 Lesson Memory
- [Hooks 机制](docs/hooks.md) — 15 个生命周期事件（Session / Prompt / Tool / Subagent / Task / Stop / Compact / TurnEnd），完整事件时序图与各事件用例
- [Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](docs/platform-tag-loading-guide.md) — 可加载对象的平台限制（termux/pc/windows/macos/linux/android）与 tag allow/deny 策略，`platform_policy.json` 配置，`/platform` 命令
- [Task 日志实时查看](docs/task-focus-viewing.md) — 方向键切换查看任务日志机制
- [终端显示机制深度解析](docs/terminal-display-internals.md) — 线程模型、状态栏控制、三阶段状态机、token 过滤
- [终端 I/O 指南](docs/terminal-io-guide.md) — 终端渲染与输入机制
- [任务与规划指南](docs/plan-and-task-guide.md) — 执行计划与并发任务，含 `plan_snapshot.json` 持久化与恢复
- [记事本机制说明](docs/notepad-guide.md) — 常驻 system prompt 的持久便签（`notepad_add`/`update`/`remove`/`summarize` 工具 + `/notepad` 命令），不受 history compact 影响，超阈值时 compact 提示建议总结
- [SubAgent 机制](docs/subagent-mechanism.md) — 子 Agent 实现细节
- [命令与工具参考](docs/commands-and-tools-reference.md) — 所有 slash 命令和工具
- [代码结构指南](docs/code-structure-guide.md) — 项目结构说明，含 `config/` 包拆分
- [配置系统指南](docs/config-guide.md) — 配置架构、子配置块、加载优先级
- [存储设计](docs/storage-design.md) — 文件布局，含 `manifest.json`/`plan_snapshot.json`
- [受保护路径清单指南](docs/protected-paths-guide.md) — T3 治理红线设计与扩展规则
- [自我演化安全网指南（Stage 2）](docs/self-evolution-stage2-guide.md) — `StateRepo`/验证流水线/`EvolutionWorkspace`/`/evolution` 命令组
- [自我演化 lesson → skill 闭环指南（Stage 3.1）](docs/self-evolution-stage3-1-guide.md) — `skill_propose`/`evolution-agent`/`/evolve review`
- [自我演化 eval 反馈环指南（Stage 3.2）](docs/self-evolution-stage3-2-guide.md) — `mini-agent eval` with/without-skill 对比
- [自我演化 SubAgent 信息继承指南（Stage 3.3）](docs/self-evolution-stage3-3-guide.md) — skill 继承/工具缓存共享/lesson 回流
- [Web Search 指南](docs/web-search-guide.md) — Web 搜索功能使用指南
- [图片技能指南](docs/image-skills-guide.md) — 图片识别与生成技能使用指南
- [Reminder 系统指南](docs/reminder-system-guide.md) — 动态提示注入机制使用指南
- [单元测试指南](docs/unit-testing-guide.md) — 测试结构、编写规范与运行方式
- [Env Info 指南](docs/env-info-guide.md) — 环境信息采集与注入，自定义 Provider 扩展
- [LLM 故障转移指南](docs/llm-failover-guide.md) — 多配置 fallback chain + 多 API Key 轮转
- [重试退避指南](docs/retry-backoff-guide.md) — fixed / linear / exponential 退避策略详解
- [Workdir 知识层与 Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md) — `project.json`/`work_index.json`/`open_threads.json`/`knowledge.md`（W2）+ `self_profile.json`/`projects_index.json`/`cross_project_index.json`/`activity_log.jsonl`（W3）
- [观察性系统指南（Stage 6）](docs/observability-guide.md) — `traces.jsonl` 追踪、`/diagnostics` 端点、异常检测、工具调用因果链
- [日志保存机制指南](docs/logging-mechanisms-guide.md) — 全项目日志/审计流（错误日志/LLM调试日志/daemon控制台日志/traces/行为事件等）保存机制汇总
- [巩固循环 后台循环指南（Stage 8）](docs/self-evolution-consolidation-guide.md) — 剪枝候选 / 能力地图 / Scope 晋升 / 演化节奏治理
- [图书馆式知识索引指南](docs/library-index-guide.md) — 分类树自动生长/合并 + 实体目录（冲突检测/去噪/近重复合并）+ 两步检索 + 检索反馈 + 纠正闭环 + 时间线查询 + 多用户书架隔离
- [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) — 图书馆式索引的平行实现（已切换为默认优先检索路径）：md 页面存储 + 显式关系图 + 双写镜像 + 三段式检索（规则粗筛→多跳图扩展→LLM精排）+ 专题页自动生成与再巩固 + 统一知识生命周期状态机（O1-O4、E1-E3 提取层与组织层改进计划）+ 双轨制退出评估/巩固分步熔断/世界知识独立触发/知识缺口扫描/daemon 定时任务（下一阶段改进计划）
- [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md) — 常驻守护进程 / Goal Backlog / 三档位 AutonomousLoop / 资源仲裁
- [跨子系统事件总线指南](docs/system-events-bus-guide.md) — `publish()`/`poll_since()` 轻量事件总线，已接入 frustration/记忆稀疏/效果回填负面判定/软目标候选复核/uncertainty 持续五类事件
- [具身智能改进指南](docs/embodied-agent-guide.md) — 本体感知 / 余裕感知 / 工具透明性 / AgentSelfModel / 时间加权记忆 / 认知锚点 / 自维护模块（A/B/C 三阶段共 12 项）
- [微信接入指南](docs/weixin-bot-guide.md) — `weixin_bot.py` 每用户 Agent 隔离 / 远程权限审批 / 同步-异步桥接；含 `_get_or_create` 事件循环死锁问题的根因分析与修复记录
- [Goal 模式指南](docs/goal-mode-guide.md) — 设定目标后自动多轮尝试直至达成，`/goal` 命令，验收标准协商 / GoalJudge 判定 / 异常中断恢复
- [轮次守门员指南](docs/turn-judge-guide.md) — 轮次结束等待用户输入前自动核查是否真的需要真人 / 是否应由系统代替用户反馈继续（`turn_judge` 配置块，默认关闭）
- [用户行为感知系统指南](docs/behavior-perception-guide.md) — 桌面（前台窗口/空闲/浏览器插件+CDP专用浏览器/Git/终端/媒体/应用启停）+ 手机（Tasker/快捷指令/Android伴侣App）行为采集，配置文件 `<project_root>/behavior_config.json`（跟 `agent_config.json` 同级），总开关与全部子开关默认关闭；分析层每日聚合工作与生活画像日报
- [脚本/LLM/Agent 混合执行系统指南](docs/hybrid-exec-guide.md) — **新增**：独立于 workflow 的 `hybrid_exec` 系统（P1-P4 已完成），探索优先 agent/llm、执行优先脚本、脚本坏了先自愈修复、修不好再降级，脚本仓库版本管理+成功率统计+自动退役，`default_executor()` 独立调用 / `hybrid_step` workflow 接入（插件文件开关）/ `GET /v1/hybrid_exec/summary` + 看板 Tab / `ReexplorePolicy` 跨 run 主动重探索（默认关闭）

## 当前进展

- Stage 0-8 均已完成（详见 `next_doc/self_evolution_implementation_plan.md` 与 `next_doc/self_evolution_stage4plus_plan.md` 各 Stage 完成记录）
- Stage 9（Phase H：自主运行时）是决策点而非常规排期 Stage，启动前置清单见 `next_doc/self_evolution_stage4plus_plan.md` 第 9.0 节；细化方案见 `next_doc/self_evolution_stage9_plan.md`
- 具身智能改进（`next_doc/embodied_agent_improvement_plan_v3.md`）A/B/C 三阶段共 12 项均已完成，详见 [具身智能改进指南](docs/embodied-agent-guide.md)；已知遗留缺口：AffordanceMap（B4）与认知锚点（C3）仅在部分路径生效（分别是"仅多用户 daemon"和"本地 CLI，daemon connected REPL 未接入"），详见改进计划文档对应小节
- 具身智能 × 自我演化四方案联动（`next_doc/embodied_autonomy_integration_design.md`）已全部完成：AffordanceMap 高风险域接入自主探索门控（方案一）、BehaviorContext 接入自主任务调度门控（方案二）、ProprioceptionModule.uncertainty 接入事件总线（方案三）、AgentSelfModel 接入 SoftGoalDeriver 候选打分单场景验证（方案四），详见 [具身智能改进指南](docs/embodied-agent-guide.md) 5.1/8/8.1 节、[Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md) 第 7/8 节、[跨子系统事件总线指南](docs/system-events-bus-guide.md) 6.6 节
- 图书馆式知识索引（分类树自动生长/合并 + 实体目录 + 两步检索 + 巩固循环 知识巩固）已完成，详见 [图书馆式知识索引指南](docs/library-index-guide.md)；正向检索反馈（"确实有用"）目前只有 API 没有自动触发点，等后续有更明确的信号源（如某 skill 被验证生效）再接入
- 新增 `MemoryConfig.per_turn_retrieval_enabled`（默认 `True`）：每轮自动检索总闸，关闭后 `context_builder.py::refresh_turn_context()` 在到达 `wiki_search`/`shelf_search`/`merge_search` 之前直接返回，本轮处理用户输入前不再自动检索任何记忆/wiki 文档，`library_wiki_search_primary`/`library_shelf_search_enabled` 两个开关此时不再生效；记忆写入、lesson/纠正检测、consolidation 等其他功能不受影响。已接入 `config/loader.py`（`agent_config.json` 里 `memory_per_turn_retrieval_enabled` 扁平键或 `memory.per_turn_retrieval_enabled` 嵌套写法均可，同级的 `library_index_enabled` 等其余 memory 子开关目前尚未接入配置文件加载，仍只能代码内构造）。详见 [图书馆式知识索引指南](docs/library-index-guide.md) 五、开关与配置项
- Goal 模式（`src/mini_agent/goal_mode/`）已完成粗粒度版本：验收标准协商 + GoalJudge 判定 + compact 整合 + 安全阀 + 异常中断恢复，详见 [Goal 模式指南](docs/goal-mode-guide.md)；细粒度 executor（`_agentic_loop` 内部工具调用后即可插入 Judge 判断）尚未实现，`GoalStepExecutor` 接口已预留扩展点