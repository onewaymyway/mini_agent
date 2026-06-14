"""
api/models.py — 所有 HTTP API 的请求/响应 Pydantic 模型 + 事件类型常量。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import time


# ── 事件类型 ──────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    # LLM 输出
    TOKEN            = "token"           # 流式文本 token
    TURN_START       = "turn_start"      # 一轮开始
    TURN_DONE        = "turn_done"       # 一轮结束
    # 工具
    TOOL_CALL        = "tool_call"       # 工具被调用
    TOOL_RESULT      = "tool_result"     # 工具结果
    TOOL_ERROR       = "tool_error"      # 工具出错
    # 权限
    PERMISSION_REQ   = "permission_req"  # 需要用户审批
    PERMISSION_DONE  = "permission_done" # 审批结果
    # 文件系统
    FS_CHANGE        = "fs_change"       # 文件被写/删/改
    # Session
    SESSION_SWITCHED = "session_switched" # 当前激活 session 发生切换
    # 系统
    STATUS           = "status"          # agent 状态变化
    ERROR            = "error"           # 运行时错误
    INFO             = "info"            # 普通信息（print_info 等）
    WARNING          = "warning"
    INTERRUPT        = "interrupt"       # 执行被中断


# ── 事件数据结构 ──────────────────────────────────────────────────────────────

class AgentEvent(BaseModel):
    """单条事件，写入 RingBuffer 并通过 SSE 推送。"""
    id:      int       = 0               # 全局自增序号（由 RingBuffer 赋值）
    type:    EventType = EventType.INFO
    turn_id: str       = ""              # 关联的 turn（无关联时为空）
    ts:      float     = Field(default_factory=time.time)
    data:    dict      = Field(default_factory=dict)

    def sse_format(self) -> str:
        """格式化为 SSE 文本帧（含 id/event/data 三行 + 空行）。"""
        import json
        payload = json.dumps(
            {"turn_id": self.turn_id, "ts": self.ts, **self.data},
            ensure_ascii=False,
        )
        return f"id: {self.id}\nevent: {self.type.value}\ndata: {payload}\n\n"


# ── HTTP 请求/响应模型 ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:  str
    turn_id:  Optional[str] = None   # 客户端可指定；留空则服务端生成

class ChatResponse(BaseModel):
    turn_id:  str
    queued:   bool = True

class InterruptResponse(BaseModel):
    ok: bool

class StatusResponse(BaseModel):
    state:      str          # "idle" | "running" | "waiting_permission"
    turn_id:    Optional[str]
    stats:      dict
    queue_depth: int

class PermissionRequest(BaseModel):
    approve:      bool
    edited_input: Optional[dict] = None   # 用户修改后的工具参数（bash edit 场景）
    mode:         str = "once"             # "once" | "always" | "deny_always"

class PermissionResponse(BaseModel):
    ok: bool

class HistoryResponse(BaseModel):
    messages: list[dict]
    count:    int

class EventsResponse(BaseModel):
    events: list[dict]
    count:  int
    min_id: int
    max_id: int

class TurnInfo(BaseModel):
    turn_id:   str
    input:     str
    state:     str           # "running" | "done" | "error" | "interrupted"
    started_at: float
    ended_at:  Optional[float] = None
    token_count: int = 0

class TurnsResponse(BaseModel):
    turns: list[TurnInfo]

# ── 文件系统模型 ──────────────────────────────────────────────────────────────

class FileEntry(BaseModel):
    name:     str
    path:     str            # 相对于 project_root
    is_dir:   bool
    size:     int = 0
    mtime:    float = 0.0

class FsListResponse(BaseModel):
    path:    str
    entries: list[FileEntry]
    total:   int

class FsReadResponse(BaseModel):
    path:     str
    content:  str            # 文本内容或 base64（binary 文件）
    encoding: str = "utf-8"  # "utf-8" | "base64"
    size:     int = 0

class FsWriteRequest(BaseModel):
    path:    str
    content: str
    encoding: str = "utf-8"

class FsMkdirRequest(BaseModel):
    path: str

class FsDeleteRequest(BaseModel):
    path:      str
    recursive: bool = False

class FsRenameRequest(BaseModel):
    src: str
    dst: str

class FsStatResponse(BaseModel):
    path:    str
    is_dir:  bool
    size:    int
    mtime:   float
    exists:  bool

class FsSearchRequest(BaseModel):
    query:        str
    search_content: bool = False    # True = 也搜文件内容
    max_results:  int = 50


# ── Session 模型 ──────────────────────────────────────────────────────────────

class SessionInfo(BaseModel):
    """Session 列表项（不含完整历史）。"""
    id:            str
    title:         str
    created_at:    str
    updated_at:    str
    provider:      str
    model:         str
    turns:         int = 0
    input_tokens:  int = 0
    output_tokens: int = 0
    tool_calls:    int = 0
    summary:       str = ""
    age:           str = ""       # 人类可读的相对时间，如 "3分钟前"
    is_current:    bool = False   # 是否为当前 agent 正在使用的 session

class SessionsListResponse(BaseModel):
    sessions:           list[SessionInfo]
    current_session_id: Optional[str] = None
    count:              int = 0

class SessionDetailResponse(BaseModel):
    id:            str
    title:         str
    created_at:    str
    updated_at:    str
    provider:      str
    model:         str
    stats:         dict
    summary:       str = ""
    history:       list[dict]
    is_current:    bool = False

class SessionActionResponse(BaseModel):
    ok:            bool
    session_id:    Optional[str] = None
    message:       str = ""
    history_count: int = 0

class SessionDeleteResponse(BaseModel):
    ok:      bool
    message: str = ""