# mini-agent

> 一个用 Python 实现的简化版 Claude Code，支持多 LLM 提供商、Skill 机制、并发 Sub-Agent 编排和完整的工具调用体系。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 特性

| 特性 | 说明 |
|------|------|
| 🤖 多 LLM 支持 | Anthropic、OpenAI、Ollama、NVIDIA NIM，一行配置切换 |
| 🔧 系统工具调用 | 工具通过 System Prompt 注入，兼容所有模型 |
| 📚 Skill 机制 | SKILL.md 文件动态加载，自动触发注入上下文 |
| 📝 Prompt 管理 | 所有 prompt 统一在 `prompts/` 目录管理 |
| ⚡ 并发 Sub-Agent | 主 Agent 可派生多个子 Agent 并行执行任务，支持自动重试（HTTP 5xx/超时最多 3 次） |
| 🔐 权限守卫 | 危险操作需要确认，支持白名单、黑名单、沙箱模式，路径规范化处理 |
| 🐛 调试日志 | 完整记录每次请求/响应到 JSONL 文件 |
| 🧠 感知与记忆 | 项目扫描、文件监视、工具缓存、跨 session 长期记忆 |
| 🌐 HTTP API | 内置 REST/SSE 服务，支持外部程序通过 HTTP 与 agent 交互 |
| 🖥️ Web Demo | Streamlit 图形界面，提供浏览器操作的对话界面 |
| 🔌 MCP 支持 | Model Context Protocol 集成，支持 stdio/SSE 传输，可扩展外部工具服务 |
| 🔍 Web Search | 支持 DuckDuckGo（默认）、Brave、Serper、Tavily 等多种搜索后端 |
| 🤖 自定义子 Agent | 预设角色模板（.agent/agents/*.md），结构化参数注入，支持工具/模型限制 |
| 🔗 Hooks 机制 | 关键事件自动执行 shell 命令，支持拦截/修改工具调用，项目级/全局级配置 |
| 🎯 Task 日志实时查看 | 运行时方向键切换查看不同任务日志，状态栏显示任务状态概要 |
| 🖼️ 图片技能 | 图片信息提取与问答（ask_image）、文本生成图片（gen_image_with_text） |
| 📝 Reminder 系统 | 动态提示注入机制，工具出错/用户意图等情境下自动追加解决经验，同轮去重防重复 |
| 🤖 Role Agent | 预设角色子 Agent 模板，结构化参数注入，支持工具/模型限制 |
| 🔄 Workflow | 工作流编排机制，支持多步骤自动化任务执行 |
| 🌍 Env Info | 环境信息自动采集与注入，内置 OS/Python/时区 Provider，支持自定义扩展 |
| 💾 History 即时落盘 | RawHistory 采用 JSONL 追加写 + fsync，每次操作立即持久化，防崩溃丢失 |
| 🎯 Selective 压缩 | 按 _type 差异化权重评分保留，优先保留用户意图和回复，智能截断工具噪音 |
| 🔁 Resume 提示 | 退出 REPL 时自动显示 resume 命令，方便恢复上次会话 |

## 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/onewaymyway/mini_agent.git
cd mini_agent

# 安装依赖
pip install -r requirements.txt
```

### 配置 API Key

```bash
# 配置 API Key linux
export ANTHROPIC_API_KEY=sk-...
export NVIDIA_API_KEY=sk-...
export BRAVE_API_KEY=...  # 可选：使用 Brave Search
export SERPER_API_KEY=...  # 可选：使用 Serper
export TAVILY_API_KEY=...  # 可选：使用 Tavily

# 配置 API Key win
$env:ANTHROPIC_API_KEY=sk-...
$env:NVIDIA_API_KEY=sk-...
$env:BRAVE_API_KEY=...
$env:SERPER_API_KEY=...
$env:TAVILY_API_KEY=...
```

### 运行

```bash
# 交互式模式（推荐）
python -m mini_agent

# 或使用传统入口
python main.py

# 单次命令模式
python -m mini_agent "写一个质数筛法的 Python 脚本"

# 使用指定模型
python -m mini_agent --model claude-haiku-4-5

# 沙箱模式（安全测试）
python -m mini_agent --sandbox

# 更多参数
python main.py --provider nvidia --model qwen/qwen3.5-122b-a10b --system-tool-call --system-msg-format system_role
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--model`, `-m` | 指定使用的模型 |
| `--provider` | LLM 提供商：`anthropic`\|`openai`\|`ollama`\|`nvidia` |
| `--base-url` | 自定义 API 端点 |
| `--agent-name` | Agent 显示名称（默认：orzooo） |
| `--sandbox` | 沙箱模式 |
| `--yes`, `-y` | 自动批准所有工具调用 |
| `--debug-llm` | 启用调试日志 |
| `--max-llm-calls` | 最大并发 LLM 调用数（默认 8） |
| `--workers` | 最大并发子 Agent 数（默认 4） |
| `--session-dir` | Session 文件保存目录 |
| `--resume` | 恢复之前的对话 |
| `--system-tool-call` | 启用系统工具调用格式 |
| `--memory` | 启用跨 session 记忆 |
| `--project-scan` | 启动时扫描项目结构 |
| `--file-watch` | 监听文件变化 |
| `--web-search-provider` | 指定搜索后端：`duckduckgo`\|`brave`\|`serper`\|`tavily` |
| `--http` | 启动内置 HTTP API 服务 |
| `--http-port` | HTTP 服务监听端口（默认 8765） |
| `--http-host` | HTTP 服务监听地址（默认 127.0.0.1） |
| `--http-token` | HTTP API 认证令牌 |
| `--http-allow-ip` | 允许的 IP 地址列表 |
| `--http-fs-readonly` | 文件系统只读模式 |
| `--http-ring-maxlen` | 事件环缓冲区大小 |
| `--reminders-dir` | 指定用户自定义 reminder 目录 |
| `--no-reminders` | 禁用 reminder 系统 |
| `--reminder-verbose` | 启用 reminder 调试日志 |

## MCP 集成

mini-agent 支持 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)，允许通过标准化协议连接外部工具服务。

### 快速开始

1. 安装 MCP SDK：

```bash
pip install mcp
```

2. 配置 `agent_config.json`（已预配置 time_server）：

```json
{
  "mcp_servers": [
    {
      "name": "time_server",
      "transport": "stdio",
      "command": "python",
      "args": ["mcp_servers/time_server.py"],
      "auto_approve": true
    }
  ]
}
```

3. 启动 Agent，会自动连接 MCP server：

```bash
python -m mini_agent
```

启动后会显示：
```
[mcp] Connected: 'time_server' (3 tools: get_current_time, calculate, echo)
```

### 可用 MCP 工具

| 工具 | 描述 |
|------|------|
| `get_current_time(timezone?)` | 获取当前时间，支持 IANA 时区（如 Asia/Shanghai） |
| `calculate(expression)` | 安全计算数学表达式（四则运算、sqrt、log、sin 等） |
| `echo(message)` | 原样返回消息，用于测试连通性 |

### 配置选项

| 字段 | 说明 |
|------|------|
| `name` | Server 唯一标识 |
| `transport` | `"stdio"` 或 `"sse"` |
| `command`/`args` | stdio 模式的命令和参数 |
| `url` | SSE 模式的 endpoint |
| `auto_approve` | 是否免审批 |
| `timeout` | 连接与调用超时（秒） |

详细文档参见 [MCP 使用指南](docs/mcp-guide.md)。

## Web Demo

使用 Streamlit 启动浏览器交互界面：

```bash
# 安装依赖
pip install streamlit requests

# 启动 HTTP 服务（先启动 mini-agent）
python -m mini_agent --http --http-port 8765

# 启动 Web Demo
streamlit run apps/mini_agent_webdemo/app.py
```

Web Demo 提供：
- 📱 浏览器对话界面
- 🔌 HTTP API 连接配置（支持 Token 认证）
- 🔐 权限审批交互式面板（内联实时渲染）
- 📡 实时事件流监控（SSE）
- 📁 文件系统浏览
- 📊 Turn 历史记录
- ⌨️ 快捷审批指令（y/a/n/d 直接输入）
- ✏️ Bash 命令在线编辑

**注意**：需要先启动 HTTP 服务，Web Demo 才能连接到 Agent。

## REPL 命令

进入交互式模式后，支持以下斜杠命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清除对话历史 |
| `/stats` | 显示会话统计 |
| `/skills` | 列出所有技能 |
| `/skill on\|off <name>` | 激活/停用技能 |
| `/agents` | 列出自定义子 agent profiles |
| `/agents show <name>` | 显示 profile 详细信息 |
| `/agents reload` | 重新扫描子 agent profiles |
| `/hooks` | 列出已加载的 hooks |
| `/hooks reload` | 重新加载 hooks 配置 |
| `/model <name>` | 切换模型 |
| `/provider list\|switch <name>` | 列出/切换提供者 |
| `/session list\|save\|load` | 管理会话 |
| `/tasks` | 显示子任务状态 |
| `/tasks focus <id>` | 进入指定任务焦点模式（实时查看日志） |
| `/tasks unfocus` | 退出任务焦点模式 |
| `/tasks dashboard` | 实时任务看板 |
| `/tasks log <id>` | 查看任务日志 |
| `/tasks cancel <id>` | 取消任务 |
| `/concurrency` | 查看并发状态 |
| `/compact` | 压缩对话历史 |
| `/prompts` | 列出所有提示词文件 |
| `/retry` | 重试上一轮 |
| `/rollback` | 回退上一轮 |

### 键盘快捷键（Task 日志查看）

运行 Task 时支持以下快捷键实时切换查看任务日志：

| 按键 | 功能 |
|------|------|
| `→` 或 `↓` | 进入/切换到下一个任务日志视图 |
| `←` 或 `↑` | 切换到上一个任务日志视图 |
| `ESC` | 退出任务焦点模式 |

详见 [Task 日志实时查看指南](docs/task-focus-viewing.md)。

### 命令行输入补全

安装 `prompt_toolkit` 后（自动安装在 `requirements.txt` 中），REPL 提供以下输入补全功能：

- **Slash 命令补全**：输入 `/` 触发所有命令列表，显示命令描述和子命令提示
- **子命令补全**：如输入 `/skill ` 后弹出 `on` / `off` / `list` 子命令
- **文件路径补全**：输入 `@src/` 触发文件路径补全
- **模糊匹配**：输入 `/sess` 即可匹配 `/session` 命令
- **历史建议**：灰色虚影显示历史命令，按 `→` 接受建议
- **Tab / Shift-Tab**：在候选项间上下移动
- **暗色主题**：Catppuccin Mocha 风格补全菜单

## 内置工具

Agent 可以调用以下内置工具：

### 文件操作
- `read_file` — 读取文件内容
- `write_file` — 写入文件
- `create_file` — 创建新文件
- `delete_file` — 删除文件
- `patch_file` — 补丁式编辑文件
- `list_dir` — 列出目录内容
- `glob` — 文件模式匹配
- `grep` — 正则搜索

### Shell 命令
- `bash` — 执行 Shell 命令

### Web Search
- `web_search` — 网络搜索，支持 DuckDuckGo（默认）、Brave、Serper、Tavily 后端

### 图片处理
- `ask_image` — 图片信息提取与问答（**不要**用 read_file 直接读图片）
- `gen_image_with_text` — 文本生成图片（text-to-image / image-to-image 编辑）

### 并发编排
- `spawn_agent` — 派生子 Agent
- `spawn_agents` — 批量派生子 Agent
- `get_task_status` — 查询任务状态
- `list_tasks` — 列出所有任务
- `wait_for_tasks` — 等待任务完成
- `cancel_task` — 取消任务

### 规划与记忆
- `plan` — 创建/更新规划
- `compact_history` — 压缩历史并重附技能上下文
- `skill_list` — 列出所有技能
- `skill_activate` — 激活技能
- `skill_deactivate` — 停用技能
- `ask_user` — 询问用户输入
- `list_agent_profiles` — 列出所有自定义子 agent profiles
- `spawn_named_agent` — 派生预设角色的子 agent

## 项目结构

```
mini_agent/
├── main.py                  # 传统入口（兼容 shim）
├── pyproject.toml           # 项目元数据
├── requirements.txt         # 依赖列表
├── README.md                # 项目说明
├── CLAUDE.md                # 开发规范
├── src/                     # 源代码
│   └── mini_agent/
│       ├── __init__.py
│       ├── __main__.py      # 模块入口
│       ├── agent.py         # Agent 主类（对话循环与编排）
│       ├── context_builder.py  # System prompt 构建
│       ├── tool_executor.py    # 工具执行（权限 + 调用 + 截断 + 缓存）
│       ├── history_manager.py  # 历史管理（压缩/快照）
│       ├── config.py        # 配置管理
│       ├── permissions.py   # 权限守卫
│       ├── session.py       # 会话管理
│       ├── skills/          # 技能加载
│       │   ├── __init__.py
│       │   ├── tracker.py   # 技能使用追踪
│       │   └── usage_detector.py  # 使用检测
│       ├── cli/             # CLI 基础设施
│       │   ├── __init__.py
│       │   ├── app.py       # 应用启动入口
│       │   ├── parser.py    # 参数解析
│       │   ├── repl.py      # REPL 循环
│       │   └── commands/    # REPL 命令处理
│       │       ├── __init__.py
│       │       ├── concurrency.py
│       │       ├── plans.py
│       │       ├── providers.py
│       │       ├── sessions.py
│       │       ├── skills.py
│       │       ├── tasks.py
│       │       ├── agents.py
│       │       └── hooks.py
│       ├── llm/             # LLM 抽象层
│       │   ├── __init__.py
│       │   ├── base.py      # 基础接口
│       │   ├── factory.py   # 工厂模式
│       │   ├── retry.py     # 重试策略
│       │   ├── system_tool_call.py  # 工具调用格式
│       │   ├── debug_logger.py  # 调试日志
│       │   └── providers/   # LLM 提供商实现
│       │       ├── __init__.py
│       │       ├── _base_mixin.py
│       │       ├── anthropic.py
│       │       ├── openai.py
│       │       ├── ollama.py
│       │       └── nvidia.py
│       ├── tools/           # 工具系统
│       │   ├── __init__.py  # 工具注册表
│       │   ├── builtin.py   # 内置工具
│       │   ├── orchestration.py  # 并发编排工具
│       │   ├── skill_manager.py  # 技能管理
│       │   ├── plan.py      # 规划工具
│       │   └── user_input.py  # 用户输入工具
│       ├── orchestrator/    # 并发编排
│       │   ├── __init__.py
│       │   ├── task.py      # 任务定义
│       │   ├── task_manager.py  # 任务调度
│       │   ├── sub_agent.py # 子 Agent
│       │   ├── concurrency.py  # 并发控制
│       │   ├── status_bar.py  # 状态栏显示
│       │   ├── plan.py      # 执行计划
│       │   ├── plan_display.py  # 计划 UI
│       │   ├── task_display.py  # 任务显示
│       │   └── agent_profiles.py  # 自定义 agent profile
│       ├── perception/      # 感知与记忆
│       │   ├── __init__.py
│       │   ├── project_scanner.py  # 项目结构扫描
│       │   ├── file_watcher.py     # 文件变化监听
│       │   ├── tool_cache.py       # 工具结果缓存
│       │   ├── memory_store.py     # 跨 session 记忆
│       │   ├── memory_base.py      # 记忆后端抽象
│       │   ├── memory_factory.py   # 记忆工厂
│       │   └── token_counter.py    # Token 预估
│       ├── ui/              # 用户界面
│       │   ├── __init__.py
│       │   ├── terminal.py  # 终端 I/O
│       │   ├── renderer.py  # 终端输出渲染
│       │   └── repl_input.py  # REPL 输入
│       ├── api/             # HTTP API 服务
│       │   ├── __init__.py
│       │   ├── server.py    # HTTP 服务封装
│       │   ├── routes.py    # API 路由
│       │   ├── bridge.py    # Agent 桥梁
│       │   ├── auth.py      # 认证中间件
│       │   ├── models.py    # 数据模型
│       │   └── fs_helper.py # 文件系统助手
│       ├── history/         # 历史管理
│       │   ├── __init__.py
│       │   ├── compression.py  # 压缩算法（turn_aligned/sliding_window/llm_summary/selective）
│       │   ├── raw_history.py  # Raw history（JSONL 即时落盘）
│       │   └── entry.py        # 历史条目类型与辅助函数
│       ├── prompts/         # Prompt 管理
│       │   ├── __init__.py
│       │   ├── manager.py   # PromptManager
│       │   ├── system/      # 系统提示词
│       │   ├── fragments/   # 文本片段
│       │   └── user/        # 用户消息
│       ├── hooks/           # hooks 机制
│       │   ├── __init__.py
│       │   ├── loader.py    # HookManager
│       │   └── runner.py    # HookResult
│       ├── mcp/             # MCP 支持
│       │   ├── __init__.py
│       │   ├── config.py    # 配置
│       │   ├── transport.py # 传输层
│       │   └── manager.py   # MCPManager
│       ├── env_info/        # 环境信息采集
│       │   ├── __init__.py
│       │   ├── base.py       # EnvInfoProvider 抽象基类
│       │   ├── registry.py   # 注册/采集/格式化
│       │   └── providers/    # 内置 Provider
│       │       ├── system.py
│       │       ├── runtime.py
│       │       └── locale.py
│       └── storage/         # 存储层
│           ├── __init__.py
│           └── paths.py     # 路径管理
├── apps/                    # Web 应用
│   └── mini_agent_webdemo/ # Streamlit Web Demo
│       └── app.py
├── prompts/                 # 提示词模板（外部）
├── skills/                  # 技能定义（外部）
├── tests/                   # 单元测试
├── docs/                    # 文档
├── sessions/                # 会话历史（生成）
├── mcp_servers/             # MCP 服务器示例
├── .agent/                  # 自定义子 agent profiles
│   └── agents/              # profile 文件 (*.md)
└── hooks/                   # hooks 示例脚本
```

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    CLI / REPL                          │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                  Agent (agent.py)                       │
│     对话循环 · 工具派发 · 流式输出                      │
├─────────────────────────────────────────────────────────┤
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────┐│
│  │ContextBuilder │  │ ToolExecutor  │  │HistoryMgr   ││
│  │ (System Prompt)│  │ (权限 + 调用)  │  │(历史/压缩)  ││
│  └───────────────┘  └───────────────┘  └─────────────┘┘
└───────┬─────────────────┬──────────────────┬──────────┘
        │                 │                  │
┌───────▼────────┐ ┌──────▼────────┐ ┌──────▼──────────┐
│   LLM Client   │ │  Tool Registry│ │  Perception     │
│  (多 Provider)   │ │  (内置/技能)  │ │  (缓存/记忆)    │
└────────────────┘ └───────────────┘ └─────────────────┘
```

## 扩展开发

### 添加新工具

```python
from mini_agent.tools import tool

@tool(
    name="my_tool",
    description="执行某个操作",
    schema={
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "参数说明"}
        },
        "required": ["param"]
    },
    requires_approval=False
)
def my_tool(param: str) -> str:
    return f"结果：{param}"
```

在 `src/mini_agent/cli/app.py` 中导入模块注册工具。

### 添加新 Skill

在 `.claude/skills/<skill-name>/SKILL.md` 创建技能文件：

```markdown
---
name: my-skill
description: 我的扩展功能
triggers: keyword1, keyword2
---

技能的具体使用说明...
```

### 添加新 LLM Provider

参见 `docs/README.md` 的 "扩展" 章节。

## 配置

在项目根目录创建 `agent_config.json` 进行自定义配置：

```json
{
  "model": "claude-sonnet-4-6",
  "llm_provider": "anthropic",
  "max_tokens": 8096,
  "max_turns": 20,
  "verbose": true,
  "sandbox": false,
  "auto_approve": false,
  "memory_enabled": true,
  "project_scan_enabled": true,
  "tool_cache_enabled": true,
  "compress": {
    "enabled": true,
    "threshold": 0.7,
    "strategy": "selective",
    "selective_weights": {"tool_result": 0.3, "reminder": 0.1},
    "selective_min_user_turns": 3
  }
}
```

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```

详见 [单元测试指南](docs/unit-testing-guide.md)。

## 文档

- [系统概览](docs/system-overview.md) — 整体架构和设计思路
- [Task 日志实时查看](docs/task-focus-viewing.md) — **新增**：方向键切换查看任务日志机制
- [权限系统指南](docs/permission-guide.md) — 权限守卫、白名单、持久化配置
- [Agent 设计](docs/agent-design.md) — Agent 核心循环与组件详解
- [CLI I/O 机制](docs/cli-io-mechanism.md) — 命令行输入输出流程，HTTP 与命令行协同
- [终端显示机制深度解析](docs/terminal-display-internals.md) — **新增**：线程模型、状态栏控制、三阶段状态机、token 过滤
- [终端 I/O 指南](docs/terminal-io-guide.md) — 终端交互细节
- [命令与工具参考](docs/commands-and-tools-reference.md) — 所有命令和工具
- [Plan 和 Task 指南](docs/plan-and-task-guide.md) — 规划和任务系统
- [SubAgent 机制](docs/subagent-mechanism.md) — Sub-Agent 执行与重试机制详解
- [自定义子 Agent](docs/custom-sub-agents.md) — 预设角色模板，结构化参数注入
- [Hooks 机制](docs/hooks.md) — 关键事件自动执行命令，支持拦截/修改工具调用
- [Skill 系统指南](docs/skill-system-guide.md) — 技能机制详解
- [代码结构指南](docs/code-structure-guide.md) — 项目结构说明
- [HTTP API 指南](docs/http-api-guide.md) — REST/SSE 服务使用指南
- [Web Demo 指南](docs/web-demo-guide.md) — Streamlit Web 界面使用
- [MCP 集成指南](docs/mcp-guide.md) — Model Context Protocol 集成
- [Web Search 指南](docs/web-search-guide.md) — Web 搜索功能使用指南
- [图片技能指南](docs/image-skills-guide.md) — 图片识别与生成技能使用指南
- [Reminder 系统指南](docs/reminder-system-guide.md) — 动态提示注入机制使用指南
- [单元测试指南](docs/unit-testing-guide.md) — 测试结构、编写规范与运行方式
- [Role Agent 指南](docs/role-agents-guide.md) — 预设角色子 Agent 模板
- [Workflow 指南](docs/workflow-guide.md) — 工作流编排机制
- [Env Info 指南](docs/env-info-guide.md) — 环境信息采集与注入，自定义 Provider 扩展

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

---

*最后更新：2026-06-18* — History 即时落盘（JSONL+fsync）、Selective 压缩策略、Reminder 去重守卫、Resume 退出提示
