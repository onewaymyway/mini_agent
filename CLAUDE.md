# mini-agent

一个用 Python 实现的简化版 Claude Code，支持技能机制。

## 项目结构

- `src/mini_agent/agent.py` — Agent 核心逻辑（对话循环、工具派发、流式输出）
- `src/mini_agent/config.py` — 配置管理和系统提示词构建
- `src/mini_agent/permissions.py` — 工具调用的权限守卫
- `src/mini_agent/session.py` — 会话管理
- `src/mini_agent/tools/__init__.py` — 工具注册表和 `@tool` 装饰器
- `src/mini_agent/tools/builtin.py` — 内置工具（bash、文件 I/O、搜索）
- `src/mini_agent/tools/orchestration.py` — 并发编排工具
- `src/mini_agent/skills/__init__.py` — 技能发现和加载
- `src/mini_agent/cli/app.py` — CLI 应用入口
- `src/mini_agent/cli/parser.py` — 参数解析
- `src/mini_agent/cli/repl.py` — REPL 交互循环
- `src/mini_agent/llm/` — LLM 抽象层
- `src/mini_agent/orchestrator/` — 并发编排
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
- 关键功能都应该在/tests 下有对应的单元测试
- 系统性的测试案例放在 /test_cases 下

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

### 工具系统 (`src/mini_agent/tools/`)

- `__init__.py` — 工具注册表，`@tool` 装饰器
- `builtin.py` — 内置工具（读/写文件、bash、grep、glob 等）
- `orchestration.py` — 并发编排工具（spawn_agent, task 管理）
- `skill_manager.py` — 技能管理工具（skill_list, skill_activate 等）

### 并发编排 (`src/mini_agent/orchestrator/`)

- `task.py` — 任务定义
- `task_manager.py` — 任务调度
- `sub_agent.py` — 子 Agent 实现
- `concurrency.py` — 并发控制
- `status_bar.py` — 状态栏显示

### 感知与记忆 (`src/mini_agent/perception/`)

- `project_scanner.py` — 项目结构扫描
- `file_watcher.py` — 文件变化监听
- `tool_cache.py` — 工具结果缓存
- `memory_store.py` — 跨 session 长期记忆
- `token_counter.py` — Token 预估

### CLI (`src/mini_agent/cli/`)

- `app.py` — 应用启动装配（解析参数、初始化组件、启动 REPL）
- `parser.py` — CLI 参数定义
- `repl.py` — REPL 循环和斜杠命令处理
- `commands/` — REPL 命令处理器（concurrency, plans, sessions, skills, tasks 等）
