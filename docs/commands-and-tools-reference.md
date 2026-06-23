# mini-agent 命令与工具速查

本文档汇总所有 CLI 启动参数、REPL slash 命令和内置工具。

**补充阅读**：
- [Task 日志实时查看与切换](task-focus-viewing.md) — 方向键实时查看任务日志
- [Plan 与 Task 指南](plan-and-task-guide.md) — 执行计划机制
- [记忆管理指南](memory-management-guide.md) — `/memory` 命令背景
- [用户画像系统指南](user-profile-guide.md) — `/profile` 命令背景
- [自定义子 Agent 指南](custom-sub-agents.md) — `/agents` 命令与 `spawn_named_agent` 工具背景
- [Hooks 机制指南](hooks.md) — `/hooks` 命令背景
- [自我演化安全网指南（Stage 2）](self-evolution-stage2-guide.md) — `/evolution` 命令组背景
- [自我演化 lesson → skill 闭环指南（Stage 3.1）](self-evolution-stage3-1-guide.md) — `/evolve` 命令与 `skill_propose` 工具背景
- [自我演化 eval 反馈环指南（Stage 3.2）](self-evolution-stage3-2-guide.md) — `mini-agent eval` 完整说明

---

## 一、启动方式

```bash
# 推荐（标准包方式）
python -m mini_agent
python -m mini_agent "单条命令"

# 安装后
mini-agent
mini-agent "单条命令"

# 兼容旧方式
python main.py
```

---

## 二、CLI 启动参数

### Web Search 参数

| 参数 | 说明 |
|------|------|
| `--web-search-provider` | 默认搜索后端：`duckduckgo` \| `brave` \| `serper` \| `tavily` |
| `--web-search-max-results` | 默认返回结果数量（默认 5） |

**环境变量：** `WEB_SEARCH_PROVIDER`, `BRAVE_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`

详见 [Web Search 指南](web-search-guide.md)。

### 基础参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--model` | `-m` | 指定模型（覆盖 CLAUDE_MODEL 环境变量） |
| `--system` | `-s` | 额外的系统提示词文本 |
| `--project` | `-p` | 项目根目录（默认当前目录） |
| `--skills-dir` | | 额外技能目录 |
| `--config` | `-c` | JSON 配置文件路径（默认 agent_config.json） |
| `--verbose` | `-v` | 显示原始工具调用 JSON |
| `--sandbox` | | 沙箱模式（禁止破坏性操作） |
| `--yes` | `-y` | 自动批准所有工具调用 |
| `--no-stream` | | 禁用流式输出 |
| `--max-turns` | | 每条用户消息的最大 agentic 轮数 |
| `--agent-name` | | Agent 显示名称（默认：orzooo） |

### LLM / Provider 参数

| 参数 | 说明 |
|------|------|
| `--provider` | LLM 提供商：`anthropic`\|`openai`\|`ollama`\|`nvidia`\|… |
| `--base-url` | 自定义 API 端点（代理、Azure、本地部署） |
| `--system-tool-call` | 使用 system prompt 工具调用模式（最大兼容性） |
| `--system-msg-format` | system prompt 格式：`system_field`（默认）或 `system_role` |
| `--debug-llm` | 启用 LLM 请求/响应调试日志（写入 `.claude/logs/`） |
| `--debug-llm-console` | 同时在控制台打印调试信息（隐含 `--debug-llm`） |

### 并发与频率限制参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--workers` | 4 | 最大并发 Sub-Agent 数 |
| `--max-llm-calls` | 8 | 最大并发 LLM 调用数 |
| `--rpm` | 0 | 每分钟最大 LLM 请求数（0 = 不限速）。超出时自动等待，避免触发平台频率限制 |

### 重试退避参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--retry-backoff` | `fixed` | 退避策略：`fixed`（固定）/ `linear`（线性递增）/ `exponential`（指数递增） |
| `--retry-backoff-step` | `60.0` | 步长值。`linear`：每次递增秒数；`exponential`：倍数（如 `1.5` 表示每次 ×1.5） |
| `--retry-backoff-max` | `0` | 等待时长上限（秒），`0` = 不限制 |

示例：
```bash
# 指数退避：5s → 7.5s → 11.25s → … 上限 120s
mini-agent --retry-backoff exponential --retry-backoff-step 1.5 --retry-backoff-max 120

# 线性退避：10s → 70s → 130s → … 上限 300s
mini-agent --retry-backoff linear --retry-backoff-step 60 --retry-backoff-max 300
```

