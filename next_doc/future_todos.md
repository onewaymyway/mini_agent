# 代码结构改进建议

> 本文专门从代码组织结构角度梳理 mini_agent 当前的主要问题、推荐目录布局、迁移步骤与风险控制。重点回应“很多脚本在根目录，不太合理”的问题。

## 1. 当前观察

当前项目已经有较清晰的领域目录，例如 `llm/`、`tools/`、`orchestrator/`、`prompts/`、`skills/`、`perception/`、`tests/` 和 `docs/`。这些目录说明项目已经具备按子系统拆分的基础。

但根目录仍保留了较多承担核心职责的 Python 文件：

| 根目录文件 | 主要职责 | 当前问题 |
|------------|----------|----------|
| `main.py` | CLI 参数解析、REPL 循环、slash 命令分发、启动 TaskManager、加载配置 | 文件过大，启动逻辑、命令处理和 REPL 逻辑混杂。 |
| `agent.py` | Agent 主循环、LLM 调用、工具执行、上下文构建、session 保存、感知能力接入 | 核心职责过多，未来继续扩展会难以测试和维护。 |
| `config.py` | AppConfig、配置加载、环境变量、JSON 配置、CLAUDE.md、system prompt 入口 | 配置定义和加载细节混在一起，且与 prompt 构建存在一定耦合。 |
| `session.py` | Session 数据结构、保存、加载、搜索、删除 | 属于持久化/存储层，放根目录不利于边界表达。 |
| `permissions.py` | 工具权限审批、沙箱判断、危险命令识别 | 属于安全/运行策略层，后续如果扩展策略会变厚。 |
| `terminal.py` | 终端消息队列、状态栏、输入模式、渲染循环 | 属于 UI/TUI 运行时，不应与核心业务入口并列。 |
| `renderer.py` | Rich 输出封装、工具调用展示、Markdown 展示 | 属于 UI 展示层，与 `terminal.py` 应合并到统一包中。 |
| `repl_input.py` | REPL 输入封装，prompt_toolkit fallback | 属于 CLI / UI 输入层，应从根目录移出。 |

根目录的文件数量本身不是最大问题；真正的问题是：**根目录同时承担入口、核心领域对象、配置、持久化、安全和 UI 层，层次边界不够明显**。

## 2. 主要结构问题

### 2.1 根目录职责过重

根目录现在既包含可执行入口，也包含核心业务模块。随着项目功能增加，根目录会越来越像“杂项区”，开发者需要先记住每个文件的隐含职责，才能判断应该修改哪里。

建议目标：根目录只保留少量项目级文件，例如：

- `README.md`
- `requirements.txt` 或 `pyproject.toml`
- `agent_config.json` 示例或默认配置
- `CLAUDE.md`
- `docs/`
- `tests/`
- `src/` 或顶层包目录

### 2.2 CLI、UI 与核心逻辑耦合

`main.py` 同时处理：

- 参数定义。
- Agent 构造。
- REPL 循环。
- slash 命令。
- session 命令。
- plan 命令。
- task 命令。
- provider 命令。
- concurrency 命令。

这会导致两个问题：

1. 新增命令时只能继续往 `main.py` 塞函数。
2. 对 slash 命令做单元测试时，需要绕过大量 CLI 启动上下文。

建议将 CLI 拆成独立包：参数解析、应用启动、REPL、slash 命令各自独立。

### 2.3 Agent 类承担过多运行时职责

`agent.py` 的 `Agent` 既是状态对象，又是工具执行器、上下文构建器、LLM 调用器、session 保存协调器和感知能力协调器。

建议后续拆分出：

- `ContextBuilder`：只负责 system prompt、Skill、ProjectScanner、Memory、Plan 等上下文组合。
- `ToolExecutor`：只负责权限检查、工具调用、结果截断、缓存和工具统计。
- `HistoryManager`：只负责历史追加、转换、压缩和清理。
- `AgentRuntime` 或继续保留 `Agent`：只负责编排上述组件。

这样能让核心循环更接近：接收用户输入 → 构建上下文 → 调用模型 → 执行工具 → 更新历史。

### 2.4 UI 层没有独立命名空间

`terminal.py`、`renderer.py`、`repl_input.py` 都属于 UI / TUI / CLI 输入输出层，但现在散落根目录。

建议统一到：

```text
src/mini_agent/ui/
  terminal.py
  renderer.py
  repl_input.py
```

或者更细分：

