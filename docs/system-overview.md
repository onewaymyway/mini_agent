# mini_agent 系统设计概述

> 本文面向开发者和维护者，梳理 mini_agent 的整体架构、关键子系统、设计决策与后续改进方向。

---

## 1. 项目定位

mini_agent 是一个用 Python 实现的命令行 Agent 框架，定位为"简化版 Claude Code"。核心目标：

- **模型提供商解耦**：Agent 只依赖统一的 `LLMClient` 抽象，不耦合具体 SDK
- **工具系统统一**：文件、Shell、搜索、计划、子任务编排等能力通过同一注册表暴露
- **Prompt 可维护**：所有提示词集中在 `prompts/`，避免硬编码
- **可观测与可恢复**：会话、统计、调试日志、任务状态均可持久化或查询
- **复杂任务可并发拆解**：主 Agent 可派生多个 Sub-Agent，通过依赖关系和信号量控制执行

---

## 2. 整体架构

### 2.1 包结构与层次

```
src/mini_agent/
│
├── cli/                  ← 命令行界面层（入口、REPL、slash 命令）
│   ├── app.py            ← 启动装配
│   ├── parser.py         ← argparse 定义
│   ├── repl.py           ← REPL 循环 + slash 路由
│   └── commands/         ← 各 slash 命令实现
│
├── ui/                   ← 终端 UI 层（唯一写屏幕的地方）
│   ├── terminal.py
│   ├── renderer.py
│   └── repl_input.py
│
├── agent.py              ← Agent 核心循环（编排层）
├── context_builder.py    ← System prompt 构建
├── tool_executor.py      ← 工具执行（权限 + 调用 + 缓存）
├── history_manager.py    ← 历史管理（压缩/快照）
├── config.py             ← 配置
├── permissions.py        ← 权限守卫
├── session.py            ← 会话持久化
│
├── llm/                  ← LLM 抽象层
├── tools/                ← 工具系统
├── orchestrator/         ← 并发编排系统
├── prompts/              ← Prompt 管理
├── skills/               ← Skill 系统
├── perception/           ← 感知与记忆系统
└── mcp/                  ← MCP 外部工具服务
```

### 2.2 顶层依赖关系

```
┌──────────────────────────────────────────────────────────┐
│  cli/（入口 + REPL + slash 命令）                         │
│  cli/app.py → cli/repl.py → cli/commands/*               │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│  Agent 核心循环（agent.py）                               │
│  - 对话历史  - system prompt 构建                         │
│  - LLM 调用  - 工具执行  - session 保存                  │
└────────┬──────────────────┬──────────────┬───────────────┘
         │                  │              │
┌────────▼──────┐  ┌────────▼──────┐  ┌───▼────────────┐
│  llm/         │  │  tools/       │  │  perception/   │
│  LLM 抽象层   │  │  工具系统     │  │  感知/记忆系统 │
└────────┬──────┘  └────────┬──────┘  └────────────────┘
         │                  │
┌────────▼──────┐  ┌────────▼──────────────┐
│  providers/   │  │  orchestrator/         │
│  各 LLM 实现  │  │  TaskManager/SubAgent  │
└───────────────┘  └────────────────────────┘
         │
┌────────▼────────────────────────────────────────────────┐
│  config / prompts / skills / session（基础设施层）        │
└─────────────────────────────────────────────────────────┘
```

### 2.3 一轮用户请求的执行链路

1. `cli/app.py` 解析参数，构建 `AppConfig`、`SkillLoader`、`PermissionGuard`、`Agent`
2. `cli/repl.py` 读取用户输入，slash 命令路由到 `cli/commands/`
3. 普通输入调用 `agent.run_turn(user_message)`
4. `agent.run_turn()` → Skill 自动激活 → 历史追加 → `_agentic_loop()`
5. `_agentic_loop()` → 构建 system prompt → LLM 调用 → 工具执行 → 重复直到最终文本
6. 每轮结束自动保存 Session，按配置生成摘要或写入长期记忆

---

## 3. 核心子系统

### 3.1 CLI 层（cli/）

重构后 CLI 层拆分为四个职责单一的模块：

