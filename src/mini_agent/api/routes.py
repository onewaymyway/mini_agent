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
            raw = bridge.agent.stats.summary()
            # summary() 可能返回字符串或字典，统一转为 dict
            if isinstance(raw, dict):
                stats = raw
            elif isinstance(raw, str):
                stats = {"summary": raw}
            else:
                stats = {"summary": str(raw)}
        except Exception:
            pass
    return StatusResponse(
        state       = state["state"],
        turn_id     = state["turn_id"],
        stats       = stats,
        queue_depth = state["queue_depth"],
    )


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


# ── 权限审批 ──────────────────────────────────────────────────────────────────

@router.get("/permissions/pending")
async def list_pending_permissions(request: Request):
    return {"permissions": _bridge(request).permission_gate.list_pending()}


@router.post("/permissions/{req_id}", response_model=PermissionResponse)
async def respond_permission(request: Request, req_id: str, body: PermissionRequest):
    gate = _bridge(request).permission_gate
    ok   = gate.respond(req_id, body.approve, body.edited_input)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Permission request {req_id!r} not found or already handled",
        )
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