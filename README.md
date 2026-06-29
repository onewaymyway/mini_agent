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
| 🧠 感知与记忆 | 项目扫描、文件监视、工具缓存、跨 session 长期记忆（含规则触发/反思生成的 Lesson Memory） |
| 👤 用户画像 | 基于长期记忆自动生成技术栈/习惯/偏好画像，注入 system prompt 实现跨 session 个性化 |
| 🌱 自我演化 | agent 可把经验（lesson）提炼为新 skill 并自我提案，全程经过风险分级（T0~T3）安全网 + 隔离验证 + 人工审核 |
| 🗂️ 知识层 | W2 Workdir 层（项目身份证/时间线/跨session待处理线索）+ W3 Global 层（自我画像/跨项目模式/活动日志）自动维护 |
| 🔭 观察性 | traces.jsonl 时序追踪 + `/diagnostics` 健康端点 + k-σ 异常检测 + 工具调用因果链（error_category / resolves_seq）|
| ♻️ Phase G | 后台循环扫描：剪枝候选 + 能力地图 + 跨项目晋升候选，24h 时间门控，`/evolve phase-g` 手动触发 |
| 🔀 SubAgent 降级 | 任务失败时按 `fallback_profiles` 切换 profile、再按 `demotion_scope` 缩小目标，不立即宣告失败 |
| 🌐 HTTP API | 内置 REST/SSE 服务，支持外部程序通过 HTTP 与 agent 交互 |
| 🖥️ Web Demo | Streamlit 图形界面，提供浏览器操作的对话界面 |
| 🔌 MCP 支持 | Model Context Protocol 集成，支持 stdio/SSE 传输，可扩展外部工具服务 |
| 🔍 Web Search | 支持 DuckDuckGo（默认）、Brave、Serper、Tavily 等多种搜索后端 |
| 🤖 自定义子 Agent | 预设角色模板（.agent/agents/*.md），结构化参数注入，支持工具/模型限制 |
| 🔗 Hooks 机制 | 15 个生命周期事件（Session / Prompt / Tool / Subagent / Task / Stop / Compact / TurnEnd），shell 命令自动执行，支持拦截工具调用、监控 SubAgent 终态、阻止不必要的历史压缩，项目级/全局级配置 |
| 🎯 Task 日志实时查看 | 运行时方向键切换查看不同任务日志，状态栏显示任务状态概要 |
| 🖼️ 图片技能 | 图片信息提取与问答（ask_image）、文本生成图片（gen_image_with_text） |
| 📝 Reminder 系统 | 动态提示注入机制，工具出错/用户意图等情境下自动追加解决经验，同轮去重防重复 |
| 🤖 Role Agent | 预设角色子 Agent 模板，结构化参数注入，支持工具/模型限制 |
| 🏃 常驻守护进程 | **Stage 9**：`mini-agent daemon start --detach` 让 agent 常驻后台，CLI/Web 均以"连接模式"接入，不依赖会话存活；PID 文件 + daemon_info.json 管理 |
| 🎯 Goal Backlog | **Stage 9**：跨会话目标层级（Goal → Objective），`.agent/goals.json` 持久化；`/goals accept/reject` 管理 agent 建议目标，关联 WorkThread 复用进展 |
| ⚙️ 三档位自主调度 | **Stage 9**：`passive`（只跑 cron job）/ `maintenance`（Objective 持续执行）/ `autonomous`（软目标 derive + 探索实验），修改 `self_profile.json` 切换 |
| ⏰ 定时任务（Cron） | **Stage 9**：`/cron` 命令管理周期性 daemon 任务；支持 `interval:<秒>` 和 `cron:<5字段>` 两种格式；5 个内置系统 job（phase_g / workdir_sync / self_eval / goal_review / digest_trim） |
| 🔄 Objective 持续执行 | **Stage 9**：ObjectiveExecutor 将 Objective 拆解为 3-8 个 Step 依次提交，步骤间自动传递上下文摘要；SSE 推送 `objective_progress` 事件实时显示进度 |
| 🧭 软目标 Derive | **Stage 9**：autonomous 档位下从三路信号（capability_map 低置信度 / WorkThread 积压 / 高频 Lesson）自动生成 Goal 建议，capability 类先经 ExplorationSandbox 验证再提案 |
| 🔄 Workflow | 工作流编排机制，支持多步骤自动化任务执行 |
| 🌍 Env Info | 环境信息自动采集与注入，内置 OS/Python/时区 Provider，支持自定义扩展 |
| 💾 History 即时落盘 | RawHistory 采用 JSONL 追加写 + fsync，每次操作立即持久化，防崩溃丢失 |
| 🎯 Selective 压缩 | 按 _type 差异化权重评分保留，优先保留用户意图和回复，智能截断工具噪音 |
| 🔁 Resume 提示 | 退出 REPL 时自动显示 resume 命令，方便恢复上次会话 |
| 🔄 LLM 故障转移 | 多配置 fallback chain + 多 API Key 轮转，自动切换保证可用性 |
| ⏳ 智能退避策略 | 重试支持 fixed / linear / exponential 三种退避模式，可配上限 |
| 🚦 RPM 限速 | 滑动窗口频率限制，防止超出平台 RPM 配额 |

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

#### LLM Provider API Key（推荐：providers.json）

复制项目根目录的 `providers.json.example` 为 `providers.json`，填入真实 API Key（已自动加入 `.gitignore`）：

```bash
cp providers.json.example providers.json
```

配置文件结构（详见 `providers.json.example`）：

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
    },
    {
      "provider": "openai",
      "model": "gpt-4o",
      "api_key": "sk-openai-...",
      "key_rotation": "passive"
    }
  ],
  "llm_fallback_on": ["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"],

  "providers": {
    "anthropic": {
      "api_keys": ["sk-ant-key-1-...", "sk-ant-key-2-..."],
      "key_rotation": "round_robin",
      "key_cooldown": 60
    },
    "openai": {
      "api_keys": ["sk-openai-key-1-...", "sk-openai-key-2-..."],
      "key_rotation": "passive"
    },
    "openrouter": {
      "api_key": "sk-or-...",
      "key_rotation": "passive",
      "extra": {
        "http_referer": "https://your-site.com",
        "x_title": "YourAppName"
      }
    }
  }
}
```

> **说明**：`providers` 块为全局 per-provider 设置，会合并到 `llm_fallback_chain` 的对应条目中（条目的显式字段优先）。也可通过 `--providers-config` 指定其他路径。详见 [LLM 故障转移指南](docs/llm-failover-guide.md)。

> **注意**：当前仅有 nvidia、openrouter、agnes 三个 provider 经过实际测试。

#### 图片 Skill API Key（环境变量）

图片相关 Skill 独立于 LLM Provider，需要通过环境变量配置对应的 API Key：

```bash
# ask_image skill — 使用 NVIDIA 视觉模型
export NVIDIA_API_KEY=nvapi-xxx          # Linux / macOS
$env:NVIDIA_API_KEY="nvapi-xxx"           # Windows PowerShell

# gen_image_with_text skill — 使用 Agnes 图片生成服务
export AGNES_API_KEY=agnes-xxx            # Linux / macOS
$env:AGNES_API_KEY="agnes-xxx"             # Windows PowerShell
```

> 这两个 Skill 的 API Key 目前仅支持环境变量方式，不支持 providers.json 配置。

### 运行

```bash
# 交互式模式（推荐）
python -m mini_agent

# 或使用传统入口
python main.py

# 单次命令模式
python -m mini_agent "写一个质数筛法的 Python 脚本"

# 沙箱模式（安全测试）
python -m mini_agent --sandbox

# 简化终端模式（状态栏不再原地刷新，关闭光标控制,如果在某些环境下状态栏显示不正常可以开启这个模式）

python -m mini_agent --simple-mode

# 使用 main.py 启动，更多参数
python main.py --debug-llm --reminder-verbose

# eval 反馈环：对比某个 skill 开启/排除前后的 turns/token/tool 失败率
mini-agent eval --scenario test_cases/ --skill docx
```

### 守护进程模式（Stage 9）

```bash
# 后台启动 daemon（agent 常驻，不依赖 CLI 会话存活）
mini-agent daemon start --detach

# 任意终端"连接"到已运行的 daemon
mini-agent                          # 自动检测到 daemon，进入连接模式

# 查看 daemon 状态（PID、端口、autonomy_level、上次 tick）
mini-agent daemon status

# ── 目标管理 ──────────────────────────────────────────────
# 设置跨会话目标
/goals add "完成认证模块重构" --priority 70
/goals obj add "完成接口层" --goal goal_xxxxxxxx

# 查看自上次交互以来的自主活动（Objective 进展 / Cron 执行 / Agent 建议目标）
/digest

# 接受或拒绝 Agent 建议的软目标
/goals accept goal_abc123           # 接受，提升优先级
/goals reject goal_abc123           # 拒绝，30 天内不再建议相同主题

# ── 定时任务 ──────────────────────────────────────────────
/cron list                          # 查看所有 cron job（含 5 个内置系统 job）
/cron status                        # 下次触发时间总览
/cron run sys:phase_g               # 立即触发一次 Phase G 扫描
/cron disable sys:workdir_sync      # 临时关闭 workdir 同步
/cron add "日报" "cron:0 9 * * *" "生成昨日工作摘要"  # 添加用户 job

# ── 切换自主档位 ──────────────────────────────────────────
# passive（默认）：只跑 cron job
# maintenance：cron + Objective 持续执行
# autonomous：maintenance + 软目标 derive + 探索实验
# 修改 .agent/self_profile.json：
# "operating_state": { "autonomy_level": "maintenance" }

# 停止 daemon
mini-agent daemon stop
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--model`, `-m` | 指定使用的模型 |
| `--provider` | LLM 提供商：`anthropic`\|`openai`\|`ollama`\|`nvidia` |
| `--base-url` | 自定义 API 端点 |
| `--agent-name` | Agent 显示名称（默认：orzooo） |
| `--sandbox` | 沙箱模式 |
| `--simple-mode` | 简化显示模式：适用于 Termux 等光标控制支持不完整的终端。关闭所有 ANSI 光标定位/擦除操作，状态栏完全不显示，其余输出改为顺序追加打印（也可用环境变量 `MINI_AGENT_SIMPLE_MODE=1` 开启），详见 [终端显示机制深度解析](docs/terminal-display-internals.md#九-simple-mode) |
| `--yes`, `-y` | 自动批准所有工具调用 |
| `--debug-llm` | 启用调试日志 |
| `--max-llm-calls` | 最大并发 LLM 调用数（默认 8） |
| `--max-turns` | 单轮对话最大 agentic turns（即一次用户消息内最多几次 LLM 调用-工具调用循环，默认 50） |
| `--workers` | 最大并发子 Agent 数（默认 4） |
| `--session-dir` | Session 文件保存目录 |
| `--resume` | 恢复之前的对话 |
| `--system-tool-call` | 启用系统工具调用格式 |
| `--memory` | 启用跨 session 记忆（含 Lesson Memory：规则触发/SessionEnd 反思/人类反馈纠正检测，详见 [记忆管理指南](docs/memory-management-guide.md#lesson-memory)）；用户画像功能依赖记忆系统，但本身没有独立 CLI flag，需在 `agent_config.json` 设置 `profile_enabled: true` 单独开启，详见 [用户画像系统指南](docs/user-profile-guide.md) |
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
| `--rpm` | 每分钟最大 LLM 请求数（0 = 不限速，默认 0） |
| `--retry-backoff` | 重试退避模式：`fixed`\|`linear`\|`exponential`（默认 fixed） |
| `--retry-backoff-step` | 退避步长值（linear: 秒数，exponential: 倍数，默认 60） |
| `--retry-backoff-max` | 退避等待上限秒数（0 = 不限制，默认 0） |
| `--providers-config` | providers 配置文件路径（含 API key，默认 providers.json） |
| `--role-agents` | 启用多角色 Agent 协作系统（默认关闭），详见 [Role Agent 指南](docs/role-agents-guide.md) |
| `--role-agents-allow` | 白名单：仅启用指定角色 Agent，逗号分隔（如 `evaluator,coach`） |
| `--role-agents-block` | 黑名单：屏蔽指定角色 Agent，逗号分隔 |
| `--role-agents-dir` | 仅从指定目录加载角色 Agent profile（覆盖默认 `.agent/agents/`） |

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
| `/memory` | 立即后台生成/刷新 session 摘要 + 写入长期记忆 + 刷新用户画像（需 `--memory`） |
| `/profile` | 立即后台刷新用户画像（需 `agent_config.json` 设置 `profile_enabled: true`） |
| `/retry` | 重试上一轮 |
| `/rollback` | 回退上一轮 |
| `/evolution log\|show\|diff\|revert` | 查看/审查/回退自我修改历史（Stage 2 安全网） |
| `/evolve review\|list` | 扫描达标 lesson 并提案/预览新 skill（Stage 3.1） |

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
- `get_task_status` — 查询任务状态（输出超 3000 字符自动截断，返回 `truncated`/`full_length` 提示用 `full=True` 取完整内容）
- `update_task_progress` — 主动记录长任务进度到 `manifest.json`（current_step/steps_done/blockers/decision_log）
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

### 自我演化
- `skill_propose` — 把 lesson 提炼为新 SKILL.md 提案，落在独立 `evolve/` 分支等待人工审核合并（不直接生效）

### 内置 Skill（`.claude/skills/`）

不同于上面的内置工具（Python 函数），Skill 是按需加载的 markdown 知识包，详见 [Skill 系统指南](docs/skill-system-guide.md)：

| Skill | 用途 |
|------|------|
| `ask_image` | 图片信息提取与问答（**不要**用 `read_file` 直接读图片） |
| `gen_image_with_text` | 文本生成图片（text-to-image / image-to-image 编辑） |
| `comic-4panel` | 四格漫画全流程生成：主题构思 → 分镜脚本 → 一次性生成完整漫画图，详见 [四格漫画生成指南](docs/comic-4panel-guide.md) |
| `agent-generator` | 创建符合项目规范的自定义子 agent（`.agent/agents/*.md`） |
| `skill-generator` | 创建符合项目规范的新 SKILL.md 技能文件 |
| `iching_oracle` | 易经智慧顾问，提供人生决策指导 |
| `git-context` | 分析当前工作目录 Git 仓库状态（commit 历史、变更文件、分支、diff） |
| `python-expert` | Python 编码最佳实践助手 |
| `reminder-generator` | 从对话提取可复用经验，生成 reminder 文件，详见 [Reminder 系统指南](docs/reminder-system-guide.md) |

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
│       ├── config/          # 配置管理包
│       │   ├── __init__.py  # 重导出，对外 import 路径不变
│       │   ├── models.py    # 14 个配置 dataclass + AppConfig
│       │   ├── loader.py    # load_config 及加载辅助函数
│       │   └── prompt_builder.py  # build_system_prompt 及辅助函数
│       ├── permissions.py   # 权限守卫
│       ├── session.py       # 会话管理
│       ├── skills/          # 技能加载
│       │   ├── __init__.py
│       │   ├── tracker.py   # 技能使用追踪
│       │   └── usage_detector.py  # 使用检测
│       ├── cli/             # CLI 基础设施
│       │   ├── __init__.py
│       │   ├── app.py       # 应用启动入口（含 daemon 子命令短路、--daemon-mode 处理）
│       │   ├── parser.py    # 参数解析（含 --daemon-mode / --no-daemon 标志）
│       │   ├── repl.py      # REPL 循环（含 /agent /goals /digest 路由）
│       │   ├── daemon.py    # 守护进程管理：start/stop/status、DaemonClient（Stage 9）
│       │   └── commands/    # REPL 命令处理
│       │       ├── __init__.py
│       │       ├── concurrency.py
│       │       ├── plans.py
│       │       ├── providers.py
│       │       ├── sessions.py
│       │       ├── skills.py
│       │       ├── tasks.py
│       │       ├── agents.py
│       │       ├── hooks.py
│       │       ├── evolution.py  # /evolution log|show|diff|revert（Stage 2）
│       │       ├── evolve.py     # /evolve review|list（Stage 3.1）
│       │       ├── eval_cmd.py   # mini-agent eval 子命令入口（Stage 3.2）
│       │       ├── goals.py      # /agent goals 全部子命令：add/obj/done/abandon/accept/reject/pause/progress/status（Stage 9）
│       │       └── cron.py       # /cron 全部子命令：list/status/enable/disable/run/add/remove/set-schedule（Stage 9 Phase 1）
│       ├── llm/             # LLM 抽象层
│       │   ├── __init__.py
│       │   ├── base.py      # 基础接口
│       │   ├── factory.py   # 工厂模式
│       │   ├── retry.py     # 重试策略（退避策略 + 条件框架）
│       │   ├── client_pool.py  # 多配置故障转移 & 多 Key 轮转
│       │   ├── system_tool_call.py  # 工具调用格式
│       │   ├── debug_logger.py  # 调试日志
│       │   └── providers/   # LLM 提供商实现
│       │       ├── __init__.py
│       │       ├── _base_mixin.py
│       │       ├── agnes.py
│       │       ├── anthropic.py
│       │       ├── openai.py
│       │       ├── ollama.py
│       │       ├── openrouter.py
│       │       └── nvidia.py
│       ├── tools/           # 工具系统
│       │   ├── __init__.py  # 工具注册表
│       │   ├── builtin.py   # 内置工具
│       │   ├── orchestration.py  # 并发编排工具
│       │   ├── skill_manager.py  # 技能管理
│       │   ├── plan.py      # 规划工具
│       │   ├── user_input.py  # 用户输入工具
│       │   └── evolution.py # skill_propose 工具（Stage 3.1）
│       ├── evolution/       # 自我演化安全网与生产闭环
│       │   ├── __init__.py
│       │   ├── state_repo.py    # StateRepo：唯一写入入口，风险分级 T0~T3（Stage 2）；initiator T0→T1 上浮（Stage 9）
│       │   ├── validators.py    # 按 tier 升级的验证流水线（Stage 2）
│       │   ├── workspace.py     # EvolutionWorkspace：git worktree 进程级隔离（Stage 2）
│       │   ├── eval_runner.py   # mini-agent eval 核心引擎（Stage 3.2）
│       │   ├── phase_g.py       # Phase G 后台循环：剪枝/能力地图/Scope晋升/节奏治理（Stage 8）
│       │   ├── autonomous_loop.py  # AutonomousLoop：三档位 tick + ExplorationSandbox + SoftGoalDeriver 接入（Stage 9）
│       │   ├── resource_arbiter.py # 资源仲裁 + activity_digest.jsonl + build_digest_summary() 六分组渲染（Stage 9）
│       │   ├── cron_scheduler.py   # CronScheduler：interval/cron 双格式，5 个内置系统 job（Stage 9 Phase 1）
│       │   ├── objective_executor.py # ObjectiveExecutor：Objective 多步持续执行，SSE objective_progress（Stage 9 Phase 2）
│       │   └── soft_goal_deriver.py  # SoftGoalDeriver：capability/workthread/lesson 三路信号软目标 derive（Stage 9 Phase 3）
│       ├── orchestrator/    # 并发编排
│       │   ├── __init__.py
│       │   ├── task.py      # 任务定义（含 manifest.json 写入；TaskStatus.PAUSED Stage 9）
│       │   ├── task_manager.py  # 任务调度
│       │   ├── sub_agent.py # 子 Agent
│       │   ├── concurrency.py  # 并发控制
│       │   ├── status_bar.py  # 状态栏显示
│       │   ├── plan.py      # 执行计划（含 plan_snapshot.json 持久化与恢复）
│       │   ├── plan_display.py  # 计划 UI
│       │   ├── task_display.py  # 任务显示
│       │   └── agent_profiles.py  # 自定义 agent profile
│       ├── perception/      # 感知与记忆
│       │   ├── __init__.py
│       │   ├── project_scanner.py  # 项目结构扫描
│       │   ├── file_watcher.py     # 文件变化监听
│       │   ├── tool_cache.py       # 工具结果缓存
│       │   ├── memory_store.py     # 跨 session 记忆（含 Lesson Memory 字段）
│       │   ├── memory_base.py      # 记忆后端抽象
│       │   ├── memory_factory.py   # 记忆工厂
│       │   ├── lesson_rules.py     # 规则触发引擎（连续失败/拒绝重试成功）
│       │   ├── correction_detector.py  # 人类反馈纠正检测
│       │   ├── lesson_review.py    # lesson 阈值扫描与分组（Stage 3.1，/evolve review）
│       │   ├── token_counter.py    # Token 预估
│       │   ├── goal_backlog.py     # 跨会话目标层级 GoalNode/GoalBacklog，goals.json（Stage 9）
│       │   └── exploration_sandbox.py  # 探索实验沙盒，包装 EvolutionWorkspace（Stage 9）
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
│           └── paths.py     # 路径管理（含 plan_snapshot/manifest 路径方法）
├── apps/                    # Web 应用
│   └── mini_agent_webdemo/ # Streamlit Web Demo
│       └── app.py
├── prompts/                 # 提示词模板（外部）
├── skills/                  # 技能定义（外部）
├── tests/                   # 单元测试
├── docs/                    # 文档
├── sessions/                # 会话历史（生成）
├── mcp_servers/             # MCP 服务器示例
├── scripts/                 # 独立治理脚本（不属于 mini_agent 包）
│   └── protected_paths.py  # 受保护路径清单（T3 治理红线）
├── .agent/                  # 自定义子 agent profiles
│   └── agents/              # profile 文件 (*.md，含 evolution-agent.md，Stage 3.1)
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

### Providers 配置

LLM Provider 的 API Key 和 Fallback Chain 配置详见上方 [配置 API Key → LLM Provider API Key](#llm-provider-api-key推荐providersjson) 章节。

`providers.json` 支持的完整功能：

- **多 API Key 轮转**：`key_rotation` 支持 `round_robin` 模式，`key_cooldown` 控制冷却时间
- **Fallback Chain**：`llm_fallback_chain` 定义故障转移链，`llm_fallback_on` 指定触发条件
- **自定义路径**：通过 `--providers-config` 指定其他配置文件路径

详见 [LLM 故障转移指南](docs/llm-failover-guide.md)。

### 重试退避策略

重试等待时长支持三种退避模式（通过 `--retry-backoff` 或配置文件指定）：

| 模式 | 说明 | 示例（delay=10, step=60） |
|------|------|--------------------------|
| `fixed` | 每次等待固定秒数（默认） | 10s, 10s, 10s |
| `linear` | 每次线性递增 | 10s, 70s, 130s |
| `exponential` | 每次指数递增（step 为倍数） | 10s, 600s, 3600s |

详见 [重试退避指南](docs/retry-backoff-guide.md)。

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```

详见 [单元测试指南](docs/unit-testing-guide.md)。

## 文档

- [系统概览](docs/system-overview.md) — 整体架构和设计思路
- [记忆管理指南](docs/memory-management-guide.md) — **更新**：长期记忆系统，含 Lesson Memory（规则触发/SessionEnd 反思/人类反馈纠正检测）
- [用户画像系统指南](docs/user-profile-guide.md) — **新增**：基于长期记忆自动生成用户画像，注入 system prompt 实现个性化
- [history 类型化设计](docs/history-typed-design.md) — **更新**：`_type` 字段化设计，新增 `user_correction` 类型
- [Task 日志实时查看](docs/task-focus-viewing.md) — **新增**：方向键切换查看任务日志机制
- [权限系统指南](docs/permission-guide.md) — **更新**：权限守卫、白名单、持久化配置，`(e)dit` 接入 Lesson Memory
- [Agent 设计](docs/agent-design.md) — Agent 核心循环与组件详解
- [CLI I/O 机制](docs/cli-io-mechanism.md) — 命令行输入输出流程，HTTP 与命令行协同
- [终端显示机制深度解析](docs/terminal-display-internals.md) — **新增**：线程模型、状态栏控制、三阶段状态机、token 过滤
- [终端 I/O 指南](docs/terminal-io-guide.md) — 终端交互细节
- [命令与工具参考](docs/commands-and-tools-reference.md) — 所有命令和工具
- [Plan 和 Task 指南](docs/plan-and-task-guide.md) — 规划和任务系统，含 `plan_snapshot.json` 持久化与 session 重启恢复
- [SubAgent 机制](docs/subagent-mechanism.md) — Sub-Agent 执行与重试机制详解
- [自定义子 Agent](docs/custom-sub-agents.md) — 预设角色模板，结构化参数注入
- [Hooks 机制](docs/hooks.md) — **更新**：15 个生命周期事件（新增 `PostToolUseFailure`、`PostToolBatch`、`SubagentStart`、`SubagentStop`、`TaskCreated`、`TaskCompleted`、`Stop`、`PreCompact`、`PostCompact`；`SessionStart` 从预留升级为已接入），完整事件时序图与用例
- [Skill 系统指南](docs/skill-system-guide.md) — 技能机制详解
- [代码结构指南](docs/code-structure-guide.md) — 项目结构说明
- [受保护路径清单指南](docs/protected-paths-guide.md) — **新增**：T3 治理红线设计与扩展规则（自我演化基础设施）
- [自我演化安全网指南（Stage 2）](docs/self-evolution-stage2-guide.md) — **新增**：`StateRepo`/验证流水线/`EvolutionWorkspace`/`/evolution` 命令组
- [自我演化 lesson → skill 闭环指南（Stage 3.1）](docs/self-evolution-stage3-1-guide.md) — **新增**：`skill_propose`/`evolution-agent`/`/evolve review`
- [自我演化 eval 反馈环指南（Stage 3.2）](docs/self-evolution-stage3-2-guide.md) — **新增**：`mini-agent eval` with/without-skill 对比
- [自我演化 SubAgent 信息继承指南（Stage 3.3）](docs/self-evolution-stage3-3-guide.md) — **新增**：skill 继承/工具缓存共享/lesson 回流
- [Workdir/Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md) — **新增**：W2 项目知识层（project.json/timeline/open_threads）+ W3 跨项目知识层（self_profile/cross_project_index/activity_log）
- [观察性系统指南（Stage 6）](docs/observability-guide.md) — **新增**：traces.jsonl 时序追踪 / `/diagnostics` 端点 / k-σ 异常检测 / 工具调用因果链（error_category/resolves_seq）
- [Phase G 后台循环指南（Stage 8）](docs/self-evolution-phase-g-guide.md) — **新增**：剪枝候选 / 能力地图 / Scope 晋升候选 / 节奏治理，`/evolve phase-g` 命令
- [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md) — **新增**：常驻守护进程 / Goal Backlog / 三档位 AutonomousLoop / 资源仲裁 / `mini-agent daemon` 命令
- [HTTP API 指南](docs/http-api-guide.md) — REST/SSE 服务使用指南
- [Web Demo 指南](docs/web-demo-guide.md) — Streamlit Web 界面使用
- [MCP 集成指南](docs/mcp-guide.md) — Model Context Protocol 集成
- [Web Search 指南](docs/web-search-guide.md) — Web 搜索功能使用指南
- [图片技能指南](docs/image-skills-guide.md) — 图片识别与生成技能使用指南
- [四格漫画生成指南](docs/comic-4panel-guide.md) — 主题构思到分镜脚本再到成图的全流程 Skill
- [Reminder 系统指南](docs/reminder-system-guide.md) — 动态提示注入机制使用指南
- [单元测试指南](docs/unit-testing-guide.md) — 测试结构、编写规范与运行方式
- [Role Agent 指南](docs/role-agents-guide.md) — EvaluatorAgent/CoachAgent 等框架自动触发的角色 Agent（不同于 `/agents` 命令管理的、由 `spawn_named_agent` 主动调用的自定义子 Agent）
- [Workflow 指南](docs/workflow-guide.md) — 工作流编排机制
- [Env Info 指南](docs/env-info-guide.md) — 环境信息采集与注入，自定义 Provider 扩展
- [LLM 故障转移指南](docs/llm-failover-guide.md) — 多配置 fallback chain + 多 API Key 轮转
- [重试退避指南](docs/retry-backoff-guide.md) — fixed / linear / exponential 退避策略详解

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

---

*最后更新：2026-06-19* — API Key 配置重构：主推 providers.json 管理 LLM API Key，图片 Skill（ask_image / gen_image_with_text）保留环境变量方式

*2026-06 Chunked Compact*：`compact_with_skills()` 增加超限自动切换路径——当历史已超出上下文窗口（`LLMContextWindowError`）时，新的 `_compact_chunked()` 把历史按 turn 边界切成多个 chunk，每 chunk 独立调用 `_llm.chat_with_retry` 生成摘要（完全绕开 `run_turn`），多 chunk 结果再合并为最终摘要；单 chunk / 合并失败均有降级保底；新增 prompt 文件 `compact_chunk_request.md` 和 `compact_merge_request.md`；所有 compact prompt 加强为要求保留工具调用结果摘要、精确文件路径、错误信息等关键成果信息；新增 [compact 设计文档](docs/compact-design.md)

*2026-06 Hooks 扩展*：`KNOWN_EVENTS` 从 7 个扩展至 15 个，按生命周期分组新增：`PostToolUseFailure`（工具函数抛异常时触发）、`PostToolBatch`（一批 tool_calls 全部结束后触发一次，payload 含 `tool_names`/`results`/`error_count`）、`SubagentStart`（SubAgent 进入 RUNNING 状态时）、`SubagentStop`（SubAgent 进入终态时，含 `status`/`error`）、`TaskCreated`（`TaskManager.submit()` 时）、`TaskCompleted`（`_handle_terminal()` 确认终态时，覆盖 DONE/FAILED/CANCELLED）、`Stop`（agentic loop 无工具调用准备结束本轮时，可通过 `context` 注入追加 user 消息）、`PreCompact`（`_auto_compress_history()` 前，exit code 2 可阻止本次压缩）、`PostCompact`（压缩后，payload 含摘要文本）；`SessionStart` 从“预留未接”升级为真正接入（`_init_session` 完成后触发）；`hook_mgr` 引用提升到 batch 级别，避免每次工具调用重复查询；对应更新 `docs/hooks.md`、`CLAUDE.md`、`docs/subagent-mechanism.md`（8.4 节）、`docs/plan-and-task-guide.md`（第 12 节）、`docs/compact-design.md`（Hooks 集成节）

*2026-06 TurnEnd Hook*：`hooks/loader.py` 新增 `TurnEnd` 事件（第 7 个 KNOWN_EVENT），在每轮 Agent 回复完成、等待用户输入之前触发；`HookResult` 新增 `user_input` 字段，`TurnEnd` hook 可返回 `{"user_input": "..."}` 替代真实用户输入，直接驱动下一轮（agent-to-agent 接管 / 自动化测试）；REPL 注入轮以灰色 `dim` 样式显示注入内容；`hooks/runner.py` 跨平台编码修复（全部改为二进制模式 + 显式 UTF-8，解决 Windows GBK `UnicodeEncodeError`；Windows 下 `shlex.split` 改用 `posix=False`）；新增示例 `.agent/hooks/turn_end_notify.py`（终端通知）和 `.agent/hooks/turn_end_auto_reply.py`（队列接管）

*2026-06 自我演化基础设施（Stage 0）*：`config.py` 拆分为 `config/` 包（外部 import 路径不变）；新增 `manifest.json`/`plan_snapshot.json` 任务与计划持久化，支持 session 重启恢复；新增 `update_task_progress` 工具；`get_task_status` 截断时主动提示；新增 `scripts/protected_paths.py` 受保护路径清单（T3 治理红线）

*2026-06 Lesson Memory（Stage 1）*：`MemoryEntry` 新增 8 个 lesson 专属字段（`entry_type`/`trigger`/`outcome`/`root_cause`/`suggested_action`/`confidence`/`occurrence_count`/`source`）；新增四条独立写入路径——规则触发（连续失败/拒绝重试成功，`perception/lesson_rules.py`）、`SessionEnd` hook 真正接入 + LLM 反思生成 lesson、人类反馈纠正检测（`perception/correction_detector.py`）、`(e)dit` 审批编辑接入；`history/entry.py` 新增 `HType.USER_CORRECTION` 类型

*2026-06 自我演化安全网（Stage 2）*：新增 `evolution/` 包——`StateRepo.apply()` 作为所有自我修改的唯一写入入口（风险分级 T0~T3，受保护路径强制升级为 T3）；按 tier 升级的验证流水线（schema/加载校验/lint+单测）；`EvolutionWorkspace` 基于 `git worktree` 的进程级隔离；新增 `/evolution log\|show\|diff\|revert` 命令组，`revert` 自动生成 `source="revert_record"` 的 lesson

*2026-06 lesson → skill 闭环（Stage 3.1）*：新增 `skill_propose` 工具，把 lesson 提炼为 SKILL.md 提案并落在独立 `evolve/` 分支（tier 固定 T1，不直接生效）；新增 `evolution-agent` profile（`.agent/agents/evolution-agent.md`）专职处理提案；新增 `perception/lesson_review.py` 做 lesson 阈值扫描分组；新增 `/evolve review\|list` 命令；修复 fresh-repo（全新项目首次触发演化）边界场景

*2026-06 eval 反馈环（Stage 3.2）*：新增 `mini-agent eval --scenario DIR [--skill NAME]` 子命令，复用 `test_cases/*.txt` 作为回归集，对比某个 skill 开启/排除前后的 turns/token/tool 失败率，输出 JSON 报告；`SkillLoader` 新增 `exclude()` 方法保证排除的 skill 不会被关键词重新激活

*2026-06 SubAgent 信息继承（Stage 3.3）*：`Task` 新增 `active_skills` 字段，spawn 的 SubAgent 自动继承主 agent 当前激活的 skill（thread-local provider 机制，独立 `ToolRegistry` 副本规避重复注册崩溃）；`ToolResultCache` 加锁支持跨 SubAgent 共享，避免重复读取同一文件；SubAgent 结束时触发主 agent memory backend `reload()`，使其产生的 lesson 能被主 agent 检索到
*2026-06-24 自主运行时（Stage 9 / Phase H）*：新增 `cli/daemon.py`（`mini-agent daemon start|stop|status`，PID 文件，`DaemonClient` CLI 连接模式）；新增 `perception/goal_backlog.py`（`GoalNode`/`GoalBacklog`，持久化 `.agent/goals.json`，`has_actionable_work()` / `next_task_description()`）；新增 `evolution/autonomous_loop.py`（三档位 tick：passive/maintenance/autonomous，方法边界物理隔离）；新增 `evolution/resource_arbiter.py`（用户优先 / 路径冲突 / 预算硬限制三条仲裁规则，探索子配额，`activity_digest.jsonl`）；新增 `cli/commands/goals.py`（`/agent goals` 全部子命令，`/goals`，`/digest`）；新增 `perception/exploration_sandbox.py`（探索沙盒，第十二节接口预留）；`InputQueue.enqueue()` / `TurnInfo` / `StateRepo.apply()` / `resolve_tier()` 均加入 `initiator` 字段，T0→T1 自动上浮规则；`TaskStatus.PAUSED` 新值；`/v1/status` 新增 `autonomy_level` / `last_autonomous_tick_at` / `tick_count` 字段
*2026-06 自主运行时 Phase 1（Stage 9 基础架构）*：新增 `evolution/cron_scheduler.py`（CronScheduler，interval/cron 双格式，内置 5 个系统 job：phase_g/workdir_sync/self_eval/goal_review/digest_trim）；`AutonomousLoop._tick_passive()` 改调 CronScheduler；新增 `evolution/objective_executor.py`（ObjectiveExecutor，Objective 拆解为 3-8 步 Step，每步完成自动推进，串行+并发上限保护）；`/cron` CLI 命令（list/status/enable/disable/run/add/remove/set-schedule）注册到 REPL 和 `_COMMANDS` 补全

*2026-06 自主运行时 Phase 2（Stage 9 接入与 API）*：`server.py` `_build_autonomous_loop()` 注入 CronScheduler + ObjectiveExecutor；AgentRunner turn 完成/失败后回调 `ObjectiveExecutor.on_turn_done()`/`on_turn_failed()`（仅 initiator="autonomous"/"cron" 时触发）；`api/models.py` 新增 `OBJECTIVE_PROGRESS` SSE 事件；`bridge.py` 新增 `emit_objective_progress()`；`api/routes.py` 新增 `/v1/autonomous/status`、`/v1/goals` CRUD、`/v1/cron/jobs` CRUD 共 8 个端点；新增 `evolution/soft_goal_deriver.py`（三路信号：capability_map 低置信度 / WorkThread 积压 / 高频 Lesson，每次最多 derive 2 个 Goal）

*2026-06 自主运行时 Phase 3（Stage 9 闭环完善）*：`_tick_autonomous()` 完整接入 ExplorationSandbox——capability 类候选先经探索验证（`_run_capability_exploration()`），成功才写 GoalBacklog + 触发 `skill_propose`，失败静默丢弃；`SoftGoalDeriver` 新增 `derive_candidates()`（返回两类候选不写 GoalBacklog）+ `commit_goals()` 方法；`build_digest_summary()` 重写为六分组渲染（Objective进展/Cron执行记录/探索实验结果/💡Agent建议目标/进化提案/其他），Agent建议目标组内嵌 `/goals accept|reject <id>` 快捷指令；`goals.py` 补全 `accept`（激活 + priority 提升）/`reject`（abandoned + `record_rejected()` 30天去重）子命令