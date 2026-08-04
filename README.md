# mini-agent

> 一个用 Python 实现的简化版 Claude Code，支持多 LLM 提供商、Skill 机制、并发 Sub-Agent 编排和完整的工具调用体系。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 项目理念

mini_agent 不追求让 AI 拥有自己的目标，而是持续提高对用户的建模精度、任务自主执行程度、以及自我诊断与改进能力，让用户需要显式交代的比例持续下降——这是唯一可操作的"进化"衡量标准。个人数字代理不是终极目标（能力持续增强的超级 AI 系统）的过渡产物，而是这个能力增强过程获得可验证目标函数和反馈信号的唯一可靠来源；至于"系统自主生成目标"，明确排除在近期和中期规划之外。完整的理念阐述与当前长期规划的优先级方向，详见 [mini_agent 核心理念与长期规划](docs/mini_agent_核心理念与长期规划.md)。

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
| ♻️ 巩固循环 | 后台循环扫描：剪枝候选 + 能力地图 + 跨项目晋升候选 + 知识巩固（分类树生长/合并、实体摘要重写、实体去噪合并），24h 时间门控，`/evolve consolidate` 手动触发 |
| 📚 图书馆式知识索引 | 分类树自动生长/合并（书架结构）+ 实体目录（冲突检测/近重复合并）+ 两步检索（先定位书架再精排）+ 检索反馈 + 人类纠正→定位旧知识→标记过时闭环 + 知识生命周期时间线查询，`/evolve timeline` 命令 |
| 🕸️ Wiki 式知识库 | 图书馆式索引的平行实现，**已切换为默认优先检索路径**：md 页面存储（frontmatter + 显式关系图）+ 双写镜像 + 三段式检索（规则粗筛→多跳衰减图扩展→LLM精排）+ 专题页自动生成与再巩固追加（陈旧专题页自动标注过时）+ 统一知识生命周期状态机（fresh/stale/superseded）+ 索引复用与信度分层 + 实体摘要反哺抽取 + 抽取与 compact 解耦（新增 `entity_density` 独立触发信号）+ 巩固分步超时熔断 + 知识缺口主动扫描 + 兜底页归并清理 + 图书馆式索引下线评估（只读评估+三步执行清单，不自动删代码），`/wiki` 命令组（含 `lifecycle-scan`/`gap-scan`/`fallback-cleanup`，均已接入交互式终端 Tab 补全） |
| ⚖️ 决策/取舍知识提炼 | compact 时顺带提炼"考虑过哪些方案/最终选了什么/为什么否决其它方案"结构化决策候选，入队等巩固循环批量合并落盘为 `wiki/decisions/*.md`（命中已有决策则更新，方案变了则推翻旧页新建并双向 `supersedes`/`superseded_by` 串联沿革链），`/evolve review` 生成新提案前自动召回相关历史决策提醒 evolution-agent 避免重复论证或重蹈被否决的方案 |
| 🧘 本体感知 | ProprioceptionModule：认知负荷/不确定性/风险感知/剩余预算/挫败感轮间快照，frustration 累积触发元认知提示 |
| 🧭 余裕感知 | AffordanceMap：session 级交叉分析未完成线索/能力地图/经验，生成"当前环境行动机会"摘要注入 system prompt |
| 🪄 工具透明性 | IntentActionMapper：工具调用按意图（探索/代码编辑/测试/环境配置/版本控制等）分组，写入 traces.jsonl，避免原始流水账 |
| 🪞 AgentSelfModel | 聚合本体感知快照 + 余裕地图 + 跨 session 能力评估，澄清与既有三个 profile 概念的语义边界 |
| ⏳ 时间加权记忆 | Lesson 按来源（人类反馈/自我反思/实验验证/回退记录）区分半衰期，反复印证的经验衰减更慢 |
| 📌 认知锚点 | 任务被 Ctrl-C 打断时自动生成"思维状态重建指南"，下次恢复 session 时自动提醒 |
| 🩺 自维护 | 定期健康检查：可能失效的工具 / 过时 skill / 矛盾的经验，生成修复建议写入晨报，不自动修复 |
| 🔀 SubAgent 降级 | 任务失败时按 `fallback_profiles` 切换 profile、再按 `demotion_scope` 缩小目标，不立即宣告失败 |
| 🌐 HTTP API | 内置 REST/SSE 服务，支持外部程序通过 HTTP 与 agent 交互 |
| 👥 多用户模式 | `--http-multi-user`：多用户独立 token + 角色权限（owner/family/colleague/agent/public）+ 独立 Session 隔离；不启用时与现有单用户模式完全兼容 |
| 🖥️ Web Demo | Streamlit 图形界面，提供浏览器操作的对话界面 |
| 🔌 MCP 支持 | Model Context Protocol 集成，支持 stdio/SSE 传输，可扩展外部工具服务 |
| 🔍 Web Search | 支持 DuckDuckGo（默认）、Brave、Serper、Tavily 等多种搜索后端 |
| 🤖 自定义子 Agent | 预设角色模板（.agent/agents/*.md），结构化参数注入，支持工具/模型限制 |
| 🎭 角色扮演（Persona） | 主 agent 自身的人格切换，跨轮持续生效直至 `/role exit`；`.agent/personas/*.md` 配置，支持工具白名单强制拦截，安全边界代码级兜底不可被角色文件覆盖 |
| 🔗 Hooks 机制 | 15 个生命周期事件（Session / Prompt / Tool / Subagent / Task / Stop / Compact / TurnEnd），shell 命令自动执行，支持拦截工具调用、监控 SubAgent 终态、阻止不必要的历史压缩，项目级/全局级配置 |
| 🎯 Task 日志实时查看 | 运行时方向键切换查看不同任务日志，状态栏显示任务状态概要 |
| 🖼️ 图片技能 | 图片信息提取与问答（ask_image）、文本生成图片（gen_image_with_text） |
| 📝 Reminder 系统 | 动态提示注入机制，工具出错/用户意图等情境下自动追加解决经验，同轮去重防重复 |
| 🤖 Role Agent | 预设角色子 Agent 模板，结构化参数注入，支持工具/模型限制 |
| 🏃 常驻守护进程 | **Stage 9**：`mini-agent daemon start --detach` 让 agent 常驻后台，CLI/Web 均以"连接模式"接入，不依赖会话存活；PID 文件 + daemon_info.json 管理 |
| 🎯 Goal Backlog | **Stage 9**：跨会话目标层级（Goal → Objective），`.agent/goals.json` 持久化；`/goals accept/reject` 管理 agent 建议目标，关联 WorkThread 复用进展 |
| ⚙️ 三档位自主调度 | **Stage 9**：`passive`（只跑 cron job）/ `maintenance`（Objective 持续执行）/ `autonomous`（软目标 derive + 探索实验），修改 `self_profile.json` 切换 |
| ⏰ 定时任务（Cron） | **Stage 9**：`/cron` 命令管理周期性 daemon 任务；支持 `interval:<秒>` 和 `cron:<5字段>` 两种格式；8 个内置系统 job（consolidation / wiki_gap_scan / wiki_fallback_cleanup / workdir_sync / self_eval / goal_review / digest_trim / self_maintain） |
| 🔄 Objective 持续执行 | **Stage 9**：ObjectiveExecutor 将 Objective 拆解为 3-8 个 Step 依次提交，步骤间自动传递上下文摘要；SSE 推送 `objective_progress` 事件实时显示进度 |
| 🧭 软目标 Derive | **Stage 9**：autonomous 档位下从三路信号（capability_map 低置信度 / WorkThread 积压 / 高频 Lesson）自动生成 Goal 建议，capability 类先经 ExplorationSandbox 验证再提案 |
| 🗞️ 日报 / 💡 推荐 / 🧭 决策画像 | 每日融合日报（`/digest daily`，行为+目标+git提交）、主动推荐排序（`/next`，停滞目标/注意力错配）、决策画像归纳（`/decision_profile`，默认关闭）；三者开关与阈值均可通过 `agent_config.json` 的 `digest_advisor` 配置块调整，Kanban 看板有对应可视化卡片 |
| 🔄 Workflow | 工作流编排机制，支持多步骤自动化任务执行 |
| 🧪 混合执行（hybrid_exec） | 独立于 workflow 的脚本/LLM/Agent 混合执行系统：探索优先 agent/llm、执行优先脚本、脚本坏了先自愈修复、修不好才降级；脚本仓库版本管理 + 成功率统计 + 自动退役，可独立 `import` 调用，也可作为 `hybrid_step` 接入 workflow |
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
# ask_image skill — 使用 Agnes 视觉模型
export AGNES_API_KEY=agnes-xxx            # Linux / macOS
$env:AGNES_API_KEY="agnes-xxx"             # Windows PowerShell

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

# 每日融合日报（行为分布+目标进展+git提交，与上面的 /digest 是两个不同功能）
/digest daily

# 主动推荐（停滞目标 / 注意力错配排序建议）
/next

# 接受或拒绝 Agent 建议的软目标
/goals accept goal_abc123           # 接受，提升优先级
/goals reject goal_abc123           # 拒绝，30 天内不再建议相同主题

# ── 定时任务 ──────────────────────────────────────────────
/cron list                          # 查看所有 cron job（含 8 个内置系统 job）
/cron status                        # 下次触发时间总览
/cron run sys:consolidation               # 立即触发一次 巩固循环 扫描
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

### 多用户模式

允许多个用户通过独立 token 和角色权限同时连接到同一个 daemon。

```bash
# 启动多用户 daemon（后台常驻）
mini-agent daemon start --http --http-multi-user --detach

# 启动日志中会显示 owner token，形如：
#   🌐  HTTP API server started
#   URL  : http://127.0.0.1:8765/v1
#   Token: abc123...def456      ← owner token
#   👥  Multi-user mode: ON  (above token = owner)

# 用户管理（需要 daemon 正在运行）
mini-agent user list                                    # 查看所有用户和角色
mini-agent user add --name "小明" --role colleague      # 新增用户（打印一次性 token）
mini-agent user add --name "小红" --role family --trust 8
mini-agent user remove u_a1b2c3d4                      # 删除用户
mini-agent user role u_a1b2c3d4 family                 # 修改用户角色
mini-agent user token u_a1b2c3d4                       # 重新生成 token（旧 token 立即失效）
```

**角色权限对比：**

| 角色 | 工具权限 | Token 上限 | 适用场景 |
|------|----------|-----------|----------|
| `owner` | 全部工具 | 200,000 | daemon 启动者，完全控制 |
| `family` | builtin + search | 80,000 | 家人 / 朋友，情感支持为主 |
| `colleague` | builtin + search | 50,000 | 工作相关，专业交流 |
| `agent` | builtin | 30,000 | 其他 AI agent，结构化通信 |
| `public` | 无工具 | 8,000 | 公开访客，只读受限 |

**用户连接方式：**

用户使用分配到的 token 通过 HTTP API 连接：

```bash
# 用户用自己的 token 发送消息
curl -X POST http://127.0.0.1:8765/v1/chat \
  -H "Authorization: Bearer <user-token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'

# 或通过 Web Demo（在界面中填入 token）
streamlit run apps/mini_agent_webdemo/app.py
```

每个用户拥有**独立 Agent 实例、独立对话历史、独立数据目录**，互不干扰。

详见 [多用户模式指南](docs/multi-user-guide.md)。

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
| `--http-token` | HTTP API 认证令牌（多用户模式下为 owner token） |
| `--http-allow-ip` | 允许的 IP 地址列表 |
| `--http-fs-readonly` | 文件系统只读模式 |
| `--http-multi-user` | 启用多用户认证模式（每用户独立 token/角色/Session） |
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

## 微信接入

`weixin_bot.py` 与 `main.py` 同级放在项目根目录，直接内嵌 `mini_agent`，
自动复用 `agent_config.json` / `providers.json` / `skills/`，无需额外配置：

```bash
# 微信网关配置（openclaw）走环境变量，默认读 ~/.openclaw/openclaw.json
export WEIXIN_BASE_URL=...
export WEIXIN_TOKEN=...

python weixin_bot.py [--project <路径>] [--yes] [--no-stream]
```

特点：
- 每个微信 `openid` 对应一个独立 `Agent` 实例，会话与权限白名单互不影响
- 危险工具调用的审批走微信消息（回复 `/yes` `/no` `/always` `/denyalways`），无需守在终端旁
- 支持 `/sessions` `/session` `/status` `/ls` `/cat` `/find` 等管理指令，发 `/help` 查看完整列表

详见 [微信接入指南](docs/weixin-bot-guide.md)（含 `Agent` 首次创建时
的 asyncio 事件循环死锁问题的根因分析与修复记录）。

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
| `/role list\\|use\\|show\\|exit\\|status\\|stats\\|reload` | 角色扮演系统：切换/退出主 agent 的人格设定 |
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
| `/goal <目标文本>` | 设定一个目标，协商验收标准后自动多轮尝试直至达成（需 `goal_mode.enabled`，详见 [Goal 模式指南](docs/goal-mode-guide.md)） |
| `/goal resume [sid]` | 恢复上次未完成的目标（进程被中断后续跑） |
| `/goal list` | 列出所有可恢复的目标任务（可能不止一个，比如多个进程各自 `/goal` 了不同目标都被杀死的场景） |
| `/goal status` | 查看当前 session 的 goal 状态 |
| `/goal cancel` | 清理当前 session 的 goal 状态记录 |
| `/prompts` | 列出所有提示词文件 |
| `/memory` | 立即后台生成/刷新 session 摘要 + 写入长期记忆 + 刷新用户画像（需 `--memory`） |
| `/profile` | 立即后台刷新用户画像（需 `agent_config.json` 设置 `profile_enabled: true`） |
| `/decision_profile [update]` | 查看/更新决策画像（历史技术决策归纳出的价值取向模式，默认关闭 cron，与上一行 `/profile` 无关，见 [决策画像指南](docs/decision-profile-guide.md)） |
| `/digest daily [日期]` | 生成/查看每日融合日报（行为分布+目标进展+git提交，见 [每日融合日报指南](docs/daily-digest-guide.md)） |
| `/next [refresh]` | 查看/重新计算主动推荐（停滞目标+注意力错配排序，见 [主动推荐排序指南](docs/next-action-advisor-guide.md)） |
| `/retry` | 重试上一轮 |
| `/rollback` | 回退上一轮 |
| `/evolution log\|show\|diff\|revert` | 查看/审查/回退自我修改历史（Stage 2 安全网） |
| `/evolve review\|list` | 扫描达标 lesson 并提案/预览新 skill（Stage 3.1） |
| `/evolve consolidate [--dry-run]` | 手动触发 巩固循环 后台维护（剪枝候选/能力地图/晋升候选/知识巩固/决策候选批量落盘，Stage 8） |
| `/evolve timeline --entity <id>\|--category <code> [--limit N]` | 查询知识生命周期编年目录（图书馆式索引） |
| `/wiki <page-id>` | 浏览 wiki 页面：frontmatter 概要 + 正文 + backlinks |
| `/wiki list [--type T]` \| `/wiki search <query> [--deep]` \| `/wiki rebuild [--full]` | 列出/三段式检索（`--deep` 强制多跳图扩展）/重建 wiki 式知识库索引（已是默认优先检索路径，图书馆式索引兜底） |
| `/wiki stats` \| `/wiki promotion` \| `/wiki lifecycle-scan [--days N]` | 内容来源与生命周期状态分布 / 转正评估（P4，只读；`promotion.py` 达标后需人工确认才由 `/evolve consolidate` 收尾提示下线清单） / 手动巡检标记久未验证的 stale 页面（O4） |
| `/wiki gap-scan [--max-results N] [--dispatch]` | 知识缺口主动扫描（浅层实体/孤儿页面/陈旧专题页），规则扫描零 LLM 成本；不传 `--dispatch` 只打印报告，传了则把每条缺口包装成任务提交进 daemon 的 `InputQueue`（交互式 CLI 无该队列，会提示而非报错） |
| `/wiki fallback-cleanup [--days N]` | 对超过 N 天（默认 30）未处理的 `session-facts` 兜底页重新判重，命中合并、未命中标 `stale` |
| `/debug system\|history\|all\|save` | 打印/导出当前 system prompt 与 history，便于分析调试 |

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
| `persona-generator` | 创建符合项目规范的角色扮演 persona（`.agent/personas/*.md`），可通过 `/role use` 激活 |
| `skill-generator` | 创建符合项目规范的新 SKILL.md 技能文件 |
| `iching_oracle` | 易经智慧顾问，提供人生决策指导 |
| `git-context` | 分析当前工作目录 Git 仓库状态（commit 历史、变更文件、分支、diff） |
| `python-expert` | Python 编码最佳实践助手 |
| `reminder-generator` | 从对话提取可复用经验，生成 reminder 文件，详见 [Reminder 系统指南](docs/reminder-system-guide.md) |
| `browser-cdp` | 通过 Chrome DevTools Protocol (CDP) 控制真实 Chrome/Edge 浏览器：打开网页、抓取内容、截图标注、模拟点击输入、执行 JS，详见 [Skill 系统指南](docs/skill-system-guide.md) |

## 项目结构

```
mini_agent/
├── main.py                  # 传统入口（兼容 shim）
├── weixin_bot.py             # 微信机器人独立启动脚本
├── pyproject.toml            # 项目元数据
├── requirements.txt          # 依赖列表
├── README.md / CLAUDE.md / Agent.md / TERMUX_README.md  # 说明与开发规范
├── agent_config.json          # Agent 运行时配置（模型/权限/记忆等）
├── behavior_config.json       # 行为感知采集配置
├── providers.json.example     # LLM Provider 配置样例
├── src/                       # 源代码（唯一 Python 包 mini_agent）
│   └── mini_agent/
│       ├── __init__.py / __main__.py / _version.py
│       ├── agent/               # Agent 主类（对话循环、工具派发、流式输出；Stage 12 起按职责拆分为包）
│       │   ├── __init__.py      # 重导出 Agent，对外导入路径不变
│       │   ├── core.py          # Agent 类骨架 + __init__ + 各 Mixin 组装
│       │   ├── _helpers.py      # 模块级共享辅助函数（锁上下文/JSON 解析等）
│       │   ├── lifecycle.py     # SessionLifecycleMixin：会话生命周期
│       │   ├── reflection.py    # ReflectionMixin：会话结束反思流水线
│       │   ├── profile.py       # ProfileMixin：用户画像/摘要
│       │   ├── llm_control.py   # LLMControlMixin：LLM 客户端与 Provider/模型切换
│       │   ├── turn_loop.py     # TurnLoopMixin：对话主循环
│       │   ├── role_judge.py    # RoleJudgeMixin：角色 Agent 联动与轮次判定
│       │   ├── reminders_correction.py  # RemindersCorrectionMixin：提醒注入与纠正检测
│       │   ├── compaction.py    # CompactionMixin：历史压缩
│       │   └── snapshot.py      # SnapshotMixin：轮次快照/重试/回滚
│       ├── context_builder.py  # System prompt 构建
│       ├── tool_executor.py    # 工具执行（权限 + 调用 + 截断 + 缓存）
│       ├── history_manager.py  # 历史管理（压缩/快照，委托 history/ 包）
│       ├── session.py          # 会话生命周期管理
│       ├── permissions.py      # 权限守卫（approve/deny/规则持久化）
│       ├── platform_filter.py  # 平台标签过滤（skill/prompt 按平台加载）
│       ├── profile.py          # 用户画像模型
│       ├── errors.py           # 统一异常类型
│       ├── time_utils.py       # 时间工具
│       │
│       ├── config/             # 配置管理：models / loader / prompt_builder
│       ├── cli/                # CLI 基础设施：app / parser / repl / daemon
│       │   └── commands/       # REPL 子命令（20+ 个，如 goals/cron/evolve/eval_cmd 等）
│       ├── llm/                # LLM 抽象层：base / factory / retry / client_pool
│       │   └── providers/      # anthropic / openai / ollama / openrouter / nvidia / agnes
│       ├── tools/               # 工具系统：builtin / orchestration / plan / evolution 等
│       ├── skills/              # 技能加载：tracker / usage_detector
│       ├── hooks/               # hooks 机制：loader / runner
│       ├── mcp/                 # MCP 支持：config / transport / manager
│       ├── web_search/          # Web 搜索抽象层
│       │   └── providers/       # brave / duckduckgo / serper / tavily
│       ├── history/             # 历史管理：compression / raw_history / entry
│       ├── prompts/             # Prompt 管理：manager + system/fragments/user 模板
│       ├── reminders/           # 情境提醒：generator / loader / manager / matcher
│       ├── env_info/            # 环境信息采集：base / registry / providers
│       ├── storage/             # 存储层：paths（含各子系统落盘路径）/ artifacts
│       ├── ui/                  # 终端界面：terminal / renderer / repl_input / raw_key_listener
│       ├── api/                 # HTTP API 服务：server / routes / bridge / auth /
│       │                        #   multi_auth / session_pool / user_store / fs_helper
│       │
│       ├── orchestrator/        # 并发编排（多子 Agent 并行执行任务）
│       │   ├── task.py / task_manager.py / sub_agent.py / concurrency.py
│       │   ├── plan.py / plan_display.py / task_display.py / status_bar.py
│       │   └── agent_profiles.py / persona_profiles.py
│       ├── role_agents/         # 角色化辅助 Agent（新增）
│       │   ├── dispatcher.py    # 角色 Agent 统一调度入口
│       │   ├── turn_judge.py    # 轮次质量判定
│       │   ├── goal_judge.py    # 目标达成判定
│       │   ├── evaluator.py / coach.py / feedback.py
│       │   └── model_resolution.py  # 角色到模型的解析
│       ├── ensemble/             # Best-of-N 集成推理（新增）
│       │   ├── runner.py / strategies.py / decision.py / judge.py / types.py
│       ├── goal_mode/            # 目标模式（新增，长程任务自动执行）
│       │   ├── spec.py / runner.py / executor.py / state.py / _compat.py
│       ├── workflow/             # 可视化/可复用工作流引擎（新增）
│       │   ├── schema.py / generator.py / runner.py / store.py / tools.py
│       ├── evolution/            # 自我演化安全网与生产闭环
│       │   ├── state_repo.py / validators.py / workspace.py / eval_runner.py
│       │   ├── consolidation.py / autonomous_loop.py / resource_arbiter.py
│       │   ├── cron_scheduler.py / objective_executor.py / soft_goal_deriver.py
│       │   ├── memory_aging.py / memory_consolidation.py / outcome_tracker.py
│       │   └── lesson_to_reminder.py / self_maintenance.py
│       ├── perception/           # 感知与记忆（体量最大的子系统，30+ 模块）
│       │   ├── project_scanner.py / file_watcher.py / tool_cache.py
│       │   ├── memory_store.py / memory_base.py / memory_factory.py / hybrid_memory_backend.py
│       │   ├── lesson_rules.py / lesson_review.py / correction_detector.py / format_correction_detector.py
│       │   ├── token_counter.py / goal_backlog.py / exploration_sandbox.py
│       │   ├── system_events.py / proprioception.py / affordance_analyzer.py / affordance_calibration.py
│       │   ├── self_model.py / classification.py / entity_index.py / catalog.py / library_index.py
│       │   ├── global_knowledge.py / workdir_knowledge.py / intent_action_mapper.py
│       │   ├── artifact_detector.py / hot_reload.py / observability.py / privacy_guard.py
│       │   └── behavior/         # 行为感知子包（新增：跨端行为采集）
│       │       ├── manager.py / analyzer.py / events.py / config.py / mobile_setup.py
│       │       └── collectors/   # active_window / app_lifecycle / idle / now_playing / cdp_browser 等
│       ├── wiki/                 # Wiki 式知识库（图书馆式索引的平行实现，现为默认优先检索路径）
│       │   ├── parser.py / graph.py / indexer.py / writer.py / validator.py
│       │   ├── migration.py / dedup.py / search.py / topics.py / decision_writer.py
│       │   └── _templates/       # entity/decision/process/experience/topic 五种页面模板
│       ├── proxy/                # 代理池（新增：科学上网/网络出口管理）
│       │   ├── pool.py / service.py / local_proxy.py / external_engine.py
│       │   ├── integration.py / subscription.py / validator.py / xray_runner.py
│       │   └── protocols/        # shadowsocks / trojan / vless
│       └── network/              # 网络连通性检测（新增）
│           └── connectivity.py
│
├── apps/                       # 独立应用（与 mini_agent 包解耦）
│   ├── mini_agent_webdemo/     # Streamlit Web Demo
│   ├── mini_agent_kanban/      # Kanban 任务看板（Flask app + 多用户 auth）
│   └── weixin_plugin/          # 微信个人号插件（登录/编解码/消息处理）
├── android_companion_app/      # Android 伴生 App（行为采集/地理围栏，Kotlin）
├── browser_extension_example/  # 浏览器扩展示例（配合 behavior 感知）
├── mcp_servers/                 # MCP 服务器参考实现（如 time_server.py）
├── scripts/                     # 独立治理脚本（不属于 mini_agent 包）
│   ├── protected_paths.py       # 受保护路径清单（T3 治理红线）
│   └── proxy_ctl.py             # 代理池命令行控制
├── .agent/                      # 自定义子 Agent / Persona / Hook 定义
│   ├── agents/                  # 子 Agent profile（*.md）
│   ├── personas/                # 人格 persona 定义（*.md）
│   ├── hooks/                   # hook 脚本
│   └── workflows/                # workflow YAML 定义
├── .claude/skills/               # Claude Code Skill 集合（浏览器自动化/图片/技能生成器等）
├── docs/                         # 功能设计文档（80+ 篇）
├── next_doc/                     # 在研 / 规划中特性的设计文档
├── release_logs/                 # 版本发布记录
├── test_cases/                   # 手工/回归测试用例脚本
├── tests/                        # pytest 单元测试
└── sessions/                     # 会话历史（运行时生成）
```

## 架构设计

mini_agent 的核心仍是"单 Agent 对话循环"，但在其外围逐渐长出了多个可独立运作的子系统：角色化辅助 Agent、并发编排、目标模式、工作流引擎、自我演化闭环，以及贯穿始终的感知/记忆层。整体可以分为四层：

```
┌──────────────────────────────────────────────────────────────────────┐
│  入口层：CLI / REPL │ Daemon(常驻进程 + 多客户端) │ HTTP API │ Web/微信/Kanban│
└───────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────▼───────────────────────────────────┐
│                       Agent 核心（agent/core.py 等）                 │
│            对话循环 · 工具派发 · 流式输出 · 权限确认                 │
│  ┌──────────────┐ ┌─────────────┐ ┌────────────┐ ┌───────────────┐ │
│  │ContextBuilder│ │ToolExecutor │ │HistoryMgr  │ │  Session      │ │
│  │(System Prompt│ │(权限+调用+  │ │(压缩/快照/ │ │ (会话生命周期) │ │
│  │ 组装)        │ │ 截断+缓存)  │ │ Raw History)│ │               │ │
│  └──────────────┘ └─────────────┘ └────────────┘ └───────────────┘ │
└──┬───────────┬────────────┬──────────────┬───────────────┬────────┘
   │           │            │              │               │
┌──▼─────┐ ┌──▼────────┐ ┌─▼──────────┐ ┌──▼───────────┐ ┌─▼──────────┐
│LLM 层  │ │Tool 层    │ │Perception  │ │协同 Agent 层  │ │治理/演化层  │
│多      │ │内置工具/  │ │感知与记忆  │ │Orchestrator   │ │Evolution    │
│Provider│ │Skill/MCP/ │ │（30+ 模块，│ │(子Agent并发)  │ │(state_repo/ │
│+故障   │ │WebSearch/ │ │含 behavior │ │Role Agents    │ │ validators/ │
│转移+   │ │Workflow   │ │ 跨端行为   │ │(judge/coach/  │ │ workspace   │
│多Key   │ │工具       │ │ 采集子包)  │ │ dispatcher)   │ │ 隔离)       │
│轮转    │ │           │ │            │ │Ensemble       │ │AutonomousLoop│
│        │ │           │ │            │ │(Best-of-N)    │ │CronScheduler │
│        │ │           │ │            │ │Goal Mode      │ │              │
└────────┘ └───────────┘ └────────────┘ └───────────────┘ └─────────────┘

支撑设施（横向贯穿各层）：
  config（配置） · prompts（提示词模板） · reminders（情境提醒） · hooks（生命周期钩子）
  storage（落盘路径） · env_info（环境信息） · proxy / network（出口网络管理） · ui（终端渲染）
```

关键点说明：

- **入口层**：`cli/app.py` 是统一启动入口，可直连 REPL、或以 `--daemon-mode` 常驻并通过 `DaemonClient` 支持多端接入同一会话；`api/server.py` 暴露 HTTP/SSE 接口供 Web Demo、Kanban 看板、微信插件等外部应用接入。
- **Agent 核心**：`agent/core.py`（Stage 12 前是单文件 `agent.py`，现已按职责拆分为 `agent/` 包，`core.py` 只保留 `__init__` 与骨架，其余方法分散在 `lifecycle.py`/`reflection.py`/`profile.py`/`llm_control.py`/`turn_loop.py`/`role_judge.py`/`reminders_correction.py`/`compaction.py`/`snapshot.py` 等 Mixin 文件中，通过多重继承组装回同一个 `Agent` 类，对外导入路径与行为不变）仍是对话主循环，`ContextBuilder`/`ToolExecutor`/`HistoryManager`/`Session` 四者协作完成"组装 Prompt → 调用 LLM → 执行工具 → 压缩历史"的单轮闭环。
- **协同 Agent 层（新增）**：`orchestrator/` 负责派生并发子 Agent 执行拆解后的任务；`role_agents/` 提供一组轻量角色（turn_judge、goal_judge、evaluator、coach）辅助主 Agent 做质量判定与反馈；`ensemble/` 支持同一请求多路生成后择优；`goal_mode/` 支撑长程目标的自动拆解与持续执行；`workflow/` 则是可复用、可视化的多步骤流程引擎。
- **感知与记忆（Perception）**：是当前体量最大的子系统，除项目扫描、跨 session 记忆、Lesson 规则等基础能力外，新增了 `behavior/` 子包用于跨端（浏览器/Android 伴生 App）行为采集，以及本体感知（`proprioception.py`）、余裕感知（`affordance_analyzer.py`）等自省能力，最终由 `library_index.py`/`self_model.py` 聚合成统一视图。
- **治理与自我演化（Evolution）**：以 `state_repo.py` 作为唯一写入入口、按 T0~T3 风险分级把关，`workspace.py` 用 git worktree 做进程级隔离；`autonomous_loop.py` + `cron_scheduler.py` + `objective_executor.py` 构成自主运行时，能在无人值守时按节奏执行巩固、自评、目标推进等任务。
- **支撑设施**：`proxy/` 和 `network/` 是新增的网络出口管理能力（代理池、连通性检测），`web_search/` 是独立于工具系统之外的搜索 Provider 抽象层，二者都通过 `tools/` 中的工具函数暴露给 Agent 使用。

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
  "max_turns_on_limit": "stop",
  "max_turns_hard_limit": 100,
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

**Compact 触发器开关**（2026-07 新增，均默认关闭，独立配置，见 [Compact 设计文档](docs/compact-design.md)）：

```json
{
  "auto_compress_enabled": true,
  "compact_turn_count_trigger_enabled": true,
  "compact_max_turns": 20,
  "compact_tool_call_count_trigger_enabled": true,
  "compact_max_tool_calls": 50,
  "compact_topic_shift_detection": "heuristic",
  "compact_redundancy_detection_enabled": true,
  "compact_redundancy_tool_result_ratio": 0.6,
  "compact_cooldown_turns": 3,
  "compact_require_confirmation": false
}
```

### 新增配置参数的统一规范

`agent_config.json` 里绝大多数配置项（如上面的 `compress`/自主性调度
`autonomy` 等 block）都通过统一的"嵌套 block 通用加载机制"读取——加一
个新配置字段，通常只需要在 `src/mini_agent/config/models.py` 里给对应
的 dataclass 加一个字段，不需要改配置解析代码。完整规范、决策树（该走
配置文件 block 还是 CLI 参数）、示例代码见
**[参数系统指南](docs/param-system-guide.md)**；配置系统整体架构见
[配置系统指南](docs/config-guide.md)。

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

### 旁路 LLM 调用（LLMHelper）

主对话循环之外的 LLM 调用（judge 评审、ensemble 候选生成、目标自动拆解、记忆摘要重写、路由判定等）统一通过 `LLMHelper`（`src/mini_agent/llm/service.py`）发起：默认跟随 agent 当前正在用的 provider/model（含 `/model` 切换），自带统一重试，需要临时切换模型/温度时可用 `override_model` / `override_provider` / `override_temperature` 显式覆盖。

详见 [LLMHelper 使用指南](docs/llm-helper-guide.md)。

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```

详见 [单元测试指南](docs/unit-testing-guide.md)。

## 文档

- [mini_agent 核心理念与长期规划](docs/mini_agent_核心理念与长期规划.md) — **必读**：终极目标与个人代理场景的关系、"自我进化"的正确定义、长期规划应遵循的理念、接下来的重点方向
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
- [记事本机制说明](docs/notepad-guide.md) — **新增**：常驻 system prompt 的持久便签，`notepad_add`/`update`/`remove`/`summarize` 工具 + `/notepad` 命令，记录关键信息/结果/注意事项且不受 history compact 影响
- [SubAgent 机制](docs/subagent-mechanism.md) — Sub-Agent 执行与重试机制详解
- [自定义子 Agent](docs/custom-sub-agents.md) — 预设角色模板，结构化参数注入
- [角色扮演（Persona）系统指南](docs/persona-guide.md) — **新增**：主 agent 自身的人格切换，`/role` 命令组，`allowed_tools` 强制拦截，安全边界代码级兜底
- [Hooks 机制](docs/hooks.md) — **更新**：15 个生命周期事件（新增 `PostToolUseFailure`、`PostToolBatch`、`SubagentStart`、`SubagentStop`、`TaskCreated`、`TaskCompleted`、`Stop`、`PreCompact`、`PostCompact`；`SessionStart` 从预留升级为已接入），完整事件时序图与用例
- [Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](docs/platform-tag-loading-guide.md) — **新增**：`platforms`/`tags` 声明式限制，`platform_policy.json` 全局策略，`/platform status|filtered|reload` 命令
- [运行时自动屏蔽（Auto Quarantine）指南](docs/auto-quarantine-guide.md) — **新增**：skill/tool/agent 因反复环境不兼容失败被自动拉黑（默认关闭），`runtime_quarantine.json`，`/quarantine status|list|remove|clear|reload|enable|disable` 命令
- [Skill 系统指南](docs/skill-system-guide.md) — 技能机制详解
- [代码结构指南](docs/code-structure-guide.md) — 项目结构说明
- [受保护路径清单指南](docs/protected-paths-guide.md) — **新增**：T3 治理红线设计与扩展规则（自我演化基础设施）
- [自我演化安全网指南（Stage 2）](docs/self-evolution-stage2-guide.md) — **新增**：`StateRepo`/验证流水线/`EvolutionWorkspace`/`/evolution` 命令组
- [自我演化 lesson → skill 闭环指南（Stage 3.1）](docs/self-evolution-stage3-1-guide.md) — **新增**：`skill_propose`/`evolution-agent`/`/evolve review`
- [自我演化 eval 反馈环指南（Stage 3.2）](docs/self-evolution-stage3-2-guide.md) — **新增**：`mini-agent eval` with/without-skill 对比
- [自我演化 SubAgent 信息继承指南（Stage 3.3）](docs/self-evolution-stage3-3-guide.md) — **新增**：skill 继承/工具缓存共享/lesson 回流
- [Workdir/Global 知识层指南（Stage 4 & 5）](docs/self-evolution-stage4-5-guide.md) — **新增**：W2 项目知识层（project.json/timeline/open_threads）+ W3 跨项目知识层（self_profile/cross_project_index/activity_log）
- [观察性系统指南（Stage 6）](docs/observability-guide.md) — **新增**：traces.jsonl 时序追踪 / `/diagnostics` 端点 / k-σ 异常检测 / 工具调用因果链（error_category/resolves_seq）
- [日志保存机制指南](docs/logging-mechanisms-guide.md) — **新增**：系统梳理全项目所有日志/审计流（错误日志/LLM调试日志/daemon控制台日志/traces/行为事件/知识编年目录等）的落盘位置、写入机制与已知缺口
- [巩固循环 后台循环指南（Stage 8）](docs/self-evolution-consolidation-guide.md) — **新增**：剪枝候选 / 能力地图 / Scope 晋升候选 / 节奏治理，`/evolve consolidate` 命令
- [自我进化效果回填指南](docs/self-evolution-outcome-tracking-guide.md) — **新增**：`skill_propose` commit 落地后的迟滞观察窗口、improved/no_change/worsened 判定、`/evolution outcomes` 命令
- [记忆机制、自我进化机制与具身智能机制完整技术文档](docs/memory-and-self-evolution-complete-reference.md) — **新增**：系统整理全部记忆存储/检索/图书馆式索引机制、自我进化 Stage 0~9 全流程（安全网/巩固循环/效果回填闭环）、具身智能 12 项能力（A1~C4），及三者的交汇点
- [图书馆式知识索引指南](docs/library-index-guide.md) — **新增**：分类树自动生长/合并 + 实体目录（冲突检测/去噪合并）+ 两步检索 + 检索反馈 + 人类纠正→标记过时闭环 + 时间线查询，`/evolve timeline` 命令
- [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) — 图书馆式索引的平行实现（已切换为默认优先检索路径），md 页面存储 + 显式关系图 + 双写镜像 + 三段式检索（规则粗筛→多跳图扩展→LLM精排）+ 专题页自动生成与再巩固 + 统一知识生命周期状态机 + 索引复用/信度分层/实体摘要反哺抽取/抽取与compact解耦（O1-O4、E1-E3 提取层与组织层改进计划）
- [Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md) — **新增**：常驻守护进程 / Goal Backlog / 三档位 AutonomousLoop / 资源仲裁 / `mini-agent daemon` 命令
- [定时任务完整参考](docs/cron-jobs-reference.md) — **新增**：汇总全部 `sys:` 内置 cron job（固定内置 9 个 + 各计划按需补注册的十余个），含每个 job 的目的、触发频率、是否含 LLM 调用、默认启用状态及关联设计文档
- [守护进程多客户端架构指南](docs/daemon-multi-client-guide.md) — **新增**：`DaemonClient`/`SessionAgentPool`/`AgentBridge`（RingBuffer/OutputBroadcaster/InputQueue/PermissionGate）三层架构、多端接入同一会话的消息生命周期、`run_connected_repl` 已连接模式渲染与斜杠命令转发，含当前已知问题排查记录
- [Cron 任务专属执行机制指南](docs/cron-dedicated-execution-guide.md) — **新增**：cron job 到期后的独立后台线程执行通道（不阻塞用户消息）、超时/步数/StuckDetector 卡死检测三重兜底、跨次触发的 progress_summary 续接、每 job 专属文件夹（prompt.md/config.json/state.json/runs/*.jsonl）、`CronConfig` 全局默认+缺省字段实时回退、5 个 REST 端点、看板 "⏰ Cron 任务" tab
- [每日融合日报指南](docs/daily-digest-guide.md) — **新增**：行为分布+目标进展+git提交融合为一份日报（`/digest daily`），`sys:daily_digest` cron job，启动打印摘要
- [主动推荐排序指南](docs/next-action-advisor-guide.md) — **新增**：停滞目标/注意力错配规则层排序（`/next`），可选 LLM 排序层、决策画像加权、持续超时 daemon 主动推送，均可配置开关
- [决策画像指南](docs/decision-profile-guide.md) — **新增**：从历史技术决策归纳可追溯的用户价值取向模式（`/decision_profile`），矛盾证据不覆盖只记录，默认关闭 cron job
- [Kanban 看板使用指南](docs/kanban-dashboard-guide.md) — Streamlit 看板：目标 Kanban / Cron 管理 / 产出物浏览 / 自我状态 / 日报-推荐-决策画像三卡片
- [具身智能改进指南](docs/embodied-agent-guide.md) — **新增**：本体感知（ProprioceptionModule）/ 余裕感知（AffordanceMap）/ 工具透明性（IntentActionMapper）/ AgentSelfModel / 时间加权记忆激活 / 认知锚点文件 / 自维护模块（SelfMaintenanceModule），A/B/C 三阶段共 12 项
- [HTTP API 指南](docs/http-api-guide.md) — REST/SSE 服务使用指南
- [Web Demo 指南](docs/web-demo-guide.md) — Streamlit Web 界面使用
- [MCP 集成指南](docs/mcp-guide.md) — Model Context Protocol 集成
- [Web Search 指南](docs/web-search-guide.md) — Web 搜索功能使用指南
- [图片技能指南](docs/image-skills-guide.md) — 图片识别与生成技能使用指南
- [四格漫画生成指南](docs/comic-4panel-guide.md) — 主题构思到分镜脚本再到成图的全流程 Skill
- [Reminder 系统指南](docs/reminder-system-guide.md) — 动态提示注入机制使用指南
- [工具结果原始留存与智能摘要指南](docs/tool-result-raw-store-and-smart-summary-guide.md) — **新增**：超长工具结果截断后原文留存（`view_raw_result` 回看）+ 可选 LLM 智能摘要（`smart_summary_enabled`），失败自动降级
- [单元测试指南](docs/unit-testing-guide.md) — 测试结构、编写规范与运行方式
- [Role Agent 指南](docs/role-agents-guide.md) — EvaluatorAgent/CoachAgent 等框架自动触发的角色 Agent（不同于 `/agents` 命令管理的、由 `spawn_named_agent` 主动调用的自定义子 Agent）
- [LLMHelper 使用指南](docs/llm-helper-guide.md) — **新增**：主对话循环之外（judge/ensemble/目标拆解/摘要重写/路由判定）的统一 LLM 调用入口，跟随 `/model` 切换 + 统一重试 + `override_*` 逃生舱，含新增旁路调用的检查清单
- [Goal 模式指南](docs/goal-mode-guide.md) — **新增**：设定一个目标，Agent 自动多轮尝试直至达成或触发安全阀，`/goal` 命令，验收标准协商、GoalJudge 判定、异常中断恢复
- [轮次守门员指南（Turn Judge）](docs/turn-judge-guide.md) — **新增**：轮次结束等待用户输入前，自动核查是"真的需要人"还是"技术性卡壳"，后者由系统代替用户反馈继续推进
- [Workflow 指南](docs/workflow-guide.md) — 工作流编排机制
- [脚本/LLM/Agent 混合执行系统指南](docs/hybrid-exec-guide.md) — **新增**：独立于 workflow 的 `hybrid_exec` 系统，脚本优先/坏了先自愈/修不好再降级 LLM/Agent，脚本仓库版本管理+成功率统计+自动退役，可独立调用也可作为 `hybrid_step` 接入 workflow（插件文件开关），`GET /v1/hybrid_exec/summary` + 看板 "🧪 混合执行" Tab，`ReexplorePolicy` 跨 run 主动重探索（默认关闭）
- [Env Info 指南](docs/env-info-guide.md) — 环境信息采集与注入，自定义 Provider 扩展
- [LLM 故障转移指南](docs/llm-failover-guide.md) — 多配置 fallback chain + 多 API Key 轮转
- [重试退避指南](docs/retry-backoff-guide.md) — fixed / linear / exponential 退避策略详解
- [微信接入指南](docs/weixin-bot-guide.md) — **新增**：`weixin_bot.py` 每用户 Agent 隔离 / 远程权限审批 / 同步-异步桥接，含 `_get_or_create` 事件循环死锁问题的根因与修复记录
- [用户行为感知系统指南](docs/behavior-perception-guide.md) — **新增**：桌面（前台窗口/空闲/浏览器插件+CDP专用浏览器/Git/终端/媒体/应用启停）+ 手机（Tasker/快捷指令/Android伴侣App：使用统计/解锁/地理围栏标签/健康聚合）行为采集，总开关与全部子开关默认关闭；分析层每日聚合"工作与生活画像"日报

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

---

*2026-08-04 脚本/LLM/Agent 混合执行系统（hybrid_exec，P1-P4）*：新增独立顶层包 `src/mini_agent/hybrid_exec/`，把"探索优先 agent/llm、执行优先脚本、脚本坏了先自愈修复、修不好才降级"封装成可复用组件——`TaskSpec`/`ExecutionResult`/`ExecutionTier` 三元组数据结构；`ScriptRepository` 做脚本版本存储+成功率统计+连续失败自动退役；`ScriptRunner` 复用 `py_step_runner.py` 的子进程隔离协议不重造沙箱；`LLMExplorer`/`AgentExplorer` 负责从 0 生成脚本（LLM 优先，dry-run 不过才升级 Agent）；`LLMRepairer`/`AgentRepairer` 负责脚本报错后修复；`FallbackExecutor` 在脚本彻底不可用时兜底给答案（不产出脚本）；`HybridExecutor` 是顶层编排器，`default_executor(project_root)` 一行拿到默认配置实例，可完全脱离 workflow 独立 `import` 调用。P2 补齐 Agent 探索/修复（复用 `agent_spawn.build_minimal_agent`，`agent_fs_write_enabled` 默认关闭走只读沙箱）并接入 workflow 新 step 类型 `hybrid_step`——**未修改 workflow 包任何源码**，通过 `register_step_executor()` 公开扩展点 + 薄插件文件 `myplugins/hybrid_step.py` 注册（删除该文件即禁用，不同于 `python_step_enabled` 配置开关模式）。P3 加上 `RunRecorder` 落盘每次 run 的完整决策轨迹到 `.agent/hybrid_exec/runs/<task_id>/`，独立调用与 workflow 场景共享同一份统计口径。P4 新增只读端点 `GET /v1/hybrid_exec/summary` + 看板 "🧪 混合执行" Tab 展示各 task 的脚本命中率/tier 分布；新增 `ReexplorePolicy` 基于累计成功率的跨 run 主动重探索（默认不启用，opt-in）。54 个单元/集成测试全部通过，且验证过对现有 workflow/kanban 相关测试无回归。新增可运行端到端演示 `examples/hybrid_exec_demo.py`（`python examples/hybrid_exec_demo.py` 直接跑）：用规则版替身代替 LLM/Agent 调用（本沙箱无 API Key），但 `HybridExecutor` 编排、`ScriptRepository` 版本管理、`ScriptRunner` 真实子进程执行、`RunRecorder` 落盘、`kanban_summary` 聚合全部走真实代码路径，覆盖"首次探索/复用脚本/报错自愈修复/连续失败自动退役降级 Fallback/可观测性"5 个场景并全部通过断言验证；仍缺真实 LLM/Agent 网络调用本身的端到端验证，详见 [脚本/LLM/Agent 混合执行系统指南](docs/hybrid-exec-guide.md) 第十一节

*最后更新：2026-07-01* — 具身智能改进 A/B/C 三阶段全部完成（12 项：本体感知/余裕感知/工具透明性/AgentSelfModel/时间加权记忆/认知锚点/自维护模块等），详见 [具身智能改进指南](docs/embodied-agent-guide.md)

*2026-07-27 GoalSpecBuilder 改为直连 LLM*：修复 `/goal`/`/goal from-history` 偶发"未产出任何文本输出，已使用通用兜底标准"的问题。根因是 `GoalSpecBuilder` 之前借用 `judge_factory.spawn_judge_agent` 构造一个"`tools_enabled=False`、`max_turns=2` 的受限 Agent"来生成验收标准——即使工具注册表为空，Agent 构造流程仍会按主循环方式连接已配置的 MCP server，模型看到自己身处通用 Agent 环境里会习惯性尝试 `skill_list`/`bash` 摸底，但这些工具不在（空）注册表里，每轮都以 `Unknown tool` 报错收场，在有限轮次内说不出一句 JSON。现改为直接调用 `LLMHelper.ask()` 发起单轮裸 chat completion，不注册工具、不连接 MCP，不存在"轮次预算"和"工具幻觉"这两个失败面；`GoalSpecBuilder` 支持注入调用方已有的 `llm_helper`（活跃 Agent 场景天然跟随 `/model`/`/provider` 切换），model/provider 解析仍遵循 `spec_builder_model`/`spec_builder_provider` 配置优先级。详见 [Goal 模式指南](docs/goal-mode-guide.md#goaljudge目标达成判定)

*2026-07-27 撞到 `max_turns` 硬顶时可配置自动续跑*：新增 `max_turns_on_limit`（`"stop"` 默认 / `"continue"` / `"compact_continue"`）+ `max_turns_hard_limit`（默认 `max_turns * 5`）两个 `AppConfig` 直接字段，并补上了 `agent_config.json → AppConfig.max_turns` 这条此前一直缺失的读取链路（此前 `max_turns` 只能靠 CLI `--max-turns` 覆盖，配置文件里写了不生效）。`"stop"` 保持原行为不变——撞顶打印 `Reached max turns (N).` 警告后交还调用方；`"continue"` 不压缩历史直接续预算、注入一条模拟"继续"消息接着跑；`"compact_continue"` 先强制 `compact_with_skills()` 压缩一次再续跑，行为与 `[AUTO-COMPACT-CONTINUE]` 自动续接一致。两种续跑策略都受 `max_turns_hard_limit` 兜底，避免长时间自主任务/daemon 场景失控地无限循环。详见 [配置系统指南](docs/config-guide.md#max_turns_on_limit--max_turns_hard_limitappconfig-直接字段)

*2026-07 记事本（Notepad）机制*：新增 `tools/notepad.py`，Agent 在执行任务过程中可通过 `notepad_add`/`notepad_update`/`notepad_remove`/`notepad_list`/`notepad_summarize` 工具记录关键信息、结果、注意事项。记事本内容通过 `context_builder.py::ContextBuilder.build()` 在每轮 system prompt 固定位置重新注入（`prompts/system/notepad.md`），因此不受 history compact 影响；持久化到 `.agent/sessions/<sid>/notepad.json`（原子写）。system prompt 中强制引导 Agent 在遇到关键结果/约束/用户明确要求记住的信息时主动记录。当记事本总字数超过 `NOTEPAD_COMPACT_HINT_THRESHOLD`（默认 20000 字符）时，`compact_with_skills()` 正常路径会在 compact prompt 中追加建议性提示，引导模型调用 `notepad_summarize` 合并冗余条目（不自动截断，取舍由模型决定）；分批降级路径（`_compact_chunked`）因不支持工具调用而不追加该提示。新增 `/notepad`、`/notepad clear`、`/notepad remove <id>` 命令，已注册进终端命令补全表（`ui/terminal.py::_COMMANDS`）与 `/help` 输出。新增配置开关 `notepad_enabled`（`agent_config.json`，默认 `true`），关闭后不注入记事本块、工具调用返回错误提示；内部用 `threading.local()` 存储 provider（与 `tools/evolution.py`/`tools/workdir_knowledge.py` 同款写法）确保多 Agent/多线程并发场景下开关互不干扰。详见 [记事本机制说明](docs/notepad-guide.md)

*2026-07 Goal 模式*：新增 `goal_mode/` 包，设定一个目标后 Agent 自动多轮尝试直至达成或触发安全阀。核心组成：`GoalSpec` + `GoalSpecBuilder`（自然语言目标→结构化验收标准，支持多轮对话式修订+版本 diff 展示，确认前不占用主 Agent 上下文；system prompt 要求具体化/分维度拆解、禁止照抄用户原话，代码层面对"几乎原封不动"的生成结果会自动带纠正提示重试一次）、`GoalJudge`（对照验收标准逐条核查，输出 `GOAL_STATUS: DONE/CONTINUE/NEED_COMPACT`，工具权限可选开启以自己跑命令验证）、`GoalRunner`（外层驱动循环，粗粒度 `CoarseStepExecutor` 每步调用一次完整 `run_turn`，为未来细粒度版本预留 `GoalStepExecutor` 接口）。与既有 Evaluator 修订循环的区别：Evaluator 循环在单次 `run_turn` 内部、受 `max_turns` 硬顶约束；GoalRunner 是跨多次 `run_turn` 的外层循环，撞到 `max_turns` 会显式 compact 后继续。安全阀：`max_rounds`（轮次上限）、`max_total_compacts`（防压缩风暴）、连续雷同反馈检测（`difflib.SequenceMatcher`）提前终止并如实汇报。异常中断恢复：`GoalState` 原子落盘到 `.agent/sessions/<sid>/goal_state.json`，只在轮次边界写入，`/goal resume` 续跑；`/goal list` 可列出所有可恢复的目标（跨 session，避免多个进程各自设定目标都被杀死后只能看到最近一个）；复用既有 session 持久化机制存储对话历史，不重复保存。新增 `/goal` `/goal resume` `/goal list` `/goal status` `/goal cancel` 命令，详见 [Goal 模式指南](docs/goal-mode-guide.md)

*2026-06-19* — API Key 配置重构：主推 providers.json 管理 LLM API Key，图片 Skill（ask_image / gen_image_with_text）保留环境变量方式

*2026-07 Compact 触发器体系*：新增 `history/triggers.py`，把"何时触发 compact"从单一的 token 阈值判断扩展为可插拔的 `CompactTrigger` 框架（与 `CompressionStrategy` 同一设计哲学）。新增四种触发器，均带独立开关、默认关闭：`TurnCountTrigger`（距上次 compact 满 N 轮，默认 20）、`ToolCallCountTrigger`（累计 N 次工具调用，默认 50）、`RedundancyTrigger`（`tool_result` 占比超过阈值，默认 60%）、`TopicShiftTrigger`（话题切换检测，`heuristic`/`llm` 两档，heuristic 用关键词重合度+切换语关键词，llm 档追加一次小模型二次确认）。各触发器可给出 `suggested_strategy`（如话题切换建议 `llm_summary`），并新增冷却期（`compact_cooldown_turns`，默认 3 轮）防止反复触发，以及触发后是否需要用户确认的开关（`compress.require_confirmation`）。同时修复了 `_auto_compress_history()` 此前未委托给 `CompressionStrategy` 注册表、导致配置的压缩策略实际不生效的问题；`compact_event` 新增 `trigger_reason` 字段记录触发来源；详见 [compact 设计文档](docs/compact-design.md)

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
*2026-06 自主运行时 Phase 1（Stage 9 基础架构）*：新增 `evolution/cron_scheduler.py`（CronScheduler，interval/cron 双格式，内置 5 个系统 job：consolidation/workdir_sync/self_eval/goal_review/digest_trim）；`AutonomousLoop._tick_passive()` 改调 CronScheduler；新增 `evolution/objective_executor.py`（ObjectiveExecutor，Objective 拆解为 3-8 步 Step，每步完成自动推进，串行+并发上限保护）；`/cron` CLI 命令（list/status/enable/disable/run/add/remove/set-schedule）注册到 REPL 和 `_COMMANDS` 补全

*2026-06 自主运行时 Phase 2（Stage 9 接入与 API）*：`server.py` `_build_autonomous_loop()` 注入 CronScheduler + ObjectiveExecutor；AgentRunner turn 完成/失败后回调 `ObjectiveExecutor.on_turn_done()`/`on_turn_failed()`（仅 initiator="autonomous"/"cron" 时触发）；`api/models.py` 新增 `OBJECTIVE_PROGRESS` SSE 事件；`bridge.py` 新增 `emit_objective_progress()`；`api/routes.py` 新增 `/v1/autonomous/status`、`/v1/goals` CRUD、`/v1/cron/jobs` CRUD 共 8 个端点；新增 `evolution/soft_goal_deriver.py`（三路信号：capability_map 低置信度 / WorkThread 积压 / 高频 Lesson，每次最多 derive 2 个 Goal）

*2026-06 自主运行时 Phase 3（Stage 9 闭环完善）*：`_tick_autonomous()` 完整接入 ExplorationSandbox——capability 类候选先经探索验证（`_run_capability_exploration()`），成功才写 GoalBacklog + 触发 `skill_propose`，失败静默丢弃；`SoftGoalDeriver` 新增 `derive_candidates()`（返回两类候选不写 GoalBacklog）+ `commit_goals()` 方法；`build_digest_summary()` 重写为六分组渲染（Objective进展/Cron执行记录/探索实验结果/💡Agent建议目标/进化提案/其他），Agent建议目标组内嵌 `/goals accept|reject <id>` 快捷指令；`goals.py` 补全 `accept`（激活 + priority 提升）/`reject`（abandoned + `record_rejected()` 30天去重）子命令

*2026-07 具身智能改进 A/B/C 三阶段（`next_doc/embodied_agent_improvement_plan_v3.md`）*：A1 `cli/daemon.py::DaemonClient` connected REPL 命令与本地模式对等；A2 `perception/correction_detector.py` 检测用户直接纠正短语生成 `source="human_feedback"` lesson；A3 `reminders/loader.py`/`manager.py` 新增 `pre_tool` 前馈触发；B1 新增 `perception/proprioception.py`（`ProprioceptionModule`，认知负荷/不确定性/风险感知/剩余预算/frustration 轮间快照，`ProprioceptionConfig`）；B2 新增 `evolution/lesson_to_reminder.py`（human_feedback 来源 1 次即激活，其余需达 T1 门槛先落草稿，`/evolution lessons-to-reminders`）；B3 `workflow/runner.py::_compute_parallel_batches()` 对 `depends_on` 拓扑排序并发执行无依赖步骤；B4 新增 `perception/affordance_analyzer.py`（`AffordanceMap`，交叉分析 open_threads/capability_map/lesson memory，接入 `api/session_pool.py`，`AffordanceConfig`）；新增工具透明性 `perception/intent_action_mapper.py`（`IntentActionMapper` 按意图分组工具调用，写入 `traces.jsonl` 的 `action_events`）；C1 新增 `perception/self_model.py`（`AgentSelfModel` 聚合视图，澄清与 UserProfile/RoleProfileManager/AgentProfile 三个既有 profile 概念的语义边界）

*2026-07 具身智能改进阶段 D（C2/C3/C4 收尾）*：C2 新增 `evolution/memory_aging.py`（`compute_decay_factor()`，lesson 按 source 区分半衰期基准 human_feedback 90d/experiment_confirmed 60d/self_reflection 30d/revert_record 14d，occurrence_count 加成封顶 4 倍，接入 `memory_store.py::_score_all()`）；C3 新增认知锚点文件机制（`agent.py::_save_cognitive_anchor()`/`_maybe_load_cognitive_anchor()` + `AgentPaths.workdir_cognitive_anchor` + `AppConfig.cognitive_anchor_enabled`，Ctrl-C 打断时 LLM 生成四段式"思维状态重建指南"，`prompts/system/cognitive_anchor.md` + `prompts/user/cognitive_anchor_request.md`，下次 session 启动注入 `system_extra` 后归档；daemon connected REPL 的 Ctrl-C 暂未接入）；C4 新增 `evolution/self_maintenance.py`（`SelfMaintenanceModule`：扫描 `traces.jsonl` 推断 stale_tools、复用 skill tracker 推断 stale_skills、复用 lesson 聚类推断 conflicting_lessons，只产出建议写入 `activity_digest.jsonl` 不自动修复，SessionEnd 时间门控 + 新增内置 cron job `sys:self_maintain`）；新增 61 个测试用例（`tests/test_intent_action_mapper.py`/`test_memory_aging.py`/`test_cognitive_anchor.py`/`test_self_maintenance.py`）；新增 [具身智能改进指南](docs/embodied-agent-guide.md) 汇总全部 12 项改进

*2026-07 工具结果智能截断（[SYS-RAWSTORE] + [SYS-SMARTTRIM]）*：`ToolTrimConfig` 新增 `raw_store_*`（原始输出留存，默认开启）与 `smart_summary_*`（LLM 智能摘要，默认关闭）字段；新增 `perception/raw_result_store.py::RawResultStore`（session 内内存 LRU，按条目数/总字符数双重淘汰，内容 md5 去重）；`tool_executor.py::_trim_result` 拆分为 `_rule_trim`（原有规则截断，作为默认策略/降级兜底）+ `_smart_summarize`（LLM 调用，失败静默降级）+ `_remember_raw`（截断/摘要后原文留存）；新增 `tools/builtin.py::view_raw_result` 工具（只读、免审批、支持行号范围，已加入 `_SAFE_TOOLS`/`_DEDUP_TOOLS`）；新增 `prompts/system/tool_result_summarizer.md` + `prompts/user/tool_result_summary_request.md`；详见 [工具结果原始留存与智能摘要指南](docs/tool-result-raw-store-and-smart-summary-guide.md)

*2026-07 轮次守门员（[SYS-TURN-JUDGE]）*：新增 `TurnJudgeConfig`（`turn_judge` 配置块，默认 `enabled=false`）。每轮 `run_turn()` 结束、`[SYS-HOOKS] TurnEnd` hook 未接管时，若开启则调用轻量的 `role_agents/turn_judge.py::run_turn_judge()`（纯文本判定，不挂工具）核查本轮结束是"真的需要真人输入"还是"遇到了技术性问题"（模型输出格式有问题、撞到 `max_turns` 硬顶、上下文臃肿需要 compact 等），输出 `TURN_STATUS: NEED_USER / AUTO_CONTINUE / NEED_COMPACT`；`AUTO_CONTINUE` 时提取具体反馈复用现有 `_turn_end_user_input` 机制自动续跑，`NEED_COMPACT` 时自动 `compact_with_skills()` 后续跑，`NEED_USER` 或达到 `max_auto_rounds` 上限则强制交还真人（防死循环）。判定原则与 GoalJudge 一致：异常/解析失败保守回退 `NEED_USER`，涉及主观决策场景一律 `NEED_USER`。新增 `prompts/system/turn_judge.md` + `prompts/user/turn_judge_request.md`，`role_agents/feedback.py` 新增 `extract_turn_status()` + `turn_status` 字段渲染；详见 [轮次守门员指南](docs/turn-judge-guide.md)

*2026-07 角色扮演（Persona）系统*：新增 `.agent/personas/*.md` 角色扮演机制，与自定义子 Agent（`.agent/agents/`，一次性任务型）不同，作用于**主 agent 自身**的人格，跨轮持续生效直到 `/role exit`。核心组成：`orchestrator/persona_profiles.py::PersonaLoader`（frontmatter 解析，`name`/`display_name`/`description`/`tone`/`break_character_policy`/`allowed_tools`）；`Agent.active_persona` 状态字段随 session `meta.json` 持久化（`new_session()` 不继承）；`ContextBuilder.build()` 单独成段注入渲染后的角色 prompt，`render_persona_prompt()` 强制追加安全边界声明（代码写死，角色文件无法覆盖）；`ToolExecutor.execute_all()` 接入 `allowed_tools` 白名单强制拦截（非白名单工具直接拒绝，不进入常规审批流程）；`~/.agent/persona_usage.jsonl` 记录激活事件供 `/role stats` 查看；新增 `/role list|use|show|exit|status|stats|reload` 命令组与 `persona-generator` skill；`.agent/personas/` 与 `.agent/agents/` 同批次接入热重载（`/reload`）；内置默认角色 `senior-swe-mentor`/`jarvis`/`socratic-tutor`/`storyteller-narrator`/`rem`；详见 [角色扮演（Persona）系统指南](docs/persona-guide.md) 与 [设计文档](next_doc/roleplay_persona_design.md)

*2026-07 用户行为感知系统*：新增 `perception/behavior/` 包，配置文件是 `<project_root>/behavior_config.json`（跟 `agent_config.json` 同级目录，独立于 `AppConfig` 加载流程；采集到的原始事件/分析摘要仍落盘在 `~/.agent/behavior/`），总开关与全部子开关默认 `False`。采集层：桌面本机线程采集器（`ActiveWindowCollector`/`IdleCollector`/`NowPlayingCollector`/`AppLifecycleCollector`，跨平台 Windows/macOS/Linux）；浏览器行为两套独立方案（`browser_extension_example/` MV3 插件 + `collectors/cdp_browser.py` 专用调试浏览器 CDP 方案，`/behavior browser start|stop|status`）；外部上报统一复用 `/v1/perception/report`（`kind` 区分 `browser`/`git`/`terminal`/`mobile`）——`collectors/external_hooks.py` 生成 git commit/checkout hook 与终端命令 hook（客户端+服务端双重脱敏，敏感命令整条丢弃）；`mobile_setup.py` 提供 Android(Tasker)/iOS(快捷指令) 接入模板，另有独立的 `android_companion_app/` Kotlin 工程（App 使用统计/屏幕解锁/地理围栏标签/Health Connect 日聚合，坐标全程不出设备、服务端强制剔除任何经纬度字段）。分析层 `analyzer.py` 把原始事件聚合为"工作画像+生活画像"结构化日报（活跃时段/前台切换次数/Top App与网站时长/Git提交/终端命令数/工作娱乐时长估算/媒体播放/手机使用与解锁次数/地点标签序列/健康聚合），落盘 `.json`+`.md`，支持 `daily_analysis_enabled` 定时自动生成。新增 `/behavior` 命令组（`status`/`on`/`off`/`enable`/`disable`/`token`/`recent`/`clear`/`browser`/`git`/`terminal`/`mobile`/`report`）与 `/v1/perception/*` 共 10 个 HTTP 端点。隐私边界：不采集聊天软件消息内容、不做按键内容记录、剪贴板只记录"发生了复制"、CDP 方案不用截图/网络内容/DOM读取、手机端只允许地理围栏标签不接受坐标、健康数据只要日聚合、不读通知正文；详见 [用户行为感知系统指南](docs/behavior-perception-guide.md)

*2026-07 图书馆式知识索引*：在原有 `MemoryStore`（TF-IDF 全库检索）之上新增一层结构化索引，思路是"先建分类体系再检索"而非"关键词碰撞式检索"。核心组成：`perception/classification.py::ClassificationTree` 分类树（书架结构），冷启动只有根节点，运行时靠规则关键词匹配 + LLM 兜底（只能入座已有节点）自动归类，新分类节点只在 巩固循环 巡检时由未分类候选批量聚类诞生（`grow_from_candidates`），`merge_similar_nodes()` 按关键词 Jaccard 相似度定期收敛重复书架（`merged_into`/`resolve_code()` 自动跳转），`feedback_score` 累积检索反馈调整打分权重；`perception/entity_index.py::EntityStore` 实体目录（模块/bug模式/概念卡片），`link_entry()` 挂载记忆，`rewrite_summary()` 攒够 3 条新证据才批量重写摘要（显式让 LLM 标注新旧证据矛盾，`⚠矛盾已更新：` + 旧结论归档进 `superseded_notes`），`consolidate_entities()` 去噪（停用词/过短实体名）+ 近重复合并（`difflib` 相似度，模糊地带才兜底问一次 LLM）；`perception/catalog.py::CategoryCatalog` 分类号→entry_id 指针索引（可从 `memory.jsonl` 重建）+ `knowledge_timeline.jsonl` 知识生命周期编年目录（侧车索引 `knowledge_timeline_index.json` 支持按实体/分类过滤查询，不必全文件扫描）；`perception/library_index.py::LibraryIndex` 组合外观类，对外提供 `on_new_entry()`（写入上架）/`shelf_search()`（两步检索：先定位书架再在架内精排，候选不足自动回退全库检索）/`record_retrieval_feedback()`/`mark_stale_from_correction()`（`agent.py::_detect_and_record_correction` 检测到人类纠正时，把 `ContextBuilder.last_injected_memory_ids` 记录的本轮实际注入记忆标记为可能过时，形成"纠正→定位旧知识→标记过时"闭环）/`timeline_for()`/`consolidate()`（巩固循环 巡检串联以上所有巩固步骤）。LLM 兜底调用复用 `agent.py` 已有的 `LLMClientPool.current_client`（`memory_factory.py::build_llm_call()` 包装），不新开 provider。全部通过 `library_index_enabled`/`library_shelf_search_enabled`/`library_index_user_scoped`（多用户软隔离）三个开关控制，默认开启但完全向后兼容——关闭后 `MemoryStore` 行为与改造前一致。`run_consolidation()` 新增 8.6 知识巩固步骤，`/evolve consolidate` 报告展示统计，新增 `/evolve timeline --entity <id>|--category <code>` 命令；详见 [图书馆式知识索引指南](docs/library-index-guide.md)

*2026-07 具身智能 × 自我演化四方案联动（`next_doc/embodied_autonomy_integration_design.md`）*：方案一 `perception/affordance_analyzer.py` 新增 `persist_affordance_map()`/`load_recent_high_risk_zones()`，`AffordanceMap.high_risk_zones` 落盘到 `<workdir>/affordance_snapshot.json`（超过 60 分钟过期），供 `evolution/soft_goal_deriver.py::_from_capability_map()` 对高风险域候选降权（`urgency *= cfg.affordance.risk_downweight_factor`，默认 0.4）与 `perception/exploration_sandbox.py::ExplorationSandbox.create()` 对高风险域探索收紧 token 上限（探索预算总额的一半，新增 `ExplorationTokenLimitExceeded` 提前止损），总开关 `cfg.affordance.risk_gating_enabled`（默认开启）；方案二私有函数 `_load_behavior_context` 提升为公共 `load_behavior_context()`，`evolution/resource_arbiter.py::ResourceArbiter.can_run_autonomous()` 新增第五条仲裁规则 `_check_user_presence()`——用户明显活跃切换（`context_switch_count` 达 `cfg.autonomy.behavior_gating_switch_threshold`，默认 3）时暂缓自主任务，总开关 `cfg.autonomy.behavior_gating_enabled`（默认关闭）；方案三 `agent.py` 新增 `_maybe_publish_uncertainty_signal()`（连续 `cfg.proprioception.uncertainty_streak_required` 默认 3 轮超 `uncertainty_threshold` 默认 0.45 才限流发布 `proprioception.uncertainty_sustained` 事件）与 `_current_task_domain_hint()`，`soft_goal_deriver.py::_recent_uncertainty_domains()` 消费该事件，与既有 `memory.sparse_region_detected` 信号对同一 domain 的加权取较大值而非相乘（上限仍 1.6x）；方案四 `perception/self_model.py::AgentSelfModel.recent_negative_outcome_domains()` 桥接 `outcome_tracker.get_revert_candidates()`，`derive_candidates()` 排序前对落在负面回填域的候选强降权（`urgency *= 0.15`），验证一个具体、影响面可控的场景，暂不做通用聚合接入。四个方案均遵循"降权不拒绝、失败静默降级、双开关默认不改变原有行为"原则；新增 25 个测试用例（`tests/test_affordance_risk_gating.py`/`test_resource_arbiter_behavior_gating.py`/`test_uncertainty_event_bridge.py`/`test_negative_outcome_downweighting.py`）；详见 [具身智能改进指南](docs/embodied-agent-guide.md)、[Stage 9 自主运行时指南](docs/self-evolution-stage9-guide.md)、[跨子系统事件总线指南](docs/system-events-bus-guide.md)

*2026-07 agent.py 拆分为 agent/ 包（Stage 12 代码结构治理）*：原 `src/mini_agent/agent.py`（3907 行, 近 100 个方法的单体类）按职责拆分为 `src/mini_agent/agent/` 包：`core.py`（`Agent` 类骨架 + `__init__`）+ 9 个 Mixin 文件（`lifecycle.py`/`reflection.py`/`profile.py`/`llm_control.py`/`turn_loop.py`/`role_judge.py`/`reminders_correction.py`/`compaction.py`/`snapshot.py`）+ `_helpers.py`（模块级共享辅助函数），`Agent` 通过多重继承组装回同一个类。纯粹搬迁重构，不改变任何方法签名与运行时行为，对外 `from mini_agent.agent import Agent` 导入路径不变。同步修复两处因此暴露的隐藏耦合：① `scripts/protected_paths.py` 原先按精确文件名 `"src/mini_agent/agent.py"` 保护 agentic loop 主循环（T3 治理红线），拆分后已补充目录级条目 `"src/mini_agent/agent/"`，否则自我演化系统会失去对核心循环的保护；② `tools/introspection.py::_get_agent_init_snippet()` 原先只扫描单文件里的 `self.xxx = ` 赋值，已改为遍历整个 `agent/` 目录并标注来源文件。全量 1791 个测试验证通过 1777 个，剩余 14 个失败经确认是拆分之前就存在、与本次改动无关的既存问题（`SkillLoader` 测试桩缺少 `_loaded_resources` 初始化、环境缺少可选依赖 `jsonschema`）。

*2026-07 Wiki 式知识库（`wiki式知识库重构计划.md`）*：图书馆式知识索引之外的一套平行新实现，核心动机是分类树"每条知识只有一个最合适位置"的假设与软件工程知识天然网状的结构不匹配。阶段一（基础设施）：新增 `src/mini_agent/wiki/` 包，`parser.py` 解析 frontmatter + 正文 + `[[link]]` 弱引用的 md 页面（`entity`/`decision`/`process`/`experience`/`topic` 五种类型），`graph.py::GraphIndex` 内存图结构（正向边+反向边，`expand()` 一跳扩展，区分 frontmatter 强关系与正文弱引用），`indexer.py::build_index()` 遍历 `wiki/` 生成 `_index/` 下 `graph.json`/`tags.json`/`backlinks.json`/`search_index.json` 四个可随时删除重建的派生索引（支持增量模式），`writer.py` 原子写，`validator.py` 死链/id冲突/孤儿页面校验。阶段二（迁移与双写）：`migration.py::migrate_entity_store()` 一次性导出脚本 + `mirror_entity()` 双写共用函数，`LibraryIndex.on_new_entry()`/`consolidate()` 命中已有实体页追加"历史沿革"、命中不到新建页面；`dedup.py::find_similar_page()` 判重默认走规则打分（tag重合度+关键词Jaccard）+ 不确定区间才问一次 LLM 确认，embedding 方案保留为显式可选路径，替代原先 `difflib` 字符串相似度。阶段三（检索切换）：新增 `search.py::wiki_shelf_search()` 三段式检索——规则粗筛（tag+关键词打分取 top-N）→ 图扩展（复用 `GraphIndex.expand(strong_only=True)`）→ LLM 精排（完整正文排序+"基于页面:"标注依据），通过 `LibraryIndex.wiki_search()` 暴露，与 `shelf_search` 完全并存、不替换，供后续 A/B 对比效果；`consolidate()` 新增步骤 6，wiki 有写入时自动触发增量索引重建。阶段四（专题页与收尾）：新增 `topics.py::consolidate_topics()`，按 tag 聚类且组内 frontmatter 强链接密度达标（默认页面数≥4、密度≥0.5）时触发 LLM 综合聚合成 `topics/*.md`（`relation: absorbs`），接入 `consolidate()` 步骤 7；新增 `/wiki <page-id>|list|search|rebuild` CLI 命令供人工浏览页面/backlinks 及检索 A/B 对比。顺带修复：核对代码发现 `wiki_paths` 参数虽已加入 `LibraryIndex.__init__` 但 `memory_factory.py` 从未真正传递，导致双写路径此前从未在真实 agent 运行中触发——补上 `MemoryConfig.wiki_enabled`（默认开启）总开关完成接线。"验证新检索路径效果稳定后下线旧路径"这一条有意保留未完成，理由是三段式检索刚落地尚未经过实际 A/B 验证。详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md)

*2026-07 决策/取舍知识提炼*：在 Wiki 式知识库之上新增一条独立提炼线，专门捕捉"考虑过哪些方案、最终选了什么、为什么否决其它方案"这类工程决策——lesson（规则触发）和 correction（人类显式纠正）都覆盖不到正常推进、没有报错也没人纠正的决策场景。提取：`history/compression.py::LLMSummaryStrategy` 复用 compact 阶段本就要发的那次摘要 LLM 调用，把输出从纯文本摘要改成 `{compact_summary, decisions[]}` 结构化 JSON（`prompts/system/compress_summarizer.md`/`prompts/user/compress_summary_request.md` 同步改造），`history/decision_extraction.py::parse_decision_response()` 负责容错解析（失败退化为纯摘要，不阻断 compact），`CompressConfig.extract_decisions` 开关默认开启。落盘：不再逐条即时落盘导致碎片化，而是 `wiki/decision_writer.py::queue_candidates()` 先把候选 append 到 `.agent/decision_candidates_pending.jsonl` pending 队列，真正的落盘延后到巩固循环（`evolution/consolidation.py::run_consolidation` 新增一步调用 `decision_writer.consolidate_pending()`）批量执行——先合并同一批次里指向同一件事的多条候选（topic slug 相同或 related_entities 有交集，只留最新一条 chosen），再走 `process_candidates()` 三分支逻辑（命中已有决策页且方案一致→只更新；命中但方案变了→旧页 `status` 改 `overturned`、新建页用 `supersedes`/`superseded_by` 双向串联沿革链；未命中→新建 `status=settled`），"新建"动作额外套 8.5 节奏治理冷却（`CompressConfig.decision_batch_min_interval_days` 可调，默认 1 天）避免同一决定短期内被反复提炼出候选。决策页 `confidence` 固定 0.5（低于 lesson 的 0.6 与 human correction 的 0.7），`parser.py::STATUS_VALUES` 新增 `settled`/`overturned`，`validator.py` 新增 supersedes/superseded_by 成对性校验。召回：`evolution/decision_recall.py::recall_related_decisions()` 复用 `wiki_shelf_search()` 三段式检索、限定 `type=decision`，按 `settled`（"已采纳过，请先确认是否要重复论证"）/`overturned`（"之前被否决，请先确认新提案是否相同"）分别渲染提醒文字，接入 `cli/commands/evolve.py::_spawn_evolution_agent()`——`/evolve review` spawn evolution-agent 前自动查一遍相关历史决策并把提醒前置注入 task context，异常静默降级不影响主流程；详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) 九·2 节、[巩固循环 后台循环指南](docs/self-evolution-consolidation-guide.md) 4.4 节

*2026-07 wiki 知识库提取层与组织层改进计划（O1-O4、E1-E3，`next_doc/wiki知识库提取与组织层改进计划.md`，全部已完成）*：在 wiki 式知识库阶段一~四 + P0-P4 落地后，针对暴露出的"提取时机/耦合度/知识盲视"（E1-E3）与"组织结构信度分层/图谱表达力/动态性/生命周期一致性"（O1-O4）两类深层问题的后续深化。**O1 全量扫描架构分层**：`search.py`/`dedup.py` 优先复用 `indexer.py` 已生成的 `_index/` 派生索引（新增 `wiki/index_reader.py`），索引缺失/明显过期才退回全量扫描；页面 frontmatter 新增 `grounded_hit_count`（每次被 LLM 精排判定为回答依据 +1），`_rule_score()` 打分公式加入 `confidence_weight * log(1+grounded_hit_count)` 信度加权项（`MemoryConfig.wiki_confidence_weight` 默认 0.1，设为 0 与改动前完全一致）。**E3 抽取"看不到"已有知识库**：新增 `wiki/entity_digest.py::build_entity_digest()` 生成极简实体索引反哺抽取 prompt，`EntityCandidate.reused_existing_id` 模型自报复用 id，`world_writer.py` 用 `dedup.py` 分数二次校验（模型优先+规则兜底），`CompressConfig.entity_digest_enabled` 默认开启。**E1 抽取时机与 compact 解耦**：新增 `history/extraction_trigger.py::scan_for_extraction_window()` 零 LLM 成本规则扫描（连接词密度+轮次兜底），命中后 `history_manager.py::maybe_trigger_extraction()` 异步排队独立的"仅抽取"LLM 调用，游标持久化在 `extraction_cursor.json`，`CompressConfig.extraction_trigger_enabled`/`extraction_trigger_dispatch_enabled` 两级开关**（2026-07 应用户要求已改为默认开启，跳过了原计划设想的观察期）**控制是否扫描/是否真正派发。**E2 抽取任务耦合度**：方案B（已生效）把 JSON schema 字段顺序调整为 `{decisions[], entities[], facts[], compact_summary}` 并要求先完整识别结构化字段再给摘要，`wiki/stats.py::compute_extraction_stats()` 新增 `avg_entities_per_extraction`/`avg_facts_per_extraction` 观测指标；方案A随 E1 独立触发路径自然解决；方案C（`extract_world_model`/`extract_decisions` 关闭 compact 路径的结构化抽取）机制就位但仍保持默认开启，待观测数据支撑后再人工切换，是本轮改进计划中唯一仍处于"代码就位、待人工决策"状态的事项。**O2 实体关系图过于扁平**：`graph.py::GraphIndex.expand()` 新增多跳衰减扩展（`max_hops`/`decay` 参数，同节点多路径取最大权重），`expand_legacy()` 保留原一跳签名兼容既有调用；`wiki_shelf_search()` 候选不足或 `/wiki search --deep` 时自动/强制升级 `max_hops=2`。**O3 topic 聚类是纯事后归纳**：`topics.py` 新增再巩固扫描 `_find_topic_reconsolidation_candidates()`，达标新页面走 `append_to_topic_page()` 追加进已有 topic 而非参与新聚类，`TopicConfig.reconsolidation_interval_runs`（默认 5）控制频率，事件记入 `_index/topics_reconsolidation_log.jsonl`，累计追加超软上限（8 次）标记 `needs_review`。**O4 统一知识生命周期状态机**：新增 `wiki/lifecycle.py::mark_page_state()`（跨页面类型统一状态标记，支持 fact 锚点粒度）/`touch_validated()`（隐式验证回升，`superseded` 不因命中回升）/`stale_candidate_scan()`（久未验证的 `fresh` 页面标记 `stale`），frontmatter 新增独立字段 `knowledge_state`/`last_validated_at`/`validated_by`（未复用已有的数值型 `confidence` 字段，避免类型冲突）；`world_writer.py` 给 fact 生成正文内锚点注释（`<page-id>#fact-N`）实现独立状态标记；`reminders_correction.py`/`library_index.py::mark_stale_from_correction()` 扩展为同步标记镜像 wiki 页面为 `superseded`；`search.py::_rule_score()` 新增 `lifecycle_discount_enabled` 折扣开关（默认关闭，`stale` 减半/`superseded` 归零）；新增 `/wiki lifecycle-scan [--days N]` 命令与 `/wiki stats` 的 `by_knowledge_state` 分布展示。全部条目均遵循"先只记录/观测，默认不改变现有行为，用真实数据校准后再决定是否切换默认值"的执行纪律；详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) §十 与 `next_doc/wiki提取层改进计划_O1实施记录.md` ~ `_O4实施记录.md`、`_E1实施记录.md` ~ `_E3实施记录.md`

*2026-07 wiki 知识库改进计划 · 下一阶段（`next_doc/wiki_next_phase_improvement_plan.md`，全部已完成）*：在 O1-O4/E1-E3 落地后，聚焦四个真实存在的缺口。**双轨制退出评估**：新增 `wiki/decommission.py::check_and_plan()`，只读复用 `promotion.py` 的三项转正标准，达标时给出「关闭 `legacy_index_enabled` → 观察 ≥2周 → 移除旧索引文件」三步执行清单而不自动删代码；`check_ready_transition()` 在"未就绪→就绪"翻转瞬间提醒一次，已挂载到 `evolution/autonomous_loop.py` 巩固循环收尾与 `/evolve consolidate` 两处直接同步调用点。**陈旧专题页标注**：`wiki/gap_scanner.py::mark_stale_topics()` 复用 O4 的 `knowledge_state` 字段，topic 页面 `absorbs` 链接成员中非 `fresh` 占比超阈值（默认 0.6）即标注过时，只标注不删除。**`consolidate()` 分步超时熔断**：新增 `evolution/step_runner.py::run_step()`，线程+轮询给每个子步骤独立超时预算，超时跳过不阻塞、不重试，下一轮巩固自然覆盖，`ConsolidationReport` 新增 `step_timings` 字段。**世界知识独立触发信号**：`history/extraction_trigger.py` 新增 `trigger_reason="entity_density"`，规则扫描纯描述性内容（不含"因为/所以"决策语境词）里的新词密度，与既有 `connective_density` 并行不冲突。**知识缺口主动扫描**：新增 `wiki/gap_scanner.py::scan_gaps()`（浅层实体/孤儿页面/陈旧专题页规则扫描，零 LLM 成本）与 `wiki/fallback_cleanup.py::cleanup_fallback_pages()`（超期兜底页重新判重，命中合并/未命中标 stale，页面级粒度），分别接入新命令 `/wiki gap-scan [--max-results N] [--dispatch]` 与 `/wiki fallback-cleanup [--days N]`，以及两个新内置 cron job `sys:wiki_gap_scan`（12h）/`sys:wiki_fallback_cleanup`（7d）。**命令行输入提示补漏**：核对发现 `/wiki` 顶级命令此前从未注册进 `ui/terminal.py::_COMMANDS`（驱动交互式终端 Tab 补全的命令表），导致新增的 `gap-scan`/`fallback-cleanup` 敲出来没有任何提示——已补上 `/wiki` 及全部 8 个子命令/选项，新增 `tests/test_wiki_slash_completer.py` 防止未来再漏。详见 [Wiki 式知识库指南](docs/wiki-knowledge-base-guide.md) 十一·5 节、`next_doc/wiki_next_phase_improvement_plan.md`、`next_doc/wiki_next_phase_implementation_record.md`

*2026-07 主动推荐、日报生成与决策画像（`next_doc/主动推荐与数字分身机制设计方案.md`，全部已完成）*：三层机制。**日报融合**：新增 `evolution/daily_digest.py` 合并行为分布+目标进展+git提交为 `.agent/daily_reports/<日期>.json/.md`，`/digest daily [日期]` 命令，内置 cron job `sys:daily_digest`（每天 22:00），启动打印一行未展示过的摘要。**主动推荐**：新增 `evolution/next_action_advisor.py`，规则层识别"停滞目标"（优先级≥1 且超 7 天无进展）与"注意力错配"（窗口内单一活动占比超阈值且与任何 active Goal 无关键词重合）两类候选并排序（可选 `rank_with_llm` 切换 LLM 排序层，失败自动回退规则排序），`/next [refresh]` 命令，内置 cron job `sys:next_action_digest`（3 小时一次，候选为空则跳过）。**决策画像**：新增 `evolution/decision_profile_builder.py`，从历史决策记录（复用 `wiki/decision_writer.py`）归纳需 ≥3 条独立证据支持的用户价值取向模式，矛盾证据记录到 `contradicted_by` 并下调置信度而非覆盖，产出 `.agent/wiki/user_value_profile.md`，`/decision_profile [update]` 命令，内置 cron job `sys:decision_profile_update`（周级，**默认关闭**，建议积累数周数据后手动开启）。三者均可通过 `agent_config.json` 新增的 `digest_advisor` 配置块调整全部开关与阈值（含是否接 LLM 排序、停滞天数、注意力窗口/占比、决策画像最少证据数等）。第二轮补齐三项：decision_profile 归纳出的高置信度模式可对 next_action_advisor 排序做同类别内加权（`next_action_profile_weighting_enabled`，默认关闭）；"注意力错配"信号持续超过阈值时长会触发 daemon 主动推送（`next_action_push_enabled`，默认关闭，复用 `InputQueue` 已有的多客户端 SSE 推送通道，不新建推送机制）；Kanban 看板新增日报/推荐/决策画像三张只读卡片（`GET /v1/digest/daily`、`/v1/next_actions`、`/v1/decision_profile` 三个新端点）。过程中发现并修复一个分发 Bug：`cli/repl.py` 里 `/digest`、`/profile` 命令分发链各自存在重复的 `elif` 分支，导致第一轮新增的日报/决策画像命令排在既有同名分支之后、从未被真正执行过；修复后决策画像命令改名为 `/decision_profile`（不再叫 `/profile`，避免与既有"强制刷新用户画像"命令重名），`/digest daily` 改为仅在显式带 `daily` 子命令时才分流。明确不做"计划 vs 实际"反拖延对比与"模拟用户直接做决策"等更激进用法。详见 [每日融合日报指南](docs/daily-digest-guide.md)、[主动推荐排序指南](docs/next-action-advisor-guide.md)、[决策画像指南](docs/decision-profile-guide.md)、[Kanban 看板使用指南](docs/kanban-dashboard-guide.md)