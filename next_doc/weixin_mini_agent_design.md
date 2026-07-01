# 微信端接入 mini_agent 设计方案

## 1. 背景与目标

`apps/weixin_plugin` 已经提供了 openclaw-weixin 网关的 Python SDK（`weixin/api.py` + `weixin/bot.py`），但示例里的接法（`ClaudeCodeHandler`）是每条消息 fork 一次 `claude` CLI 子进程，拿不到会话管理、权限审批、文件系统等能力。

本项目 `mini_agent` 自身已经具备一套完整的多用户 HTTP API（`src/mini_agent/api/`）：`/v1/chat`、`/v1/sessions*`、`/v1/permissions/*`、`/v1/fs/*`、`/v1/users` 等，并且有 `SessionAgentPool` 做多用户会话隔离、`PermissionGate` 做权限审批。

**目标**：新增一个"微信网关 → mini_agent HTTP API"的中转 Handler，让微信用户可以：
- 与 mini_agent agent 对话
- 管理/切换多个 session（隔离上下文）
- 查看工作目录结构、查看文件内容
- 收到权限审批 / 用户确认请求时，在微信里直接确认
- 既支持同机部署，也支持跨机访问

不修改 mini_agent 核心代码，只在 `apps/weixin_plugin/` 下新增文件。

## 2. 整体架构

```
微信用户
   ↕ openclaw 网关（轮询 get_updates / send_message）
weixin/bot.py (WeixinBot)
   ↕
WeixinMiniAgentHandler   ← 新增，核心路由 + 指令解析
   ↕ HTTP(S)
MiniAgentClient          ← 新增，mini_agent /v1/* 的轻量客户端封装
   ↕
mini_agent HTTP API (/v1/*)   ← 本机 or 远程
   ↕
SessionAgentPool / SessionManager / PermissionGate / fs routes
```

轮询任务（后台协程，与消息处理并行）：
```
PermissionPoller  ← 新增，定时轮询 /v1/permissions/pending
   → 发现新的待审批请求 → 主动推送微信消息询问用户
   → 用户回复 /yes /no /always → 调 /v1/permissions/{req_id}
```

## 3. 身份与用户映射

- 微信 `openid` ↔ mini_agent 用户：首次收到某个 openid 的消息时，用配置好的 **owner token** 调 `POST /v1/users` 创建一个新用户（`role` 从配置里读，默认 `user`），拿到 `user_id` + `token`。
- 映射关系（`openid → {user_id, token, role, created_at}`）持久化在本地 sqlite：`apps/weixin_plugin/data/user_mapping.db`，避免重启后重复建号。
- 之后该用户所有请求都用自己的 token 调 mini_agent API，天然复用 mini_agent 已有的多用户隔离（`.agent/users/{user_id}/sessions/`），不会看到别人的 session / 文件（fs 接口是否按用户隔离目录以 mini_agent 现有实现为准，若全局共享工作目录，则所有微信用户看到的是同一个项目目录，只是各自 session 隔离）。
- **角色可配置**：在 `config.toml` 里维护一份 `openid → role` 的白名单/规则（例如管理员的微信号直接给 `owner`，其余默认 `user`），创建用户时按此规则决定 `role`。

## 4. 指令设计（斜杠指令，先不做自然语言路由）

| 指令 | 作用 | 对应 API |
|---|---|---|
| `<普通文本>` | 发给当前 session | `POST /v1/chat` |
| `/help` | 显示帮助 | - |
| `/sessions` | 列出我的所有 session（带序号，标记当前） | `GET /v1/sessions` |
| `/session new` | 新建 session 并切换 | `POST /v1/sessions/new` |
| `/session use <序号|id>` | 切换到指定 session | `POST /v1/sessions/{id}/resume` |
| `/session del <序号|id>` | 删除指定 session | `DELETE /v1/sessions/{id}` |
| `/status` | 查看当前 agent 状态（idle/running/等待审批） | `GET /v1/status` |
| `/ls [path]` | 查看目录结构（默认工作目录根） | `GET /v1/fs/list` |
| `/cat <path>` | 查看文件内容（过长自动截断） | `GET /v1/fs/read` |
| `/find <keyword>` | 按关键词搜索文件 | `GET /v1/fs/search` |
| `/yes` `/no` `/always` `/denyalways` | 响应最近一条待审批请求 | `POST /v1/permissions/{req_id}` |
| `/interrupt` | 中断当前正在执行的任务 | `POST /v1/interrupt` |