```text
src/mini_agent/cli/
  repl.py
  commands/
  parser.py
src/mini_agent/ui/
  terminal.py
  renderer.py
```

### 2.5 配置、会话与安全层可以形成明确包

`config.py`、`session.py`、`permissions.py` 目前都在根目录。它们不是临时脚本，而是长期存在的基础设施模块。

建议分别归入：

```text
src/mini_agent/config/
  schema.py       # AppConfig, SessionStats
  loader.py       # load_config, env/json/CLAUDE.md 加载

src/mini_agent/storage/
  session.py      # Session, SessionManager

src/mini_agent/security/
  permissions.py  # PermissionGuard, sandbox policy
```

如果暂时不想拆太细，也可以先移动成：

```text
src/mini_agent/config.py
src/mini_agent/session.py
src/mini_agent/permissions.py
```

先完成“从根目录进入包”的第一步，再逐步细分。

### 2.6 缺少标准 Python 包布局

当前代码可以通过根目录 import 运行，但不是标准的 `src` 布局。随着项目被安装、测试、发布或作为库引用，容易出现：

- 本地路径污染导致测试通过但安装后失败。
- 顶层模块名与第三方包冲突。
- 入口脚本与库代码难以区分。
- 相对导入和绝对导入策略不统一。

建议迁移到标准布局：

```text
src/mini_agent/
  __init__.py
  __main__.py
  agent/
  cli/
  config/
  llm/
  tools/
  orchestrator/
  prompts/
  skills/
  perception/
  storage/
  security/
  ui/
```

## 3. 推荐目标目录结构

### 3.1 第一版推荐结构

下面是兼顾可维护性和迁移成本的推荐结构：

```text
mini_agent/
├── README.md
├── CLAUDE.md
├── requirements.txt
├── pyproject.toml                  # 建议新增，统一包配置、测试、格式化配置
├── agent_config.example.json        # 建议把默认示例配置与本地配置区分
├── docs/
│   ├── project-design.md
│   ├── code-structure-improvement-suggestions.md
│   ├── plan-and-task-guide.md
│   └── terminal-io-guide.md
├── tests/
│   └── ...
├── test_cases/
│   └── ...
└── src/
    └── mini_agent/
        ├── __init__.py
        ├── __main__.py              # python -m mini_agent 入口
        ├── agent/
        │   ├── __init__.py
        │   ├── runtime.py           # Agent 或 AgentRuntime
        │   ├── context.py           # ContextBuilder
        │   ├── history.py           # HistoryManager
        │   └── tool_executor.py     # ToolExecutor
        ├── cli/
        │   ├── __init__.py
        │   ├── app.py               # 启动装配
        │   ├── parser.py            # argparse 定义
        │   ├── repl.py              # REPL loop
        │   └── commands/
        │       ├── __init__.py
        │       ├── slash.py         # slash command router
        │       ├── sessions.py
        │       ├── tasks.py
        │       ├── plans.py
        │       ├── providers.py
        │       └── concurrency.py
        ├── config/
        │   ├── __init__.py
        │   ├── schema.py
        │   └── loader.py
        ├── security/
        │   ├── __init__.py
        │   └── permissions.py
        ├── storage/
        │   ├── __init__.py
        │   └── session.py
        ├── ui/
        │   ├── __init__.py
        │   ├── terminal.py
        │   ├── renderer.py
        │   └── repl_input.py
        ├── llm/
        ├── tools/
        ├── orchestrator/
        ├── prompts/
        ├── skills/
        └── perception/
```

### 3.2 根目录保留什么

迁移后根目录建议只保留：

- 项目元信息：`README.md`、`CLAUDE.md`、`requirements.txt` / `pyproject.toml`。
- 文档与测试：`docs/`、`tests/`、`test_cases/`。
- 配置示例：`agent_config.example.json`。
- 极薄入口：如确实需要兼容 `python main.py`，可保留一个不超过 20 行的 `main.py` shim。

兼容入口示例：

```python
from mini_agent.cli.app import main

if __name__ == "__main__":
    raise SystemExit(main())
```

## 4. 分阶段迁移建议

### 阶段 0：先建立迁移原则

在真正移动文件前，先确认这些原则：

- 每次只移动一个层或一个子系统，避免大爆炸式重构。
- 每次移动后都运行测试，并修复 import。
- 保持 `python main.py` 或至少 `python -m mini_agent` 可用。
- 不在迁移 PR 中混入行为变更，尽量只做路径和 import 调整。
- 对外文档同步更新，包括 README 的运行命令、项目结构和开发说明。

### 阶段 1：引入 `src/mini_agent` 包，但不拆内部职责

第一步只做“搬家”，不拆类：

```text
agent.py       → src/mini_agent/agent.py
config.py      → src/mini_agent/config.py
permissions.py → src/mini_agent/permissions.py
renderer.py    → src/mini_agent/renderer.py
repl_input.py  → src/mini_agent/repl_input.py
session.py     → src/mini_agent/session.py
terminal.py    → src/mini_agent/terminal.py
llm/           → src/mini_agent/llm/
tools/         → src/mini_agent/tools/
orchestrator/  → src/mini_agent/orchestrator/
prompts/       → src/mini_agent/prompts/
skills/        → src/mini_agent/skills/
perception/    → src/mini_agent/perception/
```

此阶段目标：让项目成为标准 Python package，同时最大限度降低重构风险。

### 阶段 2：拆分 `main.py`

建议把 `main.py` 拆成：

```text
src/mini_agent/cli/parser.py      # build_parser
src/mini_agent/cli/repl.py        # run_repl
src/mini_agent/cli/commands.py    # 初始可先放所有 slash command
src/mini_agent/cli/app.py         # main 装配入口
```

之后再把 `commands.py` 拆成 `commands/sessions.py`、`commands/tasks.py`、`commands/plans.py`、`commands/providers.py` 等。

验收标准：

- 根目录 `main.py` 只剩 shim。
- `python main.py --help` 与迁移前输出一致。
- `python -m mini_agent --help` 可用。
- slash 命令相关测试可以直接 import 对应 command handler。

### 阶段 3：合并 UI 相关模块到 `ui/`

将：

```text
terminal.py
renderer.py
repl_input.py
```

移动到：

```text
src/mini_agent/ui/
```

同时统一导入方式，例如把：

```python
import renderer as R
from terminal import get_terminal
```

改为：

```python
from mini_agent.ui import renderer as R
from mini_agent.ui.terminal import get_terminal
```

验收标准：

- 所有 UI 相关导入都来自 `mini_agent.ui`。
- 核心业务模块不直接依赖具体 Rich / prompt_toolkit 细节；如必须输出，优先通过 UI facade。

### 阶段 4：拆分 Agent 运行时

在完成路径迁移和 CLI 拆分后，再拆 Agent：

```text
src/mini_agent/agent/runtime.py
src/mini_agent/agent/context.py
src/mini_agent/agent/tool_executor.py
src/mini_agent/agent/history.py
```

建议职责：

| 模块 | 职责 |
|------|------|
| `runtime.py` | 主循环、调用 LLM、协调上下文和工具执行。 |
| `context.py` | system prompt 构建、Skill、项目扫描、Memory、Plan 注入。 |
| `tool_executor.py` | 权限检查、工具注册表调用、结果截断、缓存、工具统计。 |
| `history.py` | 历史追加、tool_use 转换、自动压缩、清理策略。 |

验收标准：

- `Agent` 的公开 API 尽量保持不变，如 `run_turn()`、`save_session()`、`load_session()`。
- 单测可以分别覆盖 ContextBuilder、ToolExecutor 和 HistoryManager。

### 阶段 5：拆分配置、存储与安全

将基础设施模块细分：

```text
src/mini_agent/config/schema.py
src/mini_agent/config/loader.py
src/mini_agent/storage/session.py
src/mini_agent/security/permissions.py
```

验收标准：

- `AppConfig` 等纯数据结构不依赖 CLI。
- 配置加载逻辑可用临时目录和 mock 环境变量单独测试。
- 权限策略可独立测试，不依赖真实终端输入。

## 5. Import 策略建议

迁移到包布局后，建议统一使用绝对导入：

```python
from mini_agent.config import AppConfig
from mini_agent.llm import LLMClient
from mini_agent.tools import ToolRegistry
```

不建议继续使用根目录式导入：

```python
from config import AppConfig
from llm import LLMClient
```

原因：

- 安装后路径更稳定。
- IDE 和类型检查工具更容易解析。
- 测试环境不会因为当前工作目录不同而出现 import 差异。
- 更容易发现循环依赖。

## 6. 包配置建议

建议新增 `pyproject.toml`，逐步替代单一 `requirements.txt` 的项目配置职责。

最低限度可包含：

```toml
[project]
name = "mini-agent"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "anthropic>=0.34.0",
  "rich>=13.0.0",
  "prompt_toolkit>=3.0",
  "httpx>=0.27",
  "json_repair",
]

[project.scripts]
mini-agent = "mini_agent.cli.app:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

后续可继续加入 ruff、black、mypy / pyright 等工具配置。

## 7. 测试结构建议

当前 `tests/` 已经按能力覆盖了 LLM、prompts、session、orchestrator 等方向。迁移后建议进一步按包结构组织：

```text
tests/
  unit/
    agent/
    cli/
    config/
    llm/
    tools/
    orchestrator/
    storage/
    security/
    ui/
  integration/
    test_agent_tool_loop.py
    test_cli_smoke.py
    test_session_resume.py
```

短期不一定要移动所有测试，但新增测试建议按上述结构放置。

## 8. 配置文件与运行产物建议

### 8.1 配置文件

当前根目录的 `agent_config.json` 容易让人分不清是“默认示例”还是“本地私有配置”。建议：

- 提交 `agent_config.example.json` 作为示例。
- 将真实本地配置 `agent_config.json` 加入 `.gitignore`。
- README 中说明复制方式：

```bash
cp agent_config.example.json agent_config.json
```

### 8.2 运行产物

建议统一运行产物目录，例如：

```text
.agent/
  sessions/
  logs/
  memory.jsonl
  cache/
```

这样比在根目录散落 `sessions/`、`.claude/logs/`、`.agent/memory.jsonl` 更清晰。

## 9. 风险与注意事项

### 9.1 大规模移动文件会影响历史追踪

Git 通常能识别 rename，但如果同时大量修改内容，review 难度会增加。建议迁移 PR 只做路径移动和 import 修改。

### 9.2 Prompt 路径需要特别注意

`prompts/` 当前既是代码模块，又包含大量 Markdown 模板。移动到 `src/mini_agent/prompts/` 后，需要确保：

- `PromptManager` 能正确找到模板根目录。
- 包安装后 Markdown 文件会被包含进发行包。
- 测试覆盖 `list_prompts()` 和 `build_system_prompt()`。

### 9.3 Skill 路径需要保持兼容

项目当前支持项目本地和用户目录下的 skills。迁移代码包时，不应把用户自定义 skills 锁死到包内路径。建议区分：

- 内置示例 Skill：可以放包内或 `examples/skills/`。
- 用户 Skill：继续通过配置或默认路径加载。

### 9.4 入口兼容性

用户可能已经习惯：

```bash
python main.py
```

建议至少保留一个过渡期 shim，并在 README 中同时推荐：

```bash
python -m mini_agent
mini-agent
```

## 10. 建议优先级

### P0：低风险、立刻可做

- 新增本结构改进文档并链接到 README。
- 新增 `pyproject.toml` 草案或在文档中确认目标。
- 更新 README 的项目结构描述，修正 `mini_claude_code` / `mini_agent` 命名不一致。

### P1：中等风险、收益明显

- 引入 `src/mini_agent` 包布局。
- 将根目录 Python 文件移动进包内，但暂不拆内部逻辑。
- 保留 `main.py` shim。
- 统一绝对导入。

### P2：较高风险、适合单独 PR

- 拆分 `main.py` 的 CLI / REPL / slash command。
- 拆分 `agent.py` 的 ContextBuilder、ToolExecutor、HistoryManager。
- 将 UI 层统一到 `mini_agent.ui`。

### P3：长期演进

- 任务、计划、Session、Memory 持久化模型进一步统一。
- 引入更严格的类型检查、格式化和 import lint。
- 建立端到端 CLI smoke test 和 provider mock contract test。

## 11. 推荐迁移路线图

建议按下面顺序拆成多个 PR：

1. **文档 PR**：增加本文档，明确目标结构和迁移顺序。
2. **包布局 PR**：创建 `src/mini_agent`，移动模块和目录，保留 `main.py` shim。
3. **CLI 拆分 PR**：拆出 parser、app、repl、slash commands。
4. **UI 归并 PR**：移动 terminal、renderer、repl_input 到 `ui/`。
5. **Agent 拆分 PR**：拆 ContextBuilder、ToolExecutor、HistoryManager。
6. **配置/存储/安全 PR**：拆 config、storage、security 包。
7. **测试与工具链 PR**：补 pyproject、ruff/black、类型检查、CLI smoke tests。

这样可以让每个 PR 的评审范围明确，避免一次性重构造成难以定位的回归。