| 模块 | 职责 |
|------|------|
| `cli/parser.py` | 纯 argparse 定义，47 个参数，零业务依赖 |
| `cli/app.py` | 启动装配：解析参数 → 构建各组件 → 分发单次/REPL 模式 |
| `cli/repl.py` | REPL 主循环 + slash 命令路由 + retry/rollback/compact |
| `cli/commands/` | 每类 slash 命令独立模块：skills/sessions/tasks/plans/concurrency/providers |

slash 命令使用各自的 handler 函数，通过 `cli/commands/__init__.py` 统一导出，`repl.py` 只做路由分发。

### 3.2 UI 层（ui/）

`ui/terminal.py` 是整个进程唯一写屏幕的地方（详见 [terminal-io-guide.md](terminal-io-guide.md)）：

- 渲染消息队列 + 专用渲染线程，消除多线程输出竞态
- 状态栏通过 ANSI 控制码覆写实现局部刷新
- `_enter_input_mode()` 用哨兵消息精确同步，而非 sleep，确保输入期间无意外写屏
- **Task 焦点模式**：支持方向键实时切换查看不同任务的日志输出（详见 [task-focus-viewing.md](task-focus-viewing.md)）

`ui/renderer.py` 是向后兼容适配层，将历史 `print_tool_call()` 等 API 映射到 `terminal.term`。

`ui/raw_key_listener.py` 实现跨平台键盘监听（Unix: `/dev/tty` + `termios` / Windows: `msvcrt`），支持在 Agent 运行期间响应用户快捷键。

### 3.3 Agent 核心循环（agent.py）

Agent 作为纯编排层，委托三个核心组件完成具体工作：

| 组件 | 来源 | 职责 |
|------|------|------|
| `ContextBuilder` | `context_builder.py` | System prompt 构建（skill/memory/project 注入） |
| `ToolExecutor` | `tool_executor.py` | 工具执行（权限检查 + 调用 + 截断 + 缓存） |
| `HistoryManager` | `history_manager.py` | 历史管理（追加 + 压缩 + 快照恢复） |

其他组合对象：

| 对象 | 来源 | 职责 |
|------|------|------|
| `AppConfig` | `config.py` | 控制运行行为 |
| `ToolRegistry` | `tools/` | 工具定义与调用 |
| `SkillLoader` | `skills/` | Skill 激活与上下文注入 |
| `PermissionGuard` | `permissions.py` | 工具执行前权限检查 |
| `LLMClient` | `llm/` | 统一模型调用接口 |
| `SessionManager` | `session.py` | 会话持久化 |
| 感知组件 | `perception/` | 项目扫描/文件监听/缓存/记忆 |

核心方法：
- `run_turn()` — 处理一轮用户输入（保存快照、触发技能、调用循环）
- `_agentic_loop()` — 循环调用 LLM 和工具
- `retry_last_turn()` / `rollback_turn()` — 手动重试/回退
- `compact_with_skills()` — 压缩历史并重附技能上下文

详见 [Agent 设计详解](agent-design.md)。

### 3.4 LLM 抽象层（llm/）

核心数据结构（provider 无关）：

- `LLMClient` — 抽象基类，`chat()` 和 `stream()` 接口
- `ToolCall` — 模型请求的工具调用
- `LLMResponse` — 文本、工具调用、usage、reasoning
- `LLMConfig` — provider、model、api_key、base_url 等

Provider 工厂 `llm/factory.py` 使用懒加载注册表，新增 Provider 只需实现类并注册：

```python
# llm/factory.py
def _load_myprovider():
    from .providers.myprovider import MyProvider
    return MyProvider

_REGISTRY["myprovider"] = _load_myprovider
```

### 3.5 工具系统（tools/）

装饰器注册模式：

```python
from mini_agent.tools import tool

@tool(name="bash", description="...", schema={...}, requires_approval=True)
def bash(command: str, timeout: int = 30) -> str:
    ...
```

工具类别：Shell、文件操作、搜索（Web Search）、计划管理、Sub-Agent 编排、用户交互。
工具是否需要审批由工具定义声明，`PermissionGuard` 在执行前决策。

**Web Search 工具**：

- `web_search` — 网络搜索，支持 duckduckgo（默认）、brave、serper、tavily 后端
- 详见 [Web Search 指南](web-search-guide.md)

### 3.6 MCP 外部工具服务（mcp/）

