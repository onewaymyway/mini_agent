# 微信接入指南 v2（apps/weixin_plugin，基于多用户 Daemon）

> 与 [`docs/weixin-bot-guide.md`](weixin-bot-guide.md)（`weixin_bot.py`，单进程内嵌
> 方案）是**两套独立的微信接入实现**，定位不同，见第 1 节的对比说明。
> 本文档描述 `apps/weixin_plugin/` 下基于 mini_agent **多用户 Daemon HTTP API**
> 的新方案。

---

## 1. 与 `weixin_bot.py`（v1）的关系与选型

| | `weixin_bot.py`（根目录，v1） | `apps/weixin_plugin/`（v2，本文档） |
|---|---|---|
| 进程模型 | 微信进程内直接 `import mini_agent`，每个 openid 对应一个内嵌 `Agent` 实例 | 微信侧是独立进程/服务，通过 HTTP 调用 mini_agent **daemon** 的 `/v1/*` 多用户 API |
| 依赖的 mini_agent 特性 | 无需 daemon，直接嵌入 | 依赖 `--http-multi-user` 多用户 Daemon（见 [多用户指南](multi-user-guide.md)） |
| 权限审批 | 覆盖 `PermissionGuard._prompt()`，阻塞在 `threading.Event` | 后台轮询 `/v1/permissions/pending`（`PermissionPoller`），推送微信消息 |
| 用户 ↔ 身份映射 | openid 即 Agent 实例 key，无角色概念 | openid → mini_agent `user_id`/`token`，按 `RoleRules` 映射到 `family/colleague/agent/public` 角色，本地 sqlite 持久化（`user_mapping.py`） |
| 部署形态 | 与 mini_agent 同机同进程 | 可与 mini_agent 服务端**同机或跨机**部署（`config.toml` 中 `mini_agent.base_url` 可指向远程 daemon） |
| 微信协议 | （见 weixin-bot-guide.md 原文档） | 基于 [openclaw-weixin](https://github.com/Tencent/openclaw-weixin) 网关协议，`weixin/` 目录是该协议的纯 Python 客户端（`api.py`/`bot.py`/`codec.py`/`auth.py`/`login.py`/`types.py`） |

**选型建议**：单机单用户/轻量场景用 v1（`weixin_bot.py`）更简单；需要多个微信用户
共享一个 mini_agent 服务、需要独立会话/权限隔离、或 mini_agent 与微信 Bot 分开部署时，
用本文档描述的 v2 方案。两者不互斥，也不共享状态，不要同时对同一个 mini_agent
实例混用。

---

## 2. 目录结构

```
apps/weixin_plugin/
├── weixin/                       # openclaw-weixin 协议的纯 Python 客户端（协议层，与 mini_agent 无关）
│   ├── types.py                  # 对应 TS 版 api.ts/types.ts 的 dataclass
│   ├── codec.py                  # JSON <-> dataclass 编解码
│   ├── api.py                    # 低层异步 HTTP API 调用
│   ├── auth.py / login.py        # 登录 / token 获取（auto_token()）
│   ├── bot.py                    # 高层轮询 Bot + handler 框架（@bot.on_text 等）
│   └── handlers/
│       ├── claude_code.py        # 对接本地 `claude` CLI 的示例 handler（与 mini_agent 无关）
│       └── mini_agent_handler.py # 对接 mini_agent 的核心 handler（本文档重点）
├── mini_agent_client.py          # mini_agent `/v1/*` HTTP API 的轻量异步客户端封装
├── user_mapping.py               # openid ↔ mini_agent 用户 的本地 sqlite 映射 + 角色规则（RoleRules）
├── permission_poller.py          # 轮询各用户待审批请求，推送微信消息 + 处理 /yes /no /always /denyalways
├── run_mini_agent_bot.py         # 启动入口：串起 WeixinBot + MiniAgentHandler + PermissionPoller
├── config.example.toml           # 配置样例（复制为 config.toml）
├── examples/                     # 独立示例（echo_bot、advanced_bot、claude_code_bot 等），与 mini_agent 集成无关
└── data/                         # user_mapping.db 等运行期数据默认存放位置
```

`weixin/` 目录本身是一个通用的 openclaw-weixin 协议库（自带独立 `README.md`），
可以脱离 mini_agent 单独使用（见 `examples/echo_bot_standalone.py`）；与 mini_agent
的集成粘合代码集中在 `mini_agent_client.py` / `user_mapping.py` / `permission_poller.py`
/ `weixin/handlers/mini_agent_handler.py` / `run_mini_agent_bot.py` 这五个文件。

---

## 3. 运行前提

1. 一个已经以多用户模式启动的 mini_agent daemon：

   ```bash
   mini-agent daemon start --http --http-multi-user --http-token <owner-token> --detach
   ```

   详见 [多用户指南](multi-user-guide.md) 与 [HTTP API 指南](http-api-guide.md)。
   记下启动日志中的 **owner token**（或 `.agent/agent_api.key`）。

2. 安装并登录 [OpenClaw](https://docs.openclaw.ai/install) 微信网关：

   ```bash
   npx -y @tencent-weixin/openclaw-weixin-cli install
   openclaw channels login --channel openclaw-weixin
   openclaw gateway restart
   ```

   网关地址/token 会写入 `~/.openclaw/openclaw.json`（`auto_token()` 可自动读取）。

3. 可选依赖 `httpx`（推荐；未安装时 `mini_agent_client.py` 和 `weixin/api.py` 都会
   自动退化为标准库 `urllib` + `run_in_executor`）。

---

## 4. 配置

复制 `config.example.toml` 为 `config.toml`，关键字段：

```toml
[weixin]
base_url = "http://localhost:8080"   # openclaw 网关地址；留空则走 auto_token()
token = ""

[mini_agent]
base_url = "http://localhost:8000"          # 本机部署；跨机部署改成 https://mini-agent.example.com
owner_token = ""                            # 用于自动创建微信用户对应的 mini_agent 账号
user_mapping_db = "data/user_mapping.db"
chat_poll_interval_s = 1.5
chat_timeout_s = 180
permission_poll_interval_s = 4.0

[mini_agent.roles]
owner_openids = []          # 这些微信 openid 创建出的账号使用 owner_mapped_role
default_role = "public"     # 其他微信用户的默认角色
owner_mapped_role = "family"
```

所有字段都可以用同名环境变量覆盖（`WEIXIN_BASE_URL` / `WEIXIN_TOKEN` /
`MINI_AGENT_BASE_URL` / `MINI_AGENT_OWNER_TOKEN`），优先级：环境变量 >
`config.toml` > 默认值。

角色只能是 mini_agent 侧 `VALID_ROLES` 中除 `owner` 之外的四种
（`family` / `colleague` / `agent` / `public`，见 `src/mini_agent/api/user_store.py`），
`owner` 角色永远绑定在启动 daemon 时配置的 owner token 上，不会通过本插件分发。

**跨机部署提示**：若微信 Bot 与 mini_agent 服务不在同一台机器，需要在
mini_agent 侧打开 `http_multi_user_enabled`，并在 mini_agent 服务前套一层
HTTPS 反代（避免 Bearer token 明文过公网），同时评估是否需要 IP 白名单。

---

## 5. 运行

```bash
cd apps/weixin_plugin
python run_mini_agent_bot.py
```

启动后 `run_mini_agent_bot.py` 会：

1. 加载配置（环境变量 → `config.toml` → 默认值）；
2. 构造 `WeixinBot`（openclaw 网关客户端）；
3. 构造 `MiniAgentHandler`（持有 `MiniAgentClient` + `UserMapping` 存储）并注册为
   微信文本消息 handler；
4. 启动 `PermissionPoller` 后台任务，与 `WeixinBot.run()` 一起跑在同一个
   asyncio 事件循环里。

---

## 6. 核心工作流程

### 6.1 首次收到某个微信用户的消息

1. `MiniAgentHandler` 在 `user_mapping.db` 中查找该 `openid` 是否已有映射；
2. 若没有，按 `RoleRules.role_for(openid)` 决定角色（`owner_openids` 名单内 →
   `owner_mapped_role`，否则 → `default_role`），用 owner token 调用
   `POST /v1/users` 创建一个新的 mini_agent 用户，得到 `user_id` + 专属 `token`，
   写入本地 sqlite；
3. 之后该 openid 的所有请求都使用这个专属 `token` 调用 mini_agent（对应
   mini_agent 侧独立的会话历史、工具权限、资源配额，参见
   [多用户指南](multi-user-guide.md)）。

### 6.2 对话轮次

`MiniAgentClient.chat()` 发起一轮对话（`turn_id`/`session_id`），随后按
`chat_poll_interval_s` 轮询该轮结果，直到完成或达到 `chat_timeout_s` 超时
（超时会提示用户改用 `/status` 查看）。

### 6.3 权限审批

`PermissionPoller` 按 `permission_poll_interval_s` 遍历所有已知 openid，逐个
调用 `list_pending_permissions(token)`。发现新的待审批请求时：

- 推送一条摘要消息（工具名 + 截断后的参数），并记录 `req_id`；
- 用户回复 `/yes` `/no` `/always` `/denyalways` 时，`MiniAgentHandler` 据此调用
  mini_agent 的权限决策 API 一次性处理该 `req_id`；
- 若请求超过 `PERMISSION_TIMEOUT_REMIND_S`（默认 600 秒）未处理，补发一次提醒
  （只提醒一次，避免刷屏）。

> 当前是轮询实现（3~5 秒延迟），设计文档中已注明 SSE 常驻订阅是计划中的二期
> 优化，尚未实现。

---

## 7. 已知限制

- `PermissionPoller` 按用户挨个轮询待审批列表，用户量很大时会产生 N 次轮询请求；
  mini_agent 侧目前没有"跨用户聚合"的待审批查询端点。
- 每个 openid 同一时间只关注**最新一条**待审批请求，避免 `/yes` 等回复指令的
  响应对象产生歧义；旧的未处理请求不会被单独跟踪。
- `weixin/` 协议客户端与 `examples/` 下的独立示例（`echo_bot_standalone.py`、
  `claude_code_bot.py` 等）与 mini_agent 无关，仅作为 openclaw-weixin 协议库的
  通用用法参考。

---

## 8. 相关文档

- [多用户指南](multi-user-guide.md) — mini_agent daemon 多用户会话隔离、角色、token
- [HTTP API 指南](http-api-guide.md) — `/v1/*` 端点总览
- [微信接入指南 v1](weixin-bot-guide.md) — `weixin_bot.py` 单进程内嵌方案
- [权限指南](permission-guide.md) — 权限审批模型与决策语义（对应 `/yes /no /always /denyalways`）

---

*首次编写：2026-07-04（补齐 `apps/weixin_plugin` 缺失文档）*