详见 [LLM 重试退避策略指南](retry-backoff-guide.md)。

### Session 参数

| 参数 | 说明 |
|------|------|
| `--session-dir` | Session 文件保存目录（默认 `./sessions`） |
| `--session-fmt` | Session 文件格式：`json`（默认）或 `jsonl` |
| `--no-save-session` | 禁用自动保存 Session |
| `--resume` | 按 Session ID（或前缀）恢复之前的会话 |

### 感知与记忆参数（默认全部关闭）

| 参数 | 功能标签 | 说明 |
|------|----------|------|
| `--memory` | SYS-MEMORY | 启用跨 session 长期记忆检索 |
| `--memory-top-k N` | | 每轮最多注入 N 条记忆（默认 3） |
| `--session-summary` | SYS-SUMMARY | 每次会话结束时生成 LLM 摘要 |
| `--session-search` | SYS-SEARCH | 启用 `/session search` 命令 |
| `--auto-compress` | SYS-COMPRESS | context 超阈值时自动压缩历史 |
| `--auto-compress-threshold` | | 触发压缩的 context 占比（默认 0.7） |
| `--tool-result-trim` | SYS-TRIM | 截断超长工具结果 |
| `--tool-result-trim-threshold` | | 截断阈值（字符数，默认 500） |
| `--forget-policy` | SYS-FORGET | 基于权重的历史遗忘策略 |
| `--skill-semantic` | SYS-SKILL-SEM | 语义相似度 Skill 激活 |
| `--skill-tracking` | SYS-SKILL-TRACK | 追踪 Skill 激活次数 |
| `--skill-chunking` | SYS-SKILL-CHUNK | 只注入 Skill 的相关章节 |
| `--skill-compact-budget N` | SYS-SKILL-COMPACT | 压缩重附 Skill 总 token 预算（默认 25000） |
| `--skill-compact-per-skill N` | | 单个 Skill 压缩重附上限（默认 5000） |
| `--project-scan` | SYS-PROJ | 扫描项目结构并注入 system prompt |
| `--file-watch` | SYS-WATCH | 检测外部文件变化并提示 |
| `--tool-cache` | SYS-TOOLCACHE | 缓存 read_file/web_search 结果 |
| `--token-estimate` | SYS-TOKEN | 每次 LLM 调用前估算 token 用量 |
| `--tool-stats` | SYS-STATS | 追踪工具调用次数、成功率、输出大小 |

### 环境变量

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API 密钥 |
| `OPENAI_API_KEY` | OpenAI API 密钥 |
| `CLAUDE_MODEL` | 默认模型名称 |
| `LLM_PROVIDER` | LLM 提供商 |
| `LLM_DEBUG` | 启用调试日志（`1`/`true`） |
| `LLM_DEBUG_CONSOLE` | 控制台打印调试信息（`1`/`true`） |
| `MAX_LLM_CALLS` | LLM 并发限制 |
| `SESSION_DIR` | Session 文件目录 |

---

## 三、REPL Slash 命令

> 实现位置：`src/mini_agent/cli/repl.py`（路由）+ `src/mini_agent/cli/commands/`（各命令实现）

### 基础

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助（来自 `cli/parser.py`） |
| `/clear` | 清除对话历史 |
| `/retry` | 丢弃上一轮模型输出，用相同输入重新生成 |
| `/rollback` | 完整撤销上一轮（用户消息 + 模型回复），同步保存 session |
| `/stats` | 显示会话统计（轮数、tokens、工具调用数、耗时） |
| `/verbose` | 切换详细工具 JSON 输出模式 |
| `/compact` | 压缩对话历史为摘要，重附 Skill 上下文 |
| `/prompts` | 列出所有 PromptManager 管理的 prompt 文件 |
| `/memory` | 立即在后台生成/刷新 session 摘要 + 写入长期记忆 + 刷新用户画像（跳过轮次间隔门槛），需 `--memory` 启用；详见 [记忆管理指南](memory-management-guide.md) |
| `/profile` | 立即在后台刷新用户画像（跳过刷新间隔），需在 `agent_config.json` 中设置 `profile_enabled: true`（无对应 CLI flag）；详见 [用户画像系统指南](user-profile-guide.md) |
| `exit` / `quit` | 退出程序 |