细节：
- `/sessions` 返回列表时，Handler 侧维护"该用户最近一次列表的序号→session_id"映射，允许后续用 `/session use 2` 这种短指令，而不用输入完整 id。
- `/ls`、`/cat` 只做**只读**操作，暂不接 `/fs/write`、`/fs/upload`，避免误操作改坏项目文件；后续如有需要再单独评审加白名单指令。
- 消息长度：微信单条消息有长度限制，`/ls`、`/cat` 输出过长时截断并提示"内容较长，仅显示前 N 行，如需完整内容请通过 Web 端查看"。

## 5. 对话与流式回复

- `/v1/chat` 是异步排队模型（返回 `turn_id`，`queued: true`），实际结果需要轮询/订阅拿。
- 一期方案：**轮询** `GET /v1/status`（或按 turn_id 相关的轻量端点，具体看 mini_agent 是否有"取某 turn 最终结果"的端点，若没有则轮询 `/v1/turns/{turn_id}`）直到 `turn_done`，再把最终文本一次性通过 `bot.reply_text` 发回微信。
  - 不做逐 token 转发（微信没有真流式体验，逐字发送会刷屏），改为"等一轮说完再发一条"。
  - 轮询间隔：1~2 秒，超时阈值（如 3 分钟无响应）后提示用户"任务仍在执行，请稍后用 /status 查看"。
- 二期优化：改成常驻 SSE 订阅 `/v1/stream`，减少轮询开销、降低延迟，并可以做分段流式回复（比如每积攒一段文字就先发一条）。

## 6. 权限审批 / 用户确认

- 后台常驻一个 `PermissionPoller` 协程，按用户轮询各自的 `GET /v1/permissions/pending`（间隔 3~5 秒）。
- 发现新的待审批请求时：
  1. 通过 `bot.send_message` 主动推送一条消息给对应微信用户：
     ```
     ⚠️ Agent 请求执行：<工具名>
     参数：<摘要，过长截断>
     回复 /yes 允许一次 / /no 拒绝 / /always 以后同类自动允许 / /denyalways 以后同类自动拒绝
     ```
  2. Handler 记录"该用户当前待响应的 req_id"（同一用户同一时间只保留最新一条，避免指令歧义）。
- 用户回复 `/yes` `/no` `/always` `/denyalways` 时，取出记录的 `req_id`，调 `POST /v1/permissions/{req_id}`，把 `approve` / `mode` 传回去。
- "向用户确认信息"类事件（非工具权限，而是 agent 主动询问用户的场景）按同样的推送+指令确认模式接入，具体事件类型对齐 mini_agent 现有的 `AgentEvent`/`EventType`（如果暂无对应类型，先复用 `permission_req` 模式，二期再细分）。
- 超时策略：待审批请求超过一定时间（如 10 分钟）未处理，再提醒一次；避免用户错过导致 agent 一直挂起。

## 7. 文件管理（只读）

- `/ls [path]`：调 `GET /v1/fs/list?path=xxx`，返回目录名/文件名列表；目录较深或文件数多时分页/折叠显示。
- `/cat <path>`：调 `GET /v1/fs/read?path=xxx`，超过一定字符数（如 1500 字）截断，提示"仅显示前 N 字符"。
- `/find <keyword>`：调 `GET /v1/fs/search?q=xxx`。
- 均走该用户自己的 token，权限、路径越界等校验完全复用 mini_agent 服务端已有的 `fs_helper.py` 逻辑，Handler 侧不重复做安全校验。

## 8. 跨机部署支持

- 配置项：
  - `MINI_AGENT_BASE_URL`：本机默认 `http://localhost:8080`，跨机改成实际地址（建议 HTTPS）。
  - `MINI_AGENT_OWNER_TOKEN`：用于自动创建微信用户的 owner token。
  - `WEIXIN_BASE_URL` / `WEIXIN_TOKEN`：沿用现有 openclaw 网关配置。
- mini_agent 服务端跨机访问时需要：
  1. 打开 `http_multi_user_enabled`（多用户鉴权中间件）。
  2. IP 白名单加上微信 Handler 所在机器出口 IP，或者放开白名单仅依赖 token 鉴权（视安全要求取舍）。
  3. 强烈建议在 mini_agent server 前面套 HTTPS（nginx/caddy 反代，或自带 TLS），避免 Bearer token 明文过公网。
- 本机部署时上述配置全部使用默认值，无需改动。

## 9. 新增文件清单

```
apps/weixin_plugin/
├── weixin/handlers/
│   └── mini_agent_handler.py     # 核心 Handler：指令路由 + 调用 MiniAgentClient
├── mini_agent_client.py          # mini_agent /v1/* 轻量 HTTP 客户端封装
├── user_mapping.py               # openid ↔ mini_agent user 映射（sqlite）+ 角色规则
├── permission_poller.py          # 后台轮询待审批请求并推送微信消息
├── run_mini_agent_bot.py         # 启动入口：整合 WeixinBot + Handler + Poller
├── config.example.toml           # 示例配置（本机 / 跨机两组示例）
└── data/                         # user_mapping.db 存放目录（gitignore）
```

## 10. 推进计划（分阶段实现，逐阶段可独立验证）

**阶段一：基础打通**
1. `mini_agent_client.py`：封装 `/v1/chat`、状态查询/轮询取结果、`/v1/interrupt`、鉴权 header 处理。
2. `user_mapping.py`：openid → mini_agent 用户的创建与持久化，角色规则从 config 读取。
3. `mini_agent_handler.py` 最小版：普通文本消息 → `/v1/chat` → 轮询 → 回复；`/help`、`/status`。
4. `run_mini_agent_bot.py` + `config.example.toml`：跑通"发消息、收回复"的最小闭环。

**阶段二：Session 管理**
5. 实现 `/sessions` `/session new` `/session use` `/session del`，含序号映射逻辑。

**阶段三：文件查看**
6. 实现 `/ls` `/cat` `/find`，处理长内容截断/分页。

**阶段四：权限审批 & 用户确认**
7. `permission_poller.py`：轮询 + 推送 + `/yes /no /always /denyalways` 指令处理。
8. 超时提醒逻辑。

**阶段五：跨机与文档**
9. 跨机配置项梳理、鉴权/白名单说明，补充到 `README.md`。
10. 整体自测：本机场景 + （如有条件）跨机场景各跑一遍完整流程。

**二期待定优化（本次不做）**：
- SSE 常驻订阅替代轮询
- 自然语言指令路由（"路由 agent"判断闲聊 vs 指令）
- 文件写操作（`/fs/write`、上传）

## 11. 待确认的细节（实现前最后确认）

- `/v1/turns/{turn_id}` 是否存在"取某轮最终结果"的端点；如果没有，需要改用别的轮询方式（如反复读 `/v1/history` 对比长度，或读 `/v1/status.turn_id` 判断是否已切回 idle）。实现阶段一时会先去确认这个接口细节，如与本文档假设不符会同步说明再继续。
- config 里角色规则的具体格式（白名单 openid 列表 / 正则 / 其他），实现时按最简单可用的形式（一个 owner openid 列表 + 默认角色）来做，如需更复杂规则可以后续再加。

---

以上方案确认无误后，将按"推进计划"从阶段一开始实现。
