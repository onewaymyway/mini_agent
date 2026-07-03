# HTTP API 使用指南

> mini-agent 内置 FastAPI HTTP 服务，支持通过 REST 和 SSE 与 agent 交互。

## 快速开始

### 启动 HTTP 服务

```bash
# 使用命令行参数启动
python -m mini_agent --http

# 指定端口和主机
python -m mini_agent --http --http-port 8765 --http-host 0.0.0.0

# 设置 API 令牌
python -m mini_agent --http --http-token your-secret-token

# 允许特定 IP 访问
python -m mini_agent --http --http-allow-ip "127.0.0.1,192.168.1.100"

# 文件系统只读模式
python -m mini_agent --http --http-fs-readonly
```

### 配置文件方式

在 `agent_config.json` 中配置：

```json
{
  "http_enabled": true,
  "http_host": "127.0.0.1",
  "http_port": 8765,
  "http_api_token": "your-secret-token",
  "http_allowed_ips": ["127.0.0.1", "::1"],
  "http_cors_origins": ["http://localhost:3000"],
  "http_fs_readonly": false,
  "http_fs_excludes": [".git", "node_modules"],
  "http_ring_maxlen": 2000
}
```

## 认证

HTTP API 默认使用 Bearer Token 认证。启动时会显示或生成 token：

```
  🌐  HTTP API server started
  URL  : http://127.0.0.1:8765/v1
  Token: your-generated-token
```

所有 API 请求需要在 `Authorization` header 中携带 token：

```bash
curl -H "Authorization: Bearer your-secret-token" http://127.0.0.1:8765/v1/health
```

## API 端点总览

### 系统端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/health` | GET | 健康检查 |
| `/v1/whoami` | GET | 当前 token 对应的身份（多用户模式下返回 user_id/name/role；单用户模式固定返回 owner，向后兼容） |
| `/v1/status` | GET | Agent 状态（空闲/运行中）+ 统计信息 |
| `/v1/diagnostics` | GET | **Stage 6** 实时健康诊断（性能 + 内存 + skills + 演化状态 + 异常标记）|
| `/docs` | GET | Swagger API 文档 |

### 对话端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat` | POST | 发送消息，返回 `turn_id` |
| `/v1/interrupt` | POST | 中断当前执行 |
| `/v1/history` | GET | 获取对话历史 |
| `/v1/history` | DELETE | 清空对话历史 |

### 流式输出

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/stream` | GET | SSE：订阅所有实时事件 |
| `/v1/stream/{turn_id}` | GET | SSE：只订阅特定轮次的事件 |

### 事件历史

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/events` | GET | 获取历史事件列表（JSON） |
| `/v1/turns` | GET | 列出所有轮次 |
| `/v1/turns/{turn_id}` | GET | 获取特定轮次详情 |

### 权限审批

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/permissions/pending` | GET | 获取待审批的权限请求 |
| `/v1/permissions/{req_id}` | POST | 批准/拒绝权限请求 |

### 文件系统

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/fs/list` | GET | 列出目录内容（`?path=xxx`） |
| `/v1/fs/read` | GET | 读取文件内容（`?path=xxx`） |
| `/v1/fs/stat` | GET | 获取文件详情（`?path=xxx`） |
| `/v1/fs/download` | GET | 下载文件（`?path=xxx`） |
| `/v1/fs/search` | GET | 搜索文件（`?q=xxx&content=0`） |
| `/v1/fs/write` | POST | 写入文件 |
| `/v1/fs/mkdir` | POST | 创建目录 |
| `/v1/fs/delete` | DELETE | 删除文件/目录 |
| `/v1/fs/rename` | POST | 重命名/移动文件 |
| `/v1/fs/upload` | POST | 上传文件 |

## 使用示例

### 1. 发送消息

```bash
curl -X POST http://127.0.0.1:8765/v1/chat \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我写一个 Python 质数筛法函数"}'
```

返回：
```json
{"turn_id": "turn_abc123", "queued": true}
```

### 2. SSE 流式订阅

```bash
# 使用 curl 查看 SSE 流
curl -N http://127.0.0.1:8765/v1/stream \
  -H "Authorization: Bearer your-token"

# 使用 JavaScript EventSource
const source = new EventSource("http://127.0.0.1:8765/v1/stream", {
  headers: { "Authorization": "Bearer your-token" }
});

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("Event:", data);
};
```

SSE 事件类型：
- `token` - 流式输出的 token
- `tool_call` - 工具调用请求
- `tool_result` - 工具执行结果
- `turn_start` - 新轮次开始
- `turn_done` - 轮次完成
- `error` - 错误事件
- `status` - 状态更新
- `info` / `warning` - 信息/警告

### 3. 获取历史事件