### Skill 管理（`src/mini_agent/cli/commands/skills.py`）

| 命令 | 说明 |
|------|------|
| `/skills` | 列出所有可用技能：激活状态、描述、~token 估算 |
| `/skill on <name> [name2 ...]` | 激活一个或多个技能 |
| `/skill off <name> [name2 ...]` | 卸载一个或多个技能 |
| `/skill info <name>` | 显示技能全文内容、状态、token 估算 |
| `/skill stats` | 显示 LRU 使用追踪和压缩预算预览 |
| `/skill reset` | 卸载所有当前激活的技能 |

**内置技能类型**（`.claude/skills/`）：

| 技能 | 用途 |
|------|------|
| `ask_image` | 图片信息提取与问答（**不要**用 Read 工具直接读图片文件） |
| `gen_image_with_text` | 文本生成图片（支持 text-to-image 和 image-to-image 编辑） |
| `agent-generator` | 创建符合 mini_agent 规范的自定义子 agent |
| `skill-generator` | 创建符合 mini_agent 规范的新 SKILL.md 技能文件 |
| `iching_oracle` | 易经智慧顾问，提供人生决策指导 |
| `comic-4panel` | 四格漫画全流程生成：主题构思 → 分镜脚本 → 一次性生成完整漫画图；详见 [四格漫画生成指南](comic-4panel-guide.md) |
| `git-context` | 分析当前工作目录 Git 仓库状态（commit 历史、变更文件、分支、diff） |
| `python-expert` | Python 编码最佳实践助手 |
| `reminder-generator` | 从当前对话提取可复用经验并生成 reminder 文件；详见 [Reminder 系统指南](reminder-system-guide.md) |

### 会话管理（`src/mini_agent/cli/commands/sessions.py`）

| 命令 | 说明 |
|------|------|
| `/session` | 显示当前 session 信息（ID、文件、轮数、tokens） |
| `/session list [n]` | 列出最近 n 个 session（默认 20） |
| `/session save` | 立即保存当前 session |
| `/session resume <id>` | 加载历史 session，追加到当前历史 |
| `/session new` | 清空历史，开始新 session |
| `/session delete <id>` | 删除 session 文件 |
| `/session dir` | 显示 session 目录路径 |
| `/session search <q>` | 关键词搜索 session（需 `--session-search`） |

### 任务管理（`src/mini_agent/cli/commands/tasks.py`）

| 命令 | 说明 |
|------|------|
| `/tasks` | 显示所有 Sub-Agent 任务表格 |
| `/tasks dashboard` | 实时任务看板（阻塞直到所有任务完成） |
| `/tasks log <id>` | 显示指定任务的日志和输出 |
| `/tasks cancel <id>` | 取消指定任务 |
| `/tasks cancel-all` | 取消所有 pending/running 任务 |
| `/tasks workers <n>` | 动态调整最大并发工作线程数 |
| `/tasks focus <id>` | 进入指定任务的焦点模式（实时查看日志） |
| `/tasks unfocus` | 退出任务焦点模式 |

**键盘快捷键（运行时）**：
- `→` 或 `↓` — 进入/切换到下一个任务日志视图
- `←` 或 `↑` — 切换到上一个任务日志视图
- `ESC` — 退出任务焦点模式

详见 [Task 日志实时查看与切换](task-focus-viewing.md)。

### 执行计划（`src/mini_agent/cli/commands/plans.py`）

| 命令 | 说明 |
|------|------|
| `/plan` | 显示当前执行计划（Rich 树形） |
| `/plan clear` | 清除当前计划 |
| `/plan summary` | 打印完成摘要表格（含用时、结果、来源） |

### 并发控制（`src/mini_agent/cli/commands/concurrency.py`）

| 命令 | 说明 |
|------|------|
| `/concurrency` 或 `/cc` | 显示当前并发状态（tasks/LLM 占用、队列） |
| `/concurrency tasks <n>` | 设置最大并发任务数 |
| `/concurrency llm <n>` | 设置最大并发 LLM 调用数 |

### Provider 管理（`src/mini_agent/cli/commands/providers.py`）

| 命令 | 说明 |
|------|------|
| `/provider` | 显示当前 LLM provider 信息 |
| `/provider list` | 列出所有注册的 providers |
| `/provider switch <name> [model]` | 运行时切换 provider |

### 模型

| 命令 | 说明 |
|------|------|
| `/model <name>` | 中途切换模型（如 `/model claude-haiku-4-5`） |

