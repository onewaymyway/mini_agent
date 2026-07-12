"""
api/routes.py — FastAPI 路由定义

端点总览：
  系统
    GET  /v1/health                  心跳
    GET  /v1/whoami                  当前 token 对应的身份（user_id/role/是否 owner）
    GET  /v1/status                  agent 状态 + stats
  对话
    POST /v1/chat                    发送消息，返回 turn_id
    POST /v1/interrupt               中断当前执行
    GET  /v1/history                 对话历史
    DELETE /v1/history               清空对话历史
  流式输出
    GET  /v1/stream                  SSE：订阅所有实时事件（支持历史回放）
    GET  /v1/stream/{turn_id}        SSE：只订阅某一轮的事件
  事件历史
    GET  /v1/events                  获取历史事件列表（JSON）
  Turns
    GET  /v1/turns                   所有 turn 列表
    GET  /v1/turns/{turn_id}         某 turn 详情
  Session
    GET    /v1/sessions              所有 session 列表（含当前 session 标记）
    GET    /v1/sessions/{id}         某 session 详情（含完整历史）
    POST   /v1/sessions/{id}/resume  切换到指定 session（成为当前活动 session）
    POST   /v1/sessions/new          开始一个全新 session
    DELETE /v1/sessions/{id}         删除一个 session（不可删除当前 session）
  权限审批
    GET  /v1/permissions/pending     待审批列表
    POST /v1/permissions/{req_id}    批准 / 拒绝
    GET  /v1/interactions/pending    待回答的通用交互请求列表（ask_user//goal 协商/任意 slash 命令）
    POST /v1/interactions/{req_id}   回答一次通用交互请求
  用户管理（daemon 多用户架构 Phase 1，owner only；单用户模式下这组端点返回 404）
    GET    /v1/users                 用户列表
    POST   /v1/users                 新增用户，返回 user_id + token（仅此一次明文）
    PATCH  /v1/users/{user_id}       修改角色/meta
    DELETE /v1/users/{user_id}       删除用户
    POST   /v1/users/{user_id}/token 重新生成 token
  Self 状态（daemon 多用户架构 Phase 4，owner only）
    GET    /v1/self/status           GoalBacklog + 最近自主活动 + SessionAgentPool 概况
  文件系统
    GET    /v1/fs/list               列目录（?path=xxx）
    GET    /v1/fs/read               读文件（?path=xxx）
    GET    /v1/fs/stat               文件详情（?path=xxx）
    GET    /v1/fs/download           下载文件（?path=xxx）
    GET    /v1/fs/search             搜索（?q=xxx&content=0）
    POST   /v1/fs/write              写文件
    POST   /v1/fs/mkdir              创建目录
    DELETE /v1/fs/delete             删除
    POST   /v1/fs/rename             重命名/移动
    POST   /v1/fs/upload             上传文件
  产出物（Artifacts，供「产出物看板」使用）
    GET    /v1/artifacts             列出产出物摘要（?session_id=xxx 可过滤，?limit=&offset=）
    GET    /v1/artifacts/{id}        某次产出的完整 manifest（含文件明细）
    GET    /v1/artifacts/{id}/file   下载/预览 manifest 内某个文件（?index=0）
  自主执行状态（daemon 自主运行能力）
    GET    /v1/autonomous/status     当前 autonomy_level + cron job 状态 + Objective 执行进度
    GET    /v1/goals                 GoalBacklog 完整视图（active goals + objectives）
    POST   /v1/goals                 新增 Goal
    PATCH  /v1/goals/{goal_id}       更新 Goal 状态/进度/优先级
    GET    /v1/cron/jobs             CronScheduler job 列表
    POST   /v1/cron/jobs             添加 cron job
    PUT    /v1/cron/jobs/{id}        修改 job（enable/disable/schedule）
    POST   /v1/cron/jobs/{id}/run    立即运行一次
  用户行为感知（默认关闭，见 perception/behavior/）
    GET    /v1/perception/status    总开关/采集器状态
    POST   /v1/perception/toggle    打开/关闭总开关或某个采集器（owner only）
    POST   /v1/perception/report    外部系统（浏览器插件等）上报事件
    GET    /v1/perception/events    查询已采集事件
    DELETE /v1/perception/events    清空已采集事件（owner only）
    POST   /v1/perception/browser/start  启动专用调试浏览器（CDP 方案，owner only）
    POST   /v1/perception/browser/stop   停止采集，可同时关闭浏览器进程（owner only）
    GET    /v1/perception/browser/status 专用浏览器/CDP 连接状态
    POST   /v1/perception/git/install-hooks 在指定仓库安装 git commit/checkout 上报 hook（owner only）
    GET    /v1/perception/summary        查看/生成某天的工作/生活画像摘要（分析层）
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, AsyncIterator

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Query, status
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from .bridge import AgentBridge
from .fs_helper import FsHelper
from .models import (
    ChatRequest, ChatResponse, InterruptResponse, StatusResponse,
    PermissionRequest, PermissionResponse, HistoryResponse,
    EventsResponse, TurnsResponse, TurnInfo,
    FsListResponse, FsReadResponse, FsStatResponse,
    FsWriteRequest, FsMkdirRequest, FsDeleteRequest, FsRenameRequest,
    FsSearchRequest, EventType, AgentEvent,
    SessionInfo, SessionsListResponse, SessionDetailResponse,
    SessionActionResponse, SessionDeleteResponse,
    UserInfo, UsersListResponse, UserCreateRequest, UserCreateResponse,
    UserUpdateRequest, UserActionResponse, WhoamiResponse,
    InteractionRequestBody, InteractionResponse,
)
from .user_store import UserStore, VALID_ROLES

router = APIRouter(prefix="/v1")


def _session_pool(request: Request):
    """daemon 多用户架构 Phase 3：未开启多用户模式时返回 None。"""
    return getattr(request.app.state, "session_pool", None)


def _resolve_session_id(request: Request, explicit: Optional[str] = None) -> str:
    """
    决定这次请求应该落到哪个 session_id。

    仅在多用户模式下才需要真正"决定"——单用户模式没有 session_pool，
    调用方应该直接走 app.state.bridge，根本不会调用这个函数（见 _bridge()）。

    解析优先级：
      1. explicit（POST /chat 的 body.session_id，或 URL 路径里的 {session_id}）
      2. 查询参数 ?session_id=xxx（GET 类端点的等价写法）
      3. 该用户名下最近一次活跃的 session（如果有）
      4. 全新生成一个 session_id（该用户的第一次请求）
    """
    if explicit:
        return explicit
    qp = request.query_params.get("session_id")
    if qp:
        return qp

    user_ctx = getattr(request.state, "user_ctx", None)
    pool = _session_pool(request)
    if user_ctx is not None and pool is not None:
        entries = pool.list_entries(user_id=user_ctx.user_id)
        if entries:
            most_recent = max(entries, key=lambda e: e.last_active)
            return most_recent.session_id

    import uuid as _uuid
    return _uuid.uuid4().hex[:12]


def _default_owner_ctx() -> "UserContext":
    """单 token（非多用户认证）模式下使用的固定身份。

    session pool 现在无论是否开启多用户认证都会构造（见 api/server.py），
    用来支撑"不同客户端连接到不同 session 时互不干扰"。单 token 模式没有
    真实的用户体系，所有客户端共用这个固定的 owner 身份——session 级别的
    隔离靠 session_id 区分，不靠 user_id。owner 的 session 目录与改造前
    完全一致（全局 <project_root>/.agent/sessions/），见
    SessionAgentPool._build_session_cfg() / _user_session_manager()。
    """
    from .user_store import UserContext
    return UserContext(
        user_id="owner", name="owner", role="owner",
        trust_level=10, is_loopback=True,
    )


def _bridge(request: Request, session_id: Optional[str] = None) -> AgentBridge:
    """
    取这次请求要操作的 AgentBridge。

    多用户模式（已认证，request.state.user_ctx 存在）：按 _resolve_session_id()
    决定 session_id，向 SessionAgentPool 要 / 建对应的 SessionEntry。

    单 token 模式（没有 user_ctx）：session pool 仍然存在，但只有当这次
    请求明确带了 session_id（显式参数、请求体或 ?session_id= 查询参数）
    时才走按 session 隔离的路径——这样"某个客户端 resume/new 了一个具体
    session 之后，后续请求都带着这个 session_id"就会被路由到它自己独立
    的 Agent，不会影响其它客户端。完全没有带 session_id 的请求（状态查询、
    模型列表等只读探测，或者从未调用过 /sessions/new|resume 的极简客户端）
    继续退回原来的全局共享 bridge，行为和资源开销与改造前完全一致。
    """
    pool = _session_pool(request)
    if pool is None:
        return request.app.state.bridge

    user_ctx = getattr(request.state, "user_ctx", None)
    if user_ctx is None:
        explicit_sid = session_id or request.query_params.get("session_id")
        if not explicit_sid:
            return request.app.state.bridge
        user_ctx = _default_owner_ctx()
        session_id = explicit_sid

    sid = _resolve_session_id(request, explicit=session_id)
    try:
        entry = pool.get_or_create(user_ctx, sid)
    except RuntimeError as e:
        # get_or_create 在并发上限 / Agent 构造失败时抛 RuntimeError，
        # 转换成 HTTP 层面合理的错误（503：服务暂时不可用，不是客户端的错）。
        raise HTTPException(status_code=503, detail=str(e))

    # 记录到 request.state，方便同一次请求内其它代码复用（例如 chat() 拿到的
    # turn_id 之后要回写"这次请求实际用的是哪个 session_id"到响应里）。
    request.state.resolved_session_id = entry.session_id
    return entry.bridge


def _fs(request: Request) -> FsHelper:
    return request.app.state.fs_helper


def _role_store(request: Request) -> Optional[UserStore]:
    """daemon 多用户架构 Phase 1：未开启多用户模式时返回 None。"""
    return getattr(request.app.state, "role_store", None)


def _require_owner(request: Request) -> None:
    """
    owner-only 端点的权限检查。

    单用户模式（role_store 为 None，即 multi_user_enabled=False）下直接放行——
    单 token 模式下能通过 AuthMiddleware 认证的就是唯一使用者，等同于 owner，
    不应该因为新增了 /v1/users 这组端点就把现有单用户部署挡在外面。
    """
    user_ctx = getattr(request.state, "user_ctx", None)
    if user_ctx is None:
        return  # 单用户模式，放行
    if not user_ctx.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner only")


# ── 系统 ──────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True, "ts": time.time()}


@router.get("/status", response_model=StatusResponse)
async def get_status(
    request: Request,
    session_id: Optional[str] = Query(
        default=None,
        description="查询哪个 session 的状态；不传则退回全局共享 bridge（旧行为）。"
        "单 token 多客户端场景下，各客户端应带上自己当前的 session_id，"
        "否则状态栏会显示成全局 bridge 当前所在的 session，与自己实际"
        "操作的 session 对不上。",
    ),
):
    bridge = _bridge(request, session_id=session_id)
    state  = bridge.get_state()
    stats  = {}
    if bridge.agent:
        try:
            ss = bridge.agent.stats          # SessionStats dataclass
            stats = {
                "total_turns":   getattr(ss, "turns",         0),
                "total_tokens":  getattr(ss, "input_tokens",  0) + getattr(ss, "output_tokens", 0),
                "input_tokens":  getattr(ss, "input_tokens",  0),
                "output_tokens": getattr(ss, "output_tokens", 0),
                "tool_calls":    getattr(ss, "tool_calls",    0),
                "elapsed":       getattr(ss, "elapsed",       ""),
                "summary":       ss.summary() if callable(getattr(ss, "summary", None)) else "",
            }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes')
            pass
    # Stage 9 §3: 读取 AutonomousLoop 状态（通过 request.app.state 获取 HttpServer 引用）
    autonomy_level = "passive"
    last_tick_at = None
    tick_count = 0
    subscribers = 0
    try:
        http_server = getattr(request.app.state, "http_server", None)
        if http_server:
            al = getattr(http_server, "autonomous_loop", None)
            if al:
                loop_status = al.get_digest_status()
                autonomy_level = loop_status.get("autonomy_level", "passive")
                last_tick_at = loop_status.get("last_tick_at") or None
                tick_count = loop_status.get("tick_count", 0)
            # subscriber 数量（SSE 连接数）
            sub_count = getattr(bridge, "_subscriber_count", None)
            if sub_count is None:
                sse_clients = getattr(bridge, "_sse_clients", None)
                subscribers = len(sse_clients) if sse_clients else 0
            else:
                subscribers = sub_count
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass

    return StatusResponse(
        state       = state["state"],
        turn_id     = state["turn_id"],
        stats       = stats,
        queue_depth = state["queue_depth"],
        subscribers = subscribers,
        autonomy_level = autonomy_level,
        last_autonomous_tick_at = last_tick_at if last_tick_at else None,
        tick_count = tick_count,
        # daemon 多用户架构 Phase 3：getattr 兜底——_bridge() 只有真正解析过
        # session 才会设置 request.state.resolved_session_id（单用户模式下
        # 完全不会设置这个属性，回退到 bridge.agent.session_id，对应改造前
        # 这里原本缺失但本该有的行为）。
        session_id = getattr(
            request.state, "resolved_session_id",
            getattr(bridge.agent, "session_id", None) if bridge.agent else None,
        ),
    )


# ── 模型列表（daemon 模式 /model 补全修复）───────────────────────────────────
# 背景：本地直跑模式下，cli/repl.py::run_repl() 在启动时调用
# ui/terminal.py::prime_model_completions(agent._client_pool)，从本进程内的
# LLMClientPool 读取 fallback chain，把模型名注入 "/model " 的 Tab 补全列表。
# 但 daemon 连接模式（cli/daemon.py::run_connected_repl()）下，CLI 客户端
# 并不持有本地 Agent/LLMClientPool —— 真正的 Agent 跑在 daemon 进程里 ——
# 所以 prime_model_completions() 从未被调用过，"/model " 后 Tab 永远补全不出
# 任何候选，这就是 "daemon 模式下 /model 命令不会出现可选的模型" 的根因。
# 这里补一个只读端点，把 daemon 端 LLMClientPool.snapshot() 的模型名列表
# 暴露出来，供 DaemonClient.get_models() 拉取后在客户端本地补全。
@router.get("/models")
async def list_models(request: Request):
    bridge = _bridge(request)
    models: list[str] = []
    current: Optional[str] = None
    try:
        pool = getattr(bridge.agent, "_client_pool", None) if bridge.agent else None
        if pool is not None:
            snap = pool.snapshot()
            for entry in snap["entries"]:
                _, _, model = entry["label"].partition("/")
                if model and model not in models:
                    models.append(model)
                if entry.get("active") and model:
                    current = model
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
    return {"models": models, "current": current}


# ── 诊断（Stage 6 / 6.2）──────────────────────────────────────────────────────

@router.get("/whoami", response_model=WhoamiResponse)
async def whoami(request: Request):
    """
    供 CLI 客户端在连接时确认"当前 token 对应哪个用户/角色"，
    避免带错 token 却在 REPL 里毫无提示地连到别的身份上。

    - 多用户模式（MultiUserAuthMiddleware 已注入 request.state.user_ctx）：
      如实返回该 token 对应的 user_id/name/role/trust_level。
    - 单用户模式（旧版单 token，没有 user_ctx）：
      返回一个固定的 owner 身份，行为与改造前完全一致，方便 CLI 端
      不用区分模式就能统一走一套"连接后打印身份"的逻辑。
    """
    user_ctx = getattr(request.state, "user_ctx", None)
    if user_ctx is not None:
        return WhoamiResponse(
            multi_user_enabled=True,
            user_id=user_ctx.user_id,
            name=user_ctx.name,
            role=user_ctx.role,
            trust_level=user_ctx.trust_level,
            is_owner=user_ctx.is_owner,
        )
    return WhoamiResponse(
        multi_user_enabled=False,
        user_id="owner",
        name="owner",
        role="owner",
        trust_level=10,
        is_owner=True,
    )


@router.get("/diagnostics")
async def get_diagnostics(request: Request):
    """
    [Stage 6 / 6.2] 系统健康诊断端点。

    聚合以下数据源（直接读底层文件，不依赖 self_profile.json 中转）：
      performance    — 当前 session 的 traces.jsonl 追踪摘要
      memory         — workdir memory.jsonl 统计
      skills         — 激活 skill 列表 + 使用率统计
      evolution      — pending_evolve_branches / open_threads 高优
      anomaly_flags  — 本 session 相对历史基线的异常标记
    """
    bridge = _bridge(request)
    agent = bridge.agent
    result: dict = {
        "performance":    {},
        "memory":         {},
        "skills":         {},
        "evolution":      {},
        "anomaly_flags":  [],
        "system_events":  [],
    }

    try:
        from mini_agent.storage.paths import AgentPaths
        proj_root = agent.cfg.project_root if agent else None
        paths = AgentPaths(proj_root) if proj_root else None

        # ── performance（traces.jsonl）────────────────────────────────────────
        if agent and getattr(agent, "_tracer", None):
            try:
                result["performance"] = agent._tracer.get_summary()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ── memory（workdir memory.jsonl）────────────────────────────────────
        if paths:
            try:
                import json as _json
                mem_path = paths.workdir_memory
                if mem_path.exists():
                    entries = []
                    with open(mem_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    entries.append(_json.loads(line))
                                except Exception as _mini_agent_exc:
                                    from mini_agent.errors import log_exception
                                    log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                                    pass
                    by_type: dict = {}
                    for e in entries:
                        t = e.get("entry_type", "unknown")
                        by_type[t] = by_type.get(t, 0) + 1
                    result["memory"] = {
                        "total_entries":  len(entries),
                        "by_type":        by_type,
                    }
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ── skills ────────────────────────────────────────────────────────────
        if agent and getattr(agent, "skill_loader", None):
            try:
                sl = agent.skill_loader
                active = list(sl.active)
                tracker_stats = {}
                if hasattr(sl, "tracker") and sl.tracker:
                    tracker_stats = {
                        name: sl.tracker.get_score(name)
                        for name in active
                        if hasattr(sl.tracker, "get_score")
                    }
                result["skills"] = {
                    "active_count": len(active),
                    "active":       active,
                    "usage_scores": tracker_stats,
                }
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ── evolution（Stage 4/5 数据）────────────────────────────────────────
        if paths:
            try:
                import json as _json
                evo: dict = {}
                # pending_evolve_branches from global self_profile
                sp_path = paths.global_self_profile
                if sp_path.exists():
                    sp = _json.loads(sp_path.read_text(encoding="utf-8"))
                    branches = (
                        sp.get("evolution_state", {}).get("pending_evolve_branches", [])
                    )
                    evo["pending_evolve_branches"] = branches
                    evo["pending_branches_count"]  = len(branches)
                # high-priority open_threads
                ot_path = paths.workdir_open_threads
                if ot_path.exists():
                    ot_data = _json.loads(ot_path.read_text(encoding="utf-8"))
                    high = [
                        t for t in ot_data.get("threads", [])
                        if t.get("priority") == "high"
                    ]
                    evo["open_threads_high_count"] = len(high)
                    evo["open_threads_high"]       = high[:5]  # 最多展示 5 条
                result["evolution"] = evo
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ── anomaly_flags（异常检测，依赖 activity_log 数据积累）──────────────
        if paths and agent:
            try:
                from mini_agent.perception.observability import detect_anomalies
                al_path = paths.global_activity_log
                ss = agent.stats
                current = {
                    "session_id":   agent._session.id if agent._session else "",
                    "tool_count":   getattr(ss, "tool_calls", 0),
                    "total_tokens": getattr(ss, "input_tokens", 0) + getattr(ss, "output_tokens", 0),
                    "duration_min": 0.0,  # 实时端点不计算 duration，留给 session_end 时记录
                }
                k_sigma = getattr(agent.cfg.observability, "anomaly_k_sigma", 3.0)
                min_samples = getattr(agent.cfg.observability, "anomaly_min_samples", 10)
                flags = detect_anomalies(
                    al_path, current,
                    k_sigma=k_sigma, min_samples=min_samples,
                )
                result["anomaly_flags"] = [f.to_dict() for f in flags]
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ── system_events（跨子系统事件总线，只读 peek）──────────────────────
        # [system-events-bus-guide.md 第8节] 此前 events.jsonl 只有代码在读，
        # 人看不到"最近发生了哪些跨系统事件"。用固定 consumer_name +
        # advance_cursor=False 只读最近 N 条，不推进游标、不影响任何真实
        # 消费者（daemon_instant_consumer / soft_goal_deriver 等）的进度。
        if paths:
            try:
                from mini_agent.perception import system_events as _se
                events = _se.poll_since(
                    paths,
                    consumer_name="diagnostics_peek",
                    advance_cursor=False,
                )
                recent = events[-20:]  # 最多展示最近 20 条，按时间正序
                result["system_events"] = [
                    {
                        "event_id":   e.event_id,
                        "ts":         e.ts,
                        "source":     e.source,
                        "event_type": e.event_type,
                        "tier":       e.tier,
                        "payload":    e.payload,
                    }
                    for e in recent
                ]
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

    except Exception as e:
        result["_error"] = str(e)

    return JSONResponse(content=result)


# ── 对话 ──────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    bridge = _bridge(request, session_id=body.session_id)
    # daemon 多用户架构 Phase 1：MultiUserAuthMiddleware 开启时会在 request.state
    # 上挂一个 user_ctx；单用户模式下没有这个属性，getattr 安全降级为 None。
    user_ctx = getattr(request.state, "user_ctx", None)
    meta = {"user_id": user_ctx.user_id, "role": user_ctx.role} if user_ctx else None
    turn_id = bridge.input_queue.enqueue(body.message, body.turn_id, meta=meta)
    # [FIX] 打上 turn_id：否则这条 info 事件会被每一次新 turn 的
    # /v1/stream/{turn_id} 回放放行（历史 info 越攒越多），也会被
    # observer 误判成"别的客户端发的"而重复显示、并错误复位
    # _own_printed_any_holder 导致最终回复被重复打印一遍。
    bridge.emit_info(f"[HTTP] Queued message: {body.message[:80]}", turn_id=turn_id)
    # daemon 多用户架构 Phase 3：把这次请求实际落到的 session_id 带回去——
    # 客户端如果发请求时没指定 session_id（_resolve_session_id 会自动决定一个），
    # 这样客户端才能知道"刚刚这条消息其实进了哪个 session"。
    resolved_sid = getattr(request.state, "resolved_session_id", None)
    return ChatResponse(turn_id=turn_id, queued=True, session_id=resolved_sid)


@router.post("/interrupt", response_model=InterruptResponse)
async def interrupt(request: Request):
    _bridge(request).request_interrupt()
    return InterruptResponse(ok=True)


@router.get("/history", response_model=HistoryResponse)
async def get_history(request: Request):
    bridge = _bridge(request)
    msgs   = []
    if bridge.agent:
        try:
            msgs = [m.model_dump() if hasattr(m, "model_dump") else dict(m)
                    for m in bridge.agent.history]
        except Exception:
            msgs = []
    return HistoryResponse(messages=msgs, count=len(msgs))


@router.delete("/history")
async def clear_history(request: Request):
    bridge = _bridge(request)
    if bridge.agent:
        bridge.agent.clear_history()
    bridge.emit_info("[HTTP] History cleared")
    return {"ok": True}


# ── SSE 流 ────────────────────────────────────────────────────────────────────

async def _sse_generator(
    bridge: AgentBridge,
    since_id: int = 0,
    since_ts: Optional[float] = None,
    replay: bool = True,
    turn_id_filter: Optional[str] = None,
    session_id_filter: Optional[str] = None,
) -> AsyncIterator[str]:
    """
    核心 SSE 生成器：
    1. 先回放 since_id / since_ts 之后的历史事件
    2. 实时推送后续新事件
    支持 turn_id 过滤（只输出某一轮的事件）
    支持 session_id 过滤（只输出属于某个 session 的事件）——单用户 daemon
    模式下所有 session 共用同一个全局 bridge/RingBuffer，不加这个过滤，
    多个客户端各自切换到不同 session 时会互相看到对方 session 的历史和
    实时事件，混在一起、时序错乱。传了 session_id_filter 之后：
      - 不带 session_id 标签的事件（系统级，比如 daemon 启动日志）照样放行；
      - 带了 session_id 标签、但和 filter 不一致的事件被过滤掉。
    """
    ring = bridge.ring

    def _match(evt: AgentEvent) -> bool:
        if turn_id_filter and evt.turn_id and evt.turn_id != turn_id_filter:
            return False
        # session_switched 例外：这个事件类型的作用就是"通知所有客户端
        # 当前激活 session 变了"，它本来就打的是"切换目标"的 session_id，
        # 如果也拿来跟 filter 比对，正在看旧 session 的客户端永远收不到
        # 这条通知，也就永远不知道要重新加载——所以它必须无条件放行。
        if (
            session_id_filter
            and evt.session_id
            and evt.session_id != session_id_filter
            and evt.type != EventType.SESSION_SWITCHED
        ):
            return False
        return True

    # ── 阶段 1：历史回放 ──────────────────────────────────────────────────
    if replay:
        if since_ts is not None:
            history = ring.events_since_ts(since_ts)
        else:
            history = ring.events_since(since_id)

        for evt in history:
            if not _match(evt):
                continue
            yield evt.sse_format()

        # 记录回放结束位置
        last_id = history[-1].id if history else ring.latest_id
    else:
        last_id = ring.latest_id

    # 告知客户端回放结束，后续为实时流
    yield (
        f"id: {last_id}\nevent: replay_done\n"
        f"data: {{\"replayed\": {last_id - since_id}, "
        f"\"ts\": {time.time()}}}\n\n"
    )

    # ── 阶段 2：实时推送 ──────────────────────────────────────────────────
    # 过滤条件在 subscribe() 时就传下去（见 bridge.py OutputBroadcaster 的
    # "诊断修复"说明）：这样无关的事件（例如并发 SubAgent 任务产生的高频
    # tool_call/tool_result）根本不会进入这个订阅者的队列，不会把队列挤爆
    # 导致本该收到的 token 事件被挤掉/订阅者被销毁。_match() 仍然保留在
    # 下面作为双重保险（万一未来有旧版客户端命中没有过滤的 subscribe 调用
    # 路径），但正常情况下不会再有事件在这一步被过滤掉。
    sub_id, q = bridge.broadcaster.subscribe(
        turn_id_filter=turn_id_filter, session_id_filter=session_id_filter,
    )
    try:
        while True:
            try:
                evt: AgentEvent = await asyncio.wait_for(q.get(), timeout=20.0)
            except asyncio.TimeoutError:
                # 心跳保活，防止代理/负载均衡断开空闲连接
                yield f": heartbeat {time.time()}\n\n"
                continue

            if not _match(evt):
                continue
            yield evt.sse_format()
    except asyncio.CancelledError:
        pass
    finally:
        bridge.broadcaster.unsubscribe(sub_id)


@router.get("/stream")
async def stream_all(
    request: Request,
    since_id: int = Query(default=0, description="从该 event id 之后开始回放"),
    since_ts: Optional[float] = Query(default=None, description="从该时间戳之后开始回放"),
    replay:   bool = Query(default=True, description="是否先回放历史"),
    session_id: Optional[str] = Query(
        default=None,
        description="只订阅这个 session 的事件；不传则保持旧行为（全局，不过滤）",
    ),
):
    """
    SSE：订阅全局事件流。
    支持 Last-Event-ID 请求头（浏览器 EventSource 断线重连标准协议）。
    支持 session_id 过滤：多个客户端连到同一 daemon 但各自停留在不同
    session 时，各自只应该看到自己当前 session 的历史和实时事件。
    """
    bridge = _bridge(request)

    # 优先使用 Last-Event-ID 请求头（浏览器 EventSource 标准断线重连）
    header_last_id = request.headers.get("Last-Event-ID")
    if header_last_id and header_last_id.isdigit():
        since_id = int(header_last_id)

    return StreamingResponse(
        _sse_generator(
            bridge, since_id=since_id, since_ts=since_ts, replay=replay,
            session_id_filter=session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",     # 禁止 Nginx 缓冲
            "Connection":        "keep-alive",
        },
    )


@router.get("/stream/{turn_id}")
async def stream_turn(
    request: Request,
    turn_id: str,
    replay: bool = Query(default=True),
):
    """
    SSE：只订阅某一轮（turn_id）的事件。

    daemon 多用户架构 Phase 3：不能简单调用 _bridge(request)——那会按
    "该用户最近活跃的 session"解析，但要订阅的是一个**具体的 turn_id**，
    它可能属于这个用户的*另一个*（不是最近活跃的）session，或者（在权限
    检查失败时）压根不属于这个用户。必须先用 pool.find_by_turn() 真正找到
    这个 turn_id 实际所属的 SessionEntry，校验归属后再订阅它的 bridge，
    不能靠"猜最近的那个 session"。
    """
    pool = _session_pool(request)
    if pool is not None:
        entry = pool.find_by_turn(turn_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Turn '{turn_id}' not found")
        user_ctx = getattr(request.state, "user_ctx", None)
        if user_ctx is not None and not user_ctx.is_owner and entry.user_id != user_ctx.user_id:
            # 非 owner 只能订阅自己发起的 turn；owner 可以订阅任意 turn
            # （主人有权查看任何对话，这是设计文档里明确的"owner 特权"之一）。
            raise HTTPException(status_code=403, detail="This turn does not belong to you")
        bridge = entry.bridge
    else:
        bridge = _bridge(request)

    return StreamingResponse(
        _sse_generator(bridge, replay=replay, turn_id_filter=turn_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


# ── 事件历史 ──────────────────────────────────────────────────────────────────

@router.get("/events", response_model=EventsResponse)
async def get_events(
    request: Request,
    since_id: int   = Query(default=0),
    since_ts: Optional[float] = Query(default=None),
    limit:    int   = Query(default=200, le=2000),
    type:     Optional[str] = Query(default=None, description="过滤事件类型"),
    session_id: Optional[str] = Query(
        default=None,
        description="只返回这个 session 的事件；不传则保持旧行为（不过滤，跨 session 全部返回）",
    ),
):
    ring   = _bridge(request).ring
    events = ring.events_since_ts(since_ts) if since_ts else ring.events_since(since_id)

    if type:
        events = [e for e in events if e.type.value == type]
    if session_id:
        # 不带 session_id 标签的事件（系统级）、以及 session_switched 事件
        # （职责就是通知"当前 session 变了"，必须无条件放行，否则正在看
        # 旧 session 的客户端永远不知道要跟着切换）照样放行，逻辑与
        # /v1/stream 的 session 过滤保持一致，见 _sse_generator 的 _match()。
        events = [
            e for e in events
            if not e.session_id or e.session_id == session_id
            or e.type == EventType.SESSION_SWITCHED
        ]

    events = events[-limit:]
    dicts  = [{"id": e.id, "type": e.type.value, "turn_id": e.turn_id,
               "session_id": e.session_id,
               "ts": e.ts, **e.data} for e in events]

    return EventsResponse(
        events  = dicts,
        count   = len(dicts),
        min_id  = dicts[0]["id"] if dicts else 0,
        max_id  = dicts[-1]["id"] if dicts else 0,
    )


# ── Turns ─────────────────────────────────────────────────────────────────────

@router.get("/turns", response_model=TurnsResponse)
async def list_turns(request: Request):
    turns = _bridge(request).input_queue.list_turns()
    return TurnsResponse(turns=list(reversed(turns)))


@router.get("/turns/{turn_id}", response_model=TurnInfo)
async def get_turn(request: Request, turn_id: str):
    # 同 stream_turn() 的修复理由：turn_id 可能不属于"该用户最近活跃的 session"，
    # 必须先用 pool.find_by_turn() 真正定位归属，再做权限校验。
    pool = _session_pool(request)
    if pool is not None:
        entry = pool.find_by_turn(turn_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"turn {turn_id!r} not found")
        user_ctx = getattr(request.state, "user_ctx", None)
        if user_ctx is not None and not user_ctx.is_owner and entry.user_id != user_ctx.user_id:
            raise HTTPException(status_code=403, detail="This turn does not belong to you")
        info = entry.bridge.input_queue.get_turn(turn_id)
    else:
        info = _bridge(request).input_queue.get_turn(turn_id)

    if not info:
        raise HTTPException(status_code=404, detail=f"turn {turn_id!r} not found")
    return info


# ── Session ───────────────────────────────────────────────────────────────────

def _agent_or_404(bridge: AgentBridge):
    if bridge.agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return bridge.agent


def _session_manager_or_404(bridge: AgentBridge):
    agent = _agent_or_404(bridge)
    mgr = agent.session_manager
    if mgr is None:
        raise HTTPException(
            status_code=404,
            detail="Session persistence is disabled (--no-save-session)",
        )
    return agent, mgr


def _require_idle(bridge: AgentBridge) -> None:
    """切换 / 新建 session 前检查 agent 是否空闲，避免与正在执行的 turn 冲突。"""
    state = bridge.get_state()["state"]
    if state not in ("idle", "unknown"):
        raise HTTPException(
            status_code=409,
            detail=f"Agent is busy (state={state!r}); "
                   f"interrupt or wait for the current turn to finish before switching sessions",
        )


def _user_session_manager(request: Request):
    """
    daemon 多用户架构 Phase 3：返回该用户专属的、**不需要真实 Agent** 的
    SessionManager——纯文件系统读写，构造成本几乎为零（不会触发 LLM client
    初始化、skill 扫描等 Agent() 构造的开销）。

    用于 list_sessions / get_session_detail / delete_session 这类"只是看看
    有哪些 session、不需要真的把某个 session 加载成活跃对话"的场景。
    路径计算逻辑必须和 SessionAgentPool._build_session_cfg() 完全一致
    （owner 用全局 .agent/sessions/，其他用户用 .agent/users/<id>/sessions/），
    否则会出现"这里看到的列表"和"SessionAgentPool 实际加载的"不是同一批文件。

    单用户模式（没有 user_ctx）返回 None——调用方应该走原来的
    _session_manager_or_404(bridge) 路径，不应该调用这个函数。
    """
    user_ctx = getattr(request.state, "user_ctx", None)
    if user_ctx is None:
        return None

    pool = _session_pool(request)
    project_root = getattr(request.app.state, "project_root", None)
    if pool is None or project_root is None:
        return None

    from mini_agent.session import SessionManager

    if user_ctx.user_id == "owner":
        return SessionManager(project_root=project_root)
    session_dir = project_root / ".agent" / "users" / user_ctx.user_id / "sessions"
    return SessionManager(session_dir=session_dir)


@router.get("/sessions", response_model=SessionsListResponse)
async def list_sessions(
    request: Request,
    limit: int = Query(default=50, le=200),
    session_id: Optional[str] = Query(
        default=None,
        description="单 token 多客户端场景下，传入本连接自己当前的 session_id，"
        "才能正确标出 is_current；不传则退回全局共享 bridge（旧行为）。",
    ),
):
    """列出所有已保存的 session，并标记当前（该用户最近访问过的）session。"""
    user_mgr = _user_session_manager(request)

    if user_mgr is not None:
        # ── 多用户模式 ───────────────────────────────────────────────────
        # 不通过 _bridge()（那会触发 SessionAgentPool.get_or_create()，
        # 也就是真的构造一个 Agent）——仅仅是"看看列表"不应该有这个代价。
        pool = _session_pool(request)
        user_ctx = request.state.user_ctx
        current_id = None
        if pool is not None:
            entries = pool.list_entries(user_id=user_ctx.user_id)
            if entries:
                current_id = max(entries, key=lambda e: e.last_active).session_id

        metas = user_mgr.list_sessions(limit=limit)
        infos = [
            SessionInfo(
                id=m.id, title=m.title or "(untitled)",
                created_at=m.created_at, updated_at=m.updated_at,
                provider=m.provider, model=m.model,
                turns=m.turns, input_tokens=m.input_tokens,
                output_tokens=m.output_tokens, tool_calls=m.tool_calls,
                summary=m.summary, age=m.age_str,
                is_current=(m.id == current_id),
            )
            for m in metas
        ]
        # 当前活跃 session 如果还没 save_session() 落盘（刚创建、还没说过话），
        # 不会出现在 list_sessions() 结果里——从内存里的 SessionEntry 插一条。
        if current_id and not any(i.id == current_id for i in infos) and pool is not None:
            entry = pool.get(current_id)
            if entry is not None and entry.agent is not None and entry.agent.session_meta:
                meta = entry.agent.session_meta
                infos.insert(0, SessionInfo(
                    id=meta.id, title=meta.title or "New session",
                    created_at=meta.created_at, updated_at=meta.updated_at,
                    provider=meta.provider, model=meta.model,
                    turns=meta.turns, input_tokens=meta.input_tokens,
                    output_tokens=meta.output_tokens, tool_calls=meta.tool_calls,
                    summary=meta.summary, age="刚刚", is_current=True,
                ))
        return SessionsListResponse(sessions=infos, current_session_id=current_id, count=len(infos))

    # ── 单用户模式（单 token） ────────────────────────────────────────────────
    # 传了 session_id 的话，_bridge() 会按它路由到 SessionAgentPool 里这个
    # session 专属的 bridge/Agent，current_id 也就是"这个连接自己的" session，
    # 而不是全局共享 bridge 当前碰巧停留的那个（否则多客户端下 is_current
    # 会全部错误地指向同一个、与自己实际操作的 session 无关的 id）。
    agent, mgr = _session_manager_or_404(_bridge(request, session_id=session_id))
    current_id = agent.session_id

    metas = mgr.list_sessions(limit=limit)
    infos = [
        SessionInfo(
            id=m.id, title=m.title or "(untitled)",
            created_at=m.created_at, updated_at=m.updated_at,
            provider=m.provider, model=m.model,
            turns=m.turns, input_tokens=m.input_tokens,
            output_tokens=m.output_tokens, tool_calls=m.tool_calls,
            summary=m.summary, age=m.age_str,
            is_current=(m.id == current_id),
        )
        for m in metas
    ]

    # 当前 session 可能尚未 save_session() 落盘（例如刚启动 / 刚 new_session），
    # 此时不会出现在 list_sessions() 结果里 —— 把内存中的"当前会话"插到列表最前面，
    # 确保 Web 端始终能看到并默认选中它。
    if current_id and not any(i.id == current_id for i in infos):
        meta = agent.session_meta
        if meta is not None:
            infos.insert(0, SessionInfo(
                id=meta.id, title=meta.title or "New session",
                created_at=meta.created_at, updated_at=meta.updated_at,
                provider=meta.provider, model=meta.model,
                turns=meta.turns, input_tokens=meta.input_tokens,
                output_tokens=meta.output_tokens, tool_calls=meta.tool_calls,
                summary=meta.summary, age="刚刚",
                is_current=True,
            ))

    return SessionsListResponse(sessions=infos, current_session_id=current_id, count=len(infos))


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(request: Request, session_id: str):
    """获取某个 session 的完整内容（含历史），用于切换前预览。"""
    user_mgr = _user_session_manager(request)

    if user_mgr is not None:
        # 多用户模式：如果这个 session 当前确实活跃在 pool 里，用内存数据
        # （可能比磁盘更新）；否则从该用户自己的 session 目录里读。
        # 注意：这里不会"看到"别的用户的 session——user_mgr 的 session_dir
        # 在构造时就已经被锁定为这个用户自己的目录了，物理上读不到别人的文件，
        # 不依赖额外的权限判断逻辑。
        pool = _session_pool(request)
        if pool is not None:
            entry = pool.get(session_id)
            if (
                entry is not None
                and entry.user_id == request.state.user_ctx.user_id
                and entry.agent is not None
                and entry.agent.session_id == session_id
            ):
                meta = entry.agent.session_meta
                return SessionDetailResponse(
                    id=meta.id, title=meta.title, created_at=meta.created_at,
                    updated_at=meta.updated_at, provider=meta.provider, model=meta.model,
                    stats={
                        "turns": meta.turns, "input_tokens": meta.input_tokens,
                        "output_tokens": meta.output_tokens, "tool_calls": meta.tool_calls,
                    },
                    summary=meta.summary, history=entry.agent.history, is_current=True,
                )

        session = user_mgr.load(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
        return SessionDetailResponse(
            id=session.id, title=session.title, created_at=session.created_at,
            updated_at=session.updated_at, provider=session.provider, model=session.model,
            stats=session.stats, summary=session.summary, history=session.history,
            is_current=False,
        )

    # ── 单用户模式：原有行为，完全不变 ───────────────────────────────────────
    agent, mgr = _session_manager_or_404(_bridge(request))

    # 若请求的就是当前激活 session，直接用内存中的最新数据（可能比磁盘更新）
    if session_id == agent.session_id:
        meta = agent.session_meta
        return SessionDetailResponse(
            id=meta.id, title=meta.title, created_at=meta.created_at,
            updated_at=meta.updated_at, provider=meta.provider, model=meta.model,
            stats={
                "turns": meta.turns, "input_tokens": meta.input_tokens,
                "output_tokens": meta.output_tokens, "tool_calls": meta.tool_calls,
            },
            summary=meta.summary, history=agent.history, is_current=True,
        )

    session = mgr.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    return SessionDetailResponse(
        id=session.id, title=session.title, created_at=session.created_at,
        updated_at=session.updated_at, provider=session.provider, model=session.model,
        stats=session.stats, summary=session.summary, history=session.history,
        is_current=False,
    )


@router.post("/sessions/{session_id}/resume", response_model=SessionActionResponse)
async def resume_session(request: Request, session_id: str):
    """
    切换到指定 session，成为(该用户的)当前活动 session。

    多用户模式下这是一个"轻量"操作：不立刻构造 Agent（那会在第一次真正
    /chat 时由 _bridge() 通过 SessionAgentPool.get_or_create() 触发），
    这里只是确认这个 session_id 存在（或者干脆乐观地接受任意 ID——下次
    /chat 时 get_or_create() 会按"是否有历史"决定加载还是新建）。
    """
    pool = _session_pool(request)
    if pool is not None:
        # 多用户模式 / 单 token 模式（session pool 现在总是存在）下都是
        # "轻量"操作：不立刻构造 Agent（那会在第一次真正 /chat 时由
        # _bridge() 通过 SessionAgentPool.get_or_create() 触发），这里
        # 只是确认这个 session_id 存在（或者干脆乐观地接受任意 ID——下次
        # /chat 时 get_or_create() 会按"是否有历史"决定加载还是新建）。
        return SessionActionResponse(
            ok=True, session_id=session_id,
            message="Session selected (will be loaded on first message)",
            history_count=0,
        )

    # ── 单用户模式：原有行为，完全不变 ───────────────────────────────────────
    bridge = _bridge(request)
    agent, _mgr = _session_manager_or_404(bridge)
    _require_idle(bridge)

    # 切换前先把当前会话保存下来，避免未保存的对话丢失
    try:
        agent.save_session()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass

    if not agent.load_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    bridge.emit_session_switched(agent.session_id or "", agent.session_meta.title if agent.session_meta else "")
    bridge.emit_info(f"[HTTP] Switched to session {agent.session_id!r}")

    return SessionActionResponse(
        ok=True, session_id=agent.session_id,
        message="Session resumed", history_count=len(agent.history),
    )


@router.post("/sessions/new", response_model=SessionActionResponse)
async def new_session(request: Request):
    """开始一个全新的 session（该用户的）。"""
    pool = _session_pool(request)
    if pool is not None:
        # 多用户模式：同样是"轻量"操作——只生成一个新 session_id 返回，
        # 不立刻构造 Agent（见 resume_session 的说明，原因一样）。
        import uuid as _uuid
        new_sid = _uuid.uuid4().hex[:12]
        return SessionActionResponse(
            ok=True, session_id=new_sid,
            message="New session id allocated (Agent will be created on first message)",
            history_count=0,
        )

    # ── 单用户模式：原有行为，完全不变 ───────────────────────────────────────
    bridge = _bridge(request)
    agent, _mgr = _session_manager_or_404(bridge)
    _require_idle(bridge)

    # 切换前先把当前会话保存下来，避免未保存的对话丢失
    try:
        agent.save_session()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass

    if not agent.new_session():
        raise HTTPException(status_code=500, detail="Failed to start a new session")

    bridge.emit_session_switched(agent.session_id or "", "New session")
    bridge.emit_info(f"[HTTP] Started new session {agent.session_id!r}")

    return SessionActionResponse(
        ok=True, session_id=agent.session_id,
        message="New session started", history_count=len(agent.history),
    )


@router.post("/sessions/{session_id}/save_anchor", response_model=SessionActionResponse)
async def save_cognitive_anchor(request: Request, session_id: str):
    """
    [具身改进 C3 daemon 缺口修复] daemon-connected 模式下的 Ctrl-C 触发路径。

    纯本地模式下 Ctrl-C 由 cli/repl.py 直接调用 agent._save_cognitive_anchor()；
    daemon-connected 模式下 cli/daemon.py 的 DaemonClient 进程不直接持有 Agent
    实例，Ctrl-C 到不了 Agent 那一层——这个路由补上那条路径：client 在自己的
    KeyboardInterrupt 处理里 best-effort POST 这里，server 侧找到对应 session
    的 Agent 并调用同一个 _save_cognitive_anchor()。

    与 /sessions/new 等路由一致地走 _bridge(request, session_id) 解析到具体的
    AgentBridge（单用户模式下退化为全局 bridge，session_id 被忽略）。cognitive_
    anchor_enabled=False 或 Agent 未初始化时，_save_cognitive_anchor() 自身已经
    做了 no-op / 静默降级，这里不重复判断。
    """
    bridge = _bridge(request, session_id)
    agent = _agent_or_404(bridge)
    try:
        agent._save_cognitive_anchor()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        # 认知锚点生成失败不应该让这个端点返回 500——对客户端而言这是一次
        # best-effort 的旁路调用，不影响 Ctrl-C 本身的中断流程。
    return SessionActionResponse(
        ok=True, session_id=agent.session_id or session_id,
        message="Cognitive anchor save attempted", history_count=len(agent.history),
    )


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(request: Request, session_id: str):
    """删除一个已保存的 session（不能删除当前激活的 session）。"""
    user_mgr = _user_session_manager(request)

    if user_mgr is not None:
        pool = _session_pool(request)
        if pool is not None:
            entry = pool.get(session_id)
            if entry is not None and entry.user_id == request.state.user_ctx.user_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete a currently active session; suspend or switch away first",
                )
        ok = user_mgr.delete(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
        return SessionDeleteResponse(ok=True, message=f"Session '{session_id}' deleted")

    # ── 单用户模式：原有行为，完全不变 ───────────────────────────────────────
    bridge = _bridge(request)
    agent, mgr = _session_manager_or_404(bridge)

    if session_id == agent.session_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the currently active session; switch to another session first",
        )

    ok = mgr.delete(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return SessionDeleteResponse(ok=True, message=f"Session '{session_id}' deleted")


# ── 权限审批 ──────────────────────────────────────────────────────────────────

@router.get("/permissions/pending")
async def list_pending_permissions(request: Request):
    """
    daemon 多用户架构 Phase 3：单用户模式下行为不变（看全局唯一 bridge 的
    pending 列表）。多用户模式下，"待审批列表"含义变成"该用户最近活跃
    session 的 pending 列表"——这与 chat/status 等端点的解析方式一致，
    都是按"最近活跃 session"兜底，不是全局视角（owner 想看所有用户的
    待审批，应该用 /v1/sessions 配合逐个查询，这里不展开做聚合视图）。
    """
    return {"permissions": _bridge(request).permission_gate.list_pending()}


@router.post("/permissions/{req_id}", response_model=PermissionResponse)
async def respond_permission(request: Request, req_id: str, body: PermissionRequest):
    # 同 stream_turn() 的修复理由：req_id 可能不属于"该用户最近活跃的 session"，
    # 必须先用 pool.find_by_permission_req() 真正定位归属。
    pool = _session_pool(request)
    if pool is not None:
        entry = pool.find_by_permission_req(req_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Permission request {req_id!r} not found or already handled",
            )
        user_ctx = getattr(request.state, "user_ctx", None)
        if user_ctx is not None and not user_ctx.is_owner and entry.user_id != user_ctx.user_id:
            raise HTTPException(status_code=403, detail="This permission request does not belong to you")
        bridge = entry.bridge
    else:
        bridge = _bridge(request)

    gate = bridge.permission_gate

    # 在 respond 前先取出 turn_id（respond 后 pending 会被移除）
    with gate._lock:
        pending_info = gate._pending.get(req_id)
        turn_id = pending_info.turn_id if pending_info else ""

    ok = gate.respond(req_id, body.approve, body.edited_input)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Permission request {req_id!r} not found or already handled",
        )

    # 修复：Web 端批准/拒绝后广播 permission_done 事件，并更新 bridge 状态
    # （CLI 端走 broadcast_done 路径；Web 端 respond() 只设置 event，不广播，需要在此补上）
    gate.broadcast_done(req_id, body.approve, "http", turn_id)

    # 处理 always / deny_always 模式：把决定写入权限白/黑名单。
    _persist_permission_preference(bridge, body.mode, pending_info)
    return PermissionResponse(ok=True)


# ── 通用交互式提问（ask_user 系列工具 / /goal 协商 / 任意 slash 命令）───────────

@router.get("/interactions/pending")
async def list_pending_interactions(request: Request):
    """待回答的通用交互请求列表（与 /permissions/pending 同样的"最近活跃 session"语义）。"""
    return {"interactions": _bridge(request).interaction_gate.list_pending()}


@router.post("/interactions/{req_id}", response_model=InteractionResponse)
async def respond_interaction(request: Request, req_id: str, body: InteractionRequestBody):
    pool = _session_pool(request)
    if pool is not None:
        entry = pool.find_by_interaction_req(req_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Interaction request {req_id!r} not found or already handled",
            )
        user_ctx = getattr(request.state, "user_ctx", None)
        if user_ctx is not None and not user_ctx.is_owner and entry.user_id != user_ctx.user_id:
            raise HTTPException(status_code=403, detail="This interaction request does not belong to you")
        bridge = entry.bridge
    else:
        bridge = _bridge(request)

    gate = bridge.interaction_gate

    with gate._lock:
        pending_info = gate._pending.get(req_id)
        turn_id = pending_info.turn_id if pending_info else ""

    answer = {k: v for k, v in {
        "answer": body.answer,
        "confirmed": body.confirmed,
        "choice_index": body.choice_index,
    }.items() if v is not None}

    ok = gate.respond(req_id, answer)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Interaction request {req_id!r} not found or already handled",
        )

    gate.broadcast_done(req_id, answer, "http", turn_id)
    return InteractionResponse(ok=True)


def _persist_permission_preference(bridge, mode: str, pending_info) -> None:
    """
    把 HTTP 端提交的 "always"/"deny_always" 决定持久化到对应
    PermissionGuard 实例的白/黑名单（_allow_list/_denied_tools），
    与 daemon 本地终端 CLI 交互（PermissionGuard._prompt_with_http 的
    CLI 分支，直接调用 self._add_allow()/self._denied_tools.add()）
    行为保持一致。

    ★ 这里曾经是一段未完成的占位代码：
        checker = getattr(bridge, "permission_checker", None)
        if checker is not None: pass
    AgentBridge 对象上从来没有 "permission_checker" 这个属性
    （bridge.py 里能找到的是 self.agent，没有 self.permission_checker），
    所以 getattr 总是返回 None，这个分支永远不会执行——意味着无论
    CLI/web 端通过纯 HTTP 路径选择 "always"/"deny_always"，决定从来
    没有真正被持久化，下次同样的工具调用还会再问一次。这是
    "connected 模式应该和本地模式有完全对等的交互能力"这个目标下的
    一个真实缺口，顺手在这里修掉。

    真正持久化逻辑挂在 PermissionGuard 实例上，而不是 bridge 本身；
    AgentBridge 持有 self.agent，Agent 持有 self.guard，链路是
    bridge.agent.guard。

    抽成独立函数（而不是写在路由处理函数体内）是为了能脱离 FastAPI
    request/response 生命周期单独做单元测试——只需要构造 bridge/
    pending_info/guard 这三个普通对象，不需要起一个真实的 HTTP 服务。

    mode 不是 "always"/"deny_always" 时直接返回，不做任何事
    （"once" 是最常见的情况，不需要持久化任何偏好）。
    持久化失败（比如 guard 不存在、写文件出错）只会被静默忽略——
    respond() 已经让这次审批本身生效，工具调用会按这次的 approve/deny
    决定继续执行，持久化偏好失败不是致命错误，不应该让这次响应整体
    失败（HTTP 层已经返回 200 OK 给客户端了）。
    """
    if mode not in ("always", "deny_always"):
        return
    guard = getattr(getattr(bridge, "agent", None), "guard", None)
    if guard is None:
        return
    tool_name  = pending_info.tool_name if pending_info else ""
    tool_input = pending_info.tool_input if pending_info else {}
    if not tool_name:
        return
    try:
        if mode == "always":
            guard._add_allow(tool_name, tool_input)
        else:  # deny_always
            guard._denied_tools.add(tool_name)
            guard._save_permissions()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass


# ── 文件系统 ──────────────────────────────────────────────────────────────────

def _fs_error(e: Exception) -> HTTPException:
    if isinstance(e, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, PermissionError):
        return HTTPException(status_code=403, detail=str(e))
    if isinstance(e, (IsADirectoryError, NotADirectoryError, ValueError)):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))


@router.get("/fs/list", response_model=FsListResponse)
async def fs_list(request: Request, path: str = Query(default="")):
    try:
        return _fs(request).list_dir(path)
    except Exception as e:
        raise _fs_error(e)


@router.get("/fs/read", response_model=FsReadResponse)
async def fs_read(request: Request, path: str = Query(...)):
    try:
        return _fs(request).read_file(path)
    except Exception as e:
        raise _fs_error(e)


@router.get("/fs/stat", response_model=FsStatResponse)
async def fs_stat(request: Request, path: str = Query(...)):
    try:
        return _fs(request).stat(path)
    except Exception as e:
        raise _fs_error(e)


@router.get("/fs/download")
async def fs_download(request: Request, path: str = Query(...)):
    try:
        fs   = _fs(request)
        full = fs._safe_path(path)
        if not full.exists() or full.is_dir():
            raise FileNotFoundError(f"{path!r} not found or is a directory")
        return FileResponse(
            path=str(full),
            filename=full.name,
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise _fs_error(e)


@router.get("/fs/search")
async def fs_search(
    request: Request,
    q:       str  = Query(..., description="搜索关键词"),
    content: bool = Query(default=False, description="是否搜索文件内容"),
    limit:   int  = Query(default=50, le=200),
):
    try:
        results = _fs(request).search(q, search_content=content, max_results=limit)
        return {"results": [r.model_dump() for r in results], "count": len(results)}
    except Exception as e:
        raise _fs_error(e)


@router.post("/fs/write")
async def fs_write(request: Request, body: FsWriteRequest):
    try:
        fs = _fs(request)
        fs.write_file(body.path, body.content, body.encoding)
        _bridge(request).emit_fs_change("write", body.path)
        return {"ok": True, "path": body.path}
    except Exception as e:
        raise _fs_error(e)


@router.post("/fs/mkdir")
async def fs_mkdir(request: Request, body: FsMkdirRequest):
    try:
        _fs(request).mkdir(body.path)
        _bridge(request).emit_fs_change("mkdir", body.path)
        return {"ok": True, "path": body.path}
    except Exception as e:
        raise _fs_error(e)


@router.delete("/fs/delete")
async def fs_delete(request: Request, body: FsDeleteRequest):
    try:
        _fs(request).delete(body.path, body.recursive)
        _bridge(request).emit_fs_change("delete", body.path)
        return {"ok": True, "path": body.path}
    except Exception as e:
        raise _fs_error(e)


@router.post("/fs/rename")
async def fs_rename(request: Request, body: FsRenameRequest):
    try:
        _fs(request).rename(body.src, body.dst)
        _bridge(request).emit_fs_change("rename", body.src)
        return {"ok": True, "src": body.src, "dst": body.dst}
    except Exception as e:
        raise _fs_error(e)


@router.post("/fs/upload")
async def fs_upload(
    request: Request,
    path:    str        = Query(..., description="上传目标路径（相对 project_root）"),
    file:    UploadFile = File(...),
):
    try:
        fs      = _fs(request)
        content = await file.read()
        full    = fs._safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
        _bridge(request).emit_fs_change("upload", path)
        return {"ok": True, "path": path, "size": len(content)}
    except Exception as e:
        raise _fs_error(e)


# ── 用户管理（daemon 多用户架构 Phase 1，owner only）─────────────────────────────
#
# 单用户模式（未开启 --http-multi-user）下，role_store 为 None，
# 这组端点统一返回 404——既不暴露"功能存在但未开启"的细节，
# 也避免单用户部署的人困惑于一个用不了的端点。

def _users_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Multi-user mode not enabled. Start daemon with --http-multi-user.",
    )


def _to_user_info(record) -> UserInfo:
    return UserInfo(
        user_id=record.user_id,
        name=record.name,
        role=record.role,
        trust_level=record.trust_level,
        created_at=record.created_at,
        last_seen=record.last_seen,
        meta=record.meta,
    )


@router.get("/users", response_model=UsersListResponse)
async def list_users(request: Request):
    store = _role_store(request)
    if store is None:
        raise _users_unavailable()
    _require_owner(request)
    return UsersListResponse(users=[_to_user_info(r) for r in store.list_users()])


@router.post("/users", response_model=UserCreateResponse)
async def create_user(request: Request, body: UserCreateRequest):
    store = _role_store(request)
    if store is None:
        raise _users_unavailable()
    _require_owner(request)

    if body.role not in VALID_ROLES or body.role == "owner":
        return UserCreateResponse(
            ok=False,
            message=f"Invalid role {body.role!r}. Must be one of "
                     f"{sorted(VALID_ROLES - {'owner'})}",
        )
    try:
        user_id, token = store.add_user(
            name=body.name, role=body.role,
            trust_level=body.trust_level, meta=body.meta,
        )
    except ValueError as e:
        return UserCreateResponse(ok=False, message=str(e))

    return UserCreateResponse(ok=True, user_id=user_id, token=token)


@router.delete("/users/{user_id}", response_model=UserActionResponse)
async def remove_user(request: Request, user_id: str):
    store = _role_store(request)
    if store is None:
        raise _users_unavailable()
    _require_owner(request)

    if user_id == "owner":
        return UserActionResponse(ok=False, message="Cannot remove owner")
    ok = store.remove_user(user_id)
    return UserActionResponse(
        ok=ok, message="" if ok else f"User {user_id!r} not found"
    )


@router.patch("/users/{user_id}", response_model=UserActionResponse)
async def update_user(request: Request, user_id: str, body: UserUpdateRequest):
    store = _role_store(request)
    if store is None:
        raise _users_unavailable()
    _require_owner(request)

    if user_id == "owner":
        return UserActionResponse(ok=False, message="Cannot modify owner via this endpoint")

    ok = True
    if body.role is not None:
        ok = store.update_role(user_id, body.role) and ok
    if body.meta is not None:
        ok = store.update_meta(user_id, body.meta) and ok
    return UserActionResponse(
        ok=ok, message="" if ok else f"User {user_id!r} not found or invalid role"
    )


@router.post("/users/{user_id}/token", response_model=UserCreateResponse)
async def rotate_user_token(request: Request, user_id: str):
    """重新生成某用户的 token（旧 token 立即失效）。"""
    store = _role_store(request)
    if store is None:
        raise _users_unavailable()
    _require_owner(request)

    new_token = store.rotate_token(user_id)
    if new_token is None:
        return UserCreateResponse(ok=False, message=f"User {user_id!r} not found")
    return UserCreateResponse(ok=True, user_id=user_id, token=new_token)


# ── Self 状态（daemon 多用户架构 Phase 4，owner only）───────────────────────────

@router.get("/self/status")
async def get_self_status(request: Request):
    """
    Self（主自我）的状态总览：GoalBacklog、自主活动摘要（含最近的
    session_crashed 通知）、SessionAgentPool 概况。

    注意：必须从 request.app.state.http_server 取 Self 真正使用的 bridge/agent，
    不能用 _bridge(request)——那个在多用户模式下会按"该用户最近活跃 session"
    解析，拿到的会是某个 SessionAgent，不是 Self。Self 是 HttpServer 自己持有的
    那个固定的 _bridge/_runner，跟任何用户的 session 都不是同一回事。

    单用户模式下也能用（没有 SessionAgentPool 那部分数据，其它字段仍然有效）——
    "Self"这个概念本来就不是多用户特有的，只是多用户模式下多了 pool 状态可看。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    self_agent = http_server.bridge.agent
    result: dict = {
        "autonomous_loop": None,
        "goals": {"active_objectives": [], "active_goals": []},
        "recent_activity": [],
        "session_pool": None,
    }

    al = http_server.autonomous_loop
    if al is not None:
        try:
            result["autonomous_loop"] = al.get_digest_status()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes')
            pass

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.goal_backlog import load_goal_backlog
        from mini_agent.evolution.resource_arbiter import read_activity_digest

        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if project_root is not None:
            paths = AgentPaths(project_root)

            backlog = load_goal_backlog(paths)
            result["goals"] = {
                "active_objectives": [n.to_dict() for n in backlog.active_objectives()],
                "active_goals":      [n.to_dict() for n in backlog.active_goals()],
            }

            # 最近活动（含 session_crashed 等）：默认看最近 24 小时
            since = time.time() - 24 * 3600
            records = read_activity_digest(paths, since_ts=since)
            result["recent_activity"] = records[-50:]  # 最多 50 条，避免响应过大
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass

    pool = http_server.session_pool
    if pool is not None:
        entries = pool.list_entries()
        result["session_pool"] = {
            "active_count": pool.active_count(),
            "sessions": [
                {
                    "session_id":  e.session_id,
                    "user_id":     e.user_id,
                    "role":        e.role,
                    "idle_seconds": round(e.idle_seconds, 1),
                    "is_alive":    e.is_alive,
                }
                for e in entries
            ],
        }

    return result