```bash
# 获取最近 100 个事件
curl http://127.0.0.1:8765/v1/events?limit=100 \
  -H "Authorization: Bearer your-token"

# 从指定事件 ID 之后获取
curl http://127.0.0.1:8765/v1/events?since_id=100 \
  -H "Authorization: Bearer your-token"

# 过滤特定类型事件
curl http://127.0.0.1:8765/v1/events?type=token \
  -H "Authorization: Bearer your-token"
```

### 4. 文件操作

```bash
# 列出当前目录
curl http://127.0.0.1:8765/v1/fs/list?path=. \
  -H "Authorization: Bearer your-token"

# 读取文件
curl http://127.0.0.1:8765/v1/fs/read?path=README.md \
  -H "Authorization: Bearer your-token"

# 写入文件
curl -X POST http://127.0.0.1:8765/v1/fs/write \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"path": "test.txt", "content": "Hello World"}'

# 上传文件
curl -X POST "http://127.0.0.1:8765/v1/fs/upload?path=uploaded.txt" \
  -H "Authorization: Bearer your-token" \
  -F "file=@/path/to/local/file.txt"
```

### 5. 权限审批

当 agent 需要执行需要审批的工具时：

```bash
# 查看待审批请求
curl http://127.0.0.1:8765/v1/permissions/pending \
  -H "Authorization: Bearer your-token"

# 批准权限请求
curl -X POST http://127.0.0.1:8765/v1/permissions/req_123 \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"approve": true}'

# 拒绝权限请求
curl -X POST http://127.0.0.1:8765/v1/permissions/req_123 \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"approve": false}'
```

### 6. Python 客户端示例

```python
import httpx
import asyncio

API_BASE = "http://127.0.0.1:8765/v1"
TOKEN = "your-token"

headers = {"Authorization": f"Bearer {TOKEN}"}

async def chat(message: str):
    # 发送消息
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_BASE}/chat",
            headers=headers,
            json={"message": message}
        )
        data = resp.json()
        turn_id = data["turn_id"]
        print(f"Queued turn: {turn_id}")

        # 轮询状态
        while True:
            status = await client.get(f"{API_BASE}/status", headers=headers)
            state = status.json()["state"]
            if state == "idle":
                break
            await asyncio.sleep(0.5)

        # 获取历史
        history = await client.get(f"{API_BASE}/history", headers=headers)
        print("History:", history.json())

asyncio.run(chat("你好，请介绍一下自己"))
```

## 配置选项详解

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `http_enabled` | 是否启动 HTTP 服务 | `false` |
| `http_host` | 监听地址 | `127.0.0.1` |
| `http_port` | 监听端口 | `8765` |
| `http_api_token` | API 认证令牌 | 自动生成 |
| `http_allowed_ips` | IP 白名单 | `127.0.0.1`, `::1` |
| `http_cors_origins` | CORS 允许的来源 | `*` |
| `http_fs_readonly` | 文件系统只读模式 | `false` |
| `http_fs_excludes` | 文件系统排除路径 | `[]` |
| `http_ring_maxlen` | 事件环缓冲区大小 | `2000` |

## 安全注意事项

1. **默认仅本地访问**：HTTP 服务默认仅监听 `127.0.0.1`，不要随意设置为 `0.0.0.0`
2. **使用强 token**：生产环境务必设置 `http_api_token`
3. **IP 白名单**：通过 `http_allowed_ips` 限制可访问的 IP
4. **文件系统只读**：对外服务建议使用 `--http-fs-readonly`
5. **CORS 配置**：合理配置 `http_cors_origins` 防止跨站攻击

## 架构说明

HTTP API 服务采用以下设计：

- **AgentRunner 线程**：独立线程消费命令队列，驱动 `agent.run_turn()`
- **OutputBroadcaster**：拦截 agent 输出，广播到 HTTP 客户端
- **事件环（Ring Buffer）**：存储历史事件，支持回放
- **SSE 订阅机制**：支持断线重连和增量回放
- **输出钩子**：通过 monkey-patch Renderer 实现无需修改 agent.py 的输出接入
- **命令行协同**：HTTP 请求会在终端显示 "You (web) ❯ <message>"，与正常 REPL 输入体验一致

```n
┌─────────────────────────────────────────────────────────────┐
│                    HTTP Client                              │
└──────────────────────┬──────────────────────────────────────┘
                       │ REST / SSE
┌──────────────────────▼──────────────────────────────────────┐
│              FastAPI Server (uvicorn)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ AuthMiddleware│  │  Routes   │  │  OutputBroadcaster  │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                    AgentBridge                              │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐ │
│  │InputQueue     │  │OutputBroadcas-│  │PermissionGate   │ │
│  │(命令队列)      │  │ter (事件广播) │  │(HTTP 审批)      │ │
│  └───────────────┘  └───────────────┘  └─────────────────┘ │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                AgentRunner (线程)                           │
│  dequeue() → agent.run_turn() → emit events                │
│  终端显示：You (web) ❯ <message>                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                    mini-agent Core                          │
│            (agent.py + tools + llm)                         │
└─────────────────────────────────────────────────────────────┘
```

## /diagnostics 端点详解（Stage 6）

`GET /v1/diagnostics` 聚合当前 session 的健康状态，适合监控、调试和演化数据查看。

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8765/v1/diagnostics
```

响应包含五个分组（任一失败静默降级为空对象）：

| 分组 | 数据来源 | 典型用途 |
|------|---------|---------|
| `performance` | `traces.jsonl`（当前 session）| 查看 LLM 耗时 / token 分布 / 工具错误率 |
| `memory` | `memory.jsonl` | 确认记忆条目数量与类型分布 |
| `skills` | `SkillLoader`（运行时）| 查看当前激活的 skill 列表 |
| `evolution` | `self_profile.json` + `open_threads.json` | 待审核演化分支 / 高优先级悬挂线索 |
| `anomaly_flags` | `activity_log.jsonl`（历史基线）| 检测当前 session 是否异常 |

完整响应格式见 [观察性系统指南](observability-guide.md)。

---

## 相关文档

- [权限系统指南](permission-guide.md) — HTTP 审批流程详解
- [命令与工具参考](commands-and-tools-reference.md) — 所有可用工具
- [Agent 设计](agent-design.md) — Agent 核心循环
- [CLI I/O 机制](cli-io-mechanism.md) — HTTP 与命令行协同机制
- [Web Demo 指南](web-demo-guide.md) — Streamlit Web 界面使用
- [观察性系统指南](observability-guide.md) — `/diagnostics` 端点与 traces.jsonl 详解

---

## Stage 9 Daemon 模式说明

**Stage 9** 将 HTTP 服务升级为首选的常驻接入点：

### /v1/status 新增字段

`GET /v1/status` 响应中新增 daemon 状态字段：

```json
{
  "state": "idle",
  "turn_id": null,
  "stats": {...},
  "queue_depth": 0,
  "subscribers": 1,
  "autonomy_level": "maintenance",
  "last_autonomous_tick_at": 1720000000.0,
  "tick_count": 42
}
```

| 字段 | 说明 |
|------|------|
| `subscribers` | 当前连接的 SSE 客户端数 |
| `autonomy_level` | 当前档位（`passive`/`maintenance`/`autonomous`） |
| `last_autonomous_tick_at` | 上次 autonomous tick 的 Unix 时间戳 |
| `tick_count` | daemon 启动以来的总 tick 次数 |

### /v1/autonomous/status — 自主执行状态

`GET /v1/autonomous/status` 返回 daemon 自主执行的完整实时视图：

```bash
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8765/v1/autonomous/status
```

响应示例：

```json
{
  "autonomy_level": "maintenance",
  "next_tick_in": 47.3,
  "cron_jobs": [
    {
      "id": "sys:phase_g",
      "name": "Phase G 扫描",
      "enabled": true,
      "next_run_in": 18420,
      "next_run_str": "in 5.1h",
      "run_count": 12,
      "last_run_at": 1720000000.0
    }
  ],
  "objective_executions": [
    {
      "execution_id": "exec_a1b2c3d4",
      "objective_id": "obj_xyz",
      "title": "完善测试覆盖",
      "status": "running",
      "progress": "2/4",
      "current_step": "识别未覆盖的函数路径",
      "started_at": 1720000000.0
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `autonomy_level` | 当前档位 |
| `next_tick_in` | 距下次 `AutonomousLoop.tick()` 还有多少秒 |
| `cron_jobs` | 所有 cron job 状态列表 |
| `objective_executions` | 活跃 Objective 执行进度列表（完成超过 1h 的自动移除） |

### /v1/goals — Goal Backlog REST API

```bash
# 获取完整 GoalBacklog（所有 active Goals 和 Objectives）
GET /v1/goals

# 添加新 Goal
POST /v1/goals
Body: {
  "title": "完善测试覆盖",
  "description": "提升单元测试覆盖率到 80%",
  "priority": 70,
  "source": "user"
}

# 更新 Goal 状态/进展
PATCH /v1/goals/{goal_id}
Body: {
  "status": "completed",         # active | paused | completed | abandoned
  "progress_notes": "覆盖率已达 82%",
  "priority": 50
}
```

`PATCH` 时将 `status` 设为 `"abandoned"` 且目标为 `source="agent_derived"` 时，
系统会自动记录到 `soft_goal_rejected.json`，30 天内不会再 derive 相同主题。

`GET /v1/goals` 响应示例：

```json
{
  "goals": [
    {
      "id": "goal_abc12345",
      "level": "goal",
      "title": "完善测试覆盖",
      "source": "user",
      "status": "active",
      "priority": 70,
      "created_at": 1720000000.0
    }
  ],
  "objectives": [
    {
      "id": "obj_def67890",
      "level": "objective",
      "parent_id": "goal_abc12345",
      "title": "为 agent.py 补充单元测试",
      "status": "active",
      "progress_notes": "已完成接口扫描"
    }
  ]
}
```

### /v1/cron/jobs — Cron Job REST API

```bash
# 列出所有 cron job
GET /v1/cron/jobs

# 添加用户 job
POST /v1/cron/jobs
Body: {
  "name": "daily-summary",
  "schedule": "cron:0 9 * * *",
  "task_template": "生成昨日工作摘要",
  "description": "每天 09:00 自动生成摘要"
}

# 修改 job（启用/禁用/改 schedule）
PUT /v1/cron/jobs/{job_id}
Body: { "enabled": false }
Body: { "schedule": "interval:7200" }

# 立即触发一次（不影响 next_run_at）
POST /v1/cron/jobs/{job_id}/run
```

`GET /v1/cron/jobs` 响应示例：

```json
{
  "jobs": [
    {
      "id": "sys:phase_g",
      "name": "Phase G 扫描",
      "schedule": "interval:21600",
      "description": "技能剪枝、去重、能力地图更新（每 6 小时）",
      "enabled": true,
      "last_run_at": 1720000000.0,
      "next_run_at": 1720021600.0,
      "next_run_str": "in 5.1h",
      "run_count": 12,
      "tags": ["maintenance", "evolution"]
    }
  ]
}
```

### SSE 新增事件类型：`objective_progress`

`GET /v1/stream` 或 `GET /v1/stream/{turn_id}` 中，当 daemon 自主推进 Objective 步骤时，
会推送 `objective_progress` 类型的 SSE 事件：

```json
{
  "type": "objective_progress",
  "data": {
    "execution_id": "exec_a1b2c3d4",
    "objective_id": "obj_xyz",
    "title": "完善测试覆盖",
    "status": "running",
    "progress": "3/4",
    "current_step": "运行测试并修复失败用例"
  }
}
```

客户端可通过此事件实时更新 Objective 执行进度条，无需轮询 `/v1/autonomous/status`。

### Daemon 启动流程

```bash
# 1. 启动 daemon（后台常驻）
mini-agent daemon start --detach

# 2. CLI 连接（任意终端）
mini-agent          # 检测到 daemon 运行时自动进入连接模式

# 3. 查看 daemon 状态
mini-agent daemon status

# 4. 停止
mini-agent daemon stop
```

Daemon 启动后，所有客户端（CLI 连接模式 + Web Demo）通过相同的 HTTP API 接入，行为完全对称。

## Web Demo

项目提供了一个基于 Streamlit 的 Web 演示界面，位于 `apps/mini_agent_webdemo/app.py`。

### 快速启动

```bash
# 安装依赖
pip install streamlit requests

# 启动 Web Demo
streamlit run apps/mini_agent_webdemo/app.py
```

### 功能特性

- **对话界面**：现代化的聊天界面，支持多轮对话
- **实时事件流**：显示工具调用、token 输出等实时事件
- **权限审批**：可视化审批工具调用权限请求
- **文件系统浏览**：通过 Web 界面浏览和读取项目文件
- **Turn 历史**：查看完整的对话轮次记录
- **连接状态**：实时显示 Agent 状态（空闲/运行中/等待审批）

### 界面布局

```
┌─────────────────────────────────────────────────────────────┐
│  🤖 Mini Agent Web Demo                                     │
├──────────────────────┬──────────────────────────────────────┤
│  侧边栏              │  对话区                              │
│  - 服务配置          │  ┌───────────────────────────────┐  │
│  - Token 设置        │  │ 👤 你：你好                   │  │
│  - 连接状态          │  │ 🤖 Agent：你好！有什么可以   │  │
│  - 运行统计          │  │   帮你做的吗？                │  │
│  - 视图开关          │  └───────────────────────────────┘  │
│  - 操作按钮          │                                     │
│                      │  ┌───────────────────────────────┐  │
│                      │  │ 输入框 + 发送按钮             │  │
│                      │  └───────────────────────────────┘  │
└──────────────────────┴─────────────────────────────────────┘
```

### 配置选项

| 选项 | 说明 |
|------|------|
| API 地址 | HTTP 服务地址，默认 `http://127.0.0.1:8765/v1` |
| Token 来源 | 支持手动输入或从文件读取 |
| 显示事件流 | 开关右侧事件流面板 |
| 文件系统 | 开关文件浏览面板 |

### 使用场景

1. **快速测试**：无需编写代码，直接测试 agent 功能
2. **演示展示**：向他人展示 agent 的工作方式
3. **开发调试**：实时查看事件流和工具调用
4. **移动访问**：通过浏览器在移动设备上使用
