# 多用户模式指南

> mini-agent 支持多用户模式，允许多个用户通过独立 token 和角色权限同时连接到同一个 daemon 实例，每个用户拥有独立的对话 session、工具权限和资源配额。

---

## 目录

- [概述](#概述)
- [快速开始](#快速开始)
- [启动多用户模式](#启动多用户模式)
- [用户管理](#用户管理)
- [用户角色与权限](#用户角色与权限)
- [用户如何连接](#用户如何连接)
- [会话隔离机制](#会话隔离机制)
- [配置文件方式](#配置文件方式)
- [API 端点](#api-端点)
- [安全注意事项](#安全注意事项)
- [数据目录结构](#数据目录结构)
- [常见问题](#常见问题)

---

## 概述

多用户模式（`--http-multi-user`）在现有 HTTP API 单用户模式的基础上新增了：

- **独立 token 认证**：每个用户持有专属 token，互不干扰
- **角色权限体系**：`owner / family / colleague / agent / public` 五种角色，对应不同的工具权限和资源配额
- **Session 隔离**：每个用户拥有独立的 Agent 实例和对话历史
- **社交画像**：agent 在对话中自动记录每个用户的偏好和背景，注入到 system prompt

与单用户模式**完全向后兼容**——不带 `--http-multi-user` 参数启动时，行为与现有单 token 单用户模式完全一致。

---

## 快速开始

```bash
# 1. 以多用户模式启动 daemon
mini-agent daemon start --http --http-multi-user --detach

# 2. 查看 owner token（启动日志中显示）
mini-agent daemon status

# 3. 添加新用户
mini-agent user add --name "小明" --role colleague

# 4. 查看用户列表
mini-agent user list

# 5. 用户通过 HTTP API 连接（使用各自的 token）
curl -H "Authorization: Bearer <user-token>" http://127.0.0.1:8765/v1/health
```

---

## 启动多用户模式

### 命令行参数

```bash
# 基本多用户模式
python -m mini_agent --http --http-multi-user

# 配合 daemon 后台运行（推荐）
mini-agent daemon start --http --http-multi-user --detach

# 允许局域网访问（其他机器上的用户也可连接）
mini-agent daemon start --http --http-multi-user --http-host 0.0.0.0 --detach

# 指定端口
mini-agent daemon start --http --http-multi-user --http-port 8765 --detach

# 设置 owner token（否则自动生成）
mini-agent daemon start --http --http-multi-user --http-token your-owner-token --detach

# 开启文件系统只读（对外服务时推荐）
mini-agent daemon start --http --http-multi-user --http-fs-readonly --detach
```

### 启动后的输出

成功启动时，终端会显示如下信息：

```
  🌐  HTTP API server started
  URL  : http://127.0.0.1:8765/v1
  Token: abc123...def456     ← 这是 owner token

  👥  Multi-user mode: ON  (above token = owner)
  Other users: 0
  Manage: mini-agent user list / add / remove
```

**请妥善保管 owner token**，它存储在 `.agent/users/tokens/owner.key`。

---

## 用户管理

用户管理需要 daemon 以 `--http-multi-user` 模式运行，通过 `mini-agent user` 子命令操作。

### 查看用户列表

```bash
mini-agent user list
```

输出示例：

```
  user_id        name           role       trust  last_seen
  ────────────────────────────────────────────────────────────
  owner          Owner          owner      10     2025-01-01 09:00
  u_a1b2c3d4     小明           colleague  5      2025-01-01 10:30
  u_e5f6g7h8     小红           family     8      -
```

### 添加新用户

```bash
# 添加 colleague 角色用户
mini-agent user add --name "小明" --role colleague

# 添加家人角色，指定信任等级（1-10）
mini-agent user add --name "小红" --role family --trust 8

# 添加公开访客
mini-agent user add --name "访客A" --role public
```

成功后会打印 token（**仅显示一次**，请立即保存并告知用户）：

```
[user] ✓ Created user 'u_a1b2c3d4' (role=colleague)
        Token: 3f9a2b7c1d4e8f6a...
        Give this token to the user — it will not be shown again.
```

### 删除用户

```bash
mini-agent user remove u_a1b2c3d4
```

### 修改用户角色

```bash
mini-agent user role u_a1b2c3d4 family
```

### 重新生成用户 token

若 token 泄露，立即重新生成（旧 token 立即失效）：

```bash
mini-agent user token u_a1b2c3d4
```

输出：

```
[user] ✓ New token for 'u_a1b2c3d4':
        9f3a1c8d2e7b4f5a...
        Old token is now invalid.
```

---

## 查看 Self 状态总览（`mini-agent self status`）

owner-only 命令，通过 HTTP 调用 daemon 的 `/v1/self/status` 端点查看整体运行状态
（AutonomousLoop / 当前活跃 Goal 与 Objective / 最近 24 小时自主活动 / Session Pool 中各会话的存活情况）：

```bash
mini-agent self status
```

多用户模式下，非 owner token 调用会被拒绝（返回 403，CLI 原样打印错误信息）。
用途：一眼确认某个用户会话是否还存活、daemon 的自治节奏是否正常，而不必分别去看
`/agent daemon status`、`/digest`、`/agent goals list` 等多个入口。

---

## 用户角色与权限

系统内置五种角色，权限从高到低：

| 角色 | 说明 | 工具权限 | Token 上限 | 对话轮次上限 |
|------|------|----------|-----------|-------------|
| `owner` | 主人（daemon 启动者） | 全部工具，无限制 | 200,000 | 500 |
| `family` | 家人 / 朋友 | builtin + search | 80,000 | 200 |
| `colleague` | 同事 / 工作相关 | builtin + search | 50,000 | 100 |
| `agent` | 其他 AI agent | builtin | 30,000 | 50 |
| `public` | 公开访客 | 无工具 | 8,000 | 20 |

**角色说明：**

- **owner**：完全控制权，可访问所有工具和文件，可讨论任何私人话题
- **family**：温暖亲切的对话风格，情感支持优先，不主动披露主人的工作细节
- **colleague**：专业简洁，聚焦工作事项，文件访问只读
- **agent**：结构化通信格式，明确声明能力边界，适合 AI-to-AI 协作
- **public**：保守礼貌，不透露内部信息，对话范围受限

**注意：** 角色对话风格提示会自动注入到每个用户 session 的 system prompt，无需手动配置。

### 信任等级（trust_level）

`trust_level` 是 1-10 的整数，默认为 5。数字越高信任越强。目前作为附加元数据存储，供未来扩展使用（如动态工具权限精细化）。

---

## 用户如何连接

### 通过 HTTP API 连接

用户使用分配给自己的 token 在 `Authorization: Bearer` 头中发起请求：

```bash
# 健康检查
curl -H "Authorization: Bearer <your-token>" http://127.0.0.1:8765/v1/health

# 发送消息
curl -X POST http://127.0.0.1:8765/v1/chat \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "你好，请帮我分析这段代码"}'

# 订阅 SSE 事件流
curl -N http://127.0.0.1:8765/v1/stream \
  -H "Authorization: Bearer <your-token>"
```

### 通过 CLI 连接

除了直接打 HTTP API，也可以用普通的 `mini-agent` REPL 以指定用户身份连接到正在运行的多用户 daemon —— 用 `--token`（简写 `-T`）传入该用户的 token 即可：

```bash
# 以 colleague 用户 u_a1b2c3d4 的身份连接到 daemon
mini-agent --token <u_a1b2c3d4-的-token>

# 简写
mini-agent -T <token>
```

连接成功后，REPL 会打印当前 token 对应的身份，方便确认没有带错 token：

```
[daemon] Connected ✓  (PID=12345, port=8765)
[daemon] Identity: 小明 (user_id=u_a1b2c3d4, role=colleague)
```

不传 `--token` 时行为不变：按原有优先级回退到本地 `.agent/agent_api.key` 文件（单用户/owner 场景，向后兼容）。

身份确认背后用的是新增的 `GET /v1/whoami` 端点（见下方 [API 端点](#api-端点)），也可以直接用 curl 调用来核对某个 token 到底属于谁：

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8765/v1/whoami
# → {"multi_user_enabled": true, "user_id": "u_a1b2c3d4", "name": "小明", "role": "colleague", "trust_level": 5, "is_owner": false}
```

> 注意：`--token` 只影响"REPL 连接到已存在 daemon"这一种场景，对 `--http`（启动 daemon 本身）无效——daemon 自身监听用的 token 仍然用 `--http-token` 指定。

### 通过 Web Demo 连接

启动 Web Demo 后，在侧边栏填入用户自己的 token：

```bash
# 启动 Web Demo
pip install streamlit requests
streamlit run apps/mini_agent_webdemo/app.py
```

在 Web 界面的「Token」输入框中填入用户 token，即可开始对话。详见 [Web Demo 指南](web-demo-guide.md)。

### 通过 Python 客户端连接

```python
import httpx
import asyncio

API_BASE = "http://127.0.0.1:8765/v1"
TOKEN = "your-user-token"  # 每个用户使用自己的 token
headers = {"Authorization": f"Bearer {TOKEN}"}

async def chat(message: str):
    async with httpx.AsyncClient() as client:
        # 发送消息
        resp = await client.post(
            f"{API_BASE}/chat",
            headers=headers,
            json={"message": message}
        )
        turn_id = resp.json()["turn_id"]

        # 等待完成
        while True:
            status = await client.get(f"{API_BASE}/status", headers=headers)
            if status.json()["state"] == "idle":
                break
            await asyncio.sleep(0.5)

        # 获取历史
        history = await client.get(f"{API_BASE}/history", headers=headers)
        return history.json()

asyncio.run(chat("帮我写一个质数筛法"))
```

### 带 session_id 的多 session 支持

多用户模式下，每个用户可以发起多个独立 session（每个 session 对应一个独立 Agent 实例）：

```bash
# 在 /v1/chat 中指定 session_id
curl -X POST http://127.0.0.1:8765/v1/chat \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "继续上次的任务", "session_id": "my-session-01"}'
```

如果不传 `session_id`，系统会使用该用户的默认 session。同一个 `session_id` 在断连后重连会自动恢复历史对话。

---

## 会话隔离机制

多用户模式下，每个用户（更准确说每个 `session_id`）拥有完全隔离的运行环境：

### 独立 Agent 实例

- 每个 session 对应一个独立的 `Agent` 实例，在各自的线程中运行
- 各 session 的 LLM 调用、工具执行、历史压缩互不干扰
- 最多同时支持 **20 个活跃 session**（可通过配置调整）

### 独立数据目录

```
.agent/users/
├── owner/                    ← owner 数据目录
│   ├── profile.json          ← 社交画像
│   ├── memory.jsonl          ← 专属记忆
│   └── sessions/             ← 对话历史
├── u_a1b2c3d4/              ← 某用户数据目录
│   ├── profile.json
│   ├── memory.jsonl
│   └── sessions/
├── users.json                ← 用户注册表（含 token hash）
└── tokens/
    ├── owner.key             ← owner token 明文（0600 权限）
    └── u_a1b2c3d4.key        ← 用户 token 明文（0600 权限）
```

### Session 生命周期

- Session 30 分钟无活动后自动 **挂起（suspend）**：状态保存到磁盘，从内存移除
- 同一用户再次请求同一 `session_id` 时，自动从磁盘恢复历史
- Daemon 关闭时，所有活跃 session 自动保存

### 社交画像注入

Agent 在对话中自动更新每个用户的画像（`.agent/users/<user_id>/profile.json`），包含：

- 称呼与关系
- 性格特点与兴趣
- 沟通风格
- 敏感话题（自动避免主动提及）
- 近期 agent 观察备注

这些画像在每次创建新 session 时自动注入到 system prompt，让 agent 更好地个性化服务每个用户。

---

## 配置文件方式

在 `agent_config.json` 中持久化配置多用户模式：

```json
{
  "http_enabled": true,
  "http_host": "0.0.0.0",
  "http_port": 8765,
  "http_api_token": "your-owner-token",
  "http_allowed_ips": ["127.0.0.1", "::1", "192.168.1.0/24"],
  "http_cors_origins": ["http://localhost:3000", "http://your-app.com"],
  "http_fs_readonly": true,
  "http_multi_user_enabled": true
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `http_multi_user_enabled` | 是否启用多用户模式 | `false` |
| `http_enabled` | 是否启动 HTTP 服务 | `false` |
| `http_host` | 监听地址 | `127.0.0.1` |
| `http_port` | 监听端口 | `8765` |
| `http_api_token` | owner token（留空则自动生成） | 自动生成 |
| `http_allowed_ips` | IP 白名单 | `127.0.0.1`, `::1` |
| `http_cors_origins` | CORS 允许的来源 | `*` |
| `http_fs_readonly` | 文件系统只读 | `false` |

---

## API 端点

多用户模式新增以下 API 端点：

### 身份确认 API

| 端点 | 方法 | 说明 | 权限 |
|------|------|------|------|
| `/v1/whoami` | GET | 返回当前 token 对应的 user_id/name/role/trust_level | 任意已认证用户 |

单用户模式（未开 `--http-multi-user`）下调用同样有效，固定返回 `{"multi_user_enabled": false, "user_id": "owner", "role": "owner", ...}`，方便 CLI 端不用区分模式统一处理。

### 用户管理 API（需要 owner token 认证）

| 端点 | 方法 | 说明 | 权限 |
|------|------|------|------|
| `/v1/users` | GET | 获取用户列表 | owner |
| `/v1/users` | POST | 新增用户，返回 `user_id + token` | owner |
| `/v1/users/{user_id}` | PATCH | 修改角色 / meta | owner |
| `/v1/users/{user_id}` | DELETE | 删除用户 | owner |
| `/v1/users/{user_id}/token` | POST | 重新生成 token | owner |

**新增用户示例：**

```bash
curl -X POST http://127.0.0.1:8765/v1/users \
  -H "Authorization: Bearer <owner-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "小明", "role": "colleague", "trust_level": 5}'
```

响应：

```json
{
  "ok": true,
  "user_id": "u_a1b2c3d4",
  "token": "3f9a2b7c1d4e8f6a...",
  "message": "User created. Token shown once only."
}
```

**修改角色示例：**

```bash
curl -X PATCH http://127.0.0.1:8765/v1/users/u_a1b2c3d4 \
  -H "Authorization: Bearer <owner-token>" \
  -H "Content-Type: application/json" \
  -d '{"role": "family"}'
```

### 现有对话 API

所有用户均可使用 [HTTP API 指南](http-api-guide.md) 中的对话端点（`/v1/chat`、`/v1/stream`、`/v1/history` 等），使用各自 token 认证，数据互相隔离。

---

## 安全注意事项

1. **owner token 不可泄露**：它是最高权限，一旦泄露立即通过 `mini-agent user token owner` 重置
2. **对外开放时使用强 token**：启动时通过 `--http-token` 或配置文件设置 owner token
3. **建议开启文件系统只读**：对非 owner 用户服务时，在配置文件中设置 `http_fs_readonly: true`
4. **IP 白名单**：局域网外的访问应配置 `http_allowed_ips` 限制可信 IP 段
5. **token 文件权限**：`.agent/users/tokens/*.key` 文件权限为 `0600`，请勿修改
6. **不要暴露 `.agent/users/` 目录**：该目录包含所有用户 token hash 和数据，不应对外可见

---

## 数据目录结构

```
<project_root>/
└── .agent/
    └── users/
        ├── users.json               # 用户注册表（仅存 token hash，不含明文）
        ├── tokens/
        │   ├── owner.key            # owner token 明文（0600 权限）
        │   └── u_<id>.key           # 各用户 token 明文（0600 权限）
        ├── owner/
        │   ├── profile.json         # owner 社交画像
        │   ├── memory.jsonl         # owner 专属记忆
        │   └── sessions/            # owner 对话历史
        └── u_<id>/
            ├── profile.json         # 用户社交画像
            ├── memory.jsonl         # 用户专属记忆
            └── sessions/            # 用户对话历史（与 owner 物理隔离）
```

---

## 常见问题

**Q: 单用户模式下原来的 API token 还能用吗？**

A: 能。不带 `--http-multi-user` 启动时，行为与原来完全一致，只使用单个 token。

**Q: 多用户模式下能同时有多少人连接？**

A: 最多同时 **20 个活跃 session**（可在 `session_pool.py` 的 `DEFAULT_MAX_SESSIONS` 调整）。超出时新请求会收到错误提示。

**Q: 用户的对话历史会丢失吗？**

A: 不会。Session 挂起时自动保存到磁盘，重连时自动恢复。Daemon 重启也不影响历史数据。

**Q: owner 用户能看到其他用户的对话吗？**

A: 当前版本不提供跨用户对话查看功能。各用户的对话历史和 SSE 流是隔离的。

**Q: 如果忘记了某个用户的 token 怎么办？**

A: 用 `mini-agent user token <user_id>` 重新生成一个新 token，旧 token 会立即失效。owner token 可通过 `mini-agent user token owner` 重置。

**Q: 如何临时禁止某个用户访问？**

A: 用 `mini-agent user token <user_id>` 重置 token，不把新 token 告知该用户，即可阻止其访问。

**Q: `--http-multi-user` 和 `--http` 的关系？**

A: `--http-multi-user` 隐含了 `--http`，单独使用 `--http-multi-user` 也会启动 HTTP 服务。但建议总是同时写明两个参数，表意更清晰。

**Q: 能不能用 `mini-agent` 命令行（而不是 curl/Web Demo）以某个用户身份连接多用户 daemon？**

A: 可以。用 `mini-agent --token <该用户的token>`（简写 `-T`）连接即可，详见 [通过 CLI 连接](#通过-cli-连接)。不传 `--token` 时按原有逻辑回退到 `.agent/agent_api.key`（单用户/owner）。

---

## 相关文档

- [HTTP API 指南](http-api-guide.md) — REST/SSE API 完整参考
- [权限系统指南](permission-guide.md) — 工具调用权限审批
- [Stage 9 自主运行时指南](self-evolution-stage9-guide.md) — Daemon 模式详解
- [Web Demo 指南](web-demo-guide.md) — Streamlit 图形界面
- [配置指南](config-guide.md) — 完整配置项参考
