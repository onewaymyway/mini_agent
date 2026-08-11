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
| `/v1/models` | GET | 列出 daemon 端 `LLMClientPool` 当前可用的模型名（供 daemon 连接模式下 CLI 客户端的 `/model` Tab 补全拉取，见 `api/routes.py` 顶部注释） |
| `/v1/diagnostics` | GET | **Stage 6** 实时健康诊断（性能 + 内存 + skills + 演化状态 + 异常标记）|
| `/docs` | GET | Swagger API 文档 |

> **多用户/多 session 相关端点**（`/v1/sessions`、`/v1/sessions/new`、`/v1/sessions/{session_id}`、
> `/v1/sessions/{session_id}/resume`、`/v1/users`、`/v1/users/{user_id}`、`/v1/users/{user_id}/token`）
> 单独在 [多用户模式指南](multi-user-guide.md) 的「API 端点」章节说明，这里不重复列出。

### 对话端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat` | POST | 发送消息，返回 `turn_id` |
| `/v1/interrupt` | POST | 中断当前执行 |
| `/v1/history` | GET | 获取对话历史（`limit`/`before_seq` 分页，响应含 `total`/`has_more`） |
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

### 通用交互式提问

daemon connected 模式下，`ask_user`/`ask_user_confirm`/`ask_user_choice` 三个工具、
`/goal` 目标协商子对话、以及任意 slash 命令内部残留的 `prompt_user()` 调用，都通过
这两个端点转发给远程客户端（与权限审批是完全对称的双路机制：本地终端和 HTTP 客户端
谁先回答就用谁的）。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/interactions/pending` | GET | 获取待回答的通用交互请求列表 |
| `/v1/interactions/{req_id}` | POST | 回答一次交互请求 |

SSE 里对应的事件类型是 `interaction_req`（推送问题）/`interaction_done`（回答结果），
`data` 里的 `kind` 字段区分问法：`ask_user` / `ask_user_confirm` / `ask_user_choice` /
`goal_negotiation` / `repl_prompt`。回答 body 按 kind 使用不同字段：

```jsonc
// ask_user            -> {"answer": "文本回答"}
// ask_user_confirm     -> {"confirmed": true}
// ask_user_choice      -> {"choice_index": 0}   // 或 {"answer": "选项文字"}
// goal_negotiation      -> {"answer": "/confirm" | "/cancel" | "修改意见原文"}
// repl_prompt           -> {"answer": "原始输入文本"}
```

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

### 产出物 Artifacts

供「产出物看板」/「产出预览」Tab 使用，与 `/v1/fs/*`（遍历目录）不同，这里消费的是
显式登记的产出物 Manifest（`storage/artifacts.py`），详见
`docs/artifacts-dashboard-guide.md`。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/artifacts` | GET | 列出产出物摘要（`?session_id=xxx` 过滤，`?limit=&offset=` 分页） |
| `/v1/artifacts/{manifest_id}` | GET | 获取单次产出的完整 manifest（含文件明细） |
| `/v1/artifacts/{manifest_id}/file` | GET | 取 manifest 内某个文件（`?index=0`，`?download=true` 走附件下载） |

### 用户行为感知（Behavior Perception，默认全部关闭）

采集桌面/浏览器/手机端的行为信号，聚合成"工作与生活画像"日报，详见
`docs/behavior-perception-guide.md`。总开关和各采集器子开关默认全部
关闭，需要用户显式打开（`/behavior on` 或下面的 toggle 接口）。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/perception/status` | GET | 总开关/各采集器状态 |
| `/v1/perception/toggle` | POST | 打开/关闭总开关或某个采集器（owner only） |
| `/v1/perception/report` | POST | 外部系统（浏览器插件/git hook/终端 hook/手机端）上报事件 |
| `/v1/perception/events` | GET | 查询已采集事件（`?source=&limit=&since=`） |
| `/v1/perception/events` | DELETE | 清空已采集事件（owner only） |
| `/v1/perception/browser/start` | POST | 启动专用调试浏览器（CDP 方案，owner only） |
| `/v1/perception/browser/stop` | POST | 停止采集，可选同时关闭浏览器进程（owner only） |
| `/v1/perception/browser/status` | GET | 专用浏览器/CDP 连接状态 |
| `/v1/perception/git/install-hooks` | POST | 在指定仓库安装 commit/checkout 上报 hook（owner only） |
| `/v1/perception/summary` | GET | 查看/生成某天的工作/生活画像摘要（`?date=YYYY-MM-DD`） |

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
- `permission_req` / `permission_done` - 工具调用权限审批请求/结果
- `interaction_req` / `interaction_done` - 通用交互式提问请求/结果（ask_user 系列工具、/goal 协商、任意 slash 命令的 prompt_user()）
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

### 5b. 通用交互式提问

当 agent 调用 `ask_user` 等工具，或用户在别的客户端发起了 `/goal <目标>` 协商时：

```bash
# 查看待回答的交互请求
curl http://127.0.0.1:8765/v1/interactions/pending \
  -H "Authorization: Bearer your-token"

# 回答一个开放式问题（ask_user / goal_negotiation / repl_prompt 都用 answer 字段）
curl -X POST http://127.0.0.1:8765/v1/interactions/req_456 \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"answer": "蓝色"}'

# 回答一个 y/n 确认（ask_user_confirm）
curl -X POST http://127.0.0.1:8765/v1/interactions/req_456 \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"confirmed": true}'

# 回答一个多选一（ask_user_choice）
curl -X POST http://127.0.0.1:8765/v1/interactions/req_456 \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"choice_index": 0}'
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
- [产出物看板指南](artifacts-dashboard-guide.md) — 产出物 Manifest 设计、自动侦测开关
- [用户行为感知系统指南](behavior-perception-guide.md) — 桌面/浏览器/手机端行为采集与工作生活画像日报
- [Kanban 看板使用指南](kanban-dashboard-guide.md) — Goal 执行规范生成/反馈迭代/确认的看板 UI 入口

---

## Stage 9 Daemon 模式说明

**Stage 9** 将 HTTP 服务升级为首选的常驻接入点：

### /v1/status 新增字段

`GET /v1/status` 响应中新增 daemon 状态字段：

```json
{
  "state": "running",
  "turn_id": "t_abc123",
  "stats": {...},
  "queue_depth": 0,
  "subscribers": 1,
  "autonomy_level": "maintenance",
  "last_autonomous_tick_at": 1720000000.0,
  "tick_count": 42,
  "session_id": "abc123",
  "model": "claude-sonnet-4-6",
  "session_dir": "/path/to/project/.agent/sessions/abc123",
  "project_root": "/path/to/project",
  "activity": "calling_tool",
  "activity_detail": "bash_tool"
}
```

| 字段 | 说明 |
|------|------|
| `subscribers` | 当前连接的 SSE 客户端数 |
| `autonomy_level` | 当前档位（`passive`/`maintenance`/`autonomous`） |
| `last_autonomous_tick_at` | 上次 autonomous tick 的 Unix 时间戳 |
| `tick_count` | daemon 启动以来的总 tick 次数 |
| `session_id` | 这次请求实际解析到的 session（见下方"按 session 查询状态"） |
| `model` | 当前实际使用的模型名（LLMClientPool 故障转移/`/model` 切换后会跟着更新，不是配置文件里固定的第一条） |
| `session_dir` | 该 session 的存储目录，`<project_root>/.agent/sessions/<session_id>/` |
| `project_root` | 项目根目录 |
| `activity` | 更细粒度的"正在做什么"：`waiting_input`（空闲）/ `waiting_permission`（等权限确认）/ `calling_model`（调用模型中）/ `calling_tool`（调用工具中）——比 `state` 里笼统的 `running` 更具体 |
| `activity_detail` | `activity=="calling_tool"` 时是工具名，其余情况为 `null` |

#### 按 session 查询状态 / 按 session 隔离请求

`GET /v1/status`、`GET /v1/history`、`GET /v1/events`、`GET /v1/turns`、
`GET /v1/permissions/pending`、`GET /v1/interactions/pending`、
`POST /v1/interrupt`、`DELETE /v1/history` 都支持 `?session_id=xxx` 查询参数；
`POST /v1/chat` 通过请求体 `{"session_id": "xxx"}` 传（对应 `ChatRequest.session_id`）。

单 token（非多用户）模式下：带了 `session_id` 的请求会被路由到该 session
专属的 `AgentBridge`（各自独立的 state / model / history / 事件流）；不带
`session_id` 的请求退回旧行为——操作服务端全局共享的默认 bridge。这是
`apps/mini_agent_kanban` 看板实现"多个页面各自绑定不同 session、同时并行
对话"的基础，详见 `docs/kanban-dashboard-guide.md` "多会话并行"一节。

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
      "id": "sys:consolidation",
      "name": "巩固循环 扫描",
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
| `autonomy_level` | 当前档位（配置值，见下方 `loop_active` 说明） |
| `next_tick_in` | 距下次 `AutonomousLoop.tick()` 还有多少秒 |
| `cron_jobs` | 所有 cron job 状态列表 |
| `objective_executions` | 活跃 Objective 执行进度列表（完成超过 1h 的自动移除） |
| `loop_active`（新增） | AutonomousLoop 是否真的挂在当前 daemon 上在 tick。`autonomy_level` 只是 `self_profile.json` 里的配置值，跟"tick 有没有真的在跑"是两回事——没启动 daemon、或启动时没注入 AutonomousLoop，这里恒为 `false`，Objective 永远不会被自动执行，排查"目标/Objective 加了但 agent 不执行"时第一个该看这个字段 |
| `has_actionable_work`（新增） | GoalBacklog 里是否存在 `status=active` 的 Objective（Goal 本身不算） |
| `objective_slots`（新增） | `{running, max}`，ObjectiveExecutor 并发槽位占用情况，槽位占满时新 Objective 只能排队 |
| `gating`（新增） | `ResourceArbiter.diagnose()` 的结果：`{can_run_autonomous, rules: [{rule, label, passed, reason, ...}]}`，逐条列出每日 token 预算、本体感知挫败感、用户在场行为门控三条规则的通过情况和具体数值 |

调用这个接口时，服务端会顺带记一笔仲裁状态变化（见下方
`/v1/autonomous/gating_history`），只有这次的 `gating_state` 和上一次记录
不一样时才会真正写入，不会因为轮询而膨胀成日志流。

### /v1/autonomous/gating_history — 仲裁状态变化时间线（scheduling_unification_and_kanban_visibility_improvement_plan.md P5）

`GET /v1/autonomous/gating_history?limit=50` 返回 `ResourceArbiter` 三态门控
（`full`/`degraded`/`blocked`）最近的状态变化记录，按时间正序（旧→新）排列。
只有状态相对上一条发生变化时才会产生一条记录（例如从 `full` 变成
`degraded` 又恢复到 `full` 算两条，中间反复轮询到同一状态不会重复记录），
最多保留 200 条。

```bash
curl -H "Authorization: Bearer <owner_token>" \
  "http://127.0.0.1:8765/v1/autonomous/gating_history?limit=50"
```

响应示例：

```json
{
  "history": [
    {
      "at": 1720000000.0,
      "at_str": "2026-08-05 10:00:00",
      "state": "full",
      "reason": ""
    },
    {
      "at": 1720003600.0,
      "at_str": "2026-08-05 11:00:00",
      "state": "degraded",
      "reason": "本体感知挫败感偏高"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `history` | 状态变化记录数组，按时间正序（旧→新）排列 |
| `history[].at` | Unix 时间戳（秒） |
| `history[].at_str` | 人类可读时间字符串 |
| `history[].state` | 变化后的门控状态：`full`/`degraded`/`blocked` |
| `history[].reason` | 对应的 `gating_reason`（可能为空字符串） |

数据写入依赖 `/v1/autonomous/status` 被实际调用过（该路由内部顺带触发
记录），因此如果 daemon 起来后长时间没有任何客户端轮询过
`/v1/autonomous/status`，期间发生的状态变化不会出现在这里。看板"🗓️ 全局
日程"Tab 消费此接口，详见 `docs/kanban-dashboard-guide.md`。

### /v1/self/status — Self（主自我）状态总览（daemon 多用户架构 Phase 4，owner only）

`GET /v1/self/status` 返回 daemon 里"Self"（也就是 `HttpServer` 自己持有的那个固定
`bridge`/`agent`，不是任何用户的 session）的状态总览：GoalBacklog、自主活动摘要
（含最近的 `session_crashed` 通知）、以及 `SessionAgentPool` 概况。单用户模式下也能
正常调用（只是没有 `session_pool` 那部分数据，其它字段仍然有效）——"Self"这个概念
本来就不是多用户模式特有的。

```bash
curl -H "Authorization: Bearer <owner_token>" \
  http://127.0.0.1:8765/v1/self/status
```

响应字段（均可能为 `null`/空，取决于当前是否开启了对应的自主执行/多 session 功能）：

| 字段 | 说明 |
|------|------|
| `autonomous_loop` | 自主循环摘要（等价于 `AutonomousLoop.get_digest_status()`），`AutonomousLoop` 未启用时为 `null` |
| `goals.active_objectives` / `goals.active_goals` | 当前 GoalBacklog 里活跃的 Objective / Goal 列表 |
| `recent_activity` | 最近的自主活动摘要（含最近的 `session_crashed` 通知） |
| `session_pool` | `SessionAgentPool` 概况（多用户/多 session 模式下才有内容） |

> 需要 owner token；非 owner 调用返回 403。和 `/v1/status`（面向当前 token 所在的单个
> session/agent）的区别是：`/v1/self/status` 固定看的是 daemon 进程自己的 Self agent，
> 不受调用者当前 session 影响。

### /v1/self/execution_model_status — 执行模型状态（owner only）

`GET /v1/self/execution_model_status` 只读汇总
[Daemon 执行模型与调度心跳指南](daemon-execution-model-guide.md) 里"目标级持久
Worker"（阶段一）和"调度心跳独立化"（阶段二）两个默认关闭的灰度开关当前的
生效状态，供看板"⚙️ 执行模型"区块展示。不修改任何状态、不触发任何调度。

```bash
curl -H "Authorization: Bearer <owner_token>" \
  http://127.0.0.1:8765/v1/self/execution_model_status
```

响应示例（`objective_persistent_worker_enabled=True` 且
`scheduler_heartbeat_enabled=True` 时）：

```json
{
  "objective_execution_mode": "persistent",
  "persistent_worker": {
    "enabled": true,
    "active_execution_count": 2,
    "active_execution_ids": ["exec_abc123", "exec_def456"],
    "idle_ttl_seconds": 1800.0
  },
  "isolated_runner": {"enabled": false, "max_workers": 0},
  "scheduler_heartbeat": {
    "enabled": true,
    "alive": true,
    "poll_interval_seconds": 5.0,
    "tick_interval_seconds": 60.0
  }
}
```

| 字段 | 说明 |
|------|------|
| `objective_execution_mode` | `"persistent"`（目标级持久 Worker，真并行 + 跨 step 上下文连续）/ `"isolated"`（隔离 Runner，真并行但每步失忆）/ `"shared_queue"`（默认，共享单线程队列，无独立并发） |
| `persistent_worker.active_execution_count` | 当前仍持有专属线程/Agent 实例的 Objective execution 数——就是这一刻真正并行执行的数量 |
| `scheduler_heartbeat.alive` | 心跳线程是否仍在运行；`enabled=true` 但 `alive=false` 说明线程异常退出，需要检查日志 |

> 两个开关都在 `agent_config.json` 的 `autonomy` 块下配置，修改后需要重启
> daemon 才会生效，本端点和看板面板都只做状态展示，不提供运行时切换。

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

### /v1/goals/{goal_id}/execution_spec — Goal 执行规范 REST API

把一个（可能是周期性执行的）Goal 具体化成结构化执行规范：每一轮该产出
什么（`deliverables`）、跨轮需要显式传递什么信息（`handoff_fields`）、
用什么标准判断"这一轮算做到位了"（`per_cycle_criteria`）、以及一次性
Goal 什么时候算整体完成（`overall_completion_criteria`）。详见
`next_doc/goal_execution_spec_generation_plan.md`（设计）、
`next_doc/goal_execution_spec_generation_implementation_record.md`
（逐阶段实施记录）。

```bash
# 模板库摘要列表，供"从模板起步"下拉框使用；传 goal_title/goal_description
# 时额外返回 suggested_template_id（关键词粗略匹配，匹配不到为 null）
GET /v1/goal_execution_spec_templates?goal_title=...&goal_description=...

# 查看当前执行规范（草稿或已确认版本）。没有生成过时返回 {"spec": null}，
# 不是 404——"还没生成"是合法状态
GET /v1/goals/{goal_id}/execution_spec

# 生成第 1 版草稿（不确认，不影响执行）
POST /v1/goals/{goal_id}/execution_spec/generate
Body: {
  "schedule": "0 9 * * 1",      # 可选，周期性 Goal 的 cron 表达式，供 prompt 参考
  "task_template": "...",       # 可选
  "template_id": "weekly_report",  # 可选，模板库骨架 ID，不传则完全从零生成
  "from_history": true,         # 可选，从该 Goal 最近一轮实际产出反推草稿内容
  "mode": "auto"                # 可选，"llm" | "agent" | "auto"，见下方说明
}

# 基于反馈 + 字段级锁定重新生成，只调整未锁定的部分
POST /v1/goals/{goal_id}/execution_spec/revise
Body: {
  "feedback": "每轮标准里再加一条：报告文件必须包含环比数据",
  "locked_fields": ["deliverables", "handoff_fields"],  # 可选，原样保留、不重新生成
  "mode": "auto"                # 可选，用法同 generate
}

# 确认并冻结当前草稿，下次该 Goal 触发执行时即生效
POST /v1/goals/{goal_id}/execution_spec/confirm

# 手动（重新）触发一次"整体是否可以关闭"判定
POST /v1/goals/{goal_id}/execution_spec/close_check
Body: {
  "use_agent": true             # 可选，单次覆盖是否走受限 Agent 路径核实
}                                # 实际产出文件内容，不传时回退配置默认值
```

`generate`/`revise` 的 `mode` 参数单次覆盖走"纯 LLM"（`"llm"`，不读取
项目内容，速度快）还是"只读探索 Agent"（`"agent"`，先看一眼项目实际
情况再生成，更贴合实际，耗时更长）；不传或传非法值时回退配置文件
`goal_execution_spec.builder_mode`（默认 `"auto"`：关键词规则粗筛是否
提到"参考/沿用项目已有内容"类诉求，命中直接走 Agent 路径；未命中先跑
一次纯 LLM，若其输出 JSON 里自报 `needs_project_context: true`——模型
自己判断"这道题答不准，需要先看看项目"——则丢弃这次结果、改用 Agent
路径重新生成一次）。单次覆盖不修改配置文件，只影响这一次调用。两个
端点的响应体都新增 `effective_path`（`"llm"`/`"agent"`，这次实际走的
路径）：

```json
{
  "spec": { "version": 1, "confirmed": false, "deliverables": [...], "..." : "..." },
  "effective_path": "agent"
}
```

`close_check` 只对一次性 Goal 生效（子节点全部进入终态、执行规范已确认
且 `overall_completion_criteria` 非空），Goal 不是 `active` 状态或前置
条件不满足时返回 `{"outcome": null, "reason": "..."}`，不是错误；
`outcome` 为 `"closed"` 时 Goal 已被标记为 `completed`，为 `"kept_open"`
时继续保持 `active`。响应体的 `goal.overall_completion_last_check` 带上
本次判定的持久化快照（`outcome`/`reasoning`/`used_agent`/`at`），供前端
展示"上一次判定是什么时候、判了什么、走的是哪条路径"，不需要翻
`progress_notes` 里的文本行去找：

```json
{
  "outcome": "closed",
  "goal": {
    "id": "goal_abc12345",
    "status": "completed",
    "overall_completion_last_check": {
      "outcome": "closed",
      "reasoning": "全部子 Objective 已完成，产出文件符合标准",
      "used_agent": false,
      "at": 1754800000.0
    }
  }
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

> job 到期后的**执行细节**（专属文件夹、进度续接、超时/卡死检测、
> `workspace`/`prompt`/`runs`/`reset` 另外 5 个端点）见
> [Cron 任务专属执行机制指南](cron-dedicated-execution-guide.md#8-rest-api)，
> 这里只是 job 本身的增删改查。

`GET /v1/cron/jobs` 响应示例：

```json
{
  "jobs": [
    {
      "id": "sys:consolidation",
      "name": "巩固循环 扫描",
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

### POST /v1/sessions/{session_id}/save_anchor — daemon-connected 模式 Ctrl-C 认知锚点

具身改进 C3（认知锚点，见 [具身智能改进指南](embodied-agent-guide.md#12-c3-认知锚点文件思维状态重建指南)）
原来只在纯本地 REPL 模式下生效：本地进程直接持有 Agent 实例，`cli/repl.py`
在 `KeyboardInterrupt` 里能直接调用 `agent._save_cognitive_anchor()`。
daemon-connected 模式下 `cli/daemon.py` 的 `DaemonClient` 不直接持有 Agent
实例，Ctrl-C 到不了 Agent 那一层——这个端点补上了这条路径。

```bash
POST /v1/sessions/{session_id}/save_anchor
```

`cli/daemon.py` 的 `DaemonClient.save_cognitive_anchor(session_id)` 在客户端
自己的 `KeyboardInterrupt` 处理里 best-effort 调用（2.5s 短超时，失败/超时
静默降级，不阻塞断开连接本身）。服务端按 `session_id` 找到对应 Agent 后调用
同一个 `_save_cognitive_anchor()`；`cognitive_anchor_enabled=False` 或该
session 尚未创建 Agent 时，由 `_save_cognitive_anchor()` 自身的 no-op /
`_agent_or_404` 处理，不需要调用方额外判断。

响应示例：

```json
{
  "ok": true,
  "session_id": "abc123",
  "message": "Cognitive anchor save attempted",
  "history_count": 18
}
```

`ok: true` 只表示"这次调用被受理并尝试执行"，不保证锚点一定生成成功
（锚点内容由 LLM 生成，可能因为 history 太短、LLM 调用失败等原因静默跳过——
与本地模式的行为一致）。

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