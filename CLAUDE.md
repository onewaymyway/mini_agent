# mini-agent

一个用 Python 实现的简化版 Claude Code，支持技能机制。

## 项目结构

- `src/mini_agent/agent.py` — Agent 主类（对话循环与编排）
- `src/mini_agent/context_builder.py` — System prompt 构建（skill/memory/project 注入）
- `src/mini_agent/tool_executor.py` — 工具执行（权限检查 + 调用 + 截断 + 缓存）
- `src/mini_agent/history_manager.py` — 历史管理（追加 + 压缩 + 快照恢复）
- `src/mini_agent/config/` — 配置管理包（`models.py`/`loader.py`/`prompt_builder.py`，含 providers.json 加载、llm_fallback_chain、退避策略参数；对外 `from mini_agent.config import ...` 路径不变）
- `src/mini_agent/permissions.py` — 工具调用的权限守卫
- `src/mini_agent/session.py` — 会话管理
- `src/mini_agent/tools/__init__.py` — 工具注册表和 `@tool` 装饰器
- `src/mini_agent/tools/builtin.py` — 内置工具（bash、文件 I/O、web_search 等）
- `src/mini_agent/tools/orchestration.py` — 并发编排工具（含 `update_task_progress` 任务进度叙事写入）
- `src/mini_agent/tools/skill_manager.py` — 技能管理工具
- `src/mini_agent/tools/plan.py` — 规划工具
- `src/mini_agent/tools/user_input.py` — 用户输入工具
- `src/mini_agent/mcp/` — MCP（Model Context Protocol）支持
- `src/mini_agent/skills/` — 技能发现和加载
- `src/mini_agent/cli/app.py` — CLI 应用入口
- `src/mini_agent/cli/parser.py` — 参数解析
- `src/mini_agent/cli/repl.py` — REPL 交互循环
- `src/mini_agent/cli/commands/` — REPL 命令处理器（concurrency, plans, sessions, skills, tasks, agents, hooks 等）
- `src/mini_agent/llm/` — LLM 抽象层
- `src/mini_agent/orchestrator/` — 并发编排（含 `plan.py` 的 `plan_snapshot.json` 持久化、`task.py` 的 `manifest.json` 写入）
- `src/mini_agent/hooks/` — hooks 机制（关键事件自动执行命令）
- `src/mini_agent/perception/` — 感知与记忆子系统（含具身改进：`proprioception.py` 本体感知/`affordance_analyzer.py` 余裕感知/`self_model.py` AgentSelfModel 聚合/`intent_action_mapper.py` 工具调用意图分组）
- `src/mini_agent/ui/` — 终端交互（terminal.py, renderer.py, repl_input.py）
- `src/mini_agent/api/` — HTTP API 服务
- `src/mini_agent/history/` — 历史管理（压缩算法 + RawHistory 即时落盘 + 条目类型定义）
- `src/mini_agent/prompts/` — Prompt 管理
- `src/mini_agent/storage/` — 存储层（`paths.py` 含 `session_plan_snapshot`/`task_manifest`/`workdir_xxx`/`global_xxx` 等路径方法）
- `src/mini_agent/env_info/` — 环境信息采集与注入（Provider 抽象基类 + 注册表 + 内置 Provider）
- `src/mini_agent/evolution/` — 自我演化机制：`state_repo.py`（唯一写入入口，Stage 9 加 `initiator` T0→T1 上浮）/`validators.py`（分级校验）/`workspace.py`（worktree 隔离）/`eval_runner.py`（eval 反馈环）/`phase_g.py`（Stage 8 后台循环：剪枝/能力地图/Scope 晋升/节奏治理）/`autonomous_loop.py`（Stage 9 三档位 tick + ExplorationSandbox + SoftGoalDeriver 接入）/`resource_arbiter.py`（Stage 9 资源仲裁 + activity_digest.jsonl + 六分组 build_digest_summary）/`cron_scheduler.py`（Stage 9 定时任务：interval/cron 双格式，5 个内置系统 job）/`objective_executor.py`（Stage 9 Objective 多步持续执行引擎）/`soft_goal_deriver.py`（Stage 9 autonomous 档位软目标 derive：三路信号 + ExplorationSandbox 验证）/`memory_aging.py`（具身改进 C2，lesson 按 source + occurrence_count 计算专属时间衰减半衰期）/`self_maintenance.py`（具身改进 C4，SelfMaintenanceModule：stale_tools/stale_skills/conflicting_lessons 健康检查，SessionEnd 时间门控 + `sys:self_maintain` cron job）
- `scripts/protected_paths.py` — 受保护路径清单（T3 治理红线，独立于 `src/mini_agent/` 包，自我演化相关安全机制使用）

## 开发规范

- 每个工具用 `@tool()` 装饰器注册，返回 `str` 类型
- 新工具放在 `src/mini_agent/tools/builtin.py` 或 `tools/` 目录下的新文件
- 技能文件放在 `.claude/skills/<name>/SKILL.md`
- 编辑文件时优先使用 `patch_file` 而非 `write_file`
- 核心代码放在 `src/mini_agent/` 目录下，使用包导入方式
- 所有与 LLM 的交互通过 `llm.LLMClient` 接口，切换 provider 只需修改配置
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
export NVIDIA_API_KEY=sk-...

# 配置 API Key win
$env:ANTHROPIC_API_KEY=sk-...
$env:NVIDIA_API_KEY=sk-...

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

### Agent 核心 (`src/mini_agent/`)

- `agent.py` — Agent 主类，对话循环与编排
- `context_builder.py` — System prompt 构建（skill/memory/project 注入）
- `tool_executor.py` — 工具执行（权限检查 + 调用 + 截断 + 缓存）
- `history_manager.py` — 历史管理（追加 + 压缩 + 快照恢复）
- `config/` — 配置管理包：`models.py`（14 个配置 dataclass + AppConfig）/ `loader.py`（`load_config` 及 providers.json 加载、llm_fallback_chain、退避策略参数）/ `prompt_builder.py`（`build_system_prompt`）；`__init__.py` 重导出，对外 import 路径不变
- `permissions.py` — 工具调用的权限守卫
- `session.py` — 会话管理

### 工具系统 (`src/mini_agent/tools/`)

- `__init__.py` — 工具注册表，`@tool` 装饰器
- `builtin.py` — 内置工具（读/写文件、bash、grep、glob 等）
- `orchestration.py` — 并发编排工具（spawn_agent, task 管理, `update_task_progress` 任务进度叙事写入）
- `skill_manager.py` — 技能管理工具（skill_list, skill_activate 等）
- `plan.py` — 规划工具
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
- `exploration_sandbox.py` — 探索实验沙盒（Stage 9 Phase 3）：包装 Stage 2 `EvolutionWorkspace` 加预算门控，`ExplorationReport` 结果写入 `activity_digest.jsonl`；`_tick_autonomous()` 对 capability 类软目标候选调用此沙盒做轻量验证，成功才写 GoalBacklog + 触发 `skill_propose`

### HTTP API (`src/mini_agent/api/`)

- `server.py` — FastAPI app 工厂 + AgentRunner 后台线程（Stage 9：内嵌 AutonomousLoop tick，`_build_autonomous_loop()`）+ 输出钩子；`app.state.http_server = self` 供 routes 查询 AutonomousLoop 状态
- `routes.py` — HTTP 路由定义（对话/SSE/事件/权限/文件系统/`GET /v1/diagnostics` 系统健康检查 Stage 6.2）；`/v1/status` Stage 9 新增 `autonomy_level`/`last_autonomous_tick_at`/`tick_count`/`subscribers` 字段
- `bridge.py` — 解耦桥梁（RingBuffer/OutputBroadcaster/InputQueue/PermissionGate）；`InputQueue.enqueue()` Stage 9 新增 `initiator`/`meta` 参数
- `models.py` — Pydantic 请求/响应模型 + AgentEvent；`TurnInfo` Stage 9 新增 `initiator`；`StatusResponse` Stage 9 新增 daemon 状态字段
- `auth.py` — Bearer Token 认证中间件（单用户模式）
- `multi_auth.py` — 多用户认证中间件（`MultiUserAuthMiddleware`）；与 `auth.py` 互斥，由 `create_app()` 按 `http_multi_user_enabled` 二选一挂载；认证成功后在 `request.state.user_ctx` 注入 `UserContext`
- `user_store.py` — 用户注册表（`UserStore`）与角色体系；五种角色（owner/family/colleague/agent/public）对应不同工具权限和资源配额；token 明文存 `.agent/users/tokens/*.key`（0600），hash 存 `users.json`；`RoleProfileManager` 管理每用户社交画像（`profile.json`）
- `session_pool.py` — 多用户 Session 池（`SessionAgentPool`）；每个 `(user_id, session_id)` 对应独立 Agent 实例和 AgentBridge；含 idle 超时自动挂起（默认 30 分钟）、崩溃恢复、最大并发限制（默认 20）；`SelfMessageBus` 实现 Self 与 SessionAgent 之间的内部消息
- `fs_helper.py` — 文件系统操作封装

### CLI (`src/mini_agent/cli/`)

- `app.py` — 应用启动装配（解析参数、初始化组件、启动 REPL）；含 `daemon` 子命令短路、`--daemon-mode` 持续驻留模式（Stage 9）
- `parser.py` — CLI 参数定义；含 `--daemon-mode` / `--no-daemon` 标志（Stage 9）
- `repl.py` — REPL 循环和斜杠命令处理；退出时自动打印 resume 提示（`_print_resume_hint()`）；含 `/agent` / `/goals` / `/digest` 路由（Stage 9）
- `daemon.py` — 守护进程管理：`cmd_daemon_start/stop/status`、PID 文件管理（`.agent/daemon.pid` + `.agent/daemon_info.json`）、`DaemonClient`（HTTP 连接模式 CLI）、`run_connected_repl`（Stage 9）
- `commands/` — REPL 命令处理器（concurrency, plans, sessions, skills, tasks, agents, hooks, providers, evolution, evolve, eval_cmd）
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
- `compression.py` — 历史压缩算法（turn_aligned / sliding_window / llm_summary / **selective**）
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

### Workflow

- 工作流编排机制，支持多步骤自动化任务执行
- 参见 [Workflow 指南](docs/workflow-guide.md)

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

- **受保护路径清单**：`scripts/protected_paths.py`，独立于 `src/mini_agent/` 包外，不 import 任何 mini_agent 模块；`is_protected_path(path)` 命中即强制判定为 T3 风险等级。当前覆盖 `agent.py`/`permissions.py`/`hooks/`/清单自身，并为 Stage 2 的 `evolution/` 包预留正则规则
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
- **维护机制**：SessionStart 时 `agent.py` 的 `_maybe_ensure_project_meta()` 创建/更新 `project.json`（含 12.2 横向加固 `environment_fingerprint` 漂移检测）；SessionEnd 时 `_update_workdir_knowledge_on_session_end()` 追加 `timeline.jsonl`、关联 `work_index.json`、回收 `task_manifest.outcome.unresolved` 进 `open_threads.json`
- **context 注入**：`context_builder.py` always-on 注入 `project.json` 身份信息 + 活跃 WorkThread 进度 + 高优先级 open_threads（数量上限 `WorkdirKnowledgeConfig.open_threads_inject_limit`，默认 5）。`knowledge.md` **不**走 always-on——按设计文档 8.4 节"按意图检索注入"，agent 需要主动调用 `search_knowledge` 工具
- **横向加固顺带完成**：14.1 `knowledge_index.json`（`update_knowledge()` 写入时同步生成结构化索引）+ 检索侧补全（`search_knowledge` 工具：此前索引建了但从未被读出来用过，TF-IDF 关键词检索，复用 `memory_store.py` 的中英混合分词器）
- **配置**：`WorkdirKnowledgeConfig`（默认 `enabled=True`），`work_thread_relation_days`（默认 7 天，关联启发式窗口）
- 详见 [Workdir 知识层与 Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md)

### Global 知识层（Stage 5 / Phase W3）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 5，设计依据 `next_doc/self_evolution_design.md` 第 8.3 节。`~/.agent/` 下新增四个文件，scope 从单项目升级为跨项目

- **四个文件**：`self_profile.json`（agent 自我模型，`AgentSelfProfile` 落地版，`autonomy_level` 默认 `"passive"`）、`projects_index.json`（workdir 注册表，30 天无活动自动标记 `dormant`）、`cross_project_index.json`（跨项目模式聚合，`observed_in_projects`/`confidence`/`global_skill_candidate`）、`activity_log.jsonl`（全局活动时序，与 `timeline.jsonl` 同一处代码路径写入）
- **核心模块**：`perception/global_knowledge.py`（数据模型 + 读写 + `scan_cross_project_patterns()`/`merge_cross_project_patterns()` 跨项目聚合）
- **维护机制**：session 启动时 `_maybe_register_global_project()` 注册/更新 `projects_index.json` 并做 dormant 巡检；SessionEnd 时复用 Stage 4 的 theme/duration 计算结果追加 `activity_log.jsonl` 并更新 `self_profile.json`
- **本 Stage 范围**：5.4 节"跨项目模式扫描"只实现扫描聚合函数本身（`scan_cross_project_patterns`），**不接调度自动触发**——触发时机（"该不该自动晋升"）留给 Stage 8 Phase G
- **context 注入**：`_build_global_knowledge_block()` always-on 注入 `self_assessment` 精简版 + `pending_evolve_branches`；workdir 变化时注入 `projects_index`/`activity_log` 最近若干条（`GlobalKnowledgeConfig.activity_log_inject_limit`，默认 5）
- **配置**：`GlobalKnowledgeConfig`（默认 `enabled=True`），`dormant_after_days`（默认 30）
- 详见 [Workdir 知识层与 Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md)

### 观察性（Stage 6 / 第 9 章）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 6，设计依据 `next_doc/self_evolution_design.md` 第 9/10/11 章。是 Phase G 剪枝判断和异常检测的数据基础

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

### Phase G 后台循环（Stage 8）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 8，设计依据 `next_doc/self_evolution_design.md` 第 6.4/6.5/6.6/6.7 节。不依赖常驻进程的"时间门控"调度，是 Phase H 自主运行时的前置数据沉淀

- **8.1 调度骨架**：`phase_g_rhythm.json` 的 `_last_run_at` 字段替代 cron，`should_run_phase_g()` 在每次 `trigger_session_end()` 时检查（默认 24h 间隔）；手动触发入口 `/evolve phase-g [--force] [--dry-run]`
- **8.2 剪枝候选**：`prune_skills()`，规则 A（高 token 成本 + 未使用）+ 规则 B（冲突检测），输出 `PruneCandidate` 列表，不自动执行下线
- **8.3 能力地图**：`build_capability_map()` 扫描 `tasks/*/manifest.json`，按 `_infer_domain()` 规则式推断任务类型，聚合成功率，写入 `entry_type="capability_map"` 的 memory 条目——终于激活了早期设计就预留的这个枚举值。Global scope 汇总留待数据积累后扩展
- **8.4 Scope 晋升**：`check_scope_promotion()` 读 `cross_project_index.json`，判据 `observed_in_projects ≥ 2` 且 `confidence ≥ 0.70` 且 `global_skill_candidate=true`，当前只输出候选列表（`PromotionCandidate`），不直接调用 `skill_propose`
- **8.5 节奏治理**：`rhythm_is_allowed()`/`record_proposal()`，7 天冷却期，可对任意 `(proposal_type, key)` 限流——回应设计文档开放问题 1（T1 自动合并的观察期）
- **核心模块**：`evolution/phase_g.py`（`run_phase_g()` 整体入口）+ `cli/commands/evolve.py`（`_handle_phase_g()`）+ `agent.py`（`_maybe_run_phase_g()`，SessionEnd 时间门控接入点）
- 详见 [Phase G 后台循环指南（Stage 8）](docs/self-evolution-phase-g-guide.md)

### 自主运行时（Stage 9 / Phase H）

> 对应 `next_doc/self_evolution_stage9_plan.md`。在全部 Stage 0-8 基础设施之上引入常驻守护进程、跨会话目标层级和三档位自主调度

- **进程模型升级**：daemon 进程常驻（`cli/daemon.py`），AgentRunner 线程内嵌 `AutonomousLoop` tick 调度；CLI 进入"连接模式"（通过现有 HTTP API 接入，不新增协议）；`--no-daemon` 可回退到传统模式
- **守护进程管理**（`cli/daemon.py`）：`mini-agent daemon start [--detach]|stop|status`；PID 文件写入 `.agent/daemon.pid` + `.agent/daemon_info.json`
- **Goal Backlog**（`perception/goal_backlog.py`）：`GoalNode`（Goal/Objective 统一节点），持久化到 `.agent/goals.json`；Objective 可通过 `work_thread_ref` 关联已有 WorkThread（复用 Stage 4 进展文本）；`has_actionable_work()` / `active_objectives()` 是 ObjectiveExecutor 调用的核心接口
- **AutonomousLoop**（`evolution/autonomous_loop.py`）：三档位（passive/maintenance/autonomous），边界用方法边界物理隔离；`passive` 档位调用 `CronScheduler.tick()`；`maintenance` 档位起调用 `ObjectiveExecutor`；`autonomous` 档位加入 `SoftGoalDeriver` + `ExplorationSandbox`
- **CronScheduler**（`evolution/cron_scheduler.py`）：interval/cron 双格式，5 个内置系统 job（phase_g/workdir_sync/self_eval/goal_review/digest_trim）；触发的 job 通过 `InputQueue.enqueue(initiator="cron")` 提交
- **ObjectiveExecutor**（`evolution/objective_executor.py`）：Objective 拆解为 3-8 个 Step，每步完成后 `on_turn_done()` 回调推进；SSE 推送 `OBJECTIVE_PROGRESS` 事件；同时最多 2 个 Objective 并行，单步最多重试 2 次
- **SoftGoalDeriver**（`evolution/soft_goal_deriver.py`）：三路信号（capability_map 低置信度 / WorkThread 积压 / 高频 Lesson）；`derive_candidates()` 分 capability 类和其他类；capability 类经 ExplorationSandbox 验证后才写 GoalBacklog
- **资源仲裁**（`evolution/resource_arbiter.py`）：用户优先 / 路径冲突检测 / 预算硬限制三条规则；`activity_digest.jsonl` 自主行为粗粒度日志；`build_digest_summary()` 六分组渲染（Objective进展/Cron执行/探索结果/Agent建议/进化提案/其他）
- **initiator 字段贯穿**：`_TurnCommand`/`enqueue()`/`TurnInfo`/`StateRepo.apply()` 均加入 `initiator` 参数（`"user"`/`"autonomous"`/`"cron"`）；自主发起的 T0 改动自动上浮为 T1
- **新 API 端点**：`/v1/autonomous/status`、`/v1/goals` CRUD、`/v1/cron/jobs` CRUD，共 8 个新端点
- **CLI 命令**：`/goals`（含 accept/reject）、`/cron`（含所有子命令）、`/digest`（六分组摘要）、`mini-agent daemon start|stop|status`
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
- **C3 认知锚点文件**：`agent.py::_save_cognitive_anchor()`/
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
- [Task 日志实时查看](docs/task-focus-viewing.md) — 方向键切换查看任务日志机制
- [终端显示机制深度解析](docs/terminal-display-internals.md) — 线程模型、状态栏控制、三阶段状态机、token 过滤
- [终端 I/O 指南](docs/terminal-io-guide.md) — 终端渲染与输入机制
- [任务与规划指南](docs/plan-and-task-guide.md) — 执行计划与并发任务，含 `plan_snapshot.json` 持久化与恢复
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
- [Phase G 后台循环指南（Stage 8）](docs/self-evolution-phase-g-guide.md) — 剪枝候选 / 能力地图 / Scope 晋升 / 演化节奏治理
- [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md) — 常驻守护进程 / Goal Backlog / 三档位 AutonomousLoop / 资源仲裁
- [具身智能改进指南](docs/embodied-agent-guide.md) — 本体感知 / 余裕感知 / 工具透明性 / AgentSelfModel / 时间加权记忆 / 认知锚点 / 自维护模块（A/B/C 三阶段共 12 项）

## 当前进展

- Stage 0-8 均已完成（详见 `next_doc/self_evolution_implementation_plan.md` 与 `next_doc/self_evolution_stage4plus_plan.md` 各 Stage 完成记录）
- Stage 9（Phase H：自主运行时）是决策点而非常规排期 Stage，启动前置清单见 `next_doc/self_evolution_stage4plus_plan.md` 第 9.0 节；细化方案见 `next_doc/self_evolution_stage9_plan.md`
- 具身智能改进（`next_doc/embodied_agent_improvement_plan_v3.md`）A/B/C 三阶段共 12 项均已完成，详见 [具身智能改进指南](docs/embodied-agent-guide.md)；已知遗留缺口：AffordanceMap（B4）与认知锚点（C3）仅在部分路径生效（分别是"仅多用户 daemon"和"本地 CLI，daemon connected REPL 未接入"），详见改进计划文档对应小节