# mini-agent

> 一个用 Python 实现的简化版 Claude Code，支持多 LLM 提供商、Skill 机制、并发 Sub-Agent 编排和完整的工具调用体系。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 项目理念

mini_agent 不追求让 AI 拥有自己的目标，而是持续提高对用户的建模精度、任务自主执行程度、以及自我诊断与改进能力，让用户需要显式交代的比例持续下降——这是唯一可操作的"进化"衡量标准。个人数字代理不是终极目标（能力持续增强的超级 AI 系统）的过渡产物，而是这个能力增强过程获得可验证目标函数和反馈信号的唯一可靠来源；至于"系统自主生成目标"，明确排除在近期和中期规划之外。完整的理念阐述与当前长期规划的优先级方向，详见 [mini_agent 核心理念与长期规划](docs/mini_agent_核心理念与长期规划.md)。

## 特性速览

完整能力清单见下方[文档索引](#文档索引)，这里只列大类：

| 分类 | 能力 |
|------|------|
| 🤖 LLM 与工具 | 多 Provider（Anthropic/OpenAI/Ollama/NVIDIA NIM）+ 故障转移 + 多 Key 轮转；系统工具调用；MCP 集成；Web Search 多后端 |
| 🔧 交互与编排 | Skill 机制、Prompt 管理、并发 Sub-Agent、自定义子 Agent、角色扮演（Persona）、Hooks、Workflow、混合执行（hybrid_exec） |
| 🔐 安全与可观测 | 权限守卫（白/黑名单+沙箱）、调试日志、traces.jsonl 时序追踪、`/diagnostics` 健康端点 |
| 🧠 感知与记忆 | 项目扫描、文件监视、跨 session 长期记忆（Lesson Memory）、用户画像、图书馆式知识索引 / Wiki 式知识库、决策知识提炼 |
| 🌱 自我演化 | Lesson → Skill 提案安全网（T0~T3 风险分级 + 隔离验证 + 人工审核）、巩固循环、效果回填 |
| 🧘 具身智能 | 本体感知、余裕感知、AgentSelfModel、时间加权记忆、认知锚点、自维护健康检查 |
| 🏃 自主运行时 | 常驻守护进程（daemon）、Goal Backlog、三档位自主调度、Cron 定时任务、Objective 持续执行、软目标 Derive |
| 🗞️ 数字分身 | 每日融合日报、主动推荐、决策画像、成长顾问、关注对象与分级通知 |
| 🌐 外部接入 | HTTP API、多用户模式、Web Demo（Streamlit）、微信接入、Kanban 看板 |
| ⚙️ 工程基础 | JSONL 即时落盘、Selective 压缩、智能退避重试、RPM 限速、外部输入网关、环境信息采集 |

## 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/onewaymyway/mini_agent.git
cd mini_agent

# 安装依赖
pip install -r requirements.txt
```

当前项目已经可以在安卓 Termux环境运行了！！！！
本人平时也是在Termux玩，开发的时候才用windows
> 📱 完整的 Termux 环境搭建、运行与稳定性优化指南请参考：[TERMUX_README.md](TERMUX_README.md)

### 配置 API Key

复制项目根目录的 `providers.json.example` 为 `providers.json`，填入真实 API Key（已自动加入 `.gitignore`）：

```bash
cp providers.json.example providers.json
```

```json
{
  "llm_fallback_chain": [
    {
      "provider": "anthropic",
      "model": "claude-opus-4-7",
      "api_keys": ["sk-ant-key-1-...", "sk-ant-key-2-..."],
      "key_rotation": "passive",
      "key_switch_on": ["LLMRateLimitError"],
      "key_cooldown": 60
    }
  ],
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"]
}
```

> 完整字段说明（per-provider 全局设置、多 Key 轮转、fallback chain）见 [LLM 故障转移指南](docs/llm-failover-guide.md)。图片相关 Skill（`ask_image`/`gen_image_with_text`）独立于 LLM Provider，需要单独设置环境变量 `AGNES_API_KEY`。

### 运行

实际使用中最常见的是下面两种方式。两者都建议先设置好环境变量：`PYTHONPATH` 指向 `src/` 目录（保证从任意路径启动都能正确导入 `mini_agent` 包），`TESTPROJ` 指向你要让 agent 工作的项目路径（按需替换为自己的项目路径，也可以省略直接用当前目录）。

**Windows（PowerShell）：**

```powershell
$env:PYTHONPATH = "$PWD/src"
$env:TESTPROJ = "E:/codes/mini_claude_code"
```

**Linux / macOS（bash/zsh）：**

```bash
export PYTHONPATH="$PWD/src"
export TESTPROJ="/home/user/codes/mini_claude_code"
```

#### 方式一：daemon 模式 + 看板管理（推荐日常使用）

Agent 以守护进程常驻后台，通过 Streamlit 看板远程管理目标、Cron、日报等，不依赖某个终端窗口一直开着。

```bash
# 启动 daemon（--project 指定工作目录，--yes 自动批准工具调用）
python main.py daemon start --http-port 8765 --project "$TESTPROJ" --yes

# 查看 daemon 状态
python main.py daemon status --project "$TESTPROJ"

# 结束 daemon
python main.py daemon stop --project "$TESTPROJ"

# 启动看板（--auto-token 自动读取/生成本地 owner token，无需手动填）
streamlit run apps/mini_agent_kanban/app.py -- --auto-token
```

> PowerShell 下把 `"$TESTPROJ"` 换成 `"$env:TESTPROJ"`。

#### 方式二：命令行直接对话（适合调试）

不启动 daemon，直接在当前终端进入交互式对话，便于实时看日志排查问题：

```bash
python main.py --no-daemon --debug-llm --reminder-verbose --yes
```

`--debug-llm` 记录完整请求/响应日志，`--reminder-verbose` 打印 reminder 注入细节，`--yes` 自动批准工具调用（沙箱/生产环境请按需去掉）。

其他常用参数：`--model`/`--provider` 切换模型、`--memory` 启用跨 session 记忆、`--sandbox` 沙箱模式、`--http` 启动 HTTP API、`--simple-mode` 用于 Termux 等终端。完整参数列表见 [命令与工具参考](docs/commands-and-tools-reference.md)。

### 多用户模式

在 daemon 模式基础上，还可以开启多用户模式，多个用户通过各自独立的 token 和角色权限连接到同一个 daemon：

```bash
# 开启多用户模式（每用户独立 token + 角色权限 + Session 隔离）
python main.py daemon start --http --http-multi-user --detach --project "$TESTPROJ"
mini-agent user add --name "小明" --role colleague
```

详见 [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md)、[守护进程多客户端架构指南](docs/daemon-multi-client-guide.md)、[多用户模式指南](docs/multi-user-guide.md)、[Kanban 看板使用指南](docs/kanban-dashboard-guide.md)。

## 核心交互

### REPL 高频命令

进入交互式模式后，支持斜杠命令，例如：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/skills` / `/skill on\|off <name>` | 列出/激活/停用技能 |
| `/model <name>` / `/provider list\|switch <name>` | 切换模型/提供者 |
| `/tasks` / `/tasks dashboard` | 查看并发子任务状态 / 实时看板 |
| `/compact` | 压缩对话历史 |
| `/goal <目标文本>` | 设定目标，多轮自动尝试直至达成 |
| `/memory` / `/profile` | 刷新长期记忆与用户画像 |
| `/digest` / `/next` | 查看自主活动摘要 / 主动推荐 |
| `/evolve review\|consolidate` | 自我演化提案 / 手动触发巩固循环 |
| `/wiki <page-id>\|search\|list` | 浏览/检索 Wiki 式知识库 |
| `/cron list\|add\|run` | 管理定时任务 |

完整命令表（含全部 `/goal`/`/wiki`/`/evolve`/`/agents`/`/role` 子命令与快捷键）见 [命令与工具参考](docs/commands-and-tools-reference.md)。

### 内置工具与 Skill

Agent 可调用的内置工具（Python 函数）按类别分组：文件操作（`read_file`/`write_file`/`patch_file`/`glob`/`grep` 等）、Shell（`bash`）、Web Search、图片处理（`ask_image`/`gen_image_with_text`）、并发编排（`spawn_agent`/`wait_for_tasks` 等）、规划与记忆（`plan`/`compact_history`/`skill_activate` 等）、自我演化（`skill_propose`）。

Skill 则是按需加载的 markdown 知识包（`.claude/skills/`），内置包括 `comic-4panel`（四格漫画生成）、`agent-generator`/`persona-generator`/`skill-generator`（脚手架生成器）、`git-context`、`browser-cdp`（CDP 控制真实浏览器）等。

完整工具/Skill 清单见 [命令与工具参考](docs/commands-and-tools-reference.md)、[Skill 系统指南](docs/skill-system-guide.md)。

## 更多接入方式

除了 CLI/REPL，mini-agent 还支持以下接入渠道：

- **HTTP API**：内置 REST/SSE 服务（`--http`），支持外部程序与 agent 交互，多用户模式下每用户独立 token/角色/Session。详见 [HTTP API 指南](docs/http-api-guide.md)。
- **MCP 集成**：支持 [Model Context Protocol](https://modelcontextprotocol.io/)，`pip install mcp` 后在 `agent_config.json` 的 `mcp_servers` 声明 stdio/SSE server 即可自动连接（项目自带 `time_server` 示例）。详见 [MCP 集成指南](docs/mcp-guide.md)。
- **Web Demo**：`streamlit run apps/mini_agent_webdemo/app.py`，提供浏览器对话界面、权限审批面板、SSE 事件流监控。详见 [Web Demo 指南](docs/web-demo-guide.md)。
- **微信接入**：`python weixin_bot.py` 直接内嵌 mini_agent，每个微信 `openid` 对应独立 Agent 实例，危险操作审批通过微信消息完成。详见 [微信接入指南](docs/weixin-bot-guide.md)。
- **Kanban 看板**：Streamlit 看板，目标管理、Cron 管理、日报/推荐/决策画像可视化。详见 [Kanban 看板使用指南](docs/kanban-dashboard-guide.md)。

## 项目结构与架构

```
mini_agent/
├── main.py / weixin_bot.py     # 传统入口 / 微信机器人启动脚本
├── agent_config.json            # Agent 运行时配置
├── providers.json.example       # LLM Provider 配置样例
├── src/mini_agent/              # 源代码（唯一 Python 包）
│   ├── agent/                   # Agent 主类：对话循环、工具派发、流式输出
│   ├── context_builder.py / tool_executor.py / history_manager.py / session.py
│   ├── config/ · cli/ · llm/ · tools/ · skills/ · hooks/ · mcp/ · web_search/
│   ├── history/ · prompts/ · reminders/ · env_info/ · storage/ · ui/ · api/
│   ├── orchestrator/            # 并发编排（多子 Agent）
│   ├── role_agents/ · ensemble/ · goal_mode/ · workflow/
│   ├── evolution/               # 自我演化安全网与自主运行时
│   ├── perception/               # 感知与记忆（体量最大的子系统，含 behavior/ 行为采集）
│   ├── wiki/                     # Wiki 式知识库（默认优先检索路径）
│   └── proxy/ · network/         # 网络出口管理
├── apps/                        # 独立应用：Web Demo / Kanban / 微信插件
├── android_companion_app/       # Android 伴生 App
├── docs/ · next_doc/ · release_logs/  # 功能文档 / 在研设计 / 版本发布记录
├── test_cases/ · tests/          # 手工回归用例 / pytest 单元测试
└── sessions/                     # 会话历史（运行时生成）
```

mini_agent 的核心是"单 Agent 对话循环"（`agent/core.py` 等，`ContextBuilder`/`ToolExecutor`/`HistoryManager`/`Session` 协作完成组装 Prompt → 调用 LLM → 执行工具 → 压缩历史的单轮闭环），外围逐渐长出多个可独立运作的子系统：并发编排、角色化辅助 Agent、目标模式、工作流引擎、自我演化闭环，以及贯穿始终的感知/记忆层。

完整目录树与分层架构图见 [代码结构指南](docs/code-structure-guide.md)、[系统概览](docs/system-overview.md)、[Agent 设计](docs/agent-design.md)。

## 扩展开发

### 添加新工具

```python
from mini_agent.tools import tool

@tool(
    name="my_tool",
    description="执行某个操作",
    schema={
        "type": "object",
        "properties": {"param": {"type": "string", "description": "参数说明"}},
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

参见 `docs/README.md` 的"扩展"章节。

## 配置

在项目根目录创建 `agent_config.json` 进行自定义配置：

```json
{
  "model": "claude-sonnet-4-6",
  "llm_provider": "anthropic",
  "max_tokens": 8096,
  "max_turns": 20,
  "memory_enabled": true,
  "project_scan_enabled": true,
  "compress": {
    "enabled": true,
    "threshold": 0.7,
    "strategy": "selective"
  }
}
```

`agent_config.json` 里绝大多数配置项（如 `compress`/`autonomy` 等 block）都通过统一的"嵌套 block 通用加载机制"读取——加一个新配置字段，通常只需要在 `src/mini_agent/config/models.py` 里给对应的 dataclass 加一个字段，不需要改配置解析代码。完整规范、决策树、示例代码见 [参数系统指南](docs/param-system-guide.md)；配置系统整体架构见 [配置系统指南](docs/config-guide.md)；LLM Provider 配置见 [LLM 故障转移指南](docs/llm-failover-guide.md)；重试退避策略见 [重试退避指南](docs/retry-backoff-guide.md)；主对话循环之外统一的旁路 LLM 调用入口见 [LLMHelper 使用指南](docs/llm-helper-guide.md)。

## 测试与贡献

```bash
pip install pytest
python -m pytest tests/ -q
```

详见 [单元测试指南](docs/unit-testing-guide.md)。欢迎提交 Issue 和 Pull Request！变更历史见 [release_logs/](release_logs/)。

## 文档索引

- **必读**：[mini_agent 核心理念与长期规划](docs/mini_agent_核心理念与长期规划.md)

**核心机制**
- [系统概览](docs/system-overview.md) · [Agent 设计](docs/agent-design.md) · [CLI I/O 机制](docs/cli-io-mechanism.md)
- [终端显示机制深度解析](docs/terminal-display-internals.md) · [终端 I/O 指南](docs/terminal-io-guide.md)
- [命令与工具参考](docs/commands-and-tools-reference.md) · [代码结构指南](docs/code-structure-guide.md)
- [权限系统指南](docs/permission-guide.md) · [history 类型化设计](docs/history-typed-design.md)
- [Plan 和 Task 指南](docs/plan-and-task-guide.md) · [记事本机制说明](docs/notepad-guide.md)
- [SubAgent 机制](docs/subagent-mechanism.md) · [自定义子 Agent](docs/custom-sub-agents.md)
- [角色扮演（Persona）系统指南](docs/persona-guide.md) · [Hooks 机制](docs/hooks.md)
- [Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](docs/platform-tag-loading-guide.md)
- [运行时自动屏蔽（Auto Quarantine）指南](docs/auto-quarantine-guide.md) · [Skill 系统指南](docs/skill-system-guide.md)
- [Task 日志实时查看](docs/task-focus-viewing.md)

**记忆与知识**
- [记忆管理指南](docs/memory-management-guide.md) · [用户画像系统指南](docs/user-profile-guide.md)
- [图书馆式知识索引指南](docs/library-index-guide.md) · [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md)
- [记忆机制、自我进化机制与具身智能机制完整技术文档](docs/memory-and-self-evolution-complete-reference.md)

**自我演化与自主运行**
- [受保护路径清单指南](docs/protected-paths-guide.md) · [自我演化安全网指南（Stage 2）](docs/self-evolution-stage2-guide.md)
- [lesson → skill 闭环指南（Stage 3.1）](docs/self-evolution-stage3-1-guide.md) · [eval 反馈环指南（Stage 3.2）](docs/self-evolution-stage3-2-guide.md)
- [SubAgent 信息继承指南（Stage 3.3）](docs/self-evolution-stage3-3-guide.md) · [Workdir/Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md)
- [观察性系统指南（Stage 6）](docs/observability-guide.md) · [日志保存机制指南](docs/logging-mechanisms-guide.md)
- [巩固循环 后台循环指南（Stage 8）](docs/self-evolution-consolidation-guide.md) · [自我进化效果回填指南](docs/self-evolution-outcome-tracking-guide.md)
- [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md) · [定时任务完整参考](docs/cron-jobs-reference.md)
- [守护进程多客户端架构指南](docs/daemon-multi-client-guide.md) · [Cron 任务专属执行机制指南](docs/cron-dedicated-execution-guide.md)
- [Goal/Cron 统一调度层指南](docs/unified-scheduler-guide.md) · [Goal 模式指南](docs/goal-mode-guide.md)
- [Goal 与 Cron 绑定指南](docs/goal-cron-binding-guide.md) · [Goal 执行规范指南](docs/goal-execution-spec-guide.md)
- [轮次守门员指南（Turn Judge）](docs/turn-judge-guide.md)
- [每日融合日报指南](docs/daily-digest-guide.md) · [主动推荐排序指南](docs/next-action-advisor-guide.md)
- [决策画像指南](docs/decision-profile-guide.md) · [Kanban 看板使用指南](docs/kanban-dashboard-guide.md)
- [具身智能改进指南](docs/embodied-agent-guide.md) · [用户行为感知系统指南](docs/behavior-perception-guide.md)

**集成与外部接入**
- [HTTP API 指南](docs/http-api-guide.md) · [多用户模式指南](docs/multi-user-guide.md) · [Web Demo 指南](docs/web-demo-guide.md)
- [MCP 集成指南](docs/mcp-guide.md) · [Web Search 指南](docs/web-search-guide.md) · [微信接入指南](docs/weixin-bot-guide.md)
- [图片技能指南](docs/image-skills-guide.md) · [四格漫画生成指南](docs/comic-4panel-guide.md)
- [Reminder 系统指南](docs/reminder-system-guide.md) · [工具结果原始留存与智能摘要指南](docs/tool-result-raw-store-and-smart-summary-guide.md)
- [Role Agent 指南](docs/role-agents-guide.md) · [LLMHelper 使用指南](docs/llm-helper-guide.md)
- [Workflow 指南](docs/workflow-guide.md) · [脚本/LLM/Agent 混合执行系统指南](docs/hybrid-exec-guide.md)
- [Env Info 指南](docs/env-info-guide.md) · [LLM 故障转移指南](docs/llm-failover-guide.md) · [重试退避指南](docs/retry-backoff-guide.md)

**测试**
- [单元测试指南](docs/unit-testing-guide.md)

## 许可证

MIT License