MCP（Model Context Protocol）支持将外部进程的工具动态注册进 `ToolRegistry`，与内置工具完全统一。

| 模块 | 职责 |
|------|------|
| `mcp/config.py` | `MCPServerConfig` / `MCPConfig` 数据类 |
| `mcp/transport.py` | 传输层抽象：`StdioTransport`（本地子进程）/ `SSETransport`（远程 HTTP） |
| `mcp/manager.py` | `MCPManager`：启动时并发连接、注册工具、代理调用 |

**关键设计**：

- MCP 工具注册名格式 `mcp_{server}__{tool}`，归入分组 `mcp:{server_name}`，不与内置工具冲突
- 工具调用由 `ToolExecutor` 检测名称前缀后路由到 `MCPManager.call_tool_sync()`
- 传输层通过 `BaseTransport` 抽象，新增协议只需继承一个类
- 单个 server 连接失败不阻断 Agent 启动

详见 [MCP 集成指南](mcp-guide.md)。

- `auto_approve` — 受信任环境下自动批准
- `sandbox` — 沙箱模式阻断破坏性操作
- 危险命令识别 — 正则匹配 `rm -rf`、`dd`、`sudo`、`curl | bash` 等
- 用户确认 — 通过 `terminal.term.confirm()` 安全地暂停渲染并读取输入
- 白名单机制 — 按 `tool_name` + `path_prefix` 精细管理，支持路径规范化（`./test/` 与 `test/` 等价）
- 权限持久化 — `agent_permissions.json` 保存在工作目录，跨 session 生效

详见 [权限系统指南](permission-guide.md)。

### 3.12 权限与沙箱（permissions.py）

- `auto_approve` — 受信任环境下自动批准
- `sandbox` — 沙箱模式阻断破坏性操作
- 危险命令识别 — 正则匹配 `rm -rf`、`dd`、`sudo`、`curl | bash` 等
- 用户确认 — 通过 `terminal.term.confirm()` 安全地暂停渲染并读取输入
- 白名单机制 — 按 `tool_name` + `path_prefix` 精细管理，支持路径规范化（`./test/` 与 `test/` 等价）
- 权限持久化 — `agent_permissions.json` 保存在工作目录，跨 session 生效

详见 [权限系统指南](permission-guide.md)。

### 3.13 Prompt 管理（prompts/）

system prompt 构建顺序：

1. Agent 核心身份与行为规则
2. Plan 模式能力说明
3. 当前时间
4. CLAUDE.md 项目上下文
5. 已激活 Skill（目录 + 内容）
6. 额外 system 文本
7. 编排能力说明
8. 当前执行计划状态
9. 沙箱模式警告

所有文本片段在 `prompts/fragments/*.md` 中管理，通过 `PromptManager.fragment()` 取用。

### 3.14 Skill 系统（skills/）

详见 [skill-system-guide.md](skill-system-guide.md)。核心流程：

1. `SkillLoader` 从一个或多个目录发现 `SKILL.md`
2. 每轮 `run_turn()` 按触发词自动激活，或由模型通过工具主动管理
3. 激活的 Skill 内容注入 system prompt
4. 回复后检测实际使用（显式标签 + 指纹匹配），更新 LRU 追踪
5. 压缩历史时按 LRU 顺序重附 Skill，在预算内优先保留最近使用的

### 3.15 并发编排（orchestrator/）

详见 [plan-and-task-guide.md](plan-and-task-guide.md)、[subagent-mechanism.md](subagent-mechanism.md) 和 [task-focus-viewing.md](task-focus-viewing.md)。两层结构：

- **ExecutionPlan / PlanTask** — 结构化执行计划，注入 system prompt，不启动线程
- **TaskManager / SubAgent** — 真正的并发执行，纯线程模型，不依赖 asyncio
- **Task 日志实时查看**：支持方向键切换查看各任务实时日志，状态栏显示任务状态概要

**SubAgent 关键特性**：
- 独立的对话历史、独立的统计信息
- 继承主 Agent 的 LLMConfig（可覆盖 provider/model）
- 输出通过回调写入 TaskRecord.log_lines（不直接打印 stdout）
- 对可重试错误（HTTP 5xx、超时）自动重试最多 3 次，每次间隔 2 秒
- 线程安全的状态管理，通过 TaskRecord 的 lock 保护
- Debug 日志写入 test_result/subagent_debug.jsonl

并发控制：两个 `CountingSemaphore`，分别限制并发任务数和并发 LLM 调用数。

### 3.16 感知系统（perception/）

一组可选增强能力，通过配置开关启用：

| 子系统 | 功能 |
|--------|------|
| `ProjectScanner` | 扫描项目结构、语言识别、依赖、Git 信息，生成 prompt block |
| `FileWatcher` | 检测文件 hash 变化，下轮注入变化提示 |
| `ToolResultCache` | 缓存工具调用结果，文件变化时失效 |
| `TokenCounter` | 粗略估算上下文 token，触发告警和自动压缩 |

### 3.11 记忆系统（perception/memory/

跨 session 的长期记忆管理，采用可扩展架构：

| 组件 | 功能 |
|------|------|
| `MemoryBackend` | 记忆后端抽象接口（add/search/search_by_tag） |
| `MemoryStore` | 本地 JSONL 实现，TF-IDF + n-gram 分词 + 时间衰减 |
| `MemoryFactory` | 根据配置创建后端实例，支持动态注册 |

**核心特性**：
- **接口与实现解耦**：Agent 只依赖 `MemoryBackend` 抽象，与具体存储完全分离
- **工厂模式**：新增后端只需实现接口并在工厂注册表中注册
- **中文 n-gram 分词**：双字 + 三字 n-gram 替代逐字切分，提升复合词召回率
- **时间衰减**：指数衰减因子（半衰期 30 天），防止旧记忆干扰
- **容量管理**：默认 500 条上限，超出自动淘汰最旧条目
- **原子写入**：tmp + rename 保证数据一致性

详见 [记忆管理指南](memory-management-guide.md)。

### 3.17 HTTP API 服务（api/）

内置 FastAPI HTTP 服务，支持通过 REST/SSE 与 agent 交互：

| 模块 | 文件 | 职责 |
|------|------|------|
| `api/server.py` | `HttpServer` | uvicorn 服务封装，AgentRunner 后台线程 |
| `api/routes.py` | 路由定义 | REST 端点 + SSE 流式输出 |
| `api/bridge.py` | `AgentBridge` | Agent 核心与 HTTP 层之间的解耦桥梁 |
| `api/auth.py` | 认证中间件 | Bearer Token 认证 + IP 白名单 |
| `api/models.py` | Pydantic 模型 | 请求/响应模型 + 事件类型 |
| `api/fs_helper.py` | `FsHelper` | 文件系统操作封装 |

详见 [HTTP API 指南](http-api-guide.md)。

### 3.18 Web Demo（apps/）

基于 Streamlit 的浏览器交互界面：

| 文件 | 功能 |
|------|------|
| `apps/mini_agent_webdemo/app.py` | Web Demo 主程序 |

功能特性：
- 现代化聊天界面
- 实时事件流监控
- 权限审批可视化
- 文件系统浏览
- Turn 历史记录

---

## 4. 关键设计决策

### 4.1 统一写屏通道

所有输出通过 `ui/terminal.py` 的渲染队列串行执行，消除多线程竞态。  
详细机制见 [terminal-io-guide.md](terminal-io-guide.md)。

### 4.2 双工具调用协议

同时支持原生 tools API（结构化工具调用）和 system prompt 文本工具协议（最大兼容性）。  
通过 `--system-tool-call` 参数切换，兼顾强结构化能力和本地模型兼容性。

### 4.3 配置优先级

JSON 配置文件 > 命令行参数 > 环境变量 > 内置默认值。  
注意：这与部分 CLI 工具"命令行参数最高优先级"的习惯相反，使用前请确认。

### 4.4 标准包布局

`src/mini_agent` 采用标准 `src` 布局，支持 `pip install -e .` 安装，使用绝对导入 `from mini_agent.xxx import ...`，不依赖 cwd 路径。

### 4.5 HTTP API 集成方式

v2 重构将记忆系统改为接口 + 工厂模式：

- `MemoryBackend` 是纯抽象，Agent 代码零耦合具体存储
- `MemoryFactory` 根据 `cfg.memory.backend` 创建实例
- 新增后端（如 Chroma、Redis）只需两步：实现接口 → 注册
- `MemoryConfig` 独立配置，添加参数不影响其他模块

详见 [记忆管理指南](memory-management-guide.md)。

HTTP 服务通过桥接模式与 Agent 核心解耦：

- `AgentBridge` 作为统一接口，Agent 核心无需感知 HTTP 存在
- 输出拦截通过 monkey-patch `Renderer` 实现，无需修改 agent.py
- 命令队列模式：HTTP 端 enqueue，AgentRunner 阻塞 dequeue
- 权限审批双路径：终端交互或 HTTP SSE，自动路由到可用方式

---

## 7. 后续改进方向

### P2（较高收益）

- **UI 层 facade**：业务模块目前仍有少量直接依赖 `terminal.term`，可进一步收拢
- **配置优先级语义**：明确 CLI 参数是否可覆盖 JSON 配置文件
- **memory_base.py/memory_factory.py**：新增记忆后端支持
- **terminal.py/repl_input.py**：终端交互模块新增

### P3（长期演进）

- **记忆系统已完成向量检索扩展点**：通过 `MemoryBackend` 接口可接入 Chroma/Redis 等后端
- **更多 Provider**：Gemini、Azure OpenAI、OpenRouter、Mistral
- **Plan + Task 融合**：计划节点绑定 Sub-Agent 任务 ID，实现真正的计划驱动并发
- **任务持久化**：Sub-Agent 任务记录目前在内存，崩溃后无法恢复
- **类型检查与格式化**：引入 mypy/pyright、ruff/black

---

## 8. 新增 Provider 建议流程

1. 在 `src/mini_agent/llm/providers/` 创建新文件，继承 `LLMClient`
2. 实现 `chat()` 和 `stream()`，将 SDK 响应转换为 `LLMResponse`
3. 将 `ToolSchema` 转换为目标 API 的工具 schema
4. 在 `src/mini_agent/llm/factory.py` 的 `_REGISTRY` 注册懒加载函数
5. 补充单元测试（普通文本、工具调用、流式输出、错误处理）
6. 更新 `cli/parser.py` 的 `--provider` 帮助文本

## 9. 新增记忆后端建议流程

1. 在 `src/mini_agent/perception/` 创建新文件，继承 `MemoryBackend`
2. 实现 `add()` / `search()` / `search_by_tag()` / `count`
3. 在 `src/mini_agent/perception/memory_factory.py` 的 `_REGISTRY` 注册
4. 补充单元测试（添加、检索、标签过滤、容量管理）
5. 更新 `MemoryConfig.backend` 的文档说明

## 10. 新增工具建议流程

1. 在 `src/mini_agent/tools/` 选择合适文件或新建模块
2. 用 `@tool` 声明名称、描述、JSON Schema、`requires_approval`
3. 确保模块在 `src/mini_agent/cli/app.py` 启动时被 import（完成注册）
4. 补充单元测试
5. 对有大量输出的工具，考虑配合工具结果截断或缓存策略

## 10. 新增 MCP 工具服务建议流程

1. 在 `mcp_servers/` 创建服务脚本（任何语言均可，遵循 MCP JSON-RPC 2.0 协议）
2. 在 `agent_config.json` 的 `mcp_servers` 数组中添加配置项
3. 启动 Agent，观察连接日志确认工具注册成功
4. 向 Agent 提问触发工具，验证端到端调用
5. 如需新增传输协议，继承 `mcp/transport.py` 的 `BaseTransport` 并在 `create_transport()` 注册

详见 [MCP 集成指南](mcp-guide.md)。

## 12. 相关文档

- [Agent 设计详解](agent-design.md) — agent.py 的核心架构、组件职责、执行流程
- [代码结构指南](code-structure-guide.md) — 项目结构与导入规范
- [HTTP API 指南](http-api-guide.md) — REST/SSE 服务使用指南
- [记忆管理指南](memory-management-guide.md) — 长期记忆系统与可扩展后端
- [MCP 集成指南](mcp-guide.md) — MCP 外部工具服务架构与配置
- [Task 日志实时查看](task-focus-viewing.md) — 方向键切换查看任务日志机制

---

*最后更新：2026-06（新增 MCP 子系统、history/、storage/、task_display.py、task-focus-viewing 等模块）*
