# mini-agent 项目说明

## 项目概述

mini-agent 是一个用 Python 实现的命令行 Agent 框架，定位为"简化版 Claude Code"。它提供与 Claude 及多种 LLM 模型的交互接口，支持工具调用、权限管理、Skill 上下文注入、会话持久化和并发 Sub-Agent 编排等功能。

---

## 主要功能

- **交互式 REPL**：支持对话式交互，单条命令或持续会话均可
- **多 LLM 支持**：Anthropic、OpenAI、Ollama、NVIDIA 等多种提供商
- **工具系统**：内置 bash、文件读写、glob、grep、web_search 等工具，支持自定义扩展
- **Web Search**：支持 DuckDuckGo（默认）、Brave、Serper、Tavily 等多种搜索后端
- **Skill 系统**：可加载领域技能文档，支持自动关键词激活
- **权限管理**：工具调用前需要用户确认，支持沙箱模式
- **会话管理**：保存、加载、恢复对话历史，支持长期记忆（`MemoryStore` + TF-IDF 检索）
- **并发控制**：Sub-Agent 多任务并发执行，含信号量限制
- **记忆系统**：跨 session 长期记忆，支持可扩展后端（local/Chroma/Redis）

---

## 运行方式

```bash
# 安装依赖
pip install -r requirements.txt
# 或使用 pyproject.toml
pip install -e .

# 设置 API 密钥
export ANTHROPIC_API_KEY=sk-...
export BRAVE_API_KEY=...  # 可选：使用 Brave Search
export SERPER_API_KEY=...  # 可选：使用 Serper
export TAVILY_API_KEY=...  # 可选：使用 Tavily

# 交互式模式（推荐）
python -m mini_agent
# 或
python main.py

# 单条命令模式
python -m mini_agent "分析当前目录结构"

# 安装后可直接使用
mini-agent
mini-agent "分析当前目录结构"

# 常用参数
mini-agent --provider openai --model gpt-4o
mini-agent --sandbox          # 沙箱模式
mini-agent --debug-llm        # 启用 LLM 调试日志
mini-agent --resume <session-id>  # 恢复历史会话
mini-agent --web-search-provider brave  # 指定搜索后端
```

---

## 项目目录结构

```
mini_agent/
├── main.py                      # 兼容入口 shim（python main.py）
├── pyproject.toml               # 包配置、依赖、入口点
├── requirements.txt             # 依赖列表
├── CLAUDE.md                    # 项目开发文档
├── agent_config.json            # 本地配置文件（可选）
│
├── src/
│   └── mini_agent/              # 主包
│       ├── __init__.py
│       ├── __main__.py          # python -m mini_agent 入口
│       ├── agent.py             # Agent 核心循环
│       ├── config.py            # 配置加载（AppConfig）
│       ├── permissions.py       # 权限守卫
│       ├── session.py           # 会话持久化
│       │
│       ├── cli/                 # 命令行界面层
│       │   ├── app.py           # 启动装配，main() 入口
│       │   ├── parser.py        # argparse 参数定义
│       │   ├── repl.py          # REPL 循环 + slash 命令路由
│       │   └── commands/        # slash 命令实现
│       │       ├── skills.py    # /skills /skill
│       │       ├── sessions.py  # /session
│       │       ├── tasks.py     # /tasks
│       │       ├── plans.py     # /plan
│       │       ├── concurrency.py  # /concurrency
│       │       └── providers.py    # /provider
│       │
│       ├── ui/                  # 终端 UI 层
│       │   ├── terminal.py      # 唯一写屏幕的地方，渲染队列
│       │   ├── renderer.py      # 历史 API 适配层
│       │   └── repl_input.py    # prompt_toolkit 输入封装
│       │
│       ├── llm/                 # LLM 抽象层
│       │   ├── base.py          # LLMClient 基类、数据结构
│       │   ├── factory.py       # Provider 工厂与注册表
│       │   ├── debug_logger.py  # LLM 请求/响应调试日志
│       │   ├── system_tool_call.py  # System prompt 工具调用协议
│       │   └── providers/       # Provider 实现
│       │       ├── anthropic.py
│       │       ├── openai.py
│       │       ├── ollama.py
│       │       └── nvidia.py
│       │
│       ├── tools/               # 工具系统
│       │   ├── __init__.py      # @tool 装饰器、ToolRegistry
│       │   ├── builtin.py       # 内置工具（bash、文件、搜索）
│       │   ├── orchestration.py # 编排工具（spawn_agent 等）
│       │   ├── plan.py          # 计划工具（create_plan 等）
│       │   ├── skill_manager.py # Skill 管理工具
│       │   └── user_input.py    # 用户交互工具（ask_user 等）
│       │
│       ├── orchestrator/        # 并发编排系统
│       │   ├── task.py          # 任务数据模型
│       │   ├── task_manager.py  # 任务调度器（依赖解析、SubAgent 管理）
│       │   ├── task_display.py  # 任务 UI（表格/看板）
│       │   ├── sub_agent.py     # Sub-Agent 线程包装器（重试机制、输出捕获）
│       │   ├── concurrency.py   # 信号量并发控制
│       │   ├── plan.py          # 执行计划数据模型
│       │   ├── plan_display.py  # 计划 UI（树形/摘要）
│       │   └── status_bar.py    # 终端状态栏
│       │
│       ├── prompts/             # Prompt 管理
│       │   ├── manager.py       # PromptManager
│       │   ├── system/          # 系统 prompt 片段（.md）
│       │   ├── fragments/       # 文本片段（cli_messages.md 等）
│       │   └── user/            # 用户 prompt 模板
│       │
│       ├── skills/              # Skill 系统
│       │   ├── __init__.py      # SkillLoader、Skill 数据类
│       │   ├── tracker.py       # LRU 使用追踪
│       │   └── usage_detector.py  # 双轨使用检测
│       │
│       └── perception/          # 感知与记忆系统
│           ├── project_scanner.py   # 项目结构扫描
│           ├── file_watcher.py      # 文件变化监听
│           ├── tool_cache.py        # 工具结果缓存
│           ├── token_counter.py     # Token 预估
│           ├── memory_base.py       # 记忆后端抽象接口
│           ├── memory_store.py      # 本地 JSONL 记忆实现
│           └── memory_factory.py    # 记忆后端工厂
│
├── tests/                       # 单元测试
├── test_cases/                  # 手动测试用例
├── docs/                        # 文档
└── .claude/                     # 项目本地配置
    └── skills/                  # 本地 Skill 目录
```

