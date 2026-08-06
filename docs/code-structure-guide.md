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
        ├── agent/          # Agent 核心循环（Stage 12 起由单文件拆分为包，见 agent-design.md）
        ├── config/         # 配置（AppConfig），v3 起拆分为包
        │   ├── __init__.py # 重导出，对外 import 路径不变
        │   ├── models.py   # 14 个配置 dataclass + AppConfig
        │   ├── loader.py   # load_config 及加载辅助函数
        │   └── prompt_builder.py  # build_system_prompt 及辅助函数
        ├── permissions.py  # 权限守卫
        ├── session.py      # 会话持久化
        │
        ├── llm/            # LLM 抽象层
        ├── tools/          # 工具系统
        ├── orchestrator/   # 并发编排
        ├── prompts/        # Prompt 管理
        ├── skills/         # Skill 系统
        ├── perception/     # 感知与记忆
        ├── history/        # 历史压缩管理
        ├── storage/        # 存储层
        ├── api/            # HTTP API 服务
        ├── hooks/          # hooks 机制
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
| `mini_agent/agent/` | 根目录 `agent.py`（Stage 12 起进一步拆分为包，见 [Agent 设计详解](agent-design.md#12-stage-12agentpy-拆分为-agent-包)） | Agent 主类，对话循环与编排 |
| `mini_agent/context_builder.py` | 新增 | System prompt 构建 |
| `mini_agent/tool_executor.py` | 新增 | 工具执行器 |
| `mini_agent/history_manager.py` | 新增 | 历史管理器 |
| `mini_agent/config/` | 根目录 `config.py`（v3 拆分为包，见下方说明） | 配置管理 |
| `mini_agent/permissions.py` | 根目录 `permissions.py` | 权限守卫 |
| `mini_agent/session.py` | 根目录 `session.py` | 会话持久化 |

**`config/` 包内部分工**（对应 `self_evolution_implementation_plan.md` Stage 0.4）：

| 文件 | 职责 |
|------|------|
| `config/models.py` | 14 个配置 dataclass（`MemoryConfig`/`CompressConfig`/…）+ `AppConfig` 主体 + 默认值常量 |
| `config/loader.py` | `load_config()` 主入口、`_load_config_file()`、`_load_providers_config()`、`_merge_providers_into_chain()` |
| `config/prompt_builder.py` | `build_system_prompt()`、`_read_claude_md()`、`_resolve_prompts_dir()`、`_resolve_skills_dir()` |
| `config/__init__.py` | 统一重导出全部符号，外部 `from mini_agent.config import AppConfig` 等用法不受影响 |

### 2.4 子包（整体迁入，内部结构不变）

| 子包 | 原来的位置 |
|------|------------|
| `mini_agent/llm/` | 根目录 `llm/` |
| `mini_agent/tools/` | 根目录 `tools/` |
| `mini_agent/orchestrator/` | 根目录 `orchestrator/` |
| `mini_agent/prompts/` | 根目录 `prompts/` |
| `mini_agent/skills/` | 根目录 `skills/` |
| `mini_agent/perception/` | 根目录 `perception/` |
| `mini_agent/history/` | 新增 |
| `mini_agent/storage/` | 新增 |
| `mini_agent/api/` | 新增 |
| `mini_agent/hooks/` | 新增 |
| `mini_agent/mcp/` | 新增 |

### 2.5 项目根目录 mcp_servers/

```
mcp_servers/
└── time_server.py    # 测试用 MCP 服务（get_current_time / calculate / echo）
```

用于存放本地 MCP 服务脚本。Agent 通过 `agent_config.json` 的 `mcp_servers` 配置以子进程方式启动它们。详见 [MCP 集成指南](mcp-guide.md)。

### 2.6 通用工具层：`utils/`

```
src/mini_agent/utils/
├── __init__.py
└── atomic_write.py   # 通用原子写入工具（指数退避重试、可选文件锁）
```

**`utils/atomic_write.py`** — 统一的原子写入工具，解决 Windows 上 `os.replace` 因文件被短暂锁定导致的 `PermissionError: [WinError 5]` 问题。

| 函数 | 用途 |
|------|------|
| `atomic_write_text(path, text, *, flock=False)` | 原子写入文本文件 |
| `atomic_write_json(path, data, *, flock=False)` | 原子写入 JSON（自动 `json.dumps` + indent=2） |
| `atomic_write_jsonl(path, records, *, flock=False)` | 原子写入 JSONL（覆盖模式） |
| `atomic_append_jsonl(path, record, *, flock=False)` | 原子追加单行 JSONL |

**核心特性**：
- 临时文件 + `fsync()` + `os.replace()` 原子替换
- 指数退避重试（最多 5 次，基础 50ms）
- 可选跨进程文件锁（`flock=True`，`session.py` 使用）
- 自动创建父目录

**迁移历史**：原本分散在 12+ 个模块（`perception/`、`wiki/`、`evolution/` 等）中各自实现的重试逻辑（约 300+ 行重复代码），已于 2026-08 统一迁移至此。

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

> ✅ 已完成（2026-06，`self_evolution_implementation_plan.md` Stage 0）：
> - `config.py` → `config/` 包拆分（`models.py` / `loader.py` / `prompt_builder.py`），见上方 2.3 节
> - 任务持久化：`TaskRecord` 现在会把 `manifest.json` 落盘到 `tasks/<task_id>/`，`ExecutionPlan` 状态变更同步写 `plan_snapshot.json` 并支持 session 重启恢复，不再"仅在内存"，详见 [Plan 与 Task 机制说明](plan-and-task-guide.md) 与 [存储设计](storage-design.md)

### P3 — 长期演进

- 引入 `mypy` / `pyright`、`ruff`、`black` 工具链
- `MemoryStore` 从关键词检索升级为向量检索
- Plan 节点与 Sub-Agent 任务 ID 建立显式绑定

---

*最后更新：2026-06（config.py 拆分为 config/ 包；任务持久化落地，见 [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) Stage 0）*