### 自定义子 Agent（`src/mini_agent/cli/commands/agents.py`）

> 详见 [自定义子 Agent 指南](custom-sub-agents.md)、[Role Agent 指南](role-agents-guide.md)

| 命令 | 说明 |
|------|------|
| `/agents` 或 `/agents list` | 列出所有自定义子 agent profile（名称、描述、模型、可用工具） |
| `/agents show <name>` | 显示指定 profile 的详细信息 |
| `/agents reload` | 重新扫描 `.agent/agents/`（项目级）与 `~/.agent/agents/`（全局级）目录 |

> 注意命名相似但完全不同的两个系统：本节的 `/agents`（`AgentProfile`，自定义**子 agent 角色模板**，目录在 `.agent/agents/`）与上文的 `/profile`（`UserProfileManager`，自动学习的**用户个人画像**，与 spawn 子 agent 无关）。

### Hooks（`src/mini_agent/cli/commands/hooks.py`）

> 详见 [Hooks 机制指南](hooks.md)

| 命令 | 说明 |
|------|------|
| `/hooks` 或 `/hooks list` | 按事件分组列出已加载的 hooks |
| `/hooks reload` | 重新加载 `.agent/hooks.json`（项目级）与 `~/.agent/hooks.json`（全局级） |

### 自我演化（`src/mini_agent/cli/commands/evolution.py` / `evolve.py`）

> 详见 [Stage 2 安全网指南](self-evolution-stage2-guide.md)、[Stage 3.1 lesson → skill 闭环指南](self-evolution-stage3-1-guide.md)、[Phase G 后台循环指南](self-evolution-phase-g-guide.md)

| 命令 | 说明 |
|------|------|
| `/evolution log [N]` | 展示最近 N 条自我修改 commit（默认 10），表格形式 |
| `/evolution show <commit>` | 展示单条 commit 的完整结构化信息 + diff |
| `/evolution diff <commit>` | 展示某次 commit 的改动 diff（语法高亮） |
| `/evolution revert <commit>` | 生成 revert commit，并自动记录一条 `source="revert_record"` 的 lesson |
| `/evolve review [--global] [--tier T1\|T2]` | 扫描 lesson（默认 workdir 级 `memory.jsonl`，`--global` 扫描 `~/.agent/memory.jsonl`），对达标分组 spawn `evolution-agent` 提案 |
| `/evolve list [--global] [--tier T1\|T2]` | 同 `review`，但只扫描 + 列出达标分组，不 spawn agent、不消耗 LLM 调用 |
| `/evolve phase-g [--force] [--dry-run]` | **Stage 8** 手动触发 Phase G 后台循环扫描（剪枝候选 + 能力地图 + 晋升候选）。`--force` 跳过 24h 时间门控，`--dry-run` 只展示不写入节奏记录 |

`commit` 参数支持完整 hash 或前缀。

---

### Goal Backlog 与自主调度（`src/mini_agent/cli/commands/goals.py`）

> **Stage 9** 跨会话目标层级 + AutonomousLoop 状态查询。详见 [Stage 9 自主运行时指南](self-evolution-stage9-guide.md)

| 命令 | 说明 |
|------|------|
| `/agent goals` | 列出 Goal Backlog 中所有 active Goals 和 Objectives |
| `/goals` | `/agent goals` 的快捷方式 |
| `/agent goals add <title> [--priority N] [--tag t1,t2]` | 添加 Goal（长期目标） |
| `/agent goals obj add <title> [--goal <id>] [--thread <id>]` | 添加 Objective（子目标，可关联 Goal 和 WorkThread） |
| `/agent goals done <id>` | 标记 Goal/Objective 完成 |
| `/agent goals abandon <id>` | 标记 Goal/Objective 放弃 |
| `/agent goals pause <id>` | 暂停 Goal/Objective |
| `/agent goals progress <id> <notes>` | 更新进展备注 |
| `/agent goals status` | 显示 AutonomousLoop tick 状态（档位/上次 tick/tick 次数） |
| `/digest` | 显示自上次交互以来的自主活动摘要（来自 `activity_digest.jsonl`） |
| `/agent digest` | 同 `/digest` |

---

## 五、`mini-agent daemon` 子命令

> **Stage 9** 守护进程管理。详见 [Stage 9 自主运行时指南](self-evolution-stage9-guide.md)

