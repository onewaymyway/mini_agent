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
    REASONING        = "reasoning"       # 流式思维链（CoT）token / 起止标记
    TURN_START       = "turn_start"      # 一轮开始
    TURN_DONE        = "turn_done"       # 一轮结束
    # 工具
    TOOL_CALL        = "tool_call"       # 工具被调用
    TOOL_RESULT      = "tool_result"     # 工具结果
    TOOL_ERROR       = "tool_error"      # 工具出错
    SKILL_LOADED     = "skill_loaded"    # skill 被激活（print_skill_loaded）
    # [SYS-AGENT-PREFIX] 哪个"角色"即将开始说话（主 Agent / GoalJudge / TurnJudge
    # 等内部子 Agent 都会各自调用 print_assistant_prefix(agent_name=...)）。
    # 之前这个信息完全没有转发给 SSE 客户端，导致 daemon connected 模式/
    # kanban 只能用启动时的固定 agent_name 硬编码前缀，GoalJudge 等内部
    # 子 Agent 说话时前缀显示错误（显示成主 Agent 的名字）。
    AGENT_PREFIX     = "agent_prefix"    # data: {"agent_name": "..."}

    # 权限
    PERMISSION_REQ   = "permission_req"  # 需要用户审批
    PERMISSION_DONE  = "permission_done" # 审批结果
    # 通用交互式提问（ask_user 系列工具 / /goal 协商 / 任意 slash 命令内的
    # prompt_user()/confirm() 调用），daemon connected 模式下用来把"需要
    # 用户二次输入"的请求转发给远程客户端。
    INTERACTION_REQ  = "interaction_req"  # 需要用户回答（开放文本/确认/选择/任意 REPL 输入）
    INTERACTION_DONE = "interaction_done" # 回答结果
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
    # slash 命令捕获输出（Stage: daemon 模式命令行客户端显示不全 修复）
    # run_captured() 执行期间产生的每一行输出，实时逐行转发。之前 slash
    # 命令（/evolve /skills /stats 等）的完整输出只在 run_captured() 结束
    # 后整段塞进 turn_done.text 里发一次，connected 客户端完全没处理这个
    # 字段（见 cli/daemon.py 里的历史 bug），且即便处理了，因为期间
    # print_info/print_warning 等已经各自广播过一次，会造成同一行内容被
    # 显示两次。改为：run_captured() 期间统一用这一种事件类型实时逐行转发
    # （见 ui/terminal.py::run_captured 的 on_line 回调），info/warning 等
    # 具体类型化事件在 capture 模式下改为不重复广播（见 api/server.py
    # _install_output_hook 里对 term._capture_mode 的判断），从根上避免
    # 双重发送；turn_done.text 仍然保留完整文本，作为"这一路事件一条都没
    # 收到"时的兜底（例如客户端在命令执行期间掉线重连、或老版本客户端）。
    COMMAND_OUTPUT   = "command_output"
    # 自主执行
    OBJECTIVE_PROGRESS = "objective_progress"  # Objective 步骤推进（daemon 自主执行）
    # cron 任务专用执行通道（evolution/cron_job_runner.py）完成一次后台执行
    # 后推送，供实时打开着看板的客户端不需要轮询/刷新即可看到最新状态。
    CRON_JOB_FINISHED  = "cron_job_finished"


# ── 事件数据结构 ──────────────────────────────────────────────────────────────

class AgentEvent(BaseModel):
    """单条事件，写入 RingBuffer 并通过 SSE 推送。"""
    id:      int       = 0               # 全局自增序号（由 RingBuffer 赋值）
    type:    EventType = EventType.INFO
    turn_id: str       = ""              # 关联的 turn（无关联时为空）
    # daemon 多用户架构 Phase 1：发起这条事件的用户 user_id（无关联/单用户模式下为空）。
    # 目前只是打个标记，Phase 3 才会真正用它来按用户过滤 /v1/stream 订阅。
    user_id: str       = ""
    # 这条事件产生时，agent 当前激活的 session_id。单用户模式下所有客户端
    # 共用同一个全局 bridge/RingBuffer，之前没有这个字段时，任何切换过
    # 多个 session 的 daemon 进程的 /v1/stream 历史回放会把"跨 session 的
    # 所有事件"混在一起吐给客户端——这个字段就是用来让 /v1/stream 可以按
    # session_id 过滤，客户端订阅时只看"当前这个 session"的事件。
    session_id: str    = ""
    ts:      float     = Field(default_factory=time.time)
    data:    dict      = Field(default_factory=dict)

    def sse_format(self) -> str:
        """格式化为 SSE 文本帧（含 id/event/data 三行 + 空行）。"""
        import json
        from mini_agent.time_utils import ts_to_str
        payload = json.dumps(
            {"turn_id": self.turn_id, "session_id": self.session_id,
             "ts": self.ts, "ts_str": ts_to_str(self.ts), **self.data},
            ensure_ascii=False,
        )
        return f"id: {self.id}\nevent: {self.type.value}\ndata: {payload}\n\n"


# ── HTTP 请求/响应模型 ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:  str
    turn_id:  Optional[str] = None   # 客户端可指定；留空则服务端生成
    # daemon 多用户架构 Phase 3：指定要发到哪个 session。单用户模式下忽略
    # （永远用全局唯一的 bridge）。多用户模式下若省略，_bridge() 会按
    # "该用户最近一次访问过的 session" 兜底，仍找不到则新建一个。
    # 注意：cli/daemon.py::DaemonClient.send_message() 早就在发这个字段了
    # （payload["session_id"] = session_id），只是 ChatRequest 此前没有声明
    # 这个字段，Pydantic 默认静默丢弃多余字段，一直没有真正生效过。
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    turn_id:  str
    queued:   bool = True
    # daemon 多用户架构 Phase 3：这条消息实际落到的 session_id（单用户模式下为 None）。
    session_id: Optional[str] = None

class InterruptResponse(BaseModel):
    ok: bool

class StatusResponse(BaseModel):
    state:      str          # "idle" | "running" | "waiting_permission"
    turn_id:    Optional[str]
    stats:      dict
    queue_depth: int
    # Stage 9 §3: daemon 状态字段
    subscribers: int = 0
    autonomy_level: str = "passive"
    last_autonomous_tick_at: Optional[float] = None
    tick_count: int = 0
    # daemon 多用户架构 Phase 3：当前（该用户最近访问过的）session_id。
    # 修复一个预先存在的 bug：cli/daemon.py::_pick_session() 一直在读
    # status.get("session_id", "")，但这个字段从来没有在 StatusResponse 里
    # 声明过——也就是说"●active"标记在 session 选择菜单里从未真正生效过
    # （永远拿到空字符串，永远不会匹配任何 session）。这里补上，顺带修了这个老 bug。
    session_id: Optional[str] = None
    # 看板"当前 session 信息"面板：当前实际使用的模型名 + 该 session 的
    # 存储目录（<project_root>/.agent/sessions/<session_id>/），
    # 方便用户在看板对话里直接确认"现在用的是哪个模型/数据存在哪"，
    # 不用切去终端翻配置或 `/model` 命令。
    model: Optional[str] = None
    session_dir: Optional[str] = None
    project_root: Optional[str] = None
    # 更细粒度的"agent 正在干什么"，供看板展示（区别于粗粒度的 state 字段）：
    # "waiting_input" | "waiting_permission" | "calling_model" | "calling_tool"
    activity: Optional[str] = None
    activity_detail: Optional[str] = None  # activity=="calling_tool" 时是工具名

class PermissionRequest(BaseModel):
    approve:      bool
    edited_input: Optional[dict] = None   # 用户修改后的工具参数（bash edit 场景）
    mode:         str = "once"             # "once" | "always" | "deny_always"

class PermissionResponse(BaseModel):
    ok: bool

class InteractionRequestBody(BaseModel):
    """回答一次通用交互式提问（/v1/interactions/{req_id}）。

    不同 kind 用不同字段：
      ask_user            -> answer (str)
      ask_user_confirm    -> confirmed (bool)
      ask_user_choice     -> choice_index (int)  或 answer（选项文字，模糊匹配）
      goal_negotiation    -> answer (str，/confirm /cancel 或修改意见原文)
      repl_prompt         -> answer (str，任意 slash 命令内部 prompt_user() 的原始输入)
    """
    answer:       Optional[str]  = None
    confirmed:    Optional[bool] = None
    choice_index: Optional[int]  = None

class InteractionResponse(BaseModel):
    ok: bool

class HistoryResponse(BaseModel):
    messages: list[dict]
    count:    int
    # [看板分页改进] total/has_more 用于支持"加载更早消息"分页——
    # total 是该 session 历史消息总条数，has_more 表示本次返回的这页
    # 之前是否还有更早的消息。旧调用方不读这两个字段完全不受影响。
    total:    int  = 0
    has_more: bool = False

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
    # Stage 9 §7.1: 区分发起方，用于晨报分组和 tier 上浮判断
    initiator: str = "user"  # "user" | "scheduled" | "autonomous"

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
    pinned:        bool = False   # 是否已置顶（/session pin，cleanup 时永不删除）

class SessionsListResponse(BaseModel):
    sessions:           list[SessionInfo]
    current_session_id: Optional[str] = None
    count:              int = 0
    # [看板分页改进] total 是 offset/limit 分页之前的 session 总数，
    # 供前端计算总页数；count 语义不变，继续表示"本页实际返回了几条"。
    total:              int = 0

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


# ── Session 批量清理（evolution/session_cleanup.py 的 HTTP 化）─────────────

class SessionCleanupItem(BaseModel):
    """单个 session 的清理判定结果（对应 session_cleanup.CleanupItem）。"""
    session_id:    str
    title:         str
    updated_at:    str
    turns:         int
    action:        str   # "keep" | "skip_pending_extraction" | "delete"
    reason:        str
    extracted_now: bool = False


class SessionCleanupRequest(BaseModel):
    """POST /v1/sessions/cleanup 请求体。

    dry_run 默认 True——看板是点击触发，比 CLI 更容易误触，默认给一个
    "只看会删什么、不真删"的安全默认值；确认无误后前端再带 dry_run=False
    发一次真正执行的请求。
    """
    dry_run:              bool = True
    keep_recent_days:     float = 30
    keep_recent_count:    int = 20
    extract_first:        bool = False
    # 孤儿目录（有目录、没 meta.json，一轮对话未跑完就中断留下的残留）——
    # 默认关闭，用户需要显式勾选看板上的"含孤儿目录"才会扫描/清理，避免
    # 老前端调用这个接口时行为突变。
    include_orphans:      bool = False
    orphan_min_age_hours: float = 6.0


class OrphanCleanupItem(BaseModel):
    """单个孤儿目录（有目录、没 meta.json）的清理判定结果。"""
    dir_name:      str
    last_activity: str
    size_bytes:    int
    action:        str   # "keep" | "delete"
    reason:        str


class SessionCleanupResponse(BaseModel):
    dry_run:                    bool
    total_scanned:               int
    kept_count:                  int
    deleted:                     list[SessionCleanupItem] = []
    skipped_pending_extraction:  list[SessionCleanupItem] = []
    failed:                      list[SessionCleanupItem] = []
    summary:                     str = ""  # 人类可读一句话汇总，直接展示在看板上
    # 孤儿目录清理结果，include_orphans=False 时以下均为空
    orphan_total_scanned:        int = 0
    orphan_kept_count:           int = 0
    orphan_deleted:               list[OrphanCleanupItem] = []
    orphan_failed:                list[OrphanCleanupItem] = []


# ── 受保护文件清单（protected_files_manifest_and_delete_guard_plan.md）────

class ProtectedFileEntry(BaseModel):
    """单条受保护路径（对应 scripts.protected_files.ProtectedEntry）。"""
    path:            str
    is_dir:          bool
    source_manifest: str


class ProtectedFilesStatusResponse(BaseModel):
    """GET /v1/protected-files/status 响应：当前生效清单 + 最近快照概况。"""
    entries:            list[ProtectedFileEntry] = []
    manifest_paths:      list[str] = []   # 当前实际存在的清单文件（顶层 / .agent/），供"编辑哪份"选择
    snapshot_count:      int = 0
    latest_generation_id: str = ""


class ProtectedFileAddRequest(BaseModel):
    """POST /v1/protected-files/entries 请求体：往某份清单文件追加一条声明。

    manifest 不传时默认写入顶层 `protected_files.txt`（不存在则新建）；
    传 "workdir" 时写入 `.agent/protected_files.txt`。
    """
    path:      str
    is_dir:    bool = False
    manifest:  str = "top"   # "top" | "workdir"


class ProtectedFileRemoveRequest(BaseModel):
    """DELETE /v1/protected-files/entries 请求体：从声明它的那份清单文件里
    删掉这一行。只允许删除"清单里声明的一条路径"本身，不允许删除清单
    文件自身这一条（清单文件本身受保护是自动规则，不是用户声明的）。"""
    path: str


class ProtectedFilesBackupRequest(BaseModel):
    """POST /v1/protected-files/backup 请求体：手动触发一次备份。"""
    keep_count: int = 5


class ProtectedFilesBackupResponse(BaseModel):
    generation_id:      str = ""
    backed_up:          list[str] = []
    missing:            list[str] = []
    pruned_generations: list[str] = []
    errors:             list[str] = []


class ProtectedFilesSnapshotSummary(BaseModel):
    generation_id: str
    path_count:    int


class ProtectedFilesSnapshotDetailResponse(BaseModel):
    generation_id: str
    paths:         list[str] = []


class ProtectedFilesRestoreRequest(BaseModel):
    """POST /v1/protected-files/restore 请求体。

    force 默认 False——看板是点击触发，比 CLI 更容易误触；force=False 时
    后端只返回"将要覆盖哪些路径"的预览，不写盘，前端展示确认后再带
    force=True 请求一次真正执行（与 session cleanup 的 dry_run 两段式
    交互同构）。paths 为空列表时代表恢复该快照的全部路径。
    """
    generation_id: str
    paths:         list[str] = []
    force:         bool = False


class ProtectedFilesRestoreResponse(BaseModel):
    restored: list[str] = []
    errors:   list[str] = []
    # force=False 时的预览分支填这个字段，前端据此渲染"将要覆盖以下路径"
    preview:  list[str] = []


# ── 用户管理（daemon 多用户架构 Phase 1）────────────────────────────────────────

class UserInfo(BaseModel):
    """对外展示的用户信息（不含 token_hash 等内部字段）。"""
    user_id:     str
    name:        str
    role:        str
    trust_level: int
    created_at:  float
    last_seen:   float = 0.0
    meta:        dict = Field(default_factory=dict)

class UsersListResponse(BaseModel):
    users: list[UserInfo]

class WhoamiResponse(BaseModel):
    """GET /v1/whoami —— 供 CLI 客户端确认当前 token 对应的身份。"""
    multi_user_enabled: bool
    user_id:     str
    name:        str
    role:        str
    trust_level: int
    is_owner:    bool


class UserCreateRequest(BaseModel):
    name:        str
    role:        str
    trust_level: int = 5
    meta:        Optional[dict] = None

class UserCreateResponse(BaseModel):
    ok:      bool
    user_id: str = ""
    token:   str = ""   # 仅在创建/重置 token 时返回一次明文，之后不可再查
    message: str = ""

class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    meta: Optional[dict] = None

class UserActionResponse(BaseModel):
    ok:      bool
    message: str = ""