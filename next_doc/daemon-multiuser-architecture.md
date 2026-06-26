# Daemon 多用户 · 自主 Agent · 角色系统架构设计

> 本文档基于当前 mini_agent Stage 9 代码现状，面向「具有自我意识、持续自主运行的 agent」定位，
> 设计 daemon 与多用户、多 session-agent、跨 agent 通信的完整架构。

---

## 一、核心世界观重建

### 当前模型（工具视角）

```
用户 → CLI/Web → daemon → 单 Agent → 响应 → 返回
```

Agent 是无状态的响应机器，用完即可丢弃。

### 目标模型（自主实体视角）

```
                    ┌─────────────────────────────────────────┐
                    │  daemon 进程 = agent 的「躯体」          │
                    │                                         │
                    │  ┌─────────────────────────────────┐   │
                    │  │  Self（主自我）                  │   │
                    │  │  - SelfProfile / GoalBacklog    │   │
                    │  │  - AutonomousLoop（自主驱动）   │   │
                    │  │  - 常驻，无会话绑定             │   │
                    │  └──────────┬──────────────────────┘   │
                    │             │ 派生 / 感知 / 通信         │
                    │   ┌─────────┴────────────────┐         │
                    │   │  SessionAgent 池          │         │
                    │   │  每个对话用户各一个实例   │         │
                    │   │  - Alice-Session-001      │         │
                    │   │  - Bob-Session-002        │         │
                    │   │  - AgentX-Session-003     │         │
                    │   └──────────────────────────┘         │
                    └─────────────────────────────────────────┘

外部角色（通过 HTTP API 接入）：
  owner    - 主人（拥有 daemon 的人）
  family   - 家人朋友
  colleague - 工作相关
  agent    - 其他 AI agent
  public   - 公开访客
```

**关键理念转变**：
- daemon 不是「工具服务器」，而是 agent 的「持续存在载体」
- Self（主自我）永远在运行，不依赖任何用户连接
- 每个对话 session 创建一个独立的 SessionAgent，天然隔离
- Agent 有主动性：可以向用户发起对话，可以拒绝某类请求

---

## 二、角色系统设计

### 2.1 角色定义（基于 token 区分）

```python
# .agent/users/users.json
{
  "users": [
    {
      "user_id": "owner",           # 固定 ID，主用户
      "name": "主人名字",
      "role": "owner",
      "token_hash": "sha256(token)",
      "created_at": 1234567890,
      "profile_dir": ".agent/users/owner/",
      "notes": "daemon 启动者，唯一特权用户"
    },
    {
      "user_id": "u_a1b2c3d4",
      "name": "Alice",
      "role": "family",             # family / colleague / agent / public
      "token_hash": "sha256(token)",
      "created_at": 1234567891,
      "profile_dir": ".agent/users/u_a1b2c3d4/",
      "meta": {
        "relation": "妻子",
        "contact": "微信:alice123",
        "trust_level": 9           # 1-10，影响某些权限判断
      }
    },
    {
      "user_id": "u_e5f6g7h8",
      "name": "GPT-Researcher",
      "role": "agent",             # 其他 AI agent
      "token_hash": "sha256(token)",
      "created_at": 1234567892,
      "meta": {
        "agent_type": "openai_gpt4",
        "capabilities": ["research", "analysis"],
        "trust_level": 5
      }
    }
  ]
}
```

### 2.2 角色权限矩阵

| 能力 | owner | family | colleague | agent | public |
|------|-------|--------|-----------|-------|--------|
| 发起对话 | ✓ | ✓ | ✓（工作时间）| ✓ | ✗（需审批）|
| 查看自己的 session 历史 | ✓ | ✓ | ✓ | ✓ | ✓ |
| 查看 owner 的历史 | ✓ | 配置决定 | ✗ | ✗ | ✗ |
| 访问文件系统 | 读写 | 只读 | 只读 | ✗ | ✗ |
| 工具调用审批 | 终端弹出 | 无（自动）| 需 owner 确认 | 沙箱内自动 | ✗ |
| daemon 管理 | ✓ | ✗ | ✗ | ✗ | ✗ |
| 修改 agent 配置 | ✓ | ✗ | ✗ | ✗ | ✗ |
| 修改自己的画像 | ✓ | ✓ | ✓ | ✓ | ✗ |
| 发起跨 agent 通信 | ✓ | ✗ | ✗ | ✓（peer）| ✗ |
| 查看 Self 的目标/日志 | ✓ | 摘要 | ✗ | ✗ | ✗ |

### 2.3 角色的行为规则（agent 视角）

每个角色对应的**对话风格和边界**，写入 agent 的 system prompt：

```
owner:    像对自己说话，最直接，可讨论隐私目标，可接受批评
family:   温暖、关心，优先情感支持，工作细节不主动披露
colleague: 专业简洁，聚焦工作相关，不讨论私人事务
agent:    结构化 JSON 优先，能力声明明确，协议协商显式
public:   礼貌但保守，不透露任何内部信息
```

---

## 三、每个对话用户的数据目录

### 3.1 目录结构

```
.agent/
  users/
    users.json                  # 用户注册表（含 token_hash）
    token_keys/
      owner.key                 # owner token 明文（0600 权限）
      u_a1b2c3d4.key            # Alice token（0600 权限）
    owner/                      # owner 的数据目录
      profile.json              # 用户画像
      sessions/                 # 该用户发起的 session 列表（软链接或 ID 列表）
      memory.jsonl              # 与该用户交互的记忆
      preferences.json          # 用户偏好（语言、时区、话题偏好）
    u_a1b2c3d4/                 # Alice 的数据目录
      profile.json
      sessions/
      memory.jsonl
      preferences.json
  sessions/                     # session 实际存储（按 session_id）
    <session_id>/
      meta.json                 # 含 user_id、role、created_at
      history.json
      agent_state.json          # 该 SessionAgent 的私有状态
```

### 3.2 用户画像结构（profile.json）

由 agent 在交互中自动更新，owner 也可手动编辑：

```json
{
  "user_id": "u_a1b2c3d4",
  "name": "Alice",
  "role": "family",
  "first_contact": 1234567890,
  "last_contact": 1234599999,
  "contact_count": 47,

  "persona": {
    "relation": "妻子",
    "personalities": ["温柔", "直接", "重视家庭"],
    "interests": ["烹饪", "旅行", "心理学"],
    "communication_style": "感性优先，不喜欢术语",
    "sensitive_topics": ["工作压力", "前任"],
    "preferred_language": "zh-CN"
  },

  "interaction_patterns": {
    "typical_request_types": ["情感支持", "行程安排", "信息查询"],
    "avg_session_length_turns": 8,
    "active_hours": [19, 20, 21, 22],   # 通常在晚上联系
    "response_length_preference": "medium"
  },

  "trust_state": {
    "trust_level": 9,
    "can_access_owner_calendar": true,
    "can_trigger_home_devices": true,
    "notable_incidents": []
  },

  "agent_notes": [
    {"ts": 1234567890, "note": "喜欢被称呼为亲爱的，不是 Alice"},
    {"ts": 1234599000, "note": "最近在准备考试，话题敏感，避免施压"}
  ],

  "last_updated": 1234599999,
  "update_reason": "第 47 次对话后自动更新"
}
```

**画像更新时机**：
- 每次 session 结束时，SessionAgent 写入增量
- Self 定期（Phase G tick）汇总各 session 的增量，更新 profile.json
- owner 可手动编辑 `agent notes`

---

## 四、每个 Session 一个独立 Agent 实例

### 4.1 设计决策分析

**方案 A（当前）**：单 Agent，所有用户排队
```
问题：
  - Alice 和 Bob 同时请求，Bob 等 Alice 完成才能回应
  - 历史污染：Alice 的对话影响 Bob 收到的 context
  - 崩溃影响全部用户
```

**方案 B（推荐）**：每个 Session 一个独立 Agent 实例
```
优势：
  - 天然隔离：context、history、memory 各自独立
  - 并发：多用户同时对话，互不阻塞
  - 稳定性：SessionAgent 崩溃不影响 Self 和其他 Session
  - 符合定位：每个 Session 是 agent「分裂」出的一个「意识线程」
```

**方案 B 的成本**：
- 每个 SessionAgent 持有独立 history、memory、toolregistry
- 多个 Agent 实例同时调用 LLM → 需要 ResourceArbiter 控制并发和预算
- 已有的 ResourceArbiter 正好处理这个场景

### 4.2 SessionAgent 生命周期

```
用户连接
  │
  ├─ 查找该用户已有的 active session？
  │     是 → 复用（attach）
  │     否 → 创建新 SessionAgent
  │
  │  创建 SessionAgent：
  │    - 继承 Self 的 cfg（但 history 为空或从 session 文件恢复）
  │    - 加载该用户的 profile.json 注入 system prompt
  │    - 独立的 memory（用户个人记忆 + 工作目录记忆）
  │    - 独立的 ToolRegistry（根据用户 role 过滤工具）
  │    - 受 ResourceArbiter 管控（预算、并发）
  │
用户断开连接
  │
  ├─ SessionAgent 进入 suspended 状态
  ├─ 写入 session 文件（history、agent_state）
  └─ 更新用户 profile（交互增量）

超过 idle_timeout（如 30 分钟）
  └─ SessionAgent 被回收（GC），从内存移除
     但 session 文件保留，用户重连时可恢复
```

### 4.3 SessionAgent 工厂

```python
# api/session_manager.py（新增）

class SessionAgentPool:
    """
    管理所有活跃 SessionAgent 实例。
    每个 (user_id, session_id) 对应一个独立 Agent。
    """

    def __init__(self, self_cfg, self_paths, resource_arbiter):
        self._pool: dict[str, SessionEntry] = {}   # session_id → SessionEntry
        self._self_cfg = self_cfg
        self._self_paths = self_paths
        self._arbiter = resource_arbiter
        self._lock = threading.Lock()

    def get_or_create(self, user_id: str, session_id: str, user_profile: dict) -> SessionEntry:
        with self._lock:
            if session_id in self._pool:
                entry = self._pool[session_id]
                entry.last_active = time.time()
                return entry

            # 创建新 SessionAgent
            entry = self._create_session_agent(user_id, session_id, user_profile)
            self._pool[session_id] = entry
            return entry

    def _create_session_agent(self, user_id, session_id, profile) -> "SessionEntry":
        from mini_agent.agent import Agent
        from mini_agent.config import load_config

        # 派生 cfg，注入用户角色 system prompt
        session_cfg = copy.deepcopy(self._self_cfg)
        session_cfg.extra_system = _build_user_system_prompt(profile)

        # 根据 role 过滤工具
        role = profile.get("role", "public")
        allowed_tools = ROLE_TOOL_PERMISSIONS.get(role, [])

        # 受资源仲裁器管控
        session_guard = self._arbiter.create_session_guard(user_id, session_id)

        agent = Agent(cfg=session_cfg, guard=session_guard)

        # 恢复历史（若有）
        session_file = self._self_paths.session_history(session_id)
        if session_file.exists():
            agent.load_session(session_id)

        return SessionEntry(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            role=role,
            created_at=time.time(),
            last_active=time.time(),
            bridge=AgentBridge(),    # 每个 Session 独立的 bridge（独立 SSE 流）
        )
```

---

## 五、Self（主自我）与 SessionAgent 的通信

### 5.1 三类通信模式

```
┌──────────────────────────────────────────────────────┐
│                    通信总线（内存）                    │
│                  SelfMessageBus                      │
└──────┬───────────────────┬──────────────────┬────────┘
       │                   │                  │
   Self（主自我）    SessionAgent A    SessionAgent B
   GoalBacklog       Alice-Session    Bob-Session
   AutonomousLoop
```

**模式 1：Self → SessionAgent（下达）**
- Self 产生一个与用户相关的 insight → 注入到对应 SessionAgent 的 system context
- Self 发现用户画像需要更新 → 通知 SessionAgent 在下一轮结尾时确认

**模式 2：SessionAgent → Self（上报）**
- SessionAgent 完成一轮对话 → 上报摘要（turns、用户意图、新 insight）
- SessionAgent 检测到需要超出权限的操作 → 请求 Self 审批

**模式 3：SessionAgent ↔ SessionAgent（横向）**
- Owner 的 SessionAgent 想调用 Alice 的 SessionAgent 传递消息
- 两个 agent-role SessionAgent 协作完成任务

### 5.2 消息总线实现（轻量版）

```python
# evolution/self_bus.py（新增）

class SelfMessage:
    """Self 与 SessionAgent 之间传递的消息。"""
    __slots__ = ("msg_id", "from_id", "to_id", "type", "payload", "ts", "reply_to")

    # type 枚举：
    # "context_inject"  - Self 向 Session 注入上下文片段
    # "profile_update"  - Session 向 Self 上报画像增量
    # "session_summary" - Session 向 Self 上报轮次摘要
    # "approval_req"    - Session 向 Self 请求权限审批
    # "approval_resp"   - Self 向 Session 回应审批结果
    # "peer_message"    - SessionAgent 之间的横向消息


class SelfMessageBus:
    """
    内存消息总线，无持久化。
    Self 和所有 SessionAgent 共享同一个 bus 实例（注入到各 Agent）。
    """

    def __init__(self):
        self._queues: dict[str, queue.Queue] = {}   # entity_id → Queue
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[Callable]] = {}

    def register(self, entity_id: str) -> None:
        """注册实体（Self 或 SessionAgent），创建接收队列。"""
        with self._lock:
            if entity_id not in self._queues:
                self._queues[entity_id] = queue.Queue(maxsize=100)

    def send(self, msg: SelfMessage) -> None:
        """发送消息，放入目标队列（非阻塞，满则丢弃并记录）。"""
        with self._lock:
            q = self._queues.get(msg.to_id)
        if q:
            try:
                q.put_nowait(msg)
            except queue.Full:
                # 队列满，记录丢弃（监控指标）
                pass

    def receive(self, entity_id: str, timeout: float = 0) -> Optional[SelfMessage]:
        """从自己的队列取消息。"""
        q = self._queues.get(entity_id)
        if not q:
            return None
        try:
            return q.get(timeout=timeout) if timeout > 0 else q.get_nowait()
        except (queue.Empty, queue.Full):
            return None

    def broadcast_to_sessions(self, msg: SelfMessage) -> None:
        """Self 向所有 SessionAgent 广播。"""
        with self._lock:
            targets = [k for k in self._queues if k.startswith("session:")]
        for t in targets:
            self.send(SelfMessage(**{**vars(msg), "to_id": t}))
```

### 5.3 Self 的感知能力

Self 通过 SelfMessageBus 持续感知所有 SessionAgent 的状态：

```python
# AutonomousLoop.tick() 新增逻辑
def _collect_session_summaries(self) -> list[dict]:
    """
    从 SelfMessageBus 收集所有 Session 的上报摘要。
    用于：
      - 更新用户 profile
      - 调整 GoalBacklog 优先级（与某用户的对话揭示了新信息）
      - 检测异常（某 Session 长时间无响应、某用户反复遇到同类问题）
    """
    summaries = []
    while True:
        msg = self._bus.receive("self", timeout=0)
        if msg is None:
            break
        if msg.type == "session_summary":
            summaries.append(msg.payload)
        elif msg.type == "profile_update":
            self._update_user_profile(msg.payload)
        elif msg.type == "approval_req":
            self._handle_approval_request(msg)
    return summaries
```

---

## 六、稳定性保障

### 6.1 Self 与 SessionAgent 的进程隔离策略

**轻量方案（当前阶段，推荐）**：同进程，线程隔离

```
daemon 进程
  ├── Self 线程（AutonomousLoop + Phase G tick）
  ├── SessionAgent-Alice 线程（AgentRunner）
  ├── SessionAgent-Bob 线程（AgentRunner）
  ├── HTTP uvicorn 线程
  └── SelfMessageBus（共享内存，线程安全）
```

- **崩溃隔离**：每个 AgentRunner 有独立 try/except，SessionAgent 崩溃不影响其他线程
- **Self 保护**：Self 的 AutonomousLoop 运行在独立线程，SessionAgent 崩溃不影响 Self
- **重启策略**：SessionAgent 崩溃后写入错误日志，SessionAgentPool 可以重建实例

**重量方案（未来）**：子进程隔离

```python
# 每个 SessionAgent 跑在独立子进程（使用 multiprocessing）
# 通信通过 multiprocessing.Queue 或 Unix Socket
# 好处：真正的内存隔离，崩溃不影响主进程
# 代价：Agent 初始化更慢，通信开销更大
```

### 6.2 崩溃检测与自动恢复

```python
class SessionAgentPool:

    def _monitor_loop(self):
        """后台线程，定期检查 SessionAgent 健康状态。"""
        while True:
            time.sleep(10)
            with self._lock:
                dead = [sid for sid, entry in self._pool.items()
                        if not entry.runner_thread.is_alive()]

            for sid in dead:
                entry = self._pool[sid]
                logger.error(f"[SessionAgent] {sid} (user={entry.user_id}) thread died, recovering...")
                # 1. 保存当前 history（可能已部分写入）
                self._emergency_save(entry)
                # 2. 从 pool 移除
                del self._pool[sid]
                # 3. 通知 Self
                self._bus.send(SelfMessage(
                    from_id="pool",
                    to_id="self",
                    type="session_crashed",
                    payload={"session_id": sid, "user_id": entry.user_id}
                ))
                # 4. 通知用户（下次连接时告知「上次对话异常中断」）
```

### 6.3 ResourceArbiter 管控多 Agent

现有 `evolution/resource_arbiter.py` 扩展：

```python
class ResourceArbiter:

    def create_session_budget(self, user_id: str, role: str) -> SessionBudget:
        """
        为每个 SessionAgent 分配资源预算。
        owner 预算最高，public 预算最低（或为 0）。
        """
        ROLE_BUDGETS = {
            "owner":     SessionBudget(max_tokens=100000, max_turns=200, max_tools=50),
            "family":    SessionBudget(max_tokens=50000,  max_turns=100, max_tools=20),
            "colleague": SessionBudget(max_tokens=30000,  max_turns=50,  max_tools=10),
            "agent":     SessionBudget(max_tokens=20000,  max_turns=30,  max_tools=5),
            "public":    SessionBudget(max_tokens=5000,   max_turns=10,  max_tools=0),
        }
        return ROLE_BUDGETS.get(role, ROLE_BUDGETS["public"])

    @property
    def concurrent_sessions(self) -> int:
        return len([e for e in self._pool.values() if e.is_active])

    def can_create_session(self) -> bool:
        """检查是否可以创建新 SessionAgent（全局并发上限）。"""
        return self.concurrent_sessions < self._max_concurrent_sessions
```

---

## 七、路由层扩展

### 7.1 请求路由到正确的 SessionAgent

```python
# api/routes.py 修改

@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    # 1. 从 token 解析 user_id 和 role
    user_id = request.state.user_id   # 由 AuthMiddleware 注入
    role    = request.state.role

    # 2. 确定 session_id
    session_id = body.session_id or _get_active_session(user_id) or _new_session_id()

    # 3. 从 SessionAgentPool 获取或创建 SessionAgent
    pool: SessionAgentPool = request.app.state.session_pool
    entry = pool.get_or_create(user_id, session_id, load_user_profile(user_id))

    # 4. 提交到该 SessionAgent 的独立 InputQueue
    turn_id = entry.bridge.input_queue.enqueue(
        body.message,
        initiator="user",
        meta={"user_id": user_id, "role": role}
    )

    return ChatResponse(turn_id=turn_id, session_id=session_id, queued=True)

@router.get("/stream/{turn_id}")
async def stream_turn(request: Request, turn_id: str):
    user_id = request.state.user_id

    # 只能订阅自己的 turn
    pool: SessionAgentPool = request.app.state.session_pool
    entry = pool.find_by_turn(turn_id)
    if not entry or (entry.user_id != user_id and request.state.role != "owner"):
        raise HTTPException(403, "Not authorized to view this turn")

    return StreamingResponse(
        _sse_generator(entry.bridge, turn_id_filter=turn_id),
        media_type="text/event-stream",
    )
```

### 7.2 新增用户管理端点

```
GET    /v1/users                      # owner only：列出所有用户
POST   /v1/users                      # owner only：添加用户，返回 token
DELETE /v1/users/{user_id}            # owner only：删除用户
PATCH  /v1/users/{user_id}            # owner only：修改角色/meta
GET    /v1/users/{user_id}/profile    # owner 或本人：查看画像
PATCH  /v1/users/{user_id}/profile    # owner 或本人：更新画像

GET    /v1/sessions                   # 返回当前用户的 session 列表
POST   /v1/sessions/new               # 创建新 session（自动路由到新 SessionAgent）
GET    /v1/sessions/{sid}/history     # 只能访问自己的 session

GET    /v1/self/status                # owner only：Self 的状态快照
GET    /v1/self/goals                 # owner only：GoalBacklog
GET    /v1/self/activity              # owner only：activity_log
```

---

## 八、认证扩展（AuthMiddleware）

```python
# api/auth.py 扩展

class UserContext:
    user_id: str
    name: str
    role: str          # owner / family / colleague / agent / public
    trust_level: int   # 1-10


class MultiUserAuthMiddleware(BaseHTTPMiddleware):
    """
    替换当前单 token AuthMiddleware。
    流程：
      1. 提取 Bearer token
      2. 在 users.json 里查找匹配的 token_hash
      3. 找到 → 注入 user_id/role/trust_level 到 request.state
      4. 找不到 → 401
    """

    def __init__(self, app, user_store: UserStore, allowed_ips: list[str]):
        super().__init__(app)
        self._store = user_store
        self._allowed_ips = allowed_ips

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # IP 检查
        ip = _client_ip(request)
        is_loopback = ip in ("127.0.0.1", "::1", "localhost")

        # Token 提取
        token = self._extract_token(request)
        if not token:
            return _unauthorized()

        # 用户查找（哈希比对，避免时序攻击）
        user = self._store.find_by_token(token)
        if not user:
            return _unauthorized()

        # 本机连接 + owner token = owner 特权
        if not is_loopback and user.role == "owner":
            # 非本机 owner 连接：允许但降级为 member 或拒绝（可配置）
            pass

        # 注入上下文
        request.state.user_id     = user.user_id
        request.state.user_name   = user.name
        request.state.role        = user.role
        request.state.trust_level = user.trust_level
        request.state.is_loopback = is_loopback

        return await call_next(request)
```

---

## 九、CLI 用户管理命令

```bash
# 用户管理（对应 /v1/users 端点）
mini-agent user list
mini-agent user add <name> --role family|colleague|agent|public [--trust 8]
mini-agent user remove <user_id>
mini-agent user token <user_id>          # 重新生成并显示 token
mini-agent user profile <user_id>        # 查看/编辑画像
mini-agent user note <user_id> "备注内容" # 快速添加 agent note

# Session 管理
mini-agent session list [--user <user_id>]
mini-agent session attach <session_id>   # CLI attach 到某个 session 的 SSE 流（只读）

# Self 状态
mini-agent self status
mini-agent self goals
mini-agent self activity [--days 7]
```

---

## 十、实施路线图

### Phase 1：用户识别（约 300 行，1-2 天）

```
新增：
  .agent/users/users.json + UserStore 类
  MultiUserAuthMiddleware（替换 AuthMiddleware）
  /v1/users CRUD 端点
  request.state.user_id/role 注入
  mini-agent user 子命令

改动：
  AgentEvent 新增 user_id 字段
  ChatRequest 新增 session_id（可选）
  /v1/stream/{turn_id} 权限检查（只能看自己的 turn）
```

### Phase 2：per-user 目录与画像（约 200 行，1 天）

```
新增：
  .agent/users/<user_id>/profile.json
  UserProfileManager（读写、增量更新）
  session meta.json 记录 user_id
  session 结束时自动更新画像（交互摘要）
```

### Phase 3：SessionAgent 池（约 400 行，2-3 天）

```
新增：
  api/session_manager.py（SessionAgentPool + SessionEntry）
  每个 Session 独立 AgentBridge
  ResourceArbiter 的 session_budget 扩展
  路由层修改（按 user_id+session_id 路由到正确 Agent）

改动：
  AgentRunner → 变为 per-Session（多实例）
  HttpServer._bridge → 变为 SessionAgentPool
```

### Phase 4：Self-Session 通信（约 300 行，1-2 天）

```
新增：
  evolution/self_bus.py（SelfMessageBus）
  AutonomousLoop._collect_session_summaries()
  SessionAgent 在 turn 结束时上报摘要
  Self 的感知和响应逻辑
```

---

## 十一、当前代码需要的最小改动摘要

当前 `api/server.py` 里 `HttpServer` 持有单个 `AgentBridge`，所有用户共用，
对应 Phase 3 需要把它改成 `SessionAgentPool`，但这是破坏性改动。

**建议过渡方案**：Phase 1-2 不动 AgentBridge，只在请求层面做用户识别和数据隔离；
Phase 3 做 SessionAgentPool 时统一重构 `HttpServer`，属于一次性大改，而不是打补丁。

这样每个 Phase 可以独立发布和验证，不影响现有功能。