---

## 各层职责说明

### cli/ — 命令行界面层

| 文件 | 职责 |
|------|------|
| `cli/parser.py` | 纯 argparse 定义，无任何业务依赖 |
| `cli/app.py` | 启动装配：解析参数、构建 Config/Agent/Skills，分发单次/REPL 模式 |
| `cli/repl.py` | REPL 主循环、slash 命令路由、retry/rollback/compact 实现 |
| `cli/commands/skills.py` | `/skills` `/skill on\|off\|info\|stats\|reset` |
| `cli/commands/sessions.py` | `/session` 及其子命令 |
| `cli/commands/tasks.py` | `/tasks` 及其子命令 |
| `cli/commands/plans.py` | `/plan` 及其子命令 |
| `cli/commands/concurrency.py` | `/concurrency`（别名 `/cc`）及其子命令 |
| `cli/commands/providers.py` | `/provider` 及其子命令 |

### ui/ — 终端 UI 层

| 文件 | 职责 |
|------|------|
| `ui/terminal.py` | **唯一写屏幕的地方**，渲染队列、状态栏、输入读取 |
| `ui/renderer.py` | 历史 API 适配层（`print_tool_call` 等映射到 terminal） |
| `ui/repl_input.py` | prompt_toolkit 输入封装，Tab 补全、历史 |

### 核心模块

| 文件 | 职责 |
|------|------|
| `agent.py` | Agent 核心循环：历史管理、LLM 调用、工具执行、Skill 激活、session 保存 |
| `config.py` | AppConfig 定义与加载（JSON 文件 > CLI 参数 > 环境变量 > 默认值） |
| `permissions.py` | 工具权限守卫：危险命令识别、用户确认、沙箱模式 |
| `session.py` | Session 序列化/反序列化、SessionManager |

---

## 扩展开发

### 添加新工具

```python
# src/mini_agent/tools/builtin.py 或新建文件
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
    requires_approval=True
)
def my_tool(param: str) -> str:
    return f"结果：{param}"
```

确保模块在 `cli/app.py` 的启动阶段被 import（side-effect 注册）。

### 添加新 Provider

1. 在 `src/mini_agent/llm/providers/` 下创建新文件
2. 继承 `LLMClient`，实现 `chat()` 和 `stream()`
3. 在 `src/mini_agent/llm/factory.py` 的 `_REGISTRY` 中注册懒加载函数

### 添加技能

在 `.claude/skills/<skill_name>/SKILL.md` 或 `--skills-dir` 指定目录下创建：

```markdown
---
name: my-skill
description: 我的扩展功能
triggers: keyword1, keyword2
---

技能的具体使用说明...
```

---

## 技术特点

1. **标准 Python 包布局**：`src/mini_agent` 包，支持 `pip install -e .` 和 `python -m mini_agent`
2. **分层解耦**：CLI / UI / Agent / LLM / Tools / Prompts / Skills 各层边界清晰
3. **Provider 无关的 Agentic Loop**：`LLMResponse` 和 `ToolCall` 作为统一中间结构
4. **双工具调用协议**：原生 tools API 和 system prompt 文本工具双路支持
5. **并发 Sub-Agent**：完整的任务数据模型、依赖关系、状态查询和并发信号量
6. **Prompt 工程可维护**：所有提示词在 `prompts/` 中集中管理，不硬编码在业务逻辑里
7. **安全边界明确**：工具声明 `requires_approval`，`PermissionGuard` 综合审批

---

*最后更新：2026-06*