```bash
# 前台启动（开发调试，Ctrl-C 停止）
mini-agent daemon start

# 后台启动（写 PID 文件，等待 HTTP 就绪）
mini-agent daemon start --detach

# 指定端口（默认 8765）
mini-agent daemon start --detach --http-port 9000

# 停止（发送 SIGTERM，等待优雅关闭）
mini-agent daemon stop

# 查看状态（PID/端口/autonomy_level/上次 tick）
mini-agent daemon status
```

---

> 详见 [Stage 3.2 eval 反馈环指南](self-evolution-stage3-2-guide.md)

不属于 REPL slash 命令，是独立的进程入口子命令（`mini-agent eval ...`），用于
对比某个 skill 开启/排除前后的 turns/token/tool 失败率：

```bash
mini-agent eval --scenario test_cases/ --skill docx
mini-agent eval --scenario test_cases/ --skill docx --output /tmp/report.json
mini-agent eval --scenario test_cases/                      # 不传 --skill，跑 baseline 冒烟测试
```

主要参数：`--scenario DIR`（必填）、`--skill NAME`、`--pattern GLOB`、
`--max-scenario-turns N`（默认 10）、`--project DIR`、`--skills-dir DIR`、
`--output FILE`、`--no-sandbox`、`--max-turns N`、`--quiet`。

---

## 六、内置工具

### 文件操作（builtin.py）

| 工具 | 需要审批 | 参数 | 说明 |
|------|----------|------|------|
| `read_file` | ❌ | `path`, `start_line`, `end_line` | 读取文件内容，支持行范围 |
| `write_file` | ✅ | `path`, `content` | 覆盖写入文件 |
| `create_file` | ✅ | `path`, `content` | 创建新文件（已存在则失败） |
| `delete_file` | ✅ | `path` | 删除单个文件 |
| `patch_file` | ✅ | `path`, `old_string`, `new_string` | 精确查找替换编辑文件，精确匹配失败时自动尝试空白符规整化的兜底匹配 |
| `list_dir` | ❌ | `path`, `depth` | 列出目录内容 |
| `glob` | ❌ | `pattern`, `root` | 通配符查找文件 |
| `grep` | ❌ | `pattern`, `path`, `file_pattern`, `case_sensitive` | 正则搜索文件内容 |
| `diff_files` | ❌ | `path_a`, `path_b`, `context_lines` | 比较两个文件，返回 unified diff（默认 3 行上下文，最多 20 行），结尾附加变更行数统计 |
| `tree_summary` | ❌ | `path`, `depth`, `show_files`, `include_hidden` | 输出紧凑的目录骨架（只显示目录+文件数+总大小），比 `list_dir` 更省 token；自动跳过 `.git`/`__pycache__`/`node_modules`/`.venv` 等常见构建/缓存目录 |

### Shell（builtin.py）

| 工具 | 需要审批 | 参数 | 说明 |
|------|----------|------|------|
| `bash` | ✅ | `command`, `timeout`, `workdir` | 执行 Shell 命令 |

### 搜索（builtin.py）

| 工具 | 需要审批 | 参数 | 说明 |
|------|----------|------|------|
| `web_search` | ❌ | `query`, `max_results`, `provider` | Web 搜索，支持 duckduckgo（默认）、brave、serper、tavily 后端 |

**web_search 配置：**

```json
{
  "web_search": {
    "provider": "duckduckgo",
    "max_results": 5,
    "timeout": 10.0
  }
}
```

**环境变量：** `WEB_SEARCH_PROVIDER`, `BRAVE_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`

详见 [Web Search 指南](web-search-guide.md)。

### Sub-Agent 编排（orchestration.py）