# ── 产出物 Artifacts ──────────────────────────────────────────────────────────
# 供「产出物看板」使用：与 /fs/* 不同，这里不是遍历目录，而是消费 Agent/工具
# 主动登记的 manifest（storage/artifacts.py），语义化地展示"这次任务产出了什么"。

def _artifacts_project_root(request: Request) -> Path:
    project_root = getattr(request.app.state, "project_root", None)
    if project_root is None:
        raise HTTPException(status_code=503, detail="project_root not configured")
    return project_root


@router.get("/artifacts")
async def list_artifacts_route(
    request: Request,
    session_id: Optional[str] = Query(default=None, description="按 session 过滤"),
    limit:  int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """GET /v1/artifacts — 列出产出物摘要（按时间倒序）。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.storage.artifacts import list_artifacts

    paths = AgentPaths(_artifacts_project_root(request))
    items = list_artifacts(paths, session_id=session_id, limit=limit, offset=offset)
    return {"items": items, "count": len(items)}


@router.get("/artifacts/{manifest_id}")
async def get_artifact_route(
    request: Request,
    manifest_id: str,
    session_id: Optional[str] = Query(default=None, description="已知 session_id 时提供可加速查找"),
):
    """GET /v1/artifacts/{manifest_id} — 单次产出的完整 manifest（含文件明细）。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.storage.artifacts import get_manifest

    paths = AgentPaths(_artifacts_project_root(request))
    manifest = get_manifest(paths, manifest_id, session_id=session_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"artifact manifest {manifest_id!r} not found")
    return manifest


@router.get("/artifacts/{manifest_id}/file")
async def get_artifact_file_route(
    request: Request,
    manifest_id: str,
    index: int = Query(default=0, ge=0, description="manifest.files 里的第几个文件"),
    session_id: Optional[str] = Query(default=None),
    download: bool = Query(default=False, description="true 强制走附件下载而非内联展示"),
):
    """GET /v1/artifacts/{manifest_id}/file — 取 manifest 内某个文件本身。

    注意：manifest.files[].path 是登记时的绝对路径，可能位于 project_root 之外
    （例如 /mnt/user-data/outputs/），因此这里不走 FsHelper 的 project_root jail，
    而是直接校验路径存在且的确是 manifest 里登记过的路径（不接受调用方传入任意路径）。
    """
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.storage.artifacts import get_manifest

    paths = AgentPaths(_artifacts_project_root(request))
    manifest = get_manifest(paths, manifest_id, session_id=session_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"artifact manifest {manifest_id!r} not found")

    files = manifest.get("files", [])
    if index >= len(files):
        raise HTTPException(status_code=404, detail=f"file index {index} out of range (0..{len(files)-1})")

    file_entry = files[index]
    full = Path(file_entry["path"])
    if not full.exists() or full.is_dir():
        raise HTTPException(status_code=404, detail=f"{file_entry['path']!r} not found or is a directory")

    media_type = file_entry.get("mime") or "application/octet-stream"
    return FileResponse(
        path=str(full),
        filename=full.name,
        media_type=media_type,
        content_disposition_type="attachment" if download else "inline",
    )


# ── 自主执行状态 ──────────────────────────────────────────────────────────────

@router.get("/autonomous/status")
async def get_autonomous_status(request: Request):
    """
    GET /v1/autonomous/status

    返回 daemon 自主执行的实时状态：
      - autonomy_level：当前档位（passive/maintenance/autonomous）
      - cron_jobs：各 job 的下次触发时间
      - objective_executions：活跃 Objective 的执行进度
      - next_tick_in：距下次 AutonomousLoop.tick() 还有多少秒
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {
        "autonomy_level": "unknown",
        "next_tick_in": None,
        "cron_jobs": [],
        "objective_executions": [],
    }

    al = http_server.autonomous_loop
    if al is not None:
        try:
            result["autonomy_level"] = al._get_autonomy_level()
            result["next_tick_in"] = round(max(0.0, al._last_tick_at + al._tick_interval - time.time()), 1)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes')
            pass

        # CronScheduler 状态
        cs = getattr(al, "_cron_scheduler", None)
        if cs is None:
            cs = getattr(http_server.bridge, "_cron_scheduler", None)
        if cs is not None:
            try:
                jobs = cs.list_jobs()
                result["cron_jobs"] = [
                    {
                        "id": j.id,
                        "name": j.name,
                        "enabled": j.enabled,
                        "next_run_in": round(max(0.0, j.time_until_next()), 0),
                        "next_run_str": j.next_run_str(),
                        "run_count": j.run_count,
                        "last_run_at": j.last_run_at,
                    }
                    for j in jobs
                ]
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ObjectiveExecutor 状态
        oe = getattr(al, "_objective_executor", None)
        if oe is None:
            oe = getattr(http_server.bridge, "_objective_executor", None)
        if oe is not None:
            try:
                result["objective_executions"] = oe.get_status_summary()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

    return result


# ── Goals REST API ────────────────────────────────────────────────────────────

@router.get("/goals")
async def list_goals(request: Request):
    """GET /v1/goals — 返回完整的 GoalBacklog 视图。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.goal_backlog import load_goal_backlog
        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if not project_root:
            return {"goals": [], "objectives": []}
        paths = AgentPaths(project_root)
        backlog = load_goal_backlog(paths)
        return {
            "goals":      [n.to_dict() for n in backlog.active_goals()],
            "objectives": [n.to_dict() for n in backlog.active_objectives()],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/goals")
async def add_goal(request: Request):
    """
    POST /v1/goals
    Body: { "title": str, "description": str, "priority": int, "source": str }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.goal_backlog import load_goal_backlog
        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if not project_root:
            raise HTTPException(status_code=503, detail="project_root not configured")
        paths = AgentPaths(project_root)
        backlog = load_goal_backlog(paths)
        goal = backlog.add_goal(
            title=title,
            description=body.get("description", ""),
            source=body.get("source", "user"),
            priority=int(body.get("priority", 50)),
        )
        return {"goal": goal.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/goals/{goal_id}")
async def update_goal(goal_id: str, request: Request):
    """
    PATCH /v1/goals/{goal_id}
    Body: { "status": str, "progress_notes": str, "priority": int }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    body = await request.json()
    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.goal_backlog import load_goal_backlog
        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if not project_root:
            raise HTTPException(status_code=503, detail="project_root not configured")
        paths = AgentPaths(project_root)
        backlog = load_goal_backlog(paths)

        node = backlog.get(goal_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")

        fields = {}
        if "status" in body:
            fields["status"] = body["status"]
        if "progress_notes" in body:
            fields["progress_notes"] = body["progress_notes"]
        if "priority" in body:
            fields["priority"] = int(body["priority"])

        updated = backlog.update_fields(goal_id, **fields)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")

        # reject 时通知 SoftGoalDeriver 记录拒绝历史（用改前的 node 快照判断 source，
        # 避免并发场景下拿到的是别的进程刚写入、字段含义不同的数据）
        if body.get("status") == "abandoned" and node.source == "agent_derived":
            try:
                from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver
                SoftGoalDeriver(paths, self_agent.cfg).record_rejected(updated.title)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        return {"goal": updated.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cron Jobs REST API ────────────────────────────────────────────────────────

@router.get("/cron/jobs")
async def list_cron_jobs(request: Request):
    """GET /v1/cron/jobs — 列出所有 cron job。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is None:
        al = http_server.autonomous_loop
        cs = getattr(al, "_cron_scheduler", None) if al else None
    if cs is None:
        return {"jobs": [], "note": "CronScheduler not available (daemon mode required)"}

    jobs = cs.list_jobs()
    return {
        "jobs": [
            {**j.to_dict(), "next_run_str": j.next_run_str()}
            for j in jobs
        ]
    }


@router.post("/cron/jobs")
async def add_cron_job(request: Request):
    """
    POST /v1/cron/jobs
    Body: { "name": str, "schedule": str, "task_template": str, "description": str }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is None:
        raise HTTPException(status_code=503, detail="CronScheduler not available")

    body = await request.json()
    name = body.get("name", "").strip()
    schedule = body.get("schedule", "").strip()
    task_template = body.get("task_template", "").strip()
    if not name or not schedule or not task_template:
        raise HTTPException(status_code=400, detail="name, schedule, task_template are required")

    try:
        job = cs.add_job(
            name=name,
            schedule=schedule,
            task_template=task_template,
            description=body.get("description", ""),
        )
        return {"job": {**job.to_dict(), "next_run_str": job.next_run_str()}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, request: Request):
    """
    PUT /v1/cron/jobs/{job_id}
    Body: { "enabled": bool, "schedule": str }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is None:
        raise HTTPException(status_code=503, detail="CronScheduler not available")

    body = await request.json()
    try:
        if "enabled" in body:
            if body["enabled"]:
                cs.enable(job_id)
            else:
                cs.disable(job_id)
        if "schedule" in body:
            cs.update_schedule(job_id, body["schedule"])
        job = cs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return {"job": {**job.to_dict(), "next_run_str": job.next_run_str()}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cron/jobs/{job_id}/run")
async def run_cron_job_now(job_id: str, request: Request):
    """POST /v1/cron/jobs/{job_id}/run — 立即触发一次。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is None:
        raise HTTPException(status_code=503, detail="CronScheduler not available")

    success = cs.run_now(job_id)
    if not success:
        job = cs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        raise HTTPException(status_code=500, detail="Job trigger failed")
    return {"triggered": True, "job_id": job_id}


# ── 用户行为感知系统（默认关闭，见 perception/behavior/）───────────────────
#
#   GET  /v1/perception/status   总开关/采集器状态
#   POST /v1/perception/toggle   打开/关闭总开关或某个采集器（owner only）
#   POST /v1/perception/report   外部系统（如浏览器插件）上报事件
#   GET  /v1/perception/events   查询已采集事件
#   DELETE /v1/perception/events 清空已采集事件（owner only）
#
# /v1/* 已经过 AuthMiddleware 校验（Bearer token + 127.0.0.1 白名单），
# /report 额外再校验 behavior 自己的 report_token，双重保险：即使主 API
# token 泄露，浏览器插件那一路也需要单独在 /behavior token 里取到的口令。

def _get_behavior_manager(request: Request):
    from mini_agent.perception.behavior import get_manager
    project_root = getattr(request.app.state, "project_root", None)
    return get_manager(project_root=project_root)


@router.get("/perception/status")
async def perception_status(request: Request):
    mgr = _get_behavior_manager(request)
    return mgr.status()


@router.post("/perception/toggle")
async def perception_toggle(request: Request):
    """
    Body: { "enabled": bool }                      — 总开关
       or { "collector": "active_window", "enabled": bool } — 单个采集器
    """
    _require_owner(request)
    mgr = _get_behavior_manager(request)
    body = await request.json()

    collector = body.get("collector")
    if collector:
        try:
            mgr.set_collector_enabled(collector, bool(body.get("enabled", False)))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        mgr.set_enabled(bool(body.get("enabled", False)))
    return mgr.status()


@router.post("/perception/report")
async def perception_report(request: Request):
    """
    外部系统（浏览器插件等）上报行为事件。

    Body: {
      "source": "browser_ext",
      "token": "<behavior report token, 从 /behavior token 或本接口的 owner 调用获取>",
      "events": [ { "event_type": "page_visit", "domain": "...", ... }, ... ]
    }

    受总开关 + browser_report_enabled 子开关 + token 三重校验，
    任意一项不满足都会被拒绝而不是静默丢弃（返回明确原因，便于插件侧调试）。
    """
    body = await request.json()
    source = body.get("source", "browser_ext")
    kind = body.get("kind", "browser")
    token = body.get("token", "")
    events = body.get("events", [])
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="events must be a list")

    mgr = _get_behavior_manager(request)
    ok, message = mgr.report_external(source, events, token, kind=kind)
    if not ok:
        raise HTTPException(status_code=403, detail=message)
    return {"ok": True, "message": message}


@router.post("/perception/git/install-hooks")
async def perception_git_install_hooks(request: Request):
    """在指定仓库安装 post-commit/post-checkout hook（owner only，会写本机文件）。

    Body: { "repo_path": "/path/to/repo" }
    """
    _require_owner(request)
    body = await request.json()
    repo_path = body.get("repo_path")
    if not repo_path:
        raise HTTPException(status_code=400, detail="repo_path is required")

    from pathlib import Path
    mgr = _get_behavior_manager(request)
    report_url = str(request.base_url).rstrip("/") + "/v1/perception/report"
    api_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        written = mgr.install_git_hooks(Path(repo_path), report_url, api_token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"installed": [str(p) for p in written]}


@router.get("/perception/events")
async def perception_events(
    request: Request,
    source: Optional[str] = Query(None),
    limit: int = Query(200, le=2000),
    since: Optional[float] = Query(None),
):
    mgr = _get_behavior_manager(request)
    events = mgr.query(source=source, limit=limit, since=since)
    return {"events": [e.to_dict() for e in events], "count": len(events)}


@router.delete("/perception/events")
async def perception_events_clear(request: Request):
    _require_owner(request)
    mgr = _get_behavior_manager(request)
    n = mgr.clear()
    return {"cleared_files": n}


@router.post("/perception/browser/start")
async def perception_browser_start(request: Request):
    """启动专用调试浏览器（CDP 方案），owner only（会拉起本机子进程）。"""
    _require_owner(request)
    mgr = _get_behavior_manager(request)
    try:
        st = mgr.browser_start()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return st


@router.post("/perception/browser/stop")
async def perception_browser_stop(request: Request):
    _require_owner(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    mgr = _get_behavior_manager(request)
    return mgr.browser_stop(kill_browser=bool(body.get("kill_browser", False)))


@router.get("/perception/browser/status")
async def perception_browser_status(request: Request):
    mgr = _get_behavior_manager(request)
    return mgr.browser_status()


@router.get("/perception/summary")
async def perception_summary(request: Request, date: Optional[str] = Query(None)):
    """查看某天的工作/生活画像摘要；不存在则现算一次。date 缺省为今天，格式 YYYY-MM-DD。"""
    import datetime as _dt
    from mini_agent.perception.behavior.analyzer import generate_daily_summary, load_daily_summary

    day = date or _dt.date.today().isoformat()
    mgr = _get_behavior_manager(request)
    summary = load_daily_summary(day)
    if summary is None:
        summary = generate_daily_summary(mgr, day)
    return summary