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
| ⚡ 并发 Sub-Agent | 主 Agent 可派生多个子 Agent 并行执行任务 |
| 🔐 权限守卫 | 危险操作需要确认，支持沙箱模式 |
| 🐛 调试日志 | 完整记录每次请求/响应到 JSONL 文件 |
| 🧠 感知与记忆 | 项目扫描、文件监视、工具缓存、跨 session 长期记忆 |

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
# Anthropic API
export ANTHROPIC_API_KEY=sk-ant-...

# 或 OpenAI
export OPENAI_API_KEY=sk-...
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

更多参数请使用 `python -m mini_agent --help` 查看。

## REPL 命令

进入交互式模式后，支持以下斜杠命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清除对话历史 |
| `/stats` | 显示会话统计 |
| `/skills` | 列出所有技能 |
| `/skill on\|off <name>` | 激活/停用技能 |
| `/model <name>` | 切换模型 |
| `/provider list\|switch <name>` | 列出/切换提供者 |
| `/session list\|save\|load` | 管理会话 |
| `/tasks` | 显示子任务状态 |
| `/concurrency` | 查看并发状态 |
| `/compact` | 压缩对话历史 |
| `/prompts` | 列出所有提示词文件 |
| `/retry` | 重试上一轮 |
| `/rollback` | 回退上一轮 |

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
│       ├── agent.py         # Agent 核心逻辑
│       ├── config.py        # 配置管理
│       ├── permissions.py   # 权限守卫
│       ├── session.py       # 会话管理
│       ├── skills/          # 技能加载
│       │   └── __init__.py
│       ├── cli/             # CLI 基础设施
│       │   ├── app.py       # 应用启动入口
│       │   ├── parser.py    # 参数解析
│       │   ├── repl.py      # REPL 循环
│       │   └── commands/    # REPL 命令处理
│       ├── llm/             # LLM 抽象层
│       │   ├── base.py      # 基础接口
│       │   ├── factory.py   # 工厂模式
│       │   ├── retry.py     # 重试策略
│       │   ├── system_tool_call.py  # 工具调用格式
│       │   └── providers/   # LLM 提供商实现
│       │       ├── anthropic.py
│       │       ├── openai.py
│       │       ├── ollama.py
│       │       └── nvidia.py
│       ├── tools/           # 工具系统
│       │   ├── __init__.py  # 工具注册表
│       │   ├── builtin.py   # 内置工具
│       │   ├── orchestration.py  # 并发编排工具
│       │   ├── plan.py      # 规划工具
│       │   ├── user_input.py  # 用户输入工具
│       │   └── skill_manager.py  # 技能管理
│       ├── orchestrator/    # 并发编排
│       │   ├── task.py      # 任务定义
│       │   ├── task_manager.py  # 任务调度
│       │   ├── sub_agent.py # 子 Agent
│       │   ├── concurrency.py  # 并发控制
│       │   └── status_bar.py  # 状态栏显示
│       ├── perception/      # 感知与记忆
│       │   ├── project_scanner.py  # 项目结构扫描
│       │   ├── file_watcher.py     # 文件变化监听
│       │   ├── tool_cache.py       # 工具结果缓存
│       │   ├── memory_store.py     # 跨 session 记忆
│       │   └── token_counter.py    # Token 预估
│       └── ui/              # 用户界面
│           └── renderer.py  # 终端输出渲染
├── prompts/                 # 提示词模板
│   ├── system/             # 系统提示词
│   ├── fragments/          # 文本片段
│   └── user/               # 用户消息
├── skills/                  # 技能定义
├── tests/                   # 单元测试
├── docs/                    # 文档
└── sessions/                # 会话历史（生成）
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
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │   LLM    │  │   Tool   │  │   Skill  │             │
│  │ Client   │  │ Registry │  │  Loader  │             │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
└───────┼─────────────┼─────────────┼────────────────────┘
        │             │             │
┌───────▼─────────────▼─────────────▼──────────────────┐
│              Perception Layer                        │
│  项目扫描 · 文件监听 · 工具缓存 · 长期记忆           │
└──────────────────────────────────────────────────────┘
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
  "tool_cache_enabled": true
}
```

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```

## 文档

- [完整文档](docs/README.md) — 完整架构和模块详情
- [快速开始](docs/QUICK_START.md) — 入门指南
- [工具参考](docs/TOOL_REFERENCE.md) — 所有内置工具

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

---

*最后更新：2026-06-05*
