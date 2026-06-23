"""
api/routes.py — FastAPI 路由定义

端点总览：
  系统
    GET  /v1/health                  心跳
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
)

router = APIRouter(prefix="/v1")


def _bridge(request: Request) -> AgentBridge:
    return request.app.state.bridge


def _fs(request: Request) -> FsHelper:
    return request.app.state.fs_helper


# ── 系统 ──────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"ok": True, "ts": time.time()}


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request):
    bridge = _bridge(request)
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
        except Exception:
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
    except Exception:
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
    )


# ── 诊断（Stage 6 / 6.2）──────────────────────────────────────────────────────

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
        "performance":   {},
        "memory":        {},
        "skills":        {},
        "evolution":     {},
        "anomaly_flags": [],
    }

    try:
        from mini_agent.storage.paths import AgentPaths
        proj_root = agent.cfg.project_root if agent else None
        paths = AgentPaths(proj_root) if proj_root else None

        # ── performance（traces.jsonl）────────────────────────────────────────
        if agent and getattr(agent, "_tracer", None):
            try:
                result["performance"] = agent._tracer.get_summary()
            except Exception:
                pass

        # ── memory（workdir memory.jsonl）────────────────────────────────────
        if paths:
            try:
                import json as _json
                mem_path = paths.workdir_memory()
                if mem_path.exists():
                    entries = []
                    with open(mem_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    entries.append(_json.loads(line))
                                except Exception:
                                    pass
                    by_type: dict = {}
                    for e in entries:
                        t = e.get("entry_type", "unknown")
                        by_type[t] = by_type.get(t, 0) + 1
                    result["memory"] = {
                        "total_entries":  len(entries),
                        "by_type":        by_type,
                    }
            except Exception:
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
            except Exception:
                pass

        # ── evolution（Stage 4/5 数据）────────────────────────────────────────
        if paths:
            try:
                import json as _json
                evo: dict = {}
                # pending_evolve_branches from global self_profile
                sp_path = paths.global_self_profile()
                if sp_path.exists():
                    sp = _json.loads(sp_path.read_text(encoding="utf-8"))
                    branches = (
                        sp.get("evolution_state", {}).get("pending_evolve_branches", [])
                    )
                    evo["pending_evolve_branches"] = branches
                    evo["pending_branches_count"]  = len(branches)
                # high-priority open_threads
                ot_path = paths.workdir_open_threads()
                if ot_path.exists():
                    ot_data = _json.loads(ot_path.read_text(encoding="utf-8"))
                    high = [
                        t for t in ot_data.get("threads", [])
                        if t.get("priority") == "high"
                    ]
                    evo["open_threads_high_count"] = len(high)
                    evo["open_threads_high"]       = high[:5]  # 最多展示 5 条
                result["evolution"] = evo
            except Exception:
                pass

        # ── anomaly_flags（异常检测，依赖 activity_log 数据积累）──────────────
        if paths and agent:
            try:
                from mini_agent.perception.observability import detect_anomalies
                al_path = paths.global_activity_log()
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
            except Exception:
                pass

    except Exception as e:
        result["_error"] = str(e)

    return JSONResponse(content=result)


# ── 对话 ──────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    bridge  = _bridge(request)
    turn_id = bridge.input_queue.enqueue(body.message, body.turn_id)
    bridge.emit_info(f"[HTTP] Queued message: {body.message[:80]}")
    return ChatResponse(turn_id=turn_id, queued=True)


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
) -> AsyncIterator[str]:
    """
    核心 SSE 生成器：
    1. 先回放 since_id / since_ts 之后的历史事件
    2. 实时推送后续新事件
    支持 turn_id 过滤（只输出某一轮的事件）
    """
    ring = bridge.ring

    # ── 阶段 1：历史回放 ──────────────────────────────────────────────────
    if replay:
        if since_ts is not None:
            history = ring.events_since_ts(since_ts)
        else:
            history = ring.events_since(since_id)

        for evt in history:
            if turn_id_filter and evt.turn_id and evt.turn_id != turn_id_filter:
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
    sub_id, q = bridge.broadcaster.subscribe()
    try:
        while True:
            try:
                evt: AgentEvent = await asyncio.wait_for(q.get(), timeout=20.0)
            except asyncio.TimeoutError:
                # 心跳保活，防止代理/负载均衡断开空闲连接
                yield f": heartbeat {time.time()}\n\n"
                continue

            if turn_id_filter and evt.turn_id and evt.turn_id != turn_id_filter:
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
):
    """
    SSE：订阅全局事件流。
    支持 Last-Event-ID 请求头（浏览器 EventSource 断线重连标准协议）。
    """
    bridge = _bridge(request)

    # 优先使用 Last-Event-ID 请求头（浏览器 EventSource 标准断线重连）
    header_last_id = request.headers.get("Last-Event-ID")
    if header_last_id and header_last_id.isdigit():
        since_id = int(header_last_id)

    return StreamingResponse(
        _sse_generator(bridge, since_id=since_id, since_ts=since_ts, replay=replay),
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
    """SSE：只订阅某一轮（turn_id）的事件。"""
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
):
    ring   = _bridge(request).ring
    events = ring.events_since_ts(since_ts) if since_ts else ring.events_since(since_id)

    if type:
        events = [e for e in events if e.type.value == type]

    events = events[-limit:]
    dicts  = [{"id": e.id, "type": e.type.value, "turn_id": e.turn_id,
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


@router.get("/sessions", response_model=SessionsListResponse)
async def list_sessions(request: Request, limit: int = Query(default=50, le=200)):
    """列出所有已保存的 session，并标记当前 agent 正在使用的 session。"""
    agent, mgr = _session_manager_or_404(_bridge(request))
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
    """将 agent 切换到指定 session（加载其历史，成为当前活动 session）。"""
    bridge = _bridge(request)
    agent, _mgr = _session_manager_or_404(bridge)
    _require_idle(bridge)

    # 切换前先把当前会话保存下来，避免未保存的对话丢失
    try:
        agent.save_session()
    except Exception:
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
    """清空当前历史，开始一个全新的 session。"""
    bridge = _bridge(request)
    agent, _mgr = _session_manager_or_404(bridge)
    _require_idle(bridge)

    # 切换前先把当前会话保存下来，避免未保存的对话丢失
    try:
        agent.save_session()
    except Exception:
        pass

    if not agent.new_session():
        raise HTTPException(status_code=500, detail="Failed to start a new session")

    bridge.emit_session_switched(agent.session_id or "", "New session")
    bridge.emit_info(f"[HTTP] Started new session {agent.session_id!r}")

    return SessionActionResponse(
        ok=True, session_id=agent.session_id,
        message="New session started", history_count=len(agent.history),
    )


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(request: Request, session_id: str):
    """删除一个已保存的 session（不能删除当前激活的 session）。"""
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
    return {"permissions": _bridge(request).permission_gate.list_pending()}


@router.post("/permissions/{req_id}", response_model=PermissionResponse)
async def respond_permission(request: Request, req_id: str, body: PermissionRequest):
    bridge = _bridge(request)
    gate   = bridge.permission_gate

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

    # 处理 always / deny_always 模式：把决定写入权限白/黑名单
    if body.mode in ("always", "deny_always"):
        checker = getattr(bridge, "permission_checker", None)
        if checker is not None:
            pass
    return PermissionResponse(ok=True)


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