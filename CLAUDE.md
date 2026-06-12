# mini-agent

一个用 Python 实现的简化版 Claude Code，支持技能机制。

## 项目结构

- `src/mini_agent/agent.py` — Agent 核心逻辑（对话循环、工具派发、流式输出）；拆分为三个独立组件：ContextBuilder（system prompt 构建）、ToolExecutor（权限检查 + 工具调用）、HistoryManager（历史管理）
- `src/mini_agent/config.py` — 配置管理和系统提示词构建
- `src/mini_agent/permissions.py` — 工具调用的权限守卫
- `src/mini_agent/session.py` — 会话管理
- `src/mini_agent/tools/__init__.py` — 工具注册表和 `@tool` 装饰器
- `src/mini_agent/tools/builtin.py` — 内置工具（bash、文件 I/O、搜索）
- `src/mini_agent/tools/orchestration.py` — 并发编排工具
- `src/mini_agent/tools/skill_manager.py` — 技能管理工具
- `src/mini_agent/mcp/` — MCP（Model Context Protocol）支持
- `src/mini_agent/skills/__init__.py` — 技能发现和加载
- `src/mini_agent/cli/app.py` — CLI 应用入口
- `src/mini_agent/cli/parser.py` — 参数解析
- `src/mini_agent/cli/repl.py` — REPL 交互循环
- `src/mini_agent/llm/` — LLM 抽象层
- `src/mini_agent/orchestrator/` — 并发编排
  - `sub_agent.py` — 子 Agent 实现
  - `agent_profiles.py` — 自定义子 agent profile 管理
- `src/mini_agent/hooks/` — hooks 机制（关键事件自动执行命令）
- `src/mini_agent/cli/commands/agents.py` — /agents 斜杠命令
- `src/mini_agent/cli/commands/hooks.py` — /hooks 斜杠命令
- `src/mini_agent/perception/` — 感知与记忆子系统
- `src/mini_agent/ui/renderer.py` — Rich 终端输出渲染

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
- 所有涉及调用大模型的prompt，必须保存到 src/mini_agent/prompts 目录下，然后通过 PromptManager 来获取

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

```

## 模块说明

### LLM 层 (`src/mini_agent/llm/`)

- `base.py` — LLM 客户端基础接口
- `factory.py` — Provider 工厂，根据配置创建对应客户端
- `retry.py` — 重试策略（空输出重试等）
- `system_tool_call.py` — 系统工具调用格式转换
- `providers/` — 各 LLM 提供商实现（anthropic, openai, ollama, nvidia）

### Agent 核心 (`src/mini_agent/`)

- `agent.py` — Agent 主类，对话循环与编排
- `context_builder.py` — System prompt 构建（skill/memory/project 注入）
- `tool_executor.py` — 工具执行（权限检查 + 调用 + 截断 + 缓存）
- `history_manager.py` — 历史管理（追加 + 压缩 + 快照恢复）
- `config.py` — 配置管理和系统提示词构建
- `permissions.py` — 工具调用的权限守卫
- `session.py` — 会话管理

### 工具系统 (`src/mini_agent/tools/`)

- `__init__.py` — 工具注册表，`@tool` 装饰器
- `builtin.py` — 内置工具（读/写文件、bash、grep、glob 等）
- `orchestration.py` — 并发编排工具（spawn_agent, task 管理）
- `skill_manager.py` — 技能管理工具（skill_list, skill_activate 等）

### MCP 支持 (`src/mini_agent/mcp/`)

- `__init__.py` — 公开接口导出
- `config.py` — MCPConfig / MCPServerConfig 数据类
- `transport.py` — BaseTransport / StdioTransport / SSETransport
- `manager.py` — MCPManager（连接、注册、调用路由）

### 并发编排 (`src/mini_agent/orchestrator/`)

- `task.py` — 任务定义
- `task_manager.py` — 任务调度（依赖解析、SubAgent 管理）
- `sub_agent.py` — 子 Agent 实现（线程包装、自动重试、输出捕获）
- `concurrency.py` — 并发控制
- `status_bar.py` — 状态栏显示
- `plan.py` — 执行计划数据模型
- `plan_display.py` — 计划 UI 渲染
- `agent_profiles.py` — 自定义子 agent profile（预设角色）

### 感知与记忆 (`src/mini_agent/perception/`)

- `project_scanner.py` — 项目结构扫描
- `file_watcher.py` — 文件变化监听
- `tool_cache.py` — 工具结果缓存
- `memory_store.py` — 跨 session 长期记忆
- `token_counter.py` — Token 预估

### HTTP API (`src/mini_agent/api/`)

- `server.py` — FastAPI app 工厂 + AgentRunner 后台线程 + 输出钩子
- `routes.py` — HTTP 路由定义（对话/SSE/事件/权限/文件系统）
- `bridge.py` — 解耦桥梁（RingBuffer/OutputBroadcaster/InputQueue/PermissionGate）
- `models.py` — Pydantic 请求/响应模型 + AgentEvent
- `auth.py` — Bearer Token 认证中间件
- `fs_helper.py` — 文件系统操作封装

### CLI (`src/mini_agent/cli/`)

- `app.py` — 应用启动装配（解析参数、初始化组件、启动 REPL）
- `parser.py` — CLI 参数定义
- `repl.py` — REPL 循环和斜杠命令处理
- `commands/` — REPL 命令处理器（concurrency, plans, sessions, skills, tasks, agents, hooks 等）

### hooks (`src/mini_agent/hooks/`)

- `__init__.py` — 公开接口导出
- `loader.py` — HookManager（加载、执行、动态注册）
- `runner.py` — HookResult 定义

### 终端交互 (`src/mini_agent/ui/`)

- `terminal.py` — 统一终端 I/O 管理器，支持命令行输入补全（slash 命令/文件路径/历史建议）
- `renderer.py` — Rich 终端输出渲染

### 自定义子 Agent

- profile 文件位置：`.agent/agents/*.md`（项目级）或 `~/.agent/agents/*.md`（全局级）
- 文件格式：YAML frontmatter（name/description/inputs/tools） + system prompt 模板
- 支持占位符：`{参数名}` 和 `{context}` 自动填充
- CLI 命令：`/agents list|show <name>|reload`
- 工具：`list_agent_profiles`、`spawn_named_agent`

### Hooks 机制

- 配置文件：`.agent/hooks.json`（项目级）或 `~/.agent/hooks.json`（全局级）
- 支持事件：`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`PreCompact`、`SessionStart`/`SessionEnd`
- Hook 可以通过 stdin 接收 JSON payload，通过 stdout 返回决策（allow/block/context/input）
- CLI 命令：`/hooks list|reload`
