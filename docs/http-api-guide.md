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
| `/v1/status` | GET | Agent 状态（空闲/运行中）+ 统计信息 |
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
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                    mini-agent Core                          │
│            (agent.py + tools + llm)                         │
└─────────────────────────────────────────────────────────────┘
```

## 相关文档

- [权限系统指南](permission-guide.md) — HTTP 审批流程详解
- [命令与工具参考](commands-and-tools-reference.md) — 所有可用工具
- [Agent 设计](agent-design.md) — Agent 核心循环
