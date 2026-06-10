# 代码结构说明

> 本文说明 mini_agent 当前的包布局、模块边界、import 约定和后续改进路线。

---

## 1. 当前结构（已完成重构）

项目已从根目录平铺布局迁移为标准 `src` 包布局：

```
mini_agent/
├── main.py                 # 兼容入口 shim（≤15 行）
├── pyproject.toml          # 包配置，入口点 mini-agent
├── requirements.txt
│
└── src/
    └── mini_agent/
        ├── __init__.py
        ├── __main__.py     # python -m mini_agent 入口
        │
        ├── cli/            # 命令行界面层
        │   ├── app.py      # 启动装配，main()
        │   ├── parser.py   # argparse 定义（零业务依赖）
        │   ├── repl.py     # REPL 循环 + slash 路由
        │   └── commands/   # 各 slash 命令实现
        │       ├── skills.py
        │       ├── sessions.py
        │       ├── tasks.py
        │       ├── plans.py
        │       ├── concurrency.py
        │       └── providers.py
        │
        ├── ui/             # 终端 UI 层
        │   ├── terminal.py
        │   ├── renderer.py
        │   └── repl_input.py
        │
        ├── agent.py        # Agent 核心循环
        ├── config.py       # 配置（AppConfig）
        ├── permissions.py  # 权限守卫
        ├── session.py      # 会话持久化
        │
        ├── llm/            # LLM 抽象层
        ├── tools/          # 工具系统
        ├── orchestrator/   # 并发编排
        ├── prompts/        # Prompt 管理
        ├── skills/         # Skill 系统
        ├── perception/     # 感知与记忆
        └── mcp/            # MCP 外部工具服务支持
            ├── __init__.py
            ├── config.py   # MCPServerConfig / MCPConfig
            ├── transport.py # 传输层（stdio / sse）
            └── manager.py  # MCPManager（连接、注册、调用路由）
```

---

## 2. 层次职责

### 2.1 cli/ — 命令行界面层

| 模块 | 职责 | 原来的位置 |
|------|------|------------|
| `cli/parser.py` | 纯 argparse 定义，47 个参数 | `main.py:build_parser()` |
| `cli/app.py` | 启动装配，构建所有组件 | `main.py:main()` |
| `cli/repl.py` | REPL 循环 + slash 路由 | `main.py:run_repl()` + `_handle_slash()` |
| `cli/commands/skills.py` | `/skills` `/skill` 命令 | `main.py:_handle_skills_*()` |
| `cli/commands/sessions.py` | `/session` 命令 | `main.py:_handle_session_cmd()` |
| `cli/commands/tasks.py` | `/tasks` 命令 | `main.py:_handle_tasks_cmd()` |
| `cli/commands/plans.py` | `/plan` 命令 | `main.py:_handle_plan_cmd()` |
| `cli/commands/concurrency.py` | `/concurrency` 命令 | `main.py:_handle_concurrency_cmd()` |
| `cli/commands/providers.py` | `/provider` 命令 | `main.py:_handle_provider_cmd()` |

### 2.2 ui/ — 终端 UI 层

| 模块 | 职责 | 原来的位置 |
|------|------|------------|
| `ui/terminal.py` | 唯一写屏幕的地方 | 根目录 `terminal.py` |
| `ui/renderer.py` | 历史 API 适配层 | 根目录 `renderer.py` |
| `ui/repl_input.py` | prompt_toolkit 封装 | 根目录 `repl_input.py` |

### 2.3 核心层（原根目录模块）

| 模块 | 原来的位置 | 说明 |
|------|------------|------|
| `mini_agent/agent.py` | 根目录 `agent.py` | Agent 主类，对话循环与编排 |
| `mini_agent/context_builder.py` | 新增 | System prompt 构建 |
| `mini_agent/tool_executor.py` | 新增 | 工具执行器 |
| `mini_agent/history_manager.py` | 新增 | 历史管理器 |
| `mini_agent/config.py` | 根目录 `config.py` | 配置管理 |
| `mini_agent/permissions.py` | 根目录 `permissions.py` | 权限守卫 |
| `mini_agent/session.py` | 根目录 `session.py` | 会话持久化 |

### 2.4 子包（整体迁入，内部结构不变）

| 子包 | 原来的位置 |
|------|------------|
| `mini_agent/llm/` | 根目录 `llm/` |
| `mini_agent/tools/` | 根目录 `tools/` |
| `mini_agent/orchestrator/` | 根目录 `orchestrator/` |
| `mini_agent/prompts/` | 根目录 `prompts/` |
| `mini_agent/skills/` | 根目录 `skills/` |
| `mini_agent/perception/` | 根目录 `perception/` |
| `mini_agent/mcp/` | 新增 |

### 2.5 项目根目录 mcp_servers/

```
mcp_servers/
└── time_server.py    # 测试用 MCP 服务（get_current_time / calculate / echo）
```

用于存放本地 MCP 服务脚本。Agent 通过 `agent_config.json` 的 `mcp_servers` 配置以子进程方式启动它们。详见 [MCP 集成指南](mcp-guide.md)。

---

## 3. Import 约定

所有 import 使用绝对路径，以 `mini_agent.` 开头：

```python
# ✅ 正确
from mini_agent.config import AppConfig
from mini_agent.llm import LLMConfig, create_client
from mini_agent.ui.terminal import term
from mini_agent.cli.commands.skills import handle_skill_cmd

# ❌ 不允许（根目录裸 import）
from config import AppConfig
from terminal import term
```

同包内部引用使用相对 import：

```python
# orchestrator/task_manager.py 内部
from .task import Task, TaskRecord
from .concurrency import concurrency_snapshot
```

跨包引用使用绝对 import：

```python
# orchestrator/sub_agent.py 引用 llm
from mini_agent.llm import create_client
# orchestrator/task_display.py 引用 ui
from mini_agent.ui.terminal import term as _term
```

---

## 4. 安装与运行

```bash
# 开发安装（editable）
pip install -e .

# 验证
python -m mini_agent --help
mini-agent --help
python main.py --help   # 兼容旧方式

# 运行测试
python -m pytest tests/
# pyproject.toml 已配置 pythonpath = ["src"]，测试无需手动设置 PYTHONPATH
```

---

## 5. 测试结构

测试文件位于 `tests/`，`sys.path` 已统一指向 `src/`：

```python
# 每个测试文件顶部
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# import 使用绝对路径
from mini_agent.session import Session, SessionManager
from mini_agent.orchestrator.task import Task, TaskStatus
```

---

## 6. 待完成的改进（P2/P3）

以下改进在本次重构范围之外，作为后续工作：

### P2 — 较高收益，适合独立 PR

- **UI 层完全收拢**：少量业务模块（`orchestrator/task_display.py`、`plan_display.py`）仍直接依赖 `mini_agent.ui.terminal`，可定义 UI facade 进一步解耦
- **配置/存储/安全细分**：`config.py`、`session.py`、`permissions.py` 可进一步拆分为 `config/schema.py` + `config/loader.py` 等

### P3 — 长期演进

- 引入 `mypy` / `pyright`、`ruff`、`black` 工具链
- `MemoryStore` 从关键词检索升级为向量检索
- Plan 节点与 Sub-Agent 任务 ID 建立显式绑定
- 任务持久化（当前仅在内存）

---

*最后更新：2026-06（新增 mcp/ 子包与 mcp_servers/ 说明）*
