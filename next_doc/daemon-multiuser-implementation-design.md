# Daemon 多用户架构 · 实施设计文档（基于现状代码核对版）

> 本文档是对 `next_doc/daemon-multiuser-architecture.md`（以下简称"原方案"）的**落地设计**。
> 原方案描述的是目标形态；本文档逐项核对了当前代码的真实情况，标出原方案与现状的冲突点、
> 已经存在但原方案没意识到的可复用机制，以及每个 Phase 具体要改哪些文件、新增哪些文件。
>
> 状态：**Phase 1-4 均已实施完成**（daemon 模式的两个 bug 早先已单独修复，与本文档无关）。
> 每个 Phase 的小节末尾都有"实施记录"，记录了实测中发现的真实 bug 和与原计划的差异点——
> 这些记录是后续维护时理解"为什么代码长这样"的主要依据，建议优先读这些，而不是只读
> 设计部分（设计部分描述的是出发点，不是最终落地的样子，两者有出入的地方实施记录里
> 都标出来了）。

---

## 〇、与现状代码核对后发现的关键问题

在写实施步骤之前，必须先把这几个问题定下来，否则 Phase 1-4 会建立在错误的假设上。

### 0.1 `UserProfileManager` 命名冲突 —— 必须改名

`src/mini_agent/profile.py` 里**已经有一个**正式的 `UserProfileManager` 类，是单用户个性化画像系统
（详见 `docs/user-profile-guide.md`）：

| | 已有的 `profile.py::UserProfileManager` | 新增草稿 `api/user_store.py::UserProfileManager` |
|---|---|---|
| 用途 | LLM 自动生成的技术栈/习惯总结（`tech_stack`/`habits`） | 人工维护的社交画像（`relation`/`trust_level`/`agent_notes`） |
| 路径 | `AgentPaths.profile_path(user_id)` → **`~/.agent/users/<user_id>/profile.json`**（全局，跨项目） | **`<project_root>/.agent/users/<user_id>/profile.json`**（项目本地） |
| 写入时机 | session 结束后台触发 `generate()`，调 LLM 总结 | 对话中 agent 主动调用 `add_agent_note()` 等 |
| 注入方式 | `pm.build_system_prompt(user_profile=...)`（已有专门参数） | 原方案设想注入 `cfg.extra_system`（**该字段不存在**，见 0.2） |

两者路径相似（都叫 `users/<user_id>/profile.json`）、类名完全相同，但 scope（全局 vs 项目级）和数据结构都不同。
如果不处理，未来任何人读代码都会以为是同一个东西，调试时会两边对不上。

**结论**：新的角色/token 系统模块改名为 `RoleProfileManager`（或者干脆把"社交画像"字段
合并进 `profile.py::UserProfile.derived` 里，但考虑到 owner 主用户的个性化画像（技术栈/习惯）
和"主人画像 vs 家人/同事/agent 角色档案"是两件不同的事，**建议保持两个独立类，但改名避免撞名**）。

本文档后续统一用 **`RoleProfileManager`** 指代 `api/user_store.py` 里那个类，文件内会一并改名。

### 0.2 `cfg.extra_system` 字段不存在 —— 已有 `cfg.system_extra` 可用

`api/session_pool.py` 草稿里写的：

```python
existing = getattr(session_cfg, "extra_system", "") or ""
session_cfg.extra_system = (existing + "\n\n" + user_system_ctx).strip()
```

`AppConfig` 上**没有 `extra_system` 这个字段**，`getattr(..., "")` 静默拿到空字符串，
赋值上去的 `session_cfg.extra_system` 也**没有任何代码会读它**——这段代码目前是完全的死代码，
注入用户画像这件事实际上根本没生效。

真正存在、而且已经接入 system prompt 组装链路的是：
- `cfg.system_extra` → `build_system_prompt()` 里传给 `pm.build_system_prompt(system_extra=cfg.system_extra, ...)`
- `build_system_prompt()` 还有一个专门的 `user_profile: str = ""` 参数，目前传的是
  `profile.py::UserProfileManager` 生成的个性化总结。

**结论**：SessionAgent 的角色/画像上下文，应该走 `cfg.system_extra`（用换行拼接进去，
和 `--system` CLI 参数是同一个字段，语义上也匹配——"额外系统提示词"），不要发明新字段。
角色身份/persona hint 这类"这是谁、怎么对话"的内容拼进 `system_extra`；
"这个人的兴趣/敏感话题"这类长期画像内容，可以复用 `user_profile` 参数（但来源换成
`RoleProfileManager`，而不是 `profile.py` 的 LLM 自动总结）。

### 0.3 thread-local provider 契约 —— `Agent()` 必须在它自己的线程里构造

代码里有三处用 `threading.local()` 实现的"当前 Agent 上下文"全局访问点：

```
tools/evolution.py            set_project_root_provider()
tools/workdir_knowledge.py    set_project_root_provider() / set_session_id_provider()
tools/orchestration.py        set_active_skills_provider()
```

这些都在 `Agent.__init__()` 里调用，写入的是**调用 `Agent()` 构造函数那个线程**的 thread-local。
工具函数（`skill_propose`、`add_open_thread` 等）在被调用时读取的是**它们实际运行所在线程**的
thread-local。

**现状（单 Agent 模式）下，这件事其实已经是错的**：`Agent()` 在主线程构造，
但 `run_turn()` 实际执行在 `AgentRunner` 这个独立线程里 —— 这两个线程不是同一个，
所以 `skill_propose` 等工具在 daemon 模式下读到的 `project_root`/`session_id` 其实是 `None`/空。
这是一个**当前就存在、和本次改造无关的潜在 bug**，但 Phase 3 必须正确处理它，否则会从
"只影响一个全局 Agent"变成"影响每一个 SessionAgent，互相串）"。

**Phase 3 的强制要求**：`SessionAgentPool._create_entry()` 不能在调用者线程（HTTP 请求线程）
里直接 `Agent(cfg=...)`，必须把"构造 Agent + 跑 AgentRunner"整体封装成一个函数，
丢给该 session 专属的线程去执行，构造和运行在同一线程内完成。
（这也顺带修掉了上面说的现状 bug。）

### 0.4 `AutonomousLoop`（"Self" 的心跳）目前寄生在唯一的 `AgentRunner` 上，Phase 3 后必须独立

现状：`AgentRunner.run()` 循环里，`dequeue()` 超时且判断 `should_tick()` 为真时调用
`autonomous_loop.tick()`——"用户消息检查"和"Self 自主周期任务"共享同一个线程/循环。

Phase 3 之后，每个 session 都有自己的 `AgentRunner` 线程，**不会再有"唯一"的 AgentRunner**。
如果不调整，`AutonomousLoop.tick()` 就没有宿主了，或者错误地挂在某一个随机 session 的
runner 上（该 session 断开后 Self 也跟着停摆，这违背"Self 不依赖任何用户连接"的核心理念）。

**结论**：Phase 3 必须新增一个独立的 **SelfRunner**（不依赖任何 session），
专门跑 `AutonomousLoop.tick()`，daemon 启动时就创建，和 SessionAgent 的生命周期完全脱钩。

### 0.5 `ResourceArbiter` 现状是"全局态预算"，新增的 `ROLE_BUDGETS` 是平行机制，不冲突但要分清楚

`evolution/resource_arbiter.py` 现在管的是"Self 的自主任务 vs 用户消息"之间的预算/资源锁，
读取的是 `SelfProfile.resource_budget`（全局一份，不分用户）。

`api/user_store.py` 草稿里的 `ROLE_BUDGETS`（按 owner/family/colleague/agent/public 分级的
token/turns/tools 上限）是**给 SessionAgent 用的会话级配额**，和上面那个东西管的是两件事：
- `ResourceArbiter`：管"daemon 要不要让 Self 自己去跑自主任务"
- `ROLE_BUDGETS`：管"这个用户这一个 session 还能再用多少 token/工具"

两者不冲突，但命名上容易让人以为是同一套，文档里要把这层关系挑明。本文档里把后者称为
**SessionBudget**（会话预算），与原文档第六节"ResourceArbiter 管控多 Agent"里的提法对齐，
但不在 `ResourceArbiter` 类内部实现，而是放在 `SessionAgentPool` 里管（避免污染
`ResourceArbiter` 现有的、已经测试过的全局预算逻辑）。

### 0.6 daemon 是"项目级"绑定，不是"机器级"——多用户的"family/colleague"是同一个项目下的访客

需要明确一下原方案里隐含的假设：当前 `daemon.py` 的设计原则是"daemon 与 workdir 绑定
（不是全局唯一 daemon）"。所以"owner 的家人朋友"指的是**能访问这一个项目 daemon 的人**，
不是"这个人的 mini-agent 装置认识的所有人"。如果将来要做"一个 Self 跨多个项目认识同一批人"，
那是更后面的事（理论上可以让 `RoleProfileManager` 也挪到 `~/.agent/users/` 全局目录下，
但目前先按项目级做，原方案本身也是这么设计的，这里只是显式确认，避免后续返工）。

---

## 一、总体方案（确认后的版本）

```
daemon 进程（绑定到一个 project_root）
  │
  ├── SelfRunner（新增，常驻线程，不依赖任何用户连接）
  │     - 驱动 AutonomousLoop.tick()（沿用现有实现，只是换了宿主线程）
  │     - 持有 SelfMessageBus 的 "self" 端
  │
  ├── RoleStore（原 UserStore 改名，沿用 api/user_store.py 草稿的 token/role 逻辑）
  │     - <project_root>/.agent/users/users.json
  │     - <project_root>/.agent/users/tokens/*.key
  │
  ├── RoleProfileManager（原 user_store.py::UserProfileManager 改名）
  │     - <project_root>/.agent/users/<user_id>/profile.json   （社交画像，非 profile.py 那个）
  │
  ├── MultiUserAuthMiddleware（替换现有 AuthMiddleware）
  │     - 用 RoleStore 查 token → 注入 request.state.user_id/role/trust_level
  │
  ├── SessionAgentPool（已有草稿 api/session_pool.py，按 0.3 节修正线程模型）
  │     - 每个 (user_id, session_id) → 独立 Agent + AgentBridge + 专属线程
  │     - 用户画像通过 cfg.system_extra 注入（按 0.2 节修正）
  │
  └── 现有 HTTP 路由层（routes.py）
        - 改造：所有端点从 request.app.state.bridge 取单一 bridge
          → 改成从 SessionAgentPool 按 user_id+session_id 取 entry.bridge
```

---

## 二、Phase 1：用户识别（角色系统的最小可用版本）

> **状态：已实现**（见下方"实施记录"）。本节原文保留作为设计依据，
> 实际实现与原计划的差异点列在文末"实施记录"小节。

**目标**：daemon 能区分"谁在跟我说话"，但暂时不动 AgentBridge/单 Agent 模型——
所有用户仍然共用同一个全局 Agent 和同一份历史，只是请求带上了身份。
这是为了把"认证"和"会话隔离"两件事分开验证，降低单次改动的风险（与原方案第十一节的
"过渡方案"建议一致）。

### 改动文件

1. **新增** `.agent/users/users.json` 的运行时管理 —— 复用现有 `api/user_store.py::UserStore`
   （不改名，`UserStore` 本身不冲突），改动点：
   - 删除/改名其中的 `UserProfileManager` → `RoleProfileManager`（0.1 节）
   - `ROLE_TOOL_GROUPS`/`ROLE_BUDGETS` 暂时只存着，Phase 1 不消费

2. **新增** `api/multi_auth.py`（不直接改 `auth.py`，新文件，方便两套鉴权切换/回退）：
   ```python
   class MultiUserAuthMiddleware(BaseHTTPMiddleware):
       def __init__(self, app, role_store: UserStore, allowed_ips: list[str]): ...
       async def dispatch(self, request, call_next):
           # 1. IP 白名单检查（沿用 auth.py 的 _client_ip/_ip_allowed，import 复用）
           # 2. 提取 token（沿用 AuthMiddleware._extract_token 逻辑）
           # 3. role_store.authenticate(token) → UserRecord
           # 4. 注入 request.state.user_id/user_name/role/trust_level/is_loopback
           # 5. 找不到 → 401（保持和现在一样的错误响应格式）
   ```
   `create_app()` 里按配置二选一挂载 `AuthMiddleware`（单 token，向后兼容）或
   `MultiUserAuthMiddleware`（新模式），用一个 `cfg.multi_user_enabled` 开关控制，
   **默认关闭**，避免破坏现有单用户部署。

3. **改动** `api/server.py::HttpServer.__init__`：
   - 启动时如果 `multi_user_enabled`，初始化 `RoleStore`，调用 `ensure_owner()`，
     打印 owner token（复用现有 `print_token_banner` 的风格，owner token 单独一行标注"主用户"）
   - 把 `RoleStore` 实例放进 `app.state.role_store`，供路由层和中间件使用

4. **新增端点**（`routes.py` 新增一组，前缀仍是 `/v1`）：
   ```
   GET    /v1/users                 owner only
   POST   /v1/users                 owner only，body: {name, role, trust_level?, meta?}
   DELETE /v1/users/{user_id}       owner only
   PATCH  /v1/users/{user_id}       owner only，改 role/meta
   POST   /v1/users/{user_id}/token owner only，重新生成 token
   ```
   owner-only 的判断：`request.state.role == "owner"`，不是 owner 返回 403。

5. **新增** `AgentEvent` 加 `user_id` 字段（`api/models.py`），`chat`/`emit_*` 调用处顺带传入
   （为 Phase 3 的"按用户过滤事件"打基础，Phase 1 阶段先加字段，不强制使用）。

6. **新增 CLI** `mini-agent user <list|add|remove|token|profile|note>`，写法对齐
   `cli/daemon.py::run_daemon_cli` 的子命令短路模式，在 `cli/app.py` 里加一行：
   ```python
   if len(sys.argv) > 1 and sys.argv[1] == "user":
       from mini_agent.cli.commands.user_cmd import run_user_cli
       ...
   ```
   新文件 `cli/commands/user_cmd.py`，内部通过 `DaemonClient` 调上面新增的 `/v1/users` 端点
   （CLI 本身不直接读写 `users.json`，统一走 HTTP，避免本地文件和 daemon 内存状态不一致）。

### Phase 1 验收标准
- 单用户模式（不开 `multi_user_enabled`）完全不受影响，现有 `daemon start/stop/status` 和
  CLI 连接流程（含本次修的两个 bug）都正常。
- 开启多用户模式后：
  - owner 用配置的/历史的 token 连接，能调用 `/v1/users` 管理其他用户
  - 新增一个 family 角色用户，拿到 token，能用该 token 调 `/v1/chat`（此时仍是全局共享 Agent
    和历史——预期行为，Phase 3 才隔离）
  - 非 owner 调 `/v1/users` 返回 403

### Phase 1 实施记录（与原计划的差异点）

**已完成，已用真实 HTTP 请求 + 真实 daemon 进程端到端验证通过**（不是只测了单元函数）：
单用户模式向后兼容性、owner 增删改用户、非 owner 403、token 失效立即生效、
`/v1/chat` 的 `meta.user_id/role` 正确传递到 `InputQueue`、CLI 四个子命令
（list/add/role/token；remove 也测了）全部通过。已跑过项目现有的完整 pytest 套件
（1383 passed，2 个失败项确认是改造前就存在、与本次改动完全无关的调试日志截断测试）。

与原计划的具体差异：

1. **改名**：`api/user_store.py::UserProfileManager` → `RoleProfileManager`（按 0.1 节）。
   Phase 1 阶段这个类本身还没接入任何调用链（要等 Phase 2 才会真正用到画像注入），
   这次只是把命名隐患先消掉，类的实现内容未改动。

2. **`AppConfig` 新增字段**：`HttpConfig.multi_user_enabled`（默认 `False`），
   对应 `cfg.http_multi_user_enabled` 属性、配置文件字段 `http_multi_user_enabled`、
   CLI 参数 `--http-multi-user`。完整走通了"CLI 参数 > 配置文件 > 默认值"三层优先级
   （复用 `loader.py` 现成的 `_fb` helper，没有新发明一套读取逻辑）。

3. **`AuthMiddleware` vs `MultiUserAuthMiddleware` 二选一**：没有像原计划写的那样在
   `create_app()` 内部用 `cfg.multi_user_enabled` 做判断——`create_app()` 本身不持有
   `cfg`，改成接收一个 `role_store: Optional[UserStore] = None` 参数，
   `role_store is not None` 即代表开启多用户模式。`HttpServer.__init__` 收到
   `multi_user_enabled=True` 时才会真正构造 `UserStore` 并调用 `ensure_owner()`；
   `create_app()` 本身保持"传什么就用什么"的纯函数风格，不去关心这个布尔开关从哪来。

4. **owner token 的来历**：`ensure_owner(configured_token=self._token)`——也就是说，
   多用户模式下的 owner token 直接复用"原来单 token 模式下那个 token"
   （CLI/配置/环境变量传入的，或者 `load_or_generate_token` 自动生成的那个）。
   这意味着一个原本跑在单用户模式的项目，重启时加上 `--http-multi-user`，
   **旧的 token 不会失效**，只是现在它对应的身份多了一个名字叫 "owner"。
   这个设计原文档没有明确写，是实现时做的决定，记录在这里供确认。

5. **`/v1/users` 在单用户模式下的行为**：原计划没有明确规定这组端点在
   `multi_user_enabled=False` 时该怎么响应。实现选择返回 `404`
   （而不是比如 401/403），原因是：单用户模式下这组端点"根本不存在"，
   404 比"存在但你没权限"更准确，也不会暴露"这个功能其实做了但没开"的信息。

6. **`AgentEvent.user_id` 的实际打点范围**：原计划写"`chat`/`emit_*` 调用处顺带传入"，
   实现时具体打在 `emit_turn_start` / `emit_turn_done` / `emit_error` 这三个
   per-turn 生命周期事件上（`emit_token`/`emit_info`/`emit_fs_change` 等没有加，
   因为 Phase 1 阶段还用不上，等 Phase 3 真正要按用户过滤 `/v1/stream` 订阅时，
   只看 turn 级别的这三个事件就足够判断"这条 turn 是谁发起的"）。

7. **CLI 子命令集合**：原计划写的是
   `mini-agent user <list|add|remove|token|profile|note>`，实现时去掉了
   `profile`/`note`（这两个属于 `RoleProfileManager` 的画像读写，按计划本来就该在
   Phase 2 才接，Phase 1 阶段加进 CLI 但后端什么都没连，等于挂了两个空命令，
   没有实际意义），改成加了一个 `role`（修改用户角色，原计划里有对应的 PATCH 端点，
   但子命令列表里漏列了，这次补上）。最终 Phase 1 的 CLI 子命令是：
   `list / add / remove / role / token`。

8. **`/v1/users` 鉴权细节**：`_require_owner()` 的实现是"`request.state.user_ctx`
   不存在（即单用户模式）就直接放行；存在但 `role != owner` 才 403"——
   和原计划"owner-only 的判断：`request.state.role == "owner"`"基本一致，
   只是改成读 `user_ctx.is_owner`（`UserContext` 上已有的便捷属性）而不是直接比较
   字符串，避免角色名字符串各处对不齐的风险。

---

## 三、Phase 2：per-user 目录与画像（仍不动 AgentBridge）

> **状态：已实现**（见下方"实施记录"）。

**目标**：每个用户有自己的数据目录和"社交画像"，对话内容仍然共享同一个 Agent/历史，
但 system prompt 里会按当前请求的 user_id 注入对应的画像片段。

### 改动文件

1. `api/user_store.py` 里的 `UserProfileManager` 正式改名为 `RoleProfileManager`，
   补全 0.2 节里说的接入点：
   ```python
   # agent.py 或 server.py 的 AgentRunner.run() 里，处理一条命令前：
   role_ctx = role_profile_mgr.build_system_context(cmd.meta["user_id"], cmd.meta["role"])
   bridge.agent.cfg.system_extra = (base_system_extra + "\n\n" + role_ctx).strip()
   ```
   注意：这是 Phase 2 在"共享 Agent"模型下的临时接法（每次处理消息前换一下
   `cfg.system_extra`，配合 `_cached_system` 的 turn 级缓存刚好会在下一条消息时重新构建）。
   Phase 3 进入 per-session Agent 后，这行代码会改成"session 专属 cfg 在创建时就注入好"，
   不需要每条消息都换。

2. `InputQueue.enqueue()` 已经有 `meta: Optional[dict]` 参数（`bridge.py` 里已支持），
   Phase 2 只需要在 `routes.py::chat()` 里把 `request.state.user_id/role` 塞进
   `meta={"user_id": ..., "role": ...}`，`AgentRunner.run()` 取 `cmd.meta` 即可，
   **这条链路已经打通，不需要新增字段**。

3. session 结束时的画像增量更新：
   - 复用 `routes.py::resume_session`/`new_session` 切换前的 `agent.save_session()` 时机，
     顺带调一次 `role_profile_mgr.update_profile(user_id, {"last_contact": time.time(),
     "contact_count": +1, ...})`
   - agent 主动记笔记：新增一个内置工具 `remember_about_user`（或者复用/扩展现有
     `tools/workdir_knowledge.py` 风格的写法），调用
     `role_profile_mgr.add_agent_note(user_id, note)`。这个工具只在角色不是 `owner` 时才需要
     （对 owner 已经有 `profile.py` 的自动画像在跑）。

### Phase 2 验收标准
- 用不同角色的 token 发消息，agent 的语气/边界确实按 `ROLE_PERSONA_HINTS` 变化
  （比如 public 角色不该透漏内部信息）
- `.agent/users/<user_id>/profile.json` 在多轮对话后能看到 `agent_notes`/`last_contact` 更新
- owner 的个性化画像（`profile.py` 那一套）行为不受影响——两套画像系统互不干扰

### Phase 2 实施记录（与原计划的差异点）

**已完成，端到端验证通过**：起了一个真实 `HttpServer`（`multi_user_enabled=True`），
用真实 FastAPI `TestClient` 走 `/v1/chat` → `InputQueue` → `AgentRunner.run()` →
`system_extra` 注入 → `run_turn()` → `remember_about_user` 工具 → `profile.json` 落盘，
确认全链路用的是同一个 `RoleProfileManager` 实例，没有出现"两份画像各管各的"。
也补了单用户模式（`multi_user_enabled=False`）的对照测试，确认 `system_extra`
完全不受影响、`remember_about_user` 始终返回"未开启多用户模式"。完整 pytest 套件
仍是 1383 passed / 2 个无关失败（与 Phase 1 报告的那两个完全一样）。

与原计划的具体差异：

1. **`system_extra` 注入点按计划实现，但补了一个原计划没写的细节：基底值的保存和还原。**
   原计划的伪代码 `bridge.agent.cfg.system_extra = (base_system_extra + ...).strip()`
   没说 `base_system_extra` 从哪来、turn 结束后要不要还原。实现里在 `AgentRunner`
   上懒加载式地缓存一份"构造时刻原本的 `system_extra`"（只读一次，此后不变），
   每个 turn 都基于这份固定基底重新拼接，turn 结束（`finally` 块）后还原回基底值。
   没有这一步的话会有两个问题：①连续多轮拼接会让 `system_extra` 无限增长；
   ②上一个用户的角色提示会泄漏给下一个用户（或者泄漏到 CLI 命令行侧的直接交互、
   `AutonomousLoop.tick()` 等"不是任何 web 用户发起"的场景）。

2. **session 结束时的画像更新，触发时机从"session 切换前"改成"每个 turn 结束后"。**
   原计划写的是"复用 `resume_session`/`new_session` 切换前的 `save_session()` 时机"。
   实际实现没有这么做——理由是 Phase 1/2 阶段所有用户共享同一个全局 Agent/历史，
   用户很可能整段对话都不会触发 `/sessions/new` 或 `/resume`（这两个端点本来是给
   "切换到另一个历史会话"用的，不是聊天的必经路径）。如果按原计划只在 session
   切换前更新，`last_contact`/`contact_count` 在很多真实场景下会一直停在 0，
   对不上验收标准里"多轮对话后能看到更新"的要求。改成在 `AgentRunner.run()`
   每个 turn 成功结束后更新（`api/server.py`），覆盖面更准确，且不需要等
   Phase 3 引入 per-session 模型才能修。

3. **`remember_about_user` 工具的"当前用户"获取方式，没有照搬
   `tools/workdir_knowledge.py` 的 thread-local provider 注册模式
   （在 `Agent.__init__` 里注册一次）。** 原因是 `project_root`/`session_id`
   在一个 Agent 实例生命周期内基本不变，适合"构造时注册一次、之后懒读取"；
   但"当前是哪个用户在跟我说话"在共享 Agent 模型下是**逐条消息变化**的——
   Agent 实例不变，但服务的用户在变。改成由 `AgentRunner.run()`
   （运行在它自己专属的后台线程上）在每次调用 `run_turn()` 前直接写入
   thread-local（`tools/user_memory.py::set_current_user()`），`run_turn()`
   结束后清空。这个差异点已经在新文件 `tools/user_memory.py` 的模块 docstring
   里详细写明，供以后维护时对照。

4. **`remember_about_user` 的可见性：始终注册，不按是否多用户模式隐藏。**
   工具在 `Agent.__init__` 时无条件 import（触发 `@tool` 装饰器注册），单用户
   模式下也会出现在 LLM 可见的工具列表里，只是调用后会得到"未开启多用户模式"
   的提示，不会真正写入任何文件。这与 `skill_propose` 等工具的现有惯例一致
   （永远注册，不满足前置条件时优雅拒绝，而不是动态增减工具列表）——这个
   项目目前没有"按运行时配置动态隐藏某个工具"的机制，没有为此单独新增一套。
   代价是单用户模式的用户会在工具列表里多看到一个用不上的工具定义（占一点
   token），原计划没有提到这一点，记录在这里供后续如果觉得有必要优化时参考。

5. **工具描述语言**：原计划的伪代码片段是中文注释，但实现时工具的 `description`/
   `schema` 字段全部用英文撰写——这是核对 `tools/builtin.py`、`workdir_knowledge.py`、
   `evolution.py` 等现有工具后发现的项目既有惯例（工具面向 LLM 的文本统一用英文，
   周围的 Python 注释/docstring 仍然是中文），照此惯例实现，不是新规定。

---

## 四、Phase 3：SessionAgentPool（每个 session 一个独立 Agent）

> **状态：已实现**（见本节末尾"实施记录"，内容较长，记录了 3 个在实测中
> 才发现的真实 bug，包括一个死锁）。

**目标**：真正的并发隔离。这是工作量最大、风险最高的一步，原方案第十一节也明确说
"这是破坏性改动，建议一次性大改"。

### 3.1 先修正 `api/session_pool.py` 草稿里的问题

按 0.3 节的结论，`_create_entry()` 不能在调用线程里构造 `Agent`。改成：

```python
def get_or_create(self, user_ctx, session_id, profile_manager) -> SessionEntry:
    with self._lock:
        # ...已有的查找/并发上限逻辑不变...
        ready_event = threading.Event()
        box = {}  # 用来把"线程里构造结果"带出来

        def _bootstrap():
            try:
                agent, bridge = self._build_agent_and_bridge(user_ctx, session_id, profile_manager)
                box["agent"], box["bridge"] = agent, bridge
            except Exception as e:
                box["error"] = e
            finally:
                ready_event.set()
            # 构造完成后，紧接着就在这同一个线程里跑 AgentRunner.run()
            # （不能 start() 一个新线程，否则 thread-local 又对不上了）
            runner_body(bridge)  # 阻塞，直到 stop() 被调用

        t = threading.Thread(target=_bootstrap, daemon=True,
                              name=f"session-{session_id[:8]}")
        t.start()
        ready_event.wait(timeout=30)
        if "error" in box:
            raise box["error"]
        entry = SessionEntry(agent=box["agent"], bridge=box["bridge"], runner=t, ...)
        self._pool[session_id] = entry
        return entry
```

要点：
- `Agent()` 构造 + `AgentRunner` 主循环，**在同一个线程函数里前后执行**，
  线程一启动就先构造 Agent（thread-local 在这时写入），再进入 `while not stop`，
  全程不切线程。
- 调用方（HTTP 请求线程）用 `Event` 等构造完成，但不阻塞太久（Agent 构造本身很轻，
  真正慢的 LLM 调用发生在 `run_turn()` 里，那是后面异步发生的事，不卡这个等待）。
- `runner.is_alive()` 等健康检查逻辑（已有的 `_check_health`/`_gc_idle`）不用改，
  因为 `SessionEntry.runner` 还是一个 `Thread` 对象，接口没变。

### 3.2 SelfRunner（新增）

新文件 `evolution/self_runner.py`：

```python
class SelfRunner(threading.Thread):
    """
    daemon 启动时创建，唯一职责是驱动 AutonomousLoop.tick()。
    不持有任何 SessionAgent，不处理用户消息。
    """
    def __init__(self, autonomous_loop, bus: SelfMessageBus, poll_interval=5.0): ...
    def run(self):
        self._bus.register("self")
        while not self._stop.is_set():
            if self._autonomous_loop.should_tick():
                try:
                    self._autonomous_loop.tick()
                except Exception:
                    pass
            # 顺带处理 SelfMessageBus 收到的消息（session_summary/profile_update/approval_req）
            for msg in self._bus.drain_all("self"):
                self._handle_message(msg)
            time.sleep(self._poll_interval)
```

`HttpServer.start()` 里，原来 `self._runner = AgentRunner(...)` 那一段，在
`multi_user_enabled` 模式下换成：构造 `SelfRunner` + `SessionAgentPool`，不再构造
全局唯一的 `AgentRunner`/`bridge`。非多用户模式（向后兼容）保持原样不变。

### 3.3 路由层改造

`api/routes.py` 里目前所有端点都用 `_bridge(request) -> request.app.state.bridge` 取
唯一 bridge。改造方式（保持函数签名基本不变，最小化 diff）：

```python
def _bridge(request: Request) -> AgentBridge:
    pool: Optional[SessionAgentPool] = getattr(request.app.state, "session_pool", None)
    if pool is None:
        return request.app.state.bridge          # 向后兼容：单用户模式
    session_id = _resolve_session_id(request)     # 见下
    entry = pool.get_or_create(request.state.user_ctx, session_id, ...)
    return entry.bridge

def _resolve_session_id(request: Request) -> str:
    # chat 请求体里的 session_id，或该用户当前 active session，或新建
    ...
```

这样改动面只集中在 `_bridge()` 这一个函数和 `chat()`/`new_session()`/`resume_session()`
里"决定 session_id"的那几行，其余几十个端点（fs_*、permissions、turns 等）完全不用动，
因为它们都是通过 `_bridge(request)` 间接拿 bridge 的。

`/v1/stream/{turn_id}` 的权限检查按原方案第七节实现：非 owner 只能订阅自己 user_id 下的 turn
（`pool.find_by_turn()` 已经有现成实现，补一个 `entry.user_id == request.state.user_id` 判断）。

### 3.4 连接/默认 session 的选择逻辑

原方案提到的"用户连接后默认新 session，还是给最近 session 选"——现状 CLI 端
（`cli/daemon.py::_pick_session`）已经实现了"列出最近 session + 新建"的选择界面，
这套交互**不用重新设计**，Phase 3 只需要把它背后调的 `/v1/sessions`、
`/v1/sessions/new`、`/v1/sessions/{id}/resume` 三个端点改成按 `user_id` 过滤
（一个用户只能看到/操作自己名下的 session），数据来源从"当前唯一 agent 的
session_manager"变成"该用户名下所有 session 文件"（按 `meta.json` 里的 `user_id` 过滤，
`session.py`/`SessionManager` 现有的存储格式需要确认是否已经记录 user_id——如果没有，
这里要顺带给 session 元数据加一个 `user_id` 字段）。

### Phase 3 验收标准
- 两个不同角色的用户同时发消息，互不阻塞（用 `time.sleep` 模拟慢响应验证）
- 其中一个 SessionAgent 故意抛异常（比如发一条触发工具报错的消息），不影响另一个用户的对话，
  也不影响 Self 的 tick 继续跑
- `mini-agent daemon status` 能看到 `active_sessions` 数量、Self 的 `autonomy_level`/
  `last_tick`（现有字段，确认改造后仍然准确）
- CLI 客户端（daemon.py 连接模式）的两个 bug 修复在多用户模式下依然有效
  （这条是回归测试，不是新功能）

### Phase 3 实施记录（与原计划的差异点，含 3 个实测中才发现的真实 bug）

**已完成，端到端验证通过**：起了真实 `HttpServer`（`multi_user_enabled=True`，
真实 `Agent` 实例，用不需要 API key 的 `ollama` provider 让构造能完整跑通，
而不是 mock），走完整 FastAPI `TestClient` 路径验证了：lazy session 创建
（`/sessions/new` 不会立刻构造 Agent）、per-user 目录隔离、并发创建 5 个 session
无报错、`suspend()`（含 `.join()`）、`stop_all()`、按 `turn_id`/`req_id` 路由到正确
session（而不是"猜最近活跃的那个"）。完整 pytest 套件仍是 1383 passed / 2 个
无关失败。

#### Bug 1（严重，会让 Phase 3 完全不可用）：AgentRunner 的 `self._stop` 遮蔽了
`threading.Thread._stop()`

`AgentRunner.__init__` 里原来写的是 `self._stop = threading.Event()`。
`threading.Thread` 自己有一个私有方法也叫 `_stop()`（线程真正结束后内部清理用，
见 `Thread._wait_for_tstate_lock`）。只要从来没人对这个线程调用过 `.join()`，
这个命名冲突完全不会暴露——Phase 1/2 确实从来没调用过 `.join()`。
Phase 3 的 `SessionAgentPool._do_suspend()` 第一次需要真正 `join()` 等线程退出，
一调用就报 `TypeError: 'Event' object is not callable`。
**已重命名为 `self._stop_evt`**，问题修复，并补了能复现这个问题的测试
（先在没有这次重命名的版本上跑通了失败复现，再确认修复后通过）。

#### Bug 2（严重，会让构造失败的请求永久卡死）：`get_or_create()` 持锁等待造成死锁

最初的实现里，`get_or_create()` 整个方法体（包括等待 `runner.ready_event` 的
阻塞调用）都包在 `with self._lock:` 里。而 `AgentRunner` 构造失败时调用的
`on_crash` 回调（运行在**另一条线程**——也就是刚 `start()` 的那条 AgentRunner
线程——上）需要 `with self._lock:` 才能把这个 session 从 pool 摘掉。
于是：调用方线程握着锁等 `ready_event`；`AgentRunner` 线程的 `on_crash` 想拿
同一把锁才能让调用方的等待有意义地结束；两边互相等对方，死锁。
表现出来就是：任何 Agent 构造失败（比如这次测试里"没有配置 LLM API key"）
都会让 `get_or_create()` 卡满 `AGENT_READY_TIMEOUT`（30 秒），而不是快速报错。

这个 bug 不是靠读代码看出来的，是写完测试**实际跑出来**才发现——构造一个会
失败的 factory（用没配 API key 的 anthropic provider）调 `get_or_create()`，
卡了 30 秒才超时，而单独测 `AgentRunner` 本身（不经过 pool）瞬间就能正确返回
`init_error`，对比之下才定位到问题出在 `SessionAgentPool` 自己的锁设计上。

**修复**：改成"每个 session_id 一把构造锁"（`_construction_locks: dict[str,
threading.Lock]`），`self._lock`（pool 级别的锁）只用于简短的字典读写，
**从不**跨越"等待另一个线程完成某件事"这种阻塞操作。同一个 `session_id` 的
并发请求会在各自的构造锁上排队（这是预期行为：没必要对同一个 session 并发
构造两次），不同 `session_id` 之间完全不互相阻塞。修复后用没配 API key 的
factory 重测：0.6 秒内正确抛出异常，不再有 30 秒卡死。

#### Bug 3（中等，预先存在，Phase 3 之前从未暴露）：`StatusResponse`/`ChatRequest`
缺字段，导致 CLI 的两处逻辑一直没生效

核对 `cli/daemon.py` 时发现两个预先存在、与多用户改造本身无关的 bug：
1. `DaemonClient.send_message()` 一直在发 `payload["session_id"] = session_id`，
   但 `ChatRequest`（服务端模型）从来没有声明过 `session_id` 字段——Pydantic
   默认静默丢弃多余字段，这个值一直被服务端忽略。
2. `_pick_session()` 一直在读 `status.get("session_id", "")`，但
   `StatusResponse` 从来没有 `session_id` 字段——session 选择菜单里的
   "●active"标记因此从来没有真正生效过（永远拿到空字符串，永远不匹配）。

这两个字段 Phase 3 必须补上（否则 `/v1/chat` 没法知道要发到哪个 session），
顺手就把这两个老 bug 也修了——`ChatRequest`/`StatusResponse`/`ChatResponse`
都加上了 `session_id` 字段，`cli/daemon.py` 不需要改任何代码，这两处逻辑
现在会自然生效。

#### 与原方案 3.3/3.4 节的差异

1. **per-user session 目录，而不是给 `SessionMeta` 加 `user_id` 字段**
   （3.4 节原方案的建议）。已在 `session_pool.py` 里详细论证：物理目录分离
   （`.agent/users/<user_id>/sessions/`）比"共享目录 + 按字段过滤"隔离性更强
   （不存在过滤逻辑写错导致越权读到别人 session 的风险），且不需要改
   `SessionMeta`/`Session` 的数据结构，`session.py` 完全没有改动。

2. **`/v1/sessions/new` 和 `/v1/sessions/{id}/resume` 在多用户模式下是"轻量"
   操作，不会立刻构造 Agent。** 原方案没有明确这一点。实现选择："new"只生成
   一个新 `session_id` 返回；"resume"乐观接受任意 ID，不验证是否存在。
   真正的 Agent 构造推迟到第一次 `/v1/chat` 时由 `_bridge()` 触发
   `SessionAgentPool.get_or_create()`。这样设计是因为 CLI 的 `_pick_session()`
   流程里，"选了一个 session"和"真的开始对话"之间可能有用户思考的间隙，
   不应该在用户还没说话之前就付出 Agent 构造的代价（包括这次实测中发现的
   "MCP/SkillLoader/LLMClient 都要重新建一遍"的真实成本）。

3. **`/v1/stream/{turn_id}` 和 `/v1/turns/{turn_id}` 不能用"该用户最近活跃的
   session"兜底解析。** 这是原方案 §3.3 提到但没写清楚怎么落地的一点——
   实测中发现：如果一个用户同时开着两个 session（比如两个浏览器标签），
   按 turn_id 查询/订阅如果只看"最近活跃的那个 session"，会在用户切换到
   第二个 session 后，第一个 session 的 turn_id 查询全部失效（明明那个 turn
   还在跑/刚跑完，却查不到）。已改为用 `SessionAgentPool.find_by_turn()`
   先正确定位 turn_id 实际所属的 SessionEntry，再做权限校验
   （非 owner 只能查自己的，owner 可以查任何人的——owner 特权）。
   同样的问题和修法也适用于 `/v1/permissions/{req_id}`，新增了
   `find_by_permission_req()`（原方案完全没提到这个端点需要类似处理，
   是实现时类比 `find_by_turn` 的场景发现的）。

4. **MCP 工具注册的并发安全 + SkillLoader 不能共享**（0.3 节已经提到线程模型
   问题，但原方案没有意识到这两个具体的共享可变状态风险）：
   - `tools/__init__.py::ToolRegistry.register()` 是纯 dict 操作，没有锁，
     `MCPManager.register_all()` 注册进的是**全局共享**的 `_default_registry`。
     已加全局 `_agent_construction_lock`，序列化"构造 Agent"这一步（不影响
     `run_turn()` 的并发度）。
   - `SkillLoader._active`（已激活 skill 列表）是实例级可变状态，不能跨
     session 共享，否则一个用户激活的 skill 会"传染"给另一个用户的对话。
     每个 `SessionEntry` 现在都构造自己独立的 `SkillLoader`（复用同一份
     `skill_dirs` 列表，重新跑 `_discover()` 扫描——这个扫描成本是已知、
     接受的取舍，已在 `session_pool.py` 模块 docstring 里写明）。

5. **已知限制，本次未修复**：`tools/introspection.py` 里的 `agent_status` /
   `agent_inspect` / `agent_patch` / `agent_policy` 四个工具，注册时没有用
   `override=True`，意味着**进程里第一个构造的 Agent**会成功注册，
   之后构造的所有 Agent（包括 Self 自己之后如果重建，以及 Phase 3 的每一个
   SessionAgent）调用 `register_introspection_tools()` 都会因为名字冲突直接
   跳过（已有的"失败则警告并跳过"兜底，不会崩，但后果是：这四个工具实际上
   永远只会操作"进程里第一个 Agent"的状态，不管当前是哪个 SessionAgent 在问。
   这是一个**预先存在**的设计假设（"进程里只有一个 Agent"），Phase 3 第一次
   让这个假设失效，但修复需要给 `ToolRegistry` 引入"per-Agent 命名空间"这类
   更大的改动，超出本次范围，记录为已知限制。受影响的只是这四个自省工具，
   不影响对话/工具调用/session 隔离等核心功能。

#### 文件改动（实际 vs 原计划表格）

原计划表格写的新增文件是 `evolution/self_runner.py`。**实际没有新建这个文件**——
深入看了一遍现有的 `AutonomousLoop`/`AgentRunner` 关系后发现：`AutonomousLoop.tick()`
本来就是通过"submit 一个 autonomous 类型的任务到 InputQueue，由同一个
AgentRunner 循环消费"的方式工作的（`_submit_autonomous_task()`），这个机制
不需要一个全新的 `SelfRunner` 类——本次 Phase 3 的设计是：Self 继续用
`HttpServer` 原有的 `self._bridge`/`self._runner`（`app.py` 在主线程构造的那个
`agent` 参数，多用户模式下被重新定位为"Self 的 agent"，但构造路径和接口完全
不变），`SessionAgentPool` 是纯粹新增、平行存在的——这比"重新设计 Self 的运行
模型"风险小得多，且复用了已经验证过的 `AgentRunner`/`AutonomousLoop` 协作关系。
`evolution/self_bus.py`（Phase 4 计划项）目前 `SelfMessage`/`SelfMessageBus`
仍然定义在 `session_pool.py` 里，留给 Phase 4 视情况再搬。

---

## 五、Phase 4：Self ↔ SessionAgent 通信

> **状态：已实现**（见本节末尾"实施记录"，记录了 4 个实测中发现的真实 bug，
> 其中一个比这次多用户改造本身的历史更长——`AutonomousLoop` 在 daemon 模式下
> 从一开始就没有真正工作过）。

**目标**：原方案第五节的 `SelfMessageBus`，草稿已经写在 `api/session_pool.py` 里
（`SelfMessage`/`SelfMessageBus` 两个类），基本可以直接用，只需要：

1. 把这两个类挪到独立文件 `evolution/self_bus.py`（原方案也是这么建议的），
   `session_pool.py` 改成从那边 import，避免"会话池模块"和"消息总线"耦合在一个文件里。
2. `SelfRunner`（3.2 节）负责消费 `to_id="self"` 的消息。
3. `SessionAgentPool._create_entry` 在 session 收尾（`_do_suspend`）时，往 bus 发一条
   `session_summary`，`SelfRunner` 收到后调用 `RoleProfileManager.update_profile()`
   做汇总（原方案 §5.3 的 `_collect_session_summaries` 逻辑，挂在 `SelfRunner` 而不是
   `AutonomousLoop` 内部——`AutonomousLoop` 保持现有的"自主任务调度"职责单一，
   不让它也管消息总线）。

### Phase 4 验收标准
- 一次 family 角色的对话结束后，Self 这边能在下次 `tick()` 时看到该 session 的摘要
- owner 通过 `mini-agent self status`（CLI 新命令，对应原方案第九节）能看到
  GoalBacklog、最近的 session_crashed 通知等

### Phase 4 实施记录（与原计划的差异点，含 4 个实测中发现的真实 bug）

**已完成，端到端验证通过**：用真实 `HttpServer`（multi_user_enabled=True）测试了
完整链路——session 正常结束 → Self 收到 `session_summary` → `RoleProfileManager`
正确写入 `recent_sessions`（owner 的 session 正确被排除，因为 owner 有自己独立的
画像系统）；SessionAgent 崩溃 → Self 收到 `session_crashed` → 正确写入
`activity_digest.jsonl`；`mini-agent self status` 端到端跑通，能看到 Autonomous
Loop 状态、Goals、最近活动、SessionAgentPool 概况。完整 pytest 套件仍是
1383 passed / 2 个无关失败。

#### 与原计划第 1/2 点的差异：没有新建 `evolution/self_runner.py` 和
`evolution/self_bus.py`

如 Phase 3 实施记录第 5 点已经说明的：Self 复用 `HttpServer._bridge`/`_runner`
（原有的 `AgentRunner`），没有单独的 `SelfRunner` 类。`SelfMessage`/
`SelfMessageBus` 仍然定义在 `session_pool.py` 里，没有挪到 `evolution/self_bus.py`
——目前这两个类只有 `session_pool.py`（生产端）和 `server.py`（消费端）在用，
专门为它们建一个新文件、再处理两边的 import 调整，相对于"两个类总共不到
150 行代码、留在原地完全不影响理解"的现状，收益不明显，所以没有做这次搬迁。
如果未来 Phase 5+ 还有其它模块需要用到 `SelfMessageBus`（不只是 session_pool
和 server 两边），再挪文件会更有理由。

#### Bug 1（中等，"消费端"实际从未真正接上过）：Self 的 `AgentRunner` 没有
拿到 `self_message_bus`

`AgentRunner.__init__` 支持 `self_message_bus` 参数，`_main_loop()` 里也已经写好
了"idle 周期 drain 消息"的逻辑（`self._self_message_bus is not None` 判断 +
`_drain_self_messages()` 调用）。但 `HttpServer.__init__` 构造 `self._runner =
AgentRunner(...)` 时，没有把 `self._self_message_bus` 传进去——构造时漏了这一个
参数，导致 Self 自己的 `AgentRunner` 实例里 `self_message_bus` 始终是 `None`，
"消费端"代码写对了，但从未真正被触发过。这是写本节测试时，加了一个断言
专门检查"Self 的 AgentRunner 是否真的拿到了同一个 bus 实例"才发现的——
不写这个断言的话，光看"程序没有报错"是看不出这个问题的（因为 `None` 分支
也是合法路径，不会抛异常，只是什么都不做）。已修复：补上
`self_message_bus=self._self_message_bus` 这一个参数。

#### Bug 2（中等，预先存在，比本次改造历史更长）：`Agent` 没有 `_paths` 属性，
`_build_autonomous_loop()` 因此从一开始就返回 `None`

`HttpServer._build_autonomous_loop()` 原来写的是 `getattr(agent, "_paths", None)`
来获取 `AgentPaths` 实例。但 `Agent` 类自己从来不缓存这个属性——`agent.py` 内部
任何需要 `AgentPaths` 的地方，都是 `AgentPaths(self.cfg.project_root)` 现场构造。
这意味着 `getattr(agent, "_paths", None)` **永远返回 `None`**，`_build_autonomous_loop()`
因此永远在检查 `paths is None` 的那一行就提前返回 `None`——`AutonomousLoop`
在 daemon 模式下从一开始就没有被真正构造过，`/v1/status` 里的
`autonomy_level`/`last_autonomous_tick_at`/`tick_count` 这些字段事实上一直都是
默认值，从来没有真实更新过。

这个 bug 和本次多用户改造完全无关（哪怕从来没做过这次 Phase 1-4 的任何改动，
单用户模式的 daemon 也一样中这个 bug），是写 `mini-agent self status` 时，
看到输出里"Autonomous Loop: not available"才顺着挖出来、确认、修复的。
修复方式和 Bug 3（下面）完全一样：改成 `AgentPaths(cfg.project_root)` 现场构造。

确认这个修复是安全的：`AutonomousLoop.tick()` 在默认的 `passive` autonomy
级别下（没有人去手动改 `self_profile.json` 的话，新/老部署都是这个默认级别）
只做两件已经存在、本来就该周期性运行的维护性工作（Phase G 时间门控检查、
workdir knowledge 整合），两者都包在自己的 try/except 里，不会因为"现在终于
真的会被调用了"而引入新的异常风险。`autonomous` 级别会做更多事，但需要显式
配置才会进入那个档位，不会因为这次修复就让任何现有部署意外进入更主动的模式。

#### Bug 3（同 Bug 2 的另一处）：`_handle_session_crashed` 同样错误地假设
`agent._paths` 存在

`server.py::AgentRunner._handle_session_crashed()` 里给 `append_activity_digest()`
传参数时，同样写的是 `getattr(self._bridge.agent, "_paths", None)`，同样的根因，
同样永远是 `None`，这段"把 session 崩溃记录写进 activity_digest.jsonl"的代码
之前是静默 no-op——是写本节"session_crashed 应该能在 digest 文件里查到"的测试时，
断言失败（文件不存在）才发现的。修复方式同 Bug 2。

#### Bug 4（小，纯粹是 CLI 显示层的字段名写错）：`mini-agent self status` 读
`description` 字段，但 `GoalNode.to_dict()` 用的字段名是 `title`

`cli/commands/self_cmd.py` 最初写的是 `g.get('description', '')`，实际跑出来
goal 标题那一列永远是空的。核对 `GoalNode.to_dict()` 后确认字段名是 `title`，
已修正。

#### 小结：这次 Phase 4 实际花的精力，"接消费端"本身只是几行代码，
真正的工作量在于验证

原计划第 1/2/3 点描述的功能本身代码量不大（草稿基本可以直接用），但实测下来，
4 个 bug 里有 3 个都是"代码逻辑是对的，但因为一个错误的属性名假设，从一开始
就没有被真正执行过"——这类 bug 不写端到端测试、只看代码 review 是基本看不出来
的（`getattr(obj, "wrong_name", None)` 永远合法，永远不报错，只是悄悄走了
fallback 分支）。这进一步印证了之前 Phase 1-3 实施记录里反复出现的同一个教训：
对涉及多线程/多实例协作的代码，"测试通过"和"代码逻辑看起来对"是两件不同的事，
后者不能替代前者。

---

## 六、本次需要用户确认的关键决策点

> Phase 1-3 已经按以下默认方案实施（用户选择"按计划文档开始"/"继续"，
> 未逐项单独答复，故采用文档原本建议的默认选项，遇到需要临场决策的地方
> 选择风险最低、与现有代码最一致的方案，并在对应 Phase 的"实施记录"里
> 详细记录了取舍理由）。

1. ~~**`UserProfileManager` 改名为 `RoleProfileManager`**（0.1 节）——同意吗，还是有更喜欢的名字？~~
   **已实施**：改名为 `RoleProfileManager`。
2. ~~**角色画像走 `cfg.system_extra`，不新增 `extra_system` 字段**（0.2 节）——这是修正草稿里的
   一个直接 bug，应该没有争议，但请确认。~~
   **已确认无争议**；该接入点要等 Phase 2 才会真正用上（Phase 1 只是改了名字，没动这部分逻辑）。
3. ~~**Phase 1/2 阶段先不动 AgentBridge，所有用户共享同一个全局 Agent**——……~~
   **已按此方案实施 Phase 1/2**。Phase 3 完成后这一点已经改变：每个用户每个
   session 现在都有自己独立的 Agent + 对话历史，不再共享。
4. ~~**Phase 3 的线程模型修正**（0.3/3.1 节）——是否需要现在就先单独修一下
   现状的 thread-local bug？~~
   **已在 Phase 3 里一并修复**（`AgentRunner` 加 `agent_factory` 机制），
   没有单独提前修——事后看这样选是对的，因为 Phase 3 实施过程中又额外发现
   并修复了两个更严重的真实 bug（`self._stop` 命名冲突、`get_or_create()`
   死锁），如果提前单独修线程模型问题，后面这两个 bug 大概率还是要在
   Phase 3 阶段才会暴露出来，不如一起处理、一起测试。
5. ~~**Phase 3 验收标准里"对话隔离"的测试方式**——你是否方便在本地实际跑两个
   客户端测试？~~
   **改用 `ollama` provider 自行解决**：它不需要 API key 就能完成 `Agent()`
   构造和发起真实的 LLM 请求（请求本身会因为本地没有真的跑着 ollama 服务而
   失败在网络层，但这正好把"基础设施是否正确"和"LLM 调用本身"两件事干净地
   分开了——本次所有 Phase 3 测试验证的都是前者：session 隔离、并发构造、
   崩溃处理、turn/权限路由，没有一个依赖真实 LLM 返回内容）。
6. **`tools/introspection.py` 的多 Agent 兼容问题**（见 Phase 3 实施记录第 5
   点）——`agent_status`/`agent_inspect`/`agent_patch`/`agent_policy` 四个
   工具目前只会绑定到"进程里第一个构造的 Agent"，Phase 3 之后这个假设不再
   成立。本次记录为已知限制，未修复（需要给 `ToolRegistry` 引入 per-Agent
   命名空间，是比 Phase 3 本身更大的改动）。是否需要单独排期修复，还是接受
   "这四个工具在多用户模式下不可靠，其它功能不受影响"这个现状？

---

## 七、文件改动清单（汇总，按 Phase）

| Phase | 新增文件 | 修改文件 |
|---|---|---|
| 1 | `api/multi_auth.py`, `cli/commands/user_cmd.py` | `api/user_store.py`（改名+清理）, `api/server.py`, `api/routes.py`, `api/models.py`, `cli/app.py` |
| 2 | `tools/user_memory.py`（新工具 `remember_about_user`） | `api/server.py`（`AgentRunner.run` 注入 `system_extra`、画像更新）, `agent.py`（import 触发工具注册） |
| 3 | （无新文件——`evolution/self_runner.py` 计划被取消，见 Phase 3 实施记录第 5 点） | `api/session_pool.py`（重写：线程模型修正、构造锁死锁修复、per-user 目录）, `api/server.py`（`AgentRunner` 加 `agent_factory`/`on_crash`/`ready_event`、`self._stop`→`self._stop_evt` 改名、`HttpServer` 装配 `SessionAgentPool`）, `api/routes.py`（`_bridge`/`_resolve_session_id`/`_user_session_manager`，5 个 session 端点 + `stream_turn`/`get_turn`/permissions 端点的多用户分支）, `api/models.py`（`ChatRequest`/`StatusResponse`/`ChatResponse` 补 `session_id` 字段）, `skills/__init__.py`（`SkillLoader` 加 `dirs` 只读属性） |
| 4 | `cli/commands/self_cmd.py`（`mini-agent self status`） | `api/server.py`（修复 `self_message_bus` 漏传、修复两处 `agent._paths` 不存在的 bug）, `api/routes.py`（新增 `/v1/self/status`） |

---

## 八、与本次已修复 bug 的关系（确认不冲突）

本次已经独立修复（与本文档无关，先行生效）：
- `cli/daemon.py`：连接模式提示符统一显示为 `You ❯`，不再用 agent 名字
- `api/server.py` + `api/bridge.py`：`run_turn()` 出错时也会发 `turn_done`（带 `error` 字段），
  CLI 客户端新增 `on_error` 回调，不再因为服务端异常而卡死到 600 秒超时

这两个修复都在 `AgentRunner.run()` / `DaemonClient` 范围内，Phase 3 改造时这部分逻辑会被
"每个 session 一个 AgentRunner"的新模型整体替换，**到时需要确认这两个修复的逻辑
（尤其是出错时发 turn_done 的兜底）原样保留在新的 per-session 运行循环里**，
不要在搬迁代码时把这个修复漏掉。

---

## 九、Windows 实测发现的 bug：选完 session 后看不到 `You ❯` 提示符（已修复）

> 用户实测场景：终端 B 连接 daemon → 展示 session 列表 → 选择某个 session 后，
> 画面上完全没有 `You ❯` 输入提示符，表现像是卡住。用户当时的猜测
> （"应该是状态栏刷新导致的问题，用户输入时不应该显示状态栏"）**方向是对的**，
> 但根因比"要不要显示状态栏"更具体——不是"显示了不该显示的内容"，而是
> "刷新线程的一次正常心跳，异步擦掉了主线程刚打印出来的提示符"。

### 9.1 根因

`cli/daemon.py` 里 `_pick_session()` 和 `run_connected_repl()` 主循环，在每次阻塞
`input()`/`readline()` 前后，原来的实现是：

```python
term.set_statusbar_provider(None)      # 阻塞输入前："关掉"状态栏
raw = input("...")
term.set_statusbar_provider(lambda: ...)  # 输入完成后：重新打开
```

这是一个**看起来合理但实际不充分**的简化版。`Terminal._refresh_loop()`
（`ui/terminal.py`）每 `_refresh_interval`（默认 0.25 秒）醒来一次，决定"这个
周期要不要刷新屏幕"用的标志是 `_refresh_paused`，**不是** "`_statusbar_provider`
是否为 `None`"。把 provider 设成 `None` 只是让 `_refresh_loop` 在拉取内容那一步
拿到空列表，但它后面无条件执行的 `self._q.put(_Msg("_refresh", None))` 完全不受
影响——这条 `_refresh` 消息照常每 250ms 投递给 `render_thread`。

`render_thread` 收到 `_refresh` 时的判断逻辑（`_handle()` 里的 `elif kind ==
"_refresh"` 分支）：只要不在 `streaming` 也不在 `bar_suspended` 状态，就执行
`self._erase_bar(); self._draw_bar()`。`_erase_bar()` 发出的是**相对于当前光标
位置**的 ANSI 序列（反复 `\x1b[1A\x1b[2K` 上移清行），它的前提假设是"光标现在
停在状态栏正下方"——但 connected 模式从来不会调用任何会设置 `_bar_suspended`
的方法（那些方法只在 `repl.py` 本地模式路径里用），所以这个分支永远会被命中；
而此刻光标实际位置是 `daemon.py` 自己用 `_sys.stdout.write(prompt)` 直接写出来
的、`Terminal` 完全不知情的新位置——`You ❯ ` 提示符后面。`_erase_bar()` 按照
"状态栏在这下面"的错误假设做相对擦除，结果擦掉/打乱的是刚打印的提示符那一行；
随后 `_draw_bar()` 因为 `_statusbar_lines` 早已被清空（provider 是 `None`）什么
都不画——净效果就是提示符消失，画面空白。

这本质上是 connected 模式自己发明了一套和 `Terminal` 真正的同步机制语义不同的
"暂停"实现，重新踩中了 `Terminal` 类本来就是为了解决这类竞态才设计的那套机制
（`_enter_input_mode()`/`_exit_input_mode()`，见 `tools/user_input.py`、
`permissions.py` 里阻塞输入点的标准用法）原本要防住的坑。

### 9.2 修复

`_pick_session()` 和 `run_connected_repl()` 主循环里的 `_bar_pause()`/
`_bar_resume()`，改为直接调用 `term._enter_input_mode()`/`term._exit_input_mode()`：

```python
term._enter_input_mode()   # 阻塞输入前：设置 _refresh_paused + 双重哨兵排空队列
                            #   + 直接同步擦掉已画的状态栏
raw = input("...")
term._exit_input_mode()    # 输入完成后：清除 _refresh_paused，刷新线程恢复周期重绘
```

`_enter_input_mode()` 内部会先设置 `_refresh_paused`，再用两次 `_Msg("_noop")` +
`q.join()` 确认 `render_thread` 真正空闲（杜绝"标志已设置但队列里还有残余消息"
的窗口），最后才同步调用 `_erase_bar_direct()` 清掉当前画着的状态栏——三步做完
之后才返回，这时候再打印提示符是绝对安全的：刷新线程在 `_refresh_paused` 被
清除之前不会再产生任何状态栏相关的队列消息。`_exit_input_mode()` 则负责把
阻塞期间积压的其它消息按顺序重新入队、清除 `_refresh_paused`，让刷新线程
重新开始按周期工作。

`provider` 本身不再需要"摘掉再装回"——它全程保持注册为
`_connected_status_bar_provider`，阻塞输入期间的"不刷新"完全由
`_refresh_paused` 保证，这也是为什么旧实现"看起来合理"：它确实让 provider
返回空，只是没意识到 `_refresh` 心跳的投递跟 provider 是否为空是两件独立的事。

### 9.3 影响范围与验证

只改了 `cli/daemon.py` 两处（`_pick_session()` 的 input 循环、
`run_connected_repl()` 的 `_bar_pause`/`_bar_resume`），未改动 `ui/terminal.py`
本身——`_enter_input_mode`/`_exit_input_mode` 是已经存在、已经被其它模块
（`tools/user_input.py`、`permissions.py`）验证过的官方机制，本次只是让
connected 模式也走这条路径，而不是用自己的平行实现。

新增 `tests/test_daemon_connected_statusbar.py`（6 个测试），核心断言是
"`_pick_session`/`run_connected_repl` 调用的是 `_enter_input_mode`/
`_exit_input_mode`，不是 `set_statusbar_provider(None)`"，并验证了调用顺序
（暂停必须先于阻塞输入完成、恢复必须在输入返回之后，包括 EOF/KeyboardInterrupt
异常路径下 `finally` 仍能正确调用 `_exit_input_mode`）。曾手动把代码改回旧的
`set_statusbar_provider(None)` 实现验证过这组测试确实会失败（3/6 测试能正确
抓住回归），确认测试本身有效，不是摆设。完整 `pytest` 套件跑下来
1387 passed（含新增 6 个），失败的 4 个与本次改动完全无关
（`jsonschema` 模块缺失、格式纠错标签判定、日志截断边界，均是预先存在的
、和 daemon/Terminal 无关的失败）。

---

## 十、第九节修复之后暴露的新问题：agent 回复内容被状态栏刷新打断（已修复）

> 用户实测反馈：第九节的修复让 `You ❯` 提示符正常出现了，但紧接着发现
> 新问题——agent 的回复在流式输出过程中被状态栏行反复打断，一段连续的
> 中文句子被切成"看起来随机断开"的碎片：
> ```
> 他的主要身份标签：
>   🌐 [connected] session=84a0b9e5  ● running  queue=0  clients=0
>   🌐 [connected] session=84a0b9e5  ● running  queue=0  clients=0
> 。
>   🌐 [connected] session=84a0b9e5  ● running  queue=0  clients=0
> 音乐风格产生了深远影响。
> ```
> 用户判断"感觉是被状态栏刷新破坏了"——方向完全正确，这是同一个根因
> 在另一个时间窗口里的表现。

### 10.1 根因

第九节的修复把 `_bar_pause()`/`_bar_resume()` 从 `set_statusbar_provider(None)`
换成了 `_enter_input_mode()`/`_exit_input_mode()`，解决了"等待用户输入"
这个时间窗口的竞态。但 `run_connected_repl()` 主循环里 `_bar_resume()` 的
**调用时机**没有一起改对：

```python
line = _sys.stdin.readline()
...
_bar_resume()           # 有输入了，立刻恢复状态栏   ← 问题就在这一行
...
turn_id = client.send_message(user_input, ...)   # 发消息
...
threading.Thread(target=stream_worker, ...).start()  # 流式接收
done_event.wait(timeout=600)
```

代码原来的注释写的设计意图是"等待用户输入时：暂停；agent 回复期间：
恢复，显示 session/state 信息"——这个意图本身就是错的。`_bar_resume()`
清除 `_refresh_paused` 之后，刷新线程立刻恢复每 250ms 的心跳，而这正是
`_connected_print_token()` 用裸 `_sys.stdout.write()` 持续输出流式 token
的时间段——这条写入路径完全绕开了 `Terminal` 的渲染队列，跟刷新线程的
`_draw_bar()`/`_erase_bar()`（同样直接写 stdout，但在另一个线程里）没有
任何互斥协调。两个线程各写各的，必然在某个 token 写到一半的时候插入一行
状态栏文字，把输出切成碎片——这正是用户截图里看到的现象。

这个问题在第九节修复之前其实也存在（`_bar_resume()` 提前调用这件事
本身没变过），只是当时连 `You ❯` 提示符都看不见，没人能注意到流式输出
也有问题；修好提示符之后，这个一直存在的问题才第一次变得可观察。

### 10.2 修复

核心原则改成一句话："只要 connected REPL 自己还要往 stdout 写任何东西
（提示符、session 列表、流式 token、结果提示、observer 旁观输出），
刷新线程就必须保持暂停；只有在确定不会再自己写 stdout 的间隙，才允许
恢复。" 具体改法：

1. **删除"读完输入就立刻 `_bar_resume()`"这一行**，把恢复动作挪到
   每一条独立路径各自的末尾——内置命令分支（`/session new`、
   `/session list`、`/session`、`send_message` 失败）原来就会
   `continue`、不会进入 streaming，所以各自在 `continue` 前调用一次
   `_bar_resume()` 是正确的；唯一的主干路径（成功发消息 → 启动
   `stream_worker` → `done_event.wait()` → 打印收尾换行）则要等到
   **流式输出完全结束之后**才调用 `_bar_resume()`。

2. **空输入分支**（`if not user_input: continue`）单独调一次
   `_bar_resume()`——这条路径同样不会进入 streaming，必须自己负责恢复，
   否则刷新线程会一直卡在暂停状态。

3. `_pick_session()` 内部增加了重入检测（见 `_pick_session` 文档字符串
   "重入保护"一节）：主循环 `/session list` 分支调用 `_pick_session()`
   之前已经 `_bar_pause()` 过了，如果 `_pick_session()` 内部又无条件
   调用一次 `_exit_input_mode()`，会把外层还想维持的暂停状态提前解除——
   `_pick_session()` 返回后主循环还要打印 "✓ Switched to: ..." 这类
   结果提示，这段输出会重新暴露在竞态里。`_pick_session()` 现在进入时
   检查一次 `term._refresh_paused.is_set()`：已经是 `True` 说明外层在
   管，自己全程不碰；否则自己用一次 `enter_input_mode`/`exit_input_mode`
   包住"打印 session 列表 + 所有输入循环"整个函数体（而不是像第九节
   那样只在每次循环内部反复进出——打印列表用的也是裸 `print()`，同样
   需要保护）。

### 10.3 影响范围与验证

只改了 `cli/daemon.py`：`run_connected_repl()` 主循环里 `_bar_resume()`
的调用位置、`_pick_session()` 的暂停范围和重入检测。未改动
`ui/terminal.py`。

`tests/test_daemon_connected_statusbar.py` 从 6 个扩到 12 个，新增的测试
覆盖：
- `_pick_session` 的暂停范围必须覆盖打印列表（不只是 `input()`）；
- `_pick_session` 的重入保护——外层已暂停时不能再调用 `enter`/`exit`，
  包括正常返回和 EOF 异常路径；
- `run_connected_repl` 主干路径（消息发送成功 → 流式接收）之间不能有
  `_bar_resume()`；
- "刚读完输入、还没判断命令"这个区间不能有无条件执行的 `_bar_resume()`
  （这是 bug 最初的错误写法，最容易在未来重构时被无意中重新引入）；
- `_bar_resume()` 必须在 `done_event.wait()` 之后才出现；
- 空输入分支必须自己调用一次 `_bar_resume()`。

这几条静态源码检查都用了排除注释行的辅助函数 `_bar_resume_calls_in_code()`
——本文件的注释里大量引用 `_bar_resume()` 这个词来解释历史 bug，纯字符串
`in` 检测会把注释误判成代码，写测试时确认过这一点（最初几版测试在正确
代码上也会"误报"失败，原因正是匹配到了注释文字）。

验证方法同第九节：手动把 `_bar_resume()` 改回"读完输入就立刻调用"的
旧写法生成一份模拟文件，确认相关测试（`test_run_connected_repl_
no_unconditional_resume_right_after_readline`、`test_run_connected_repl_
empty_input_path_resumes_bar`）能正确捕捉到回归，再恢复正确实现重新
跑通全部 12 个测试。同时重新跑了一遍第九节模拟的旧
`set_statusbar_provider(None)` 实现，确认对应测试依然能捕捉到那个更早的
回归——两次修复互相独立可验证，没有互相掩盖。

完整 `pytest` 套件跑下来 1393 passed（含新增的 12 个 daemon 状态栏测试），
失败的 4 个与本次改动完全无关，跟第九节记录的是同一批
（`jsonschema` 模块缺失、格式纠错标签判定、日志截断边界）。

### 10.4 仍然存在的已知局限（有意为之，非 bug）

修复后，状态栏在 connected 模式下基本不会再刷新——因为 connected REPL
几乎全程都处于"打印提示符 / 等待输入 / 流式输出"这三种状态之一，刷新线程
持续保持暂停。两次循环之间确实留了一个理论上"活的"窗口（`_bar_resume()`
调用之后到下一轮 `_bar_pause()` 调用之前），但中间没有任何阻塞 I/O，
Python 字节码执行速度远快于刷新线程 250ms 的心跳周期，实际上几乎不可能
被刷新线程抓住这个窗口画出东西。也就是说，**这次修复的代价是 connected
模式的状态栏几乎成了摆设——但这是必要的代价**：当前 `Terminal` 的刷新
线程和 connected REPL 自己的裸 stdout 写入之间没有任何互斥机制，只要
两者有重叠的活跃窗口就会冲突，没有办法在"让状态栏可见"和"保证输出不被
打断"之间找到更好的折中。如果未来想恢复状态栏在 connected 模式下的
可见性，需要先给 `_connected_print`/`_connected_print_token` 这类裸写
路径接入 `Terminal` 的渲染队列（让它们也走 `_q.put(...)` 而不是直接
`sys.stdout.write`），从根本上统一成单一写入路径，而不是在现有的"两条
路径各写各的"架构上继续打补丁。
