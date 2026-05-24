# mini_agent

> 一个用 Python 实现的简化版 Claude Code，支持多 LLM 提供商、Skill 机制、并发 Sub-Agent 编排和完整的工具调用体系。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 特性

| 特性 | 说明 |
|------|------|
| 🤖 多 LLM 支持 | Anthropic、OpenAI、Ollama、NVIDIA NIM，一行切换 |
| 🔧 系统工具调用 | 工具调用通过 System Prompt 传递，兼容所有模型 |
| 📚 Skill 机制 | SKILL.md 文件动态加载，自动触发注入上下文 |
| 📝 Prompt 管理 | 所有 prompt 统一在 `prompts/` 目录管理 |
| ⚡ 并发 Sub-Agent | 主 Agent 可派生多个子 Agent 并行执行任务 |
| 🔐 权限守卫 | 危险操作需要确认，支持沙箱模式 |
| 🐛 调试日志 | 完整记录每次请求/响应到 JSONL 文件 |

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

# 或者 OpenAI
export OPENAI_API_KEY=sk-...
```

### 运行

```bash
# 交互式模式
python main.py

# 单次命令模式
python main.py "写一个质数筛法的 Python 脚本"

# 使用指定模型
python main.py --model claude-haiku-4-5

# 沙箱模式（安全测试）
python main.py --sandbox
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--model`, `-m` | 指定使用的模型 |
| `--provider` | LLM 提供商：anthropic\|openai\|ollama\|nvidia |
| `--base-url` | 自定义 API 端点 |
| `--agent-name` | Agent 显示名称（默认：orzooo） |
| `--sandbox` | 沙箱模式 |
| `--yes`, `-y` | 自动批准所有工具调用 |
| `--debug-llm` | 启用调试日志 |
| `--workers` | 最大并发子 Agent 数（默认 4） |
| `--max-llm-calls` | 最大并发 LLM 调用数（默认 8） |
| `--session-dir` | Session 文件保存目录 |
| `--resume` | 恢复之前的对话 |

更多参数请使用 `python main.py --help` 查看。

## REPL 命令

进入交互式模式后，支持以下斜杠命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清除对话历史 |
| `/stats` | 显示会话统计 |
| `/skills` | 列出所有技能 |
| `/skill on/off <name>` | 激活/停用技能 |
| `/model <name>` | 切换模型 |
| `/provider list` | 列出所有提供者 |
| `/provider switch <name>` | 切换提供者 |
| `/session list` | 列出历史会话 |
| `/tasks` | 显示子任务状态 |
| `/concurrency` | 查看并发状态 |
| `/compact` | 压缩对话历史 |
| `/prompts` | 列出所有提示词文件 |

## 内置工具

Agent 可以调用以下内置工具：

### 文件操作
- `read_file` - 读取文件内容
- `write_file` - 写入文件
- `create_file` - 创建新文件
- `delete_file` - 删除文件
- `patch_file` - 补丁式编辑文件
- `list_dir` - 列出目录内容
- `glob` - 文件模式匹配
- `grep` - 正则搜索

### Shell 命令
- `bash` - 执行 Shell 命令

### 并发编排
- `spawn_agent` - 派生子 Agent
- `spawn_agents` - 批量派生子 Agent
- `get_task_status` - 查询任务状态
- `list_tasks` - 列出所有任务
- `wait_for_tasks` - 等待任务完成
- `cancel_task` - 取消任务

## 项目结构

```
mini_claude_code/
├── main.py              # CLI 入口和 REPL
├── agent.py             # Agent 核心逻辑
├── config.py            # 配置管理
├── permissions.py       # 权限守卫
├── renderer.py          # 终端输出渲染
├── session.py           # 会话管理
├── repl_input.py        # REPL 输入处理
├── requirements.txt     # 依赖列表
├── README.md            # 项目说明
├── CLAUDE.md            # 开发文档
├── 项目说明.md           # 详细项目说明
├── 项目内置命令与工具信息汇总.md
├── tools/               # 工具系统
│   ├── __init__.py     # 工具注册表
│   ├── builtin.py      # 内置工具
│   └── orchestration.py # 并发编排工具
├── llm/                 # LLM 抽象层
│   ├── base.py         # 基础接口
│   ├── factory.py      # 工厂模式
│   ├── debug_logger.py # 调试日志
│   └── providers/      # LLM 提供商实现
├── orchestrator/        # 并发编排
│   ├── task.py         # 任务定义
│   ├── task_manager.py # 任务调度
│   ├── sub_agent.py    # 子 Agent
│   ├── concurrency.py  # 并发控制
│   └── status_bar.py   # 状态栏
├── prompts/             # 提示词管理
│   ├── system/         # 系统提示词
│   ├── fragments/      # 文本片段
│   └── user/           # 用户消息
├── skills/              # 技能系统
├── .claude/skills/      # 本地技能
├── tests/               # 单元测试
└── docs/                # 文档
```

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    CLI / REPL                          │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    Agent (agent.py)                     │
│   对话历史  ·  工具派发  ·  流式输出                    │
└────┬──────────────┬────────────┬────────────────────────┘
     │              │            │
┌────▼────┐  ┌──────▼──────┐  ┌─▼──────────────────────┐
│ LLM     │  │  Tool       │  │  PromptManager         │
│ 抽象层  │  │  Registry   │  │  + SkillLoader         │
└────┬────┘  └──────┬──────┘  └────────────────────────┘
     │              │
┌────▼────────────────────────────────────────────────┐
│              Provider 实现                          │
│  Anthropic · OpenAI · NVIDIA · Ollama              │
└─────────────────────────────────────────────────────┘
```

## 扩展开发

### 添加新工具

```python
from tools import tool

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

参见 `docs/README.md` 的 "扩展指南" 章节。

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```

## 文档

- [详细技术文档](docs/README.md) - 完整架构、模块详解、数据流
- [项目说明](项目说明.md) - 主要功能和目录结构
- [命令与工具汇总](项目内置命令与工具信息汇总.md) - 所有命令和工具参考

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

---

*最后更新：2026-05-24*