| 工具 | 需要审批 | 参数 | 说明 |
|------|----------|------|------|
| `spawn_agent` | ❌ | `prompt`, `name`, `depends_on`, `model`, `system_extra`, `tags` | 派生单个 Sub-Agent |
| `spawn_agents` | ❌ | `tasks` | 批量派生多个 Sub-Agent |
| `list_agent_profiles` | ❌ | （无） | 列出所有预设的自定义子 agent profile，含描述与所需输入参数，配合 `spawn_named_agent` 使用 |
| `spawn_named_agent` | ❌ | `agent_type`, `inputs`, `context`, `name`, `depends_on` | 派生一个预设角色的子 agent（`agent_type` 取自 `list_agent_profiles`），`inputs` 需匹配该 profile 声明的输入参数；异步执行，返回 `task_id` |
| `get_task_status` | ❌ | `task_id`, `full` | 查询任务状态和结果；输出超 3000 字符且 `full=False` 时返回 `truncated`/`full_length` 字段，提示用 `full=True` 重新取完整内容 |
| `update_task_progress` | ❌ | `task_id`, `current_step`, `steps_done`, `steps_remaining`, `blockers`, `note` | 主动记录长任务进度到 `manifest.json`，`note` 追加到 `decision_log` |
| `list_tasks` | ❌ | `status`, `tag` | 列出所有任务 |
| `cancel_task` | ❌ | `task_id` | 取消指定任务 |
| `wait_for_tasks` | ❌ | `task_ids`, `timeout_seconds` | 等待多个任务完成 |

详见 [Plan 与 Task 机制说明](plan-and-task-guide.md) 中 `manifest.json` 相关章节、[存储设计](storage-design.md#44-subagent-任务文件)、[自定义子 Agent 指南](custom-sub-agents.md)。

### 执行计划（plan.py）

| 工具 | 说明 |
|------|------|
| `create_plan` | 创建完整执行计划（含任务树） |
| `start_task` | 标记任务开始（pending → running） |
| `complete_task` | 标记任务完成，记录结果摘要 |
| `fail_task` | 标记任务失败，触发级联 skipped |
| `add_task` | 运行时动态追加任务节点 |
| `get_plan_status` | 返回完整计划状态 JSON |
| `clear_plan` | 清除当前计划 |

### 用户交互（user_input.py）

| 工具 | 说明 |
|------|------|
| `ask_user` | 向用户请求补充文本信息 |
| `ask_user_confirm` | 向用户请求 yes/no 确认 |
| `ask_user_choice` | 向用户提供多选项选择 |

### Skill 管理（skill_manager.py，由 Agent 动态注册）

| 工具 | 说明 |
|------|------|
| `skill_list` | 列出所有技能的名称、描述、激活状态 |
| `skill_activate` | 按名称激活技能，需提供原因 |
| `skill_deactivate` | 按名称卸载技能，需提供原因 |
| `skill_stats` | 返回技能使用追踪和预算状态 |
| `compact_history` | 触发带 Skill 重附逻辑的历史压缩 |

### 自我演化（evolution.py，Stage 3.1）

| 工具 | 需要审批 | 参数 | 说明 |
|------|----------|------|------|
| `skill_propose` | ❌（沙箱拦截 + T1 校验流水线把关） | `name`, `content`, `source_lessons`, `reason` | 把新 SKILL.md 提案为一个 `evolve/<date>-skill-<name>` 分支上的 commit（`StateRepo.apply()`，tier=T1），不会自动生效，需人工 review + merge |

详见 [自我演化 lesson → skill 闭环指南（Stage 3.1）](self-evolution-stage3-1-guide.md)。

---

## 七、命令执行流程

```
用户输入
  ↓
ui/terminal.py（prompt_user，阻塞读取）
  ↓
cli/repl.py（slash 命令路由 or agent.run_turn()）
  ↓
agent.py（_agentic_loop：LLM 调用 + 工具执行循环）
  ↓
tools/*（@tool 函数）← permissions.py（权限检查）
  ↓
结果追加历史 → 下一轮 LLM 调用 or 输出最终答复
  ↓
ui/terminal.py（stream_token / print）
```

---

## 八、常用命令示例

```bash
# 启动并指定模型
mini-agent --model claude-haiku-4-5

# 沙箱模式（安全测试）
mini-agent --sandbox

# 自动批准所有操作
mini-agent --yes

# 使用本地 Ollama
mini-agent --provider ollama --model qwen2.5-coder:7b

# 恢复历史会话
mini-agent --resume abc123

# 启用项目扫描 + 文件监听
mini-agent --project-scan --file-watch

# 调试 LLM 请求
mini-agent --debug-llm-console
```

```text
# 常用 REPL 操作
/help              查看帮助
/skills            查看技能列表
/skill on docx     激活 docx 技能
/skill stats       查看技能使用情况
/session list      查看历史会话
/session resume abc123   恢复历史会话
/tasks             查看 Sub-Agent 任务
/tasks dashboard   实时任务看板
/plan              查看执行计划
/concurrency       查看并发状态
/compact           压缩历史释放 context
/stats             查看会话统计
```

---

*最后更新：2026-06*