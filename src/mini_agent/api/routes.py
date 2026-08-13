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
    GET    /v1/autonomous/gating_history  [scheduling_unification_and_kanban_
                                       visibility_improvement_plan.md P5]
                                       ResourceArbiter 三态门控状态变化时间线
                                       （?limit=50），响应附带 ratio_summary
                                       （近 7 天三态占比，见 kanban_perception_
                                       gaps_improvement_plan.md 方向 C）
    GET    /v1/self/llm_pool_status  [kanban_perception_gaps_improvement_plan.md
                                       方向 B.1] LLMClientPool 故障转移状态
    GET    /v1/self/llm_call_stats   [同上 方向 B.2] 按天聚合的 LLM 调用计数
    GET    /v1/objectives/completion_trend  [同上 方向 D.1] Objective 完成率
                                       每日趋势（快照挂在 /growth/scan 上记录）
    GET    /v1/wiki/quarantine_status  [同上 方向 E] wiki 隔离区积压
    GET    /v1/sentinel/summary      [同上 方向 A] 哨兵聚合面板
    GET    /v1/goal_mode/stuck_stats [goal_stuck_stats_and_llm_progress_judge_
                                       plan.md §1] Goal stuck 历史统计（只读）
    GET    /v1/self/fairness_diagnostics [goal_fairness_scheduling_
                                       diagnostics_plan.md] 调度公平性参数快照
    GET    /v1/goals/similar_confirmed_specs [cross_goal_experience_reuse_
                                       plan.md] 相似历史 Goal 执行规范推荐
    GET    /v1/goals                 GoalBacklog 完整视图（active goals + objectives）
    POST   /v1/goals                 新增 Goal
    PATCH  /v1/goals/{goal_id}       更新 Goal 状态/进度/优先级/标题/描述
    POST   /v1/goals/{goal_id}/feedback  持久化提意见（合入 description，双向同步 cron）
    GET    /v1/goals/{goal_id}/cycle_diagnostics  [goal_cron_cycle_diagnostics_
                                       and_interactive_tuning_plan.md Stage 1]
                                       跨轮次诊断报告：阶段/健康告警/cron 状态/
                                       最近轮次产出/机制说明一次性聚合返回
    POST   /v1/goals/{goal_id}/tuning_proposals  [同上 Stage 2] 生成调优草案
                                       （白名单参数：schedule/priority/
                                       execution_phase/task_template/
                                       regenerate_spec）
    POST   /v1/goals/{goal_id}/tuning_proposals/suggest  规则触发的调优建议
                                       （不含 LLM，命中信号才生成）
    GET    /v1/goals/{goal_id}/tuning_proposals  列出历史草案（含状态）
    POST   /v1/goals/{goal_id}/tuning_proposals/{id}/confirm  确认草案（仍未生效）
    POST   /v1/goals/{goal_id}/tuning_proposals/{id}/apply    应用已确认的草案
    POST   /v1/goals/{goal_id}/tuning_proposals/{id}/reject   拒绝草案，作废
    GET    /v1/self/diagnosis_feedback  自诊断信号闭环 P1-P4 汇总（改进候选清单/
                                         建议采纳率回看/能力快照 diff/skill 有效性）
    GET    /v1/self/goal_fairness    [goal_execution_fairness_improvement_plan.md
                                       P5] 各 active Goal 的调度公平性快照
                                       （last_scheduled_at/aging_boost/effective_priority）
    GET    /v1/self/system_connectivity  [system_connectivity_gaps_and_missing_
                                       capabilities_plan.md P1] F1-F4 四路数据
                                       汇总（决策消费率/失败模式/建议反馈账本/
                                       纠正事件）
    GET    /v1/self/execution_model_status  [daemon_execution_model_and_
                                       scheduler_heartbeat_improvement_plan.md]
                                       目标级持久 Worker / 调度心跳独立化
                                       两个灰度开关的当前生效状态
    GET    /v1/self/scheduling_overview  [goal_cron_unified_scheduler_
                                       improvement_plan.md P4] 一个视图聚合
                                       Goal/普通 cron/goal_cycle 三条执行
                                       通道当前的运行/排队/跳过状态 + 共享
                                       的 ResourceArbiter 仲裁结果
    GET    /v1/self/unified_scheduler_preview  [goal_cron_unified_scheduler_
                                       improvement_plan.md P5 第 1-2 步]
                                       UnifiedTaskScheduler 只读预览：三条
                                       通道 poll_due() 快照 + 跨通道建议
                                       执行顺序，不触发任何实际执行
    GET    /v1/self/config           [kanban_config_management_plan.md] 分类
                                       字段目录状态（agent_config.json）
    PATCH  /v1/self/config           [kanban_config_management_plan.md] 批量
                                       更新 agent_config.json 里的若干字段
    POST   /v1/objectives/{execution_id}/cancel    终止一个正在运行的 Objective 执行
    POST   /v1/objectives/{execution_id}/retry     手动重试当前 step（不等超时）
    POST   /v1/objectives/{execution_id}/steps/{step_index}/reset
                                      [daemon_autonomous_state_recovery_plan.md]
                                      手动把某一步打回 pending 重做（含清空
                                      其之后所有步骤的既有进度）
    POST   /v1/objectives/{execution_id}/steps/{step_index}/edit
                                      [daemon_stability_and_ux_improvement_plan.md
                                      P2-10] 编辑一个已完成 step 的产出并继续，
                                      不重新执行该 step（与 /reset 互补）
    POST   /v1/objectives/{execution_id}/guidance  插一句补充说明，供下次提交时使用
    GET    /v1/objectives/{execution_id}/steps/{step_index}/trace
                                     查看某个 step 实际执行过程（完整 tool_call/
                                     tool_result 序列），而非截断摘要
    GET    /v1/inbox                 全局待办中心：跨 session 聚合权限/交互请求 + 失败 Objective +
                                     外部输入网关 notify_only 告警（type: external_alert）
    POST   /v1/inbox/external_alerts/{alert_id}/ack
                                     标记一条外部输入告警为已处理（不再出现在 /v1/inbox 里）
    GET    /v1/external_input/sources   已配置 source 列表 + 运行时健康度（看板"🔌 外部输入"面板，P6）
    GET    /v1/external_input/policies  policies.yaml 路由规则（只读）
    GET    /v1/external_input/events    最近 external.* 事件流水（不消费游标，仅供人工核对；支持 limit/offset 分页）
    GET    /v1/external_input/alerts    待处理 notify_only 告警（分页，供看板"待处理告警"面板用）
    GET    /v1/notification/watchlist     watchlist.yaml 关注对象列表（只读，P7）
    GET    /v1/notification/report_tiers  report_tiers.yaml + job 运行时状态（只读，P7）
    GET    /v1/notification/dispatch_log?limit=50
                                     NotificationDispatcher 最近发送记录（只读，P7）
    GET    /v1/evolution/proposals   [Track I] 列出 evolve/* 提案分支及风险分级
    POST   /v1/evolution/proposals/{branch}/merge
                                     [Track I] 一键合并提案分支（Body 可选 {"force": bool}，
                                     risk=low 时 force 可省略；risk=high 时必须显式 force=true）
    GET    /v1/evolution/feedback_loop_summary
                                     [外部知识反馈闭环 P1-P5] 一次性汇总候选队列过期巡检/
                                     wiki 利用率/阈值自校准/外部趋势候选/生态定位扫描/
                                     月度战略回顾五个模块的当前状态（只读）
    GET    /v1/hybrid_exec/summary   [hybrid_exec P4] 汇总所有 task_id 的脚本仓库状态
                                     （active 版本/成功率）+ run 统计，供看板展示（只读）
    GET    /v1/cron/jobs             CronScheduler job 列表
    POST   /v1/cron/jobs             添加 cron job
    PUT    /v1/cron/jobs/{id}        修改 job（enable/disable/schedule）
    DELETE /v1/cron/jobs/{id}        删除 job（sys: 系统内置 job 不可删，只能 disable）
    POST   /v1/cron/jobs/{id}/run    立即运行一次
    POST   /v1/cron/jobs/{id}/feedback  持久化提意见（合入 description/task_template/prompt.md）
    GET    /v1/cron/jobs/{id}/workspace       专属执行状态（state/config/最近执行列表）
    GET    /v1/cron/jobs/{id}/prompt          读取用户可编辑的 prompt.md
    PUT    /v1/cron/jobs/{id}/prompt          修改 prompt.md
    GET    /v1/cron/jobs/{id}/runs/{run_id}   某次执行的完整事件流
    POST   /v1/cron/jobs/{id}/reset           把 needs_human_review 状态重置为 idle
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
  Workflow（工作流机制改进计划（P7）一、1.2，供看板集成使用，owner only）
    GET    /v1/workflows                     列出已保存的工作流
    GET    /v1/workflows/{name}               查看 YAML 定义
    POST   /v1/workflows/{name}/preview       dry-run 预览执行计划（不实际执行）
    GET    /v1/workflows/{name}/stats         [P9-1a] 汇总历史执行统计（成功率/各步骤耗时评分重试率/condition命中率）
    POST   /v1/workflows/{name}/run           启动一次执行（前台/后台，语义同 run_workflow 工具）
    GET    /v1/workflow_runs                  列出所有执行记录（?name= 可按工作流名过滤）
    GET    /v1/workflow_runs/{id}             单次执行详情
    GET    /v1/workflow_runs/{id}/events      events.jsonl 增量拉取（?since_line=N）
    POST   /v1/workflow_runs/{id}/pause       请求暂停
    POST   /v1/workflow_runs/{id}/cancel      请求取消
    POST   /v1/workflow_runs/{id}/mark_interrupted 清理孤儿运行（daemon 重启后遗留的假"running"）
    POST   /v1/workflow_runs/{id}/resume      断点续跑（Body 可选 force_rerun_from 做单步编辑续跑）
    POST   /v1/workflow_runs/{id}/approve     批准当前等待审批的 step
    POST   /v1/workflow_runs/{id}/reject      拒绝当前等待审批的 step（Body: {"reason": str}）
    POST   /v1/workflow_runs/{id}/input       向等待 human_input 的 step 送入文本（Body: {"text": str}）
    POST   /v1/workflow_runs/{id}/steps/{step_id}/override
                                              人工编辑已完成 step 的输出（单步编辑续跑，见改进计划二、3.3）
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

    # 当前实际使用的模型名（复用 /models 端点同样的 snapshot() 取法：
    # LLMClientPool 支持故障转移/切换模型，所以要读 active 的那条 entry，
    # 不能直接读配置文件里的第一条，否则切换模型/故障转移后看板会显示错）。
    current_model: Optional[str] = None
    try:
        pool = getattr(bridge.agent, "_client_pool", None) if bridge.agent else None
        if pool is not None:
            for entry in pool.snapshot()["entries"]:
                _, _, _m = entry["label"].partition("/")
                if entry.get("active") and _m:
                    current_model = _m
                    break
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass

    resolved_session_id = getattr(
        request.state, "resolved_session_id",
        getattr(bridge.agent, "session_id", None) if bridge.agent else None,
    )

    # session 存储目录：<project_root>/.agent/sessions/<session_id>/
    session_dir: Optional[str] = None
    project_root_str: Optional[str] = None
    try:
        proj_root = getattr(bridge.agent.cfg, "project_root", None) if bridge.agent else None
        if proj_root is not None:
            project_root_str = str(proj_root)
            if resolved_session_id:
                from mini_agent.storage.paths import AgentPaths
                session_dir = str(AgentPaths(proj_root).sessions_dir / resolved_session_id)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes')
        pass

    # 把粗粒度 state（idle/running/waiting_permission）+ 细粒度 phase
    # （model/tool:<name>，见 bridge.py::AgentBridge._phase）合成一个看板
    # 直接能用的 activity 字段，不用把 phase 的内部格式泄漏到前端去解析。
    _phase = state.get("phase")
    if state["state"] == "idle":
        activity, activity_detail = "waiting_input", None
    elif state["state"] == "waiting_permission":
        activity, activity_detail = "waiting_permission", None
    elif isinstance(_phase, str) and _phase.startswith("tool:"):
        activity, activity_detail = "calling_tool", _phase[len("tool:"):]
    else:
        # running 但 phase 还没来得及打上标签（刚进入 running 的极短窗口）
        # 时兜底按"调用模型"展示，比留空/显示内部字符串更友好。
        activity, activity_detail = "calling_model", None

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
        session_id = resolved_session_id,
        model = current_model,
        session_dir = session_dir,
        project_root = project_root_str,
        activity = activity,
        activity_detail = activity_detail,
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


# ── LLM 故障转移状态（方向 B.1）─────────────────────────────────────────────
# 背景：LLMClientPool.snapshot() / ApiKeyPool.snapshot() 早就实现好了
# （供上面 /models 端点内部取当前模型名用），但 key 级 fail_count/
# cooldown_remaining、entry 级切换状态从未被任何端点返回过——daemon 在
# 后台因为某个 provider 频繁触发限流而不断切 key/切配置时，用户完全没有
# 渠道知道这件事正在发生。这里只是"接上一根已经焊好的线"：把已经在内存里
# 的状态通过只读端点读出来，不新增任何持久化。
@router.get("/self/llm_pool_status")
async def get_self_llm_pool_status(request: Request):
    """GET /v1/self/llm_pool_status — LLMClientPool 当前故障转移状态：
    每条 fallback 配置的 label/是否激活，激活配置下每个 key 的
    fail_count/冷却剩余时间/是否可用。

    返回结构：
    {
      "entries": [{"label": str, "active": bool, "keys": [...]}, ...],
      "current": int,                    # 当前激活的 entry 下标
      "switched_from_preferred": bool,   # current != 0，即已经不在首选配置上
    }

    _client_pool 不存在（没有配置 llm_fallback_chain，或 agent 未就绪）时
    返回全部字段为空/False 的结构，不报错——这是"未启用"，不是"出错"。
    """
    bridge = _bridge(request)
    from mini_agent.perception.sentinel import read_llm_pool_snapshot

    pool = getattr(bridge.agent, "_client_pool", None) if bridge.agent else None
    snap = read_llm_pool_snapshot(pool)
    if snap is None:
        return {"entries": [], "current": 0, "switched_from_preferred": False, "enabled": False}
    return {**snap, "enabled": True}


# ── 调度公平性诊断（goal_fairness_scheduling_diagnostics_plan.md）──────────
@router.get("/self/fairness_diagnostics")
async def get_self_fairness_diagnostics(request: Request):
    """GET /v1/self/fairness_diagnostics — 调度公平性参数只读快照：公平
    轮询/老化加成/时间片抢占当前的配置值 + 每个 active objective 当前的
    priority/aging_boost/effective_priority + 当前因时间片抢占被暂停的
    execution 列表，不修改任何状态，纯观测。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.perception.fairness_diagnostics import fairness_diagnostics_snapshot

        self_agent = http_server.bridge.agent
        cfg = getattr(self_agent, "cfg", None) if self_agent else None

        al = http_server.autonomous_loop
        paths = getattr(al, "_paths", None) if al is not None else None
        if paths is None and cfg is not None and getattr(cfg, "project_root", None) is not None:
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(cfg.project_root)

        goal_backlog = None
        if paths is not None:
            try:
                from mini_agent.perception.goal_backlog import load_goal_backlog
                goal_backlog = load_goal_backlog(paths)
            except Exception:
                goal_backlog = None

        objective_executor = getattr(al, "_objective_executor", None) if al is not None else None
        if objective_executor is None:
            objective_executor = getattr(http_server.bridge, "_objective_executor", None)

        return fairness_diagnostics_snapshot(goal_backlog, objective_executor, cfg)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_fairness_diagnostics')
        from mini_agent.perception.fairness_diagnostics import _empty_snapshot
        return _empty_snapshot()


# ── LLM 调用计数（方向 B.2）─────────────────────────────────────────────────
@router.get("/self/llm_call_stats")
async def get_self_llm_call_stats(request: Request, days: int = Query(7, ge=1, le=90)):
    """GET /v1/self/llm_call_stats?days=7 — 按天聚合的轻量 LLM 调用计数
    （调用次数/成功数/失败数/key 切换数/配置切换数/token 用量/平均耗时），
    数据来源见 `llm/call_stats.py`。默认开启、不含任何请求/响应正文，
    跟需要手动开启的 `LLM_DEBUG=1` 完整调试日志是两套独立的东西。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.llm.call_stats import call_stats_series

        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if project_root is None:
            return {"series": []}
        paths = AgentPaths(project_root)
        return {"series": call_stats_series(paths, days=days)}
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_llm_call_stats')
        return {"series": []}


# ── wiki 隔离区积压（方向 E）─────────────────────────────────────────────────
# 背景：wiki/quarantine.py 的 load_quarantine()/ScanReport 目前是一个完全
# 独立于看板/API 之外的孤岛，只有 cli/commands/quarantine.py 能访问。这类
# "格式损坏/解析失败被隔离的 wiki 页面"如果持续积压，用户除非记得定期敲
# CLI 命令检查，否则永远不会知道。这里只加只读暴露，不新增修复流程
# （修复仍然走 wiki/quarantine_repair.py 描述的 LLM 修复流程 / CLI）。
@router.get("/wiki/quarantine_status")
async def get_wiki_quarantine_status(request: Request):
    """GET /v1/wiki/quarantine_status — wiki 隔离区当前积压情况（不含已修复
    记录）。供看板哨兵面板 quarantine_backlog 一类展示，也可独立调用。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.sentinel import _scan_quarantine_backlog

        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if project_root is None:
            return {"pending_count": 0, "earliest_first_seen_at": None, "items": []}
        paths = AgentPaths(project_root)
        return _scan_quarantine_backlog(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_wiki_quarantine_status')
        return {"pending_count": 0, "earliest_first_seen_at": None, "items": []}


# ── 哨兵聚合面板（kanban_perception_gaps_improvement_plan.md 方向 A）────────
@router.get("/sentinel/summary")
async def get_sentinel_summary(
    request: Request,
    cron_failure_threshold: int = Query(2, ge=1, description="cron 连续失败达到此次数才提醒"),
):
    """GET /v1/sentinel/summary — 聚合"系统状态可能不太对劲，用户大概率
    没注意到"的信号：cron 连续失败、Objective 重试热点、wiki 隔离区积压、
    LLM 故障转移状态、最近 7 天仲裁降级/阻塞占比。

    跟 /v1/inbox（全局待办中心）是姊妹关系但语义不同：inbox 的每一条都有
    明确的下一步操作（批准/拒绝/查看），本端点的很多条目本身不需要用户
    立即做什么，只是"提醒留意"。详见 kanban_perception_gaps_improvement_
    plan.md 方向 A.0。

    全部只读聚合，不修改任何现有状态；单个数据源失败时该类返回空结构，
    不影响其它类别（见 perception/sentinel.py 的失败降级约定）。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.sentinel import sentinel_summary

        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if project_root is None:
            return {
                "generated_at": time.time(), "total_count": 0,
                "cron_jobs_with_failures": [], "stuck_objective_steps": [],
                "quarantine_backlog": {"pending_count": 0, "earliest_first_seen_at": None, "items": []},
                "llm_failover_state": None, "arbitration_recent_ratio": None,
            }
        paths = AgentPaths(project_root)
        client_pool = getattr(self_agent, "_client_pool", None)
        return sentinel_summary(paths, client_pool=client_pool, cron_failure_threshold=cron_failure_threshold)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_sentinel_summary')
        return {
            "generated_at": time.time(), "total_count": 0,
            "cron_jobs_with_failures": [], "stuck_objective_steps": [],
            "quarantine_backlog": {"pending_count": 0, "earliest_first_seen_at": None, "items": []},
            "llm_failover_state": None, "arbitration_recent_ratio": None,
        }


# ── Goal stuck 历史统计（goal_stuck_stats_and_llm_progress_judge_plan.md §1）──
@router.get("/goal_mode/stuck_stats")
async def get_goal_mode_stuck_stats(
    request: Request,
    recent_days: int = Query(30, ge=1, description="最近多少天内的 stuck 记录计入 recent_stuck_count"),
):
    """GET /v1/goal_mode/stuck_stats — 只读聚合 goal_mode 会话历史里被判定
    `stuck`（GoalJudge/StuckDetector 多次恢复无效后的终态）的次数/占比/
    高频目标，为"要不要上并行多路径择优"之类更高成本机制的立项提供真实
    触发频率参考，不修改任何现有状态。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.perception.goal_stuck_stats import stuck_stats_summary

        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        return stuck_stats_summary(project_root, recent_days=recent_days)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_goal_mode_stuck_stats')
        return {
            "total_sessions": 0, "stuck_count": 0, "stuck_ratio": 0.0,
            "recent_stuck_count": 0, "recent_window_days": recent_days,
            "top_stuck_goal_texts": [], "generated_at": time.time(),
        }


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
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.get_diagnostics')
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
async def get_history(
    request: Request,
    limit: int = Query(default=100, le=1000),
    before_seq: Optional[int] = Query(
        default=None,
        description="[看板分页改进] 分页游标：不传表示取最新的一页；"
        "传了表示取下标小于该值的、最近的 limit 条（用于\"加载更早\"）。"
        "这里的 seq 就是 agent.history 里的下标，历史本来就是内存里的一份"
        "有序 list（history_manager.py 的 history 属性），不需要额外存储。",
    ),
):
    bridge = _bridge(request)
    all_msgs: list[dict] = []
    if bridge.agent:
        try:
            all_msgs = [m.model_dump() if hasattr(m, "model_dump") else dict(m)
                        for m in bridge.agent.history]
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_history')
            all_msgs = []

    total = len(all_msgs)
    end = total if before_seq is None else max(0, min(before_seq, total))
    start = max(0, end - limit)
    msgs = all_msgs[start:end]
    has_more = start > 0
    return HistoryResponse(messages=msgs, count=len(msgs), total=total, has_more=has_more)


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
    offset: int = Query(default=0, description="[看板分页改进] 分页偏移量，配合 limit 做标准分页"),
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

        metas, total = user_mgr.list_sessions_page(limit=limit, offset=offset)
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
        # 不会出现在 list_sessions_page() 结果里——从内存里的 SessionEntry 插
        # 一条。[看板分页改进] 只在第一页（offset=0）插入，避免翻页到后面
        # 每一页都多出一条重复的"当前会话"。
        if offset == 0 and current_id and not any(i.id == current_id for i in infos) and pool is not None:
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
                total += 1
        return SessionsListResponse(sessions=infos, current_session_id=current_id, count=len(infos), total=total)

    # ── 单用户模式（单 token） ────────────────────────────────────────────────
    # 传了 session_id 的话，_bridge() 会按它路由到 SessionAgentPool 里这个
    # session 专属的 bridge/Agent，current_id 也就是"这个连接自己的" session，
    # 而不是全局共享 bridge 当前碰巧停留的那个（否则多客户端下 is_current
    # 会全部错误地指向同一个、与自己实际操作的 session 无关的 id）。
    agent, mgr = _session_manager_or_404(_bridge(request, session_id=session_id))
    current_id = agent.session_id

    metas, total = mgr.list_sessions_page(limit=limit, offset=offset)
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
    # 此时不会出现在 list_sessions_page() 结果里 —— 把内存中的"当前会话"插到
    # 列表最前面，确保 Web 端始终能看到并默认选中它。[看板分页改进] 同上，
    # 只在第一页插入。
    if offset == 0 and current_id and not any(i.id == current_id for i in infos):
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
            total += 1

    return SessionsListResponse(sessions=infos, current_session_id=current_id, count=len(infos), total=total)


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
    #
    # [BUGFIX] Self（主自我）会话根本不在 session_pool 里管理（见
    # session_pool.py 模块开头说明），它的审批请求永远登记在
    # app.state.bridge 上。之前这里在 pool 不为 None 时，find 不到就直接
    # 404，完全没有 fallback 到 app.state.bridge 去找——导致 Self 会话、
    # 或任何走"单 token / 无 session_id"路径登记的请求，看板等 HTTP 客户端
    # 能在 /permissions/pending 里看到（那边走 _bridge() 有 fallback），
    # 但一提交回复就 404 "not found or already handled"。
    pool = _session_pool(request)
    entry = None
    if pool is not None:
        entry = pool.find_by_permission_req(req_id)
    if entry is not None:
        user_ctx = getattr(request.state, "user_ctx", None)
        if user_ctx is not None and not user_ctx.is_owner and entry.user_id != user_ctx.user_id:
            raise HTTPException(status_code=403, detail="This permission request does not belong to you")
        bridge = entry.bridge
    else:
        # pool 里没找到：fallback 到默认全局 bridge（Self 会话 / 单 token
        # 无 session_id 场景），而不是直接判定 404。
        default_bridge = getattr(request.app.state, "bridge", None)
        if default_bridge is not None and req_id in default_bridge.permission_gate._pending:
            bridge = default_bridge
        elif pool is not None:
            raise HTTPException(
                status_code=404,
                detail=f"Permission request {req_id!r} not found or already handled",
            )
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
    # [BUGFIX] 见 respond_permission() 同一根因：Self（主自我）会话不在
    # session_pool 里管理，它登记的交互请求只存在于 app.state.bridge 上。
    # 之前 pool 不为 None 时 find 不到就直接 404，漏掉了这个 fallback，
    # 是看板"回复后 404 not found or already handled"的根因。
    pool = _session_pool(request)
    entry = None
    if pool is not None:
        entry = pool.find_by_interaction_req(req_id)
    if entry is not None:
        user_ctx = getattr(request.state, "user_ctx", None)
        if user_ctx is not None and not user_ctx.is_owner and entry.user_id != user_ctx.user_id:
            raise HTTPException(status_code=403, detail="This interaction request does not belong to you")
        bridge = entry.bridge
    else:
        default_bridge = getattr(request.app.state, "bridge", None)
        if default_bridge is not None and req_id in default_bridge.interaction_gate._pending:
            bridge = default_bridge
        elif pool is not None:
            raise HTTPException(
                status_code=404,
                detail=f"Interaction request {req_id!r} not found or already handled",
            )
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

@router.get("/self/diagnosis_feedback")
async def get_self_diagnosis_feedback(request: Request):
    """GET /v1/self/diagnosis_feedback — 汇总"自诊断信号闭环深化"计划
    （next_doc/self_diagnosis_feedback_loop_deepening_plan.md）P1-P4 四路输出，
    供看板一次性拉取展示，避免看板自己解析 activity_digest.jsonl / 各 job 的
    落盘文件格式。全部只读，不触发任何 job 重新运行。

    返回结构：
    {
      "improvement_backlog": {"ran_at": float, "sources_read": [...], "items": [...]},  # P1
      "suggestion_outcome_review": {...} | None,   # P2，activity_digest 最近一条
      "self_model_snapshot_diff": {...} | None,    # P3，activity_digest 最近一条
      "skill_effectiveness": [...],                # P4，最近一条 health_report 里的字段
    }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {
        "improvement_backlog": None,
        "suggestion_outcome_review": None,
        "self_model_snapshot_diff": None,
        "skill_effectiveness": [],
    }
    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.evolution.resource_arbiter import read_activity_digest

        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if project_root is None:
            return result
        paths = AgentPaths(project_root)

        # P1 — improvement_backlog.json，直接落盘的排序候选清单快照。
        try:
            backlog_path = paths.improvement_backlog_path
            if backlog_path.exists():
                result["improvement_backlog"] = json.loads(backlog_path.read_text(encoding="utf-8"))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_diagnosis_feedback.backlog')

        # P2/P3/P4 都写在 activity_digest.jsonl 里，扫最近 500 条找各自最新一条即可
        # （这三种记录都是低频 job 产出，不需要按时间窗口过滤，只要最新状态）。
        try:
            records = read_activity_digest(paths, since_ts=None)[-500:]
            for rec in reversed(records):
                rtype = rec.get("type")
                if rtype == "suggestion_outcome_review" and result["suggestion_outcome_review"] is None:
                    result["suggestion_outcome_review"] = rec
                elif rtype == "self_model_snapshot_diff" and result["self_model_snapshot_diff"] is None:
                    result["self_model_snapshot_diff"] = rec
                elif rtype == "health_report" and not result["skill_effectiveness"]:
                    result["skill_effectiveness"] = rec.get("skill_effectiveness", []) or []
                if (result["suggestion_outcome_review"] is not None
                        and result["self_model_snapshot_diff"] is not None
                        and result["skill_effectiveness"]):
                    break
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_diagnosis_feedback.digest')
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_diagnosis_feedback')

    return result


@router.get("/self/goal_fairness")
async def get_self_goal_fairness(request: Request):
    """GET /v1/self/goal_fairness — [goal_execution_fairness_improvement_plan.md
    P5] 只读汇总每个 active Goal 当前的调度公平性状态，供看板"⚖️ 执行公平性"
    区块展示，避免用户只能靠翻 goals.json/activity_digest.jsonl 猜"哪些 Goal
    最近获得了执行机会、哪些被冷落"。纯读取，不触发调度、不修改任何状态。

    返回结构：
    {
      "strategy": "fair_round_robin" | "priority",   # 当前生效的调度策略
      "goals": [
        {
          "goal_id": str, "title": str, "priority": int,
          "aging_boost": float, "effective_priority": float,
          "last_scheduled_at": float,   # 0 表示从未被调度过
          "last_touched_at": float,
          "objective_count": int,       # 该 Goal 下 active Objective 数
        },
        ...
      ],  # 按 last_scheduled_at 升序（最久没轮到的排最前，与实际调度顺序一致）
    }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {"strategy": "fair_round_robin", "goals": []}
    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.goal_backlog import GoalBacklog, compute_aging_boost

        self_agent = http_server.bridge.agent
        cfg = getattr(self_agent, "cfg", None) if self_agent else None
        project_root = getattr(cfg, "project_root", None) if cfg is not None else None
        if project_root is None:
            return result
        paths = AgentPaths(project_root)
        backlog = GoalBacklog(paths)
        backlog.load()

        autonomy_cfg = getattr(cfg, "autonomy", None)
        strategy = getattr(autonomy_cfg, "goal_scheduling_strategy", "fair_round_robin") \
            if autonomy_cfg is not None else "fair_round_robin"
        stale_days = getattr(cfg, "next_action_stale_days", 7.0)
        boost_per_day = getattr(autonomy_cfg, "fairness_aging_boost_per_day", 1.0) \
            if autonomy_cfg is not None else 1.0
        boost_max_days = getattr(autonomy_cfg, "fairness_aging_boost_max_days", 14.0) \
            if autonomy_cfg is not None else 14.0
        result["strategy"] = strategy

        now = time.time()
        objective_counts: dict[str, int] = {}
        for obj in backlog.active_objectives():
            if obj.parent_id:
                objective_counts[obj.parent_id] = objective_counts.get(obj.parent_id, 0) + 1

        rows = []
        for goal in backlog.active_goals():
            boost = compute_aging_boost(
                goal, now, stale_days=stale_days,
                boost_per_day=boost_per_day, max_boost_days=boost_max_days,
            )
            rows.append({
                "goal_id": goal.id,
                "title": goal.title,
                "priority": goal.priority,
                "aging_boost": round(boost, 2),
                "effective_priority": round(goal.priority + boost, 2),
                "last_scheduled_at": goal.last_scheduled_at,
                "last_touched_at": goal.last_touched_at,
                "objective_count": objective_counts.get(goal.id, 0),
            })
        rows.sort(key=lambda r: r["last_scheduled_at"] or 0.0)
        result["goals"] = rows
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_goal_fairness')

    return result


@router.get("/self/system_connectivity")
async def get_self_system_connectivity(request: Request):
    """GET /v1/self/system_connectivity — [system_connectivity_gaps_and_
    missing_capabilities_plan.md P1] 汇总本方案 F1-F4 四个新模块产出的数据，
    供看板"🧠 自我状态"tab 一次性展示，避免这些数据继续停留在"埋头产生、
    没人看"的状态（方案文档 P1 建议原话）。全部只读，不触发任何 job/聚合
    重新运行。

    返回结构：
    {
      "decision_consumption": {...} | None,     # F1，decision_consumption_rate()
      "failure_patterns": [...],                 # F2，load_failure_patterns()（按频次排序）
      "suggestion_feedback": {category: {...}},  # F3，all_categories()
      "recent_corrections": [...],                # F4，recent_correction_events()
    }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {
        "decision_consumption": None,
        "failure_patterns": [],
        "suggestion_feedback": {},
        "recent_corrections": [],
    }
    try:
        from mini_agent.storage.paths import AgentPaths

        self_agent = http_server.bridge.agent
        cfg = getattr(self_agent, "cfg", None) if self_agent else None
        project_root = getattr(cfg, "project_root", None) if cfg is not None else None
        if project_root is None:
            return result
        paths = AgentPaths(project_root)

        try:
            from mini_agent.wiki.decision_consumption import decision_consumption_rate
            result["decision_consumption"] = decision_consumption_rate(paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_system_connectivity.decision_consumption')

        try:
            from mini_agent.evolution.failure_pattern_store import load_failure_patterns
            result["failure_patterns"] = load_failure_patterns(paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_system_connectivity.failure_patterns')

        try:
            from mini_agent.evolution.suggestion_feedback_ledger import all_categories
            result["suggestion_feedback"] = {
                category: entry.to_dict() for category, entry in all_categories(paths).items()
            }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_system_connectivity.suggestion_feedback')

        try:
            from mini_agent.wiki.correction_writer import recent_correction_events
            result["recent_corrections"] = recent_correction_events(paths, limit=20)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_system_connectivity.recent_corrections')
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_system_connectivity')

    return result


@router.get("/self/execution_model_status")
async def get_self_execution_model_status(request: Request):
    """GET /v1/self/execution_model_status —
    [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md]
    只读汇总"目标级持久 Worker"（阶段一）和"调度心跳独立化"（阶段二）
    两个默认关闭的灰度开关当前的生效状态，供看板"⚙️ 执行模型"区块展示，
    避免这两个开关"开没开、起没起作用"只能靠翻配置文件/看进程猜。纯读取，
    不修改任何状态、不触发任何调度。

    返回结构：
    {
      "objective_execution_mode": "persistent" | "isolated" | "shared_queue",
      "persistent_worker": {
        "enabled": bool,
        "active_execution_count": int,
        "active_execution_ids": [str, ...],
        "idle_ttl_seconds": float,
        "discarded_worker_count": int,   # [阶段三] 累计丢弃过的专属线程池
                                          # 次数（含正常终止收尾 + 卡死回收，
                                          # 不特指后者；见该字段来源
                                          # ObjectivePersistentRunner.
                                          # discarded_worker_count 的说明）
      },
      "isolated_runner": {
        "enabled": bool, "max_workers": int,
        "pool_rebuild_count": int,  # [阶段四] 共享线程池被整体重建的次数
        "stale_turn_count": int,    # [阶段四] 累计检测到的卡死 turn 数
      },
      "scheduler_heartbeat": {
        "enabled": bool,
        "alive": bool,             # 心跳线程是否仍在运行
        "poll_interval_seconds": float,
        "tick_interval_seconds": float,   # 对照用：AutonomousLoop 自己的 tick 周期
        "last_tick_started_at": float,    # [阶段二] 0.0 表示尚未发生过
        "last_tick_finished_at": float,   # [阶段二] 判断心跳假死的关键字段：
                                           # now - last_tick_finished_at 长期
                                           # 远大于 tick_interval_seconds，
                                           # 但 alive 仍为 True，说明心跳线程
                                           # 卡在某次 tick() 里没有返回。
        "last_tick_duration_seconds": float,  # [阶段二] 上一次 tick() 耗时
        "suspected_stuck": bool,  # [goal_cron_unified_scheduler_improvement_
                                   # plan.md P3] 看门狗当前是否怀疑心跳线程
                                   # 卡在某次未返回的 tick() 里（alive=True
                                   # 但已经很久没有产生新的 tick）。
      },
      "cron": {
        "reaped_job_count": int,   # [阶段一/三] CronJobRunner 累计强制
                                    # 回收过的卡死 job 次数
        "arbiter_skipped_count": int,   # [P3] 累计因 ResourceArbiter
                                          # 仲裁未通过被跳过触发的次数
      },
      "objective_executor": {
        "stale_step_reap_count": int,   # [阶段三] ObjectiveExecutor 累计
                                          # 强制回收过的卡死 step 次数
      },
      "recent_recoveries": [            # [kanban_execution_visibility_and_
                                          # control_plan.md 阶段 B] 最近发生
                                          # 过的卡死回收事件（进程内环形
                                          # 缓冲，最多 50 条，按时间倒序），
                                          # 不持久化，daemon 重启后清空。
        {"time": float, "kind": "cron_job" | "objective_step" | "isolated_pool",
         "id": str, "detail": str},
      ],
    }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {
        "objective_execution_mode": "shared_queue",
        "persistent_worker": {"enabled": False, "active_execution_count": 0,
                               "active_execution_ids": [], "idle_ttl_seconds": 0.0,
                               "discarded_worker_count": 0},
        "isolated_runner": {"enabled": False, "max_workers": 0,
                            "pool_rebuild_count": 0, "stale_turn_count": 0},
        "scheduler_heartbeat": {"enabled": False, "alive": False,
                                 "poll_interval_seconds": 0.0, "tick_interval_seconds": 0.0,
                                 "last_tick_started_at": 0.0, "last_tick_finished_at": 0.0,
                                 "last_tick_duration_seconds": 0.0, "suspected_stuck": False},
        "cron": {"reaped_job_count": 0},
        "objective_executor": {"stale_step_reap_count": 0},
        "recent_recoveries": [],
    }
    try:
        self_agent = http_server.bridge.agent
        cfg = getattr(self_agent, "cfg", None) if self_agent else None
        autonomy_cfg = getattr(cfg, "autonomy", None) if cfg is not None else None

        persistent_runner = getattr(http_server, "_objective_persistent_runner", None)
        isolated_runner = getattr(http_server, "_objective_isolated_runner", None)
        heartbeat = getattr(http_server, "_scheduler_heartbeat", None)

        if persistent_runner is not None:
            result["objective_execution_mode"] = "persistent"
            active_ids = persistent_runner.active_execution_ids()
            result["persistent_worker"] = {
                "enabled": True,
                "active_execution_count": len(active_ids),
                "active_execution_ids": active_ids,
                "idle_ttl_seconds": getattr(
                    autonomy_cfg, "objective_persistent_worker_idle_ttl_seconds", 1800.0
                ) if autonomy_cfg is not None else 1800.0,
                "discarded_worker_count": getattr(persistent_runner, "discarded_worker_count", 0),
            }
        elif isolated_runner is not None:
            result["objective_execution_mode"] = "isolated"
            result["isolated_runner"] = {
                "enabled": True,
                "max_workers": getattr(autonomy_cfg, "objective_isolated_max_workers", 4)
                if autonomy_cfg is not None else 4,
                "pool_rebuild_count": getattr(isolated_runner, "pool_rebuild_count", 0),
                "stale_turn_count": getattr(isolated_runner, "stale_turn_count", 0),
            }

        autonomous_loop = getattr(http_server, "_autonomous_loop", None)
        tick_interval_seconds = 60.0
        if autonomous_loop is not None:
            try:
                tick_interval_seconds = autonomous_loop.get_digest_status().get(
                    "tick_interval_seconds", 60.0
                )
            except Exception:
                pass

        # [P3] 顺带把最新的 tick_interval_seconds 刷新给看门狗——tick_interval
        # 可能在心跳线程构造之后被灰度调整，这里只是更新一个纯内存阈值，
        # 失败不影响本端点其余字段正常返回。
        if heartbeat is not None:
            try:
                heartbeat.set_tick_interval_seconds(tick_interval_seconds)
            except Exception:
                pass

        result["scheduler_heartbeat"] = {
            "enabled": heartbeat is not None,
            "alive": bool(heartbeat.is_alive()) if heartbeat is not None else False,
            "poll_interval_seconds": getattr(
                autonomy_cfg, "scheduler_heartbeat_poll_interval_seconds", 5.0
            ) if autonomy_cfg is not None else 5.0,
            "tick_interval_seconds": tick_interval_seconds,
            "last_tick_started_at": getattr(heartbeat, "last_tick_started_at", 0.0) if heartbeat is not None else 0.0,
            "last_tick_finished_at": getattr(heartbeat, "last_tick_finished_at", 0.0) if heartbeat is not None else 0.0,
            "last_tick_duration_seconds": getattr(heartbeat, "last_tick_duration_seconds", 0.0) if heartbeat is not None else 0.0,
            # [goal_cron_unified_scheduler_improvement_plan.md P3]
            "suspected_stuck": bool(getattr(heartbeat, "suspected_stuck", False)) if heartbeat is not None else False,
        }

        # [阶段一/三] cron watchdog 回收计数——job_runner 未注入（旧路径）
        # 时 CronScheduler 不存在或没有 reap 相关状态，getattr 链路保持
        # 0 的默认值。cron_scheduler/objective_executor 挂在 bridge 上
        # （见 HttpServer._build_autonomous_loop() 接线），不是 http_server
        # 本身的属性。
        cron_scheduler = getattr(http_server.bridge, "_cron_scheduler", None)
        job_runner = getattr(cron_scheduler, "_job_runner", None) if cron_scheduler is not None else None
        result["cron"] = {
            "reaped_job_count": getattr(job_runner, "reaped_job_count", 0) if job_runner is not None else 0,
            # [scheduling_unification_and_kanban_visibility_improvement_plan.md
            # P3] 因 ResourceArbiter 仲裁未通过而被跳过本次触发的次数，
            # 与 reaped_job_count 同属"cron 通道健康度"观测指标。
            "arbiter_skipped_count": getattr(job_runner, "arbiter_skipped_count", 0) if job_runner is not None else 0,
        }

        # [阶段三] ObjectiveExecutor 卡死 step 回收计数。
        objective_executor = getattr(http_server.bridge, "_objective_executor", None)
        result["objective_executor"] = {
            "stale_step_reap_count": getattr(objective_executor, "stale_step_reap_count", 0)
            if objective_executor is not None else 0,
        }

        # [kanban_execution_visibility_and_control_plan.md 阶段 B] 汇总
        # 最近发生过的卡死回收事件，供看板"📋 执行总览"的"🔴 异常/已回收"
        # 栏目直接渲染，而不是只有几个孤立的累计数字。
        try:
            from mini_agent.evolution.recovery_event_log import recent_recovery_events
            result["recent_recoveries"] = recent_recovery_events()
        except Exception:
            result["recent_recoveries"] = []
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_execution_model_status')

    return result


@router.get("/self/scheduling_overview")
async def get_self_scheduling_overview(request: Request):
    """GET /v1/self/scheduling_overview —
    [goal_cron_unified_scheduler_improvement_plan.md P4]
    只读聚合视图：Goal / 普通 cron / goal_cycle 三条执行通道当前各自的
    运行/排队/跳过状态，以及三者共享的 ResourceArbiter 仲裁结果。取代
    此前需要在 autonomous_status / execution_model_status / goal_fairness
    / cron 面板之间来回切换才能拼出的全貌。纯读取，不修改任何状态、不
    触发任何调度；任一子系统数据缺失时对应字段返回占位默认值，不影响
    其它字段正常返回（沿用项目一贯的"非核心信息降级不影响主链路"风格）。

    返回结构：
    {
      "gating": {"state": "full"|"degraded"|"blocked", "reason": str},
      "scheduling_mode": {   # [调度模式可见性改进] 当前生效的调度机制配置，
                              # 解决"到底是哪种调度模式在生效"只能翻配置文件
                              # 才知道的问题
        "unified_arbitration_enabled": bool,  # degraded 槽位是否由
                                                # UnifiedTaskScheduler 按
                                                # channel_weights 统一裁决
        "adaptive_concurrency_enabled": bool,  # Goal 通道并发上限是否按
                                                 # 近期失败率/耗时自适应收紧
        "resource_gating_degraded_enabled": bool,  # degraded 是否会收紧
                                                      # 并发（False 时 degraded
                                                      # 只是提示，不收并发）
        "channel_weights": {"goal": float, "cron": float} | None,  # 仅
                              # unified_arbitration_enabled=True 时有意义
        "degraded_allocation": {"goal": int, "cron": int} | None,  # 当前
                              # gating.state=="degraded" 且 unified_arbitration
                              # 开启时，两条通道各自分到的并发槽位；其它
                              # 情况下为 None（不适用）
      },
      "usage_breakdown": {   # [P1] 三类消耗的分项数字 + 当日预算上限
        "daily_token_budget": int, "used_today": int,
        "used_today_goals": int, "used_today_cron": int,
        "used_today_exploration": int,
      },
      "goal_channel": {
        "objective_slots": {"running": int, "max": int, "static_cap": int} | None,
        "queue_head_goal": {  # 公平排序队首（最久没轮到的 Goal），None 表示
                               # 没有 active Goal
          "goal_id": str, "title": str, "last_scheduled_at": float,
        } | None,
      },
      "cron_channel": {   # 不含 goal_cycle 通道的 job（run_mode 区分）
        "running": int, "queued": int,
        "max_concurrent": int | None,   # 当前生效的并发上限（effective_
                                          # max_concurrent()，degraded 时会
                                          # 比 static_max_concurrent 更低）
        "static_max_concurrent": int | None,  # full 状态下的并发天花板
                                                # （构造时传入的 max_concurrent）
        "arbiter_skipped_count": int,   # [P1] 进程内累计
        "jobs_over_skip_threshold": [   # [P2] consecutive_skip_count 达到
                                          # cron.skip_alert_threshold 的 job
          {"job_id": str, "name": str, "consecutive_skip_count": int},
        ],
      },
      "goal_cycle_channel": {
        "total_count": int,        # run_mode="goal_cycle" 的 job 总数
        "pending_due_count": int,  # 其中已到期（next_run_at <= now）待触发的数量
        "recent": [                # 按 last_run_at 倒序，最多 5 条
          {"job_id": str, "goal_title": str, "last_run_at": float,
           "run_count": int, "consecutive_skip_count": int},
        ],
      },
    }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {
        "gating": None,
        "scheduling_mode": {
            "unified_arbitration_enabled": False,
            "adaptive_concurrency_enabled": False,
            "resource_gating_degraded_enabled": True,
            "channel_weights": None,
            "degraded_allocation": None,
        },
        "usage_breakdown": None,
        "goal_channel": {"objective_slots": None, "queue_head_goal": None},
        "cron_channel": {"running": 0, "queued": 0, "max_concurrent": None,
                          "static_max_concurrent": None, "arbiter_skipped_count": 0,
                          "jobs_over_skip_threshold": []},
        "goal_cycle_channel": {"total_count": 0, "pending_due_count": 0, "recent": []},
    }

    al = http_server.autonomous_loop
    paths = getattr(al, "_paths", None) if al is not None else None
    cfg = getattr(al, "_cfg", None) if al is not None else None
    if paths is None or cfg is None:
        try:
            from mini_agent.storage.paths import AgentPaths
            self_agent = http_server.bridge.agent
            _cfg = getattr(self_agent, "cfg", None) if self_agent else None
            if _cfg is not None and getattr(_cfg, "project_root", None) is not None:
                paths = AgentPaths(_cfg.project_root)
                cfg = _cfg
        except Exception:
            pass

    # ── 共享仲裁结果 + P1 分项消耗 ──────────────────────────────────────
    if paths is not None and cfg is not None:
        try:
            from mini_agent.evolution.resource_arbiter import ResourceArbiter
            result["gating"] = ResourceArbiter(paths, cfg).gating_state()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.gating')

        # ── 调度模式（哪些开关生效、degraded 时槽位怎么分）──────────────────
        try:
            autonomy_cfg = getattr(cfg, "autonomy", None)
            scheduler_cfg = getattr(cfg, "scheduler", None)
            cron_cfg = getattr(cfg, "cron", None)
            unified_enabled = bool(getattr(scheduler_cfg, "unified_arbitration_enabled", False)) if scheduler_cfg is not None else False
            weights = (getattr(scheduler_cfg, "channel_weights", None) or {}) if scheduler_cfg is not None else {}
            goal_weight = weights.get("goal", 1.0)
            cron_weight = weights.get("cron", 1.0)
            result["scheduling_mode"] = {
                "unified_arbitration_enabled": unified_enabled,
                "adaptive_concurrency_enabled": bool(getattr(autonomy_cfg, "adaptive_concurrency_enabled", False)) if autonomy_cfg is not None else False,
                "resource_gating_degraded_enabled": bool(getattr(autonomy_cfg, "resource_gating_degraded_enabled", True)) if autonomy_cfg is not None else True,
                "channel_weights": {"goal": goal_weight, "cron": cron_weight} if unified_enabled else None,
                "degraded_allocation": None,
            }
            gating_state = (result["gating"] or {}).get("state")
            if unified_enabled and gating_state == "degraded":
                try:
                    from mini_agent.evolution.unified_task_scheduler import allocate_weighted_slots
                    reserved_min_cron = getattr(cron_cfg, "reserved_min_concurrent", 1) if cron_cfg is not None else 1
                    total_slots = getattr(scheduler_cfg, "degraded_total_slots", 2) if scheduler_cfg is not None else 2
                    allocation = allocate_weighted_slots(
                        total_slots,
                        {"goal": goal_weight, "cron": cron_weight},
                        reserved_min={"cron": reserved_min_cron},
                    )
                    result["scheduling_mode"]["degraded_allocation"] = allocation
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.degraded_allocation')
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.scheduling_mode')

        try:
            from mini_agent.perception.global_knowledge import ensure_self_profile
            rb = ensure_self_profile(paths).resource_budget
            result["usage_breakdown"] = {
                "daily_token_budget": rb.daily_token_budget,
                "used_today": rb.used_today,
                "used_today_goals": rb.used_today_goals,
                "used_today_cron": rb.used_today_cron,
                "used_today_exploration": rb.used_today_exploration,
            }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.usage_breakdown')

    # ── Goal 通道 ────────────────────────────────────────────────────────
    oe = getattr(al, "_objective_executor", None) if al is not None else None
    if oe is None:
        oe = getattr(http_server.bridge, "_objective_executor", None)
    if oe is not None:
        try:
            from mini_agent.evolution.objective_executor import MAX_CONCURRENT_OBJECTIVES
            try:
                effective_max = oe.effective_max_concurrent()
            except Exception:
                effective_max = MAX_CONCURRENT_OBJECTIVES
            result["goal_channel"]["objective_slots"] = {
                "running": oe.running_count(),
                "max": effective_max,
                "static_cap": MAX_CONCURRENT_OBJECTIVES,
            }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.objective_slots')

    if paths is not None:
        try:
            from mini_agent.perception.goal_backlog import GoalBacklog
            backlog = GoalBacklog(paths)
            backlog.load()
            active = list(backlog.active_goals())
            if active:
                head = min(active, key=lambda g: g.last_scheduled_at or 0.0)
                result["goal_channel"]["queue_head_goal"] = {
                    "goal_id": head.id,
                    "title": head.title,
                    "last_scheduled_at": head.last_scheduled_at,
                }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.queue_head_goal')

    # ── 普通 cron 通道 + goal_cycle 通道（同一份 job 列表，按 run_mode 区分）──
    cs = getattr(al, "_cron_scheduler", None) if al is not None else None
    if cs is None:
        cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is not None:
        try:
            job_runner = getattr(cs, "_job_runner", None)
            base_cfg = getattr(job_runner, "_base_cfg", None) if job_runner is not None else None
            cron_cfg = getattr(base_cfg, "cron", None) if base_cfg is not None else None
            skip_threshold = getattr(cron_cfg, "skip_alert_threshold", 5) if cron_cfg is not None else 5

            now = time.time()
            jobs = cs.list_jobs()
            cron_running = 0
            cron_queued = 0
            over_threshold = []
            goal_cycle_jobs = []
            for j in jobs:
                run_mode = getattr(j, "run_mode", "message")
                if run_mode == "goal_cycle":
                    goal_cycle_jobs.append(j)
                    continue
                try:
                    phase = cs.execution_phase(j.id)
                except Exception:
                    phase = "not_running"
                if phase == "running":
                    cron_running += 1
                elif phase == "queued":
                    cron_queued += 1
                if skip_threshold > 0 and getattr(j, "consecutive_skip_count", 0) >= skip_threshold:
                    over_threshold.append({
                        "job_id": j.id, "name": j.name,
                        "consecutive_skip_count": j.consecutive_skip_count,
                    })

            max_concurrent = None
            static_max_concurrent = None
            if job_runner is not None:
                try:
                    max_concurrent = job_runner.effective_max_concurrent()
                except Exception:
                    max_concurrent = None
                static_max_concurrent = getattr(job_runner, "_max_concurrent", None)

            result["cron_channel"] = {
                "running": cron_running,
                "queued": cron_queued,
                "max_concurrent": max_concurrent,
                "static_max_concurrent": static_max_concurrent,
                "arbiter_skipped_count": getattr(job_runner, "arbiter_skipped_count", 0)
                if job_runner is not None else 0,
                "jobs_over_skip_threshold": over_threshold,
            }

            goal_cycle_jobs.sort(key=lambda j: j.last_run_at or 0.0, reverse=True)
            result["goal_cycle_channel"] = {
                "total_count": len(goal_cycle_jobs),
                "pending_due_count": sum(
                    1 for j in goal_cycle_jobs if j.enabled and (j.next_run_at or 0.0) <= now
                ),
                "recent": [
                    {
                        "job_id": j.id,
                        "goal_title": j.name,
                        "last_run_at": j.last_run_at,
                        "run_count": j.run_count,
                        "consecutive_skip_count": getattr(j, "consecutive_skip_count", 0),
                    }
                    for j in goal_cycle_jobs[:5]
                ],
            }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_scheduling_overview.cron')

    return result



@router.get("/self/unified_scheduler_preview")
async def get_self_unified_scheduler_preview(request: Request):
    """GET /v1/self/unified_scheduler_preview —
    [goal_cron_unified_scheduler_improvement_plan.md P5 第 1-2 步]
    `UnifiedTaskScheduler`（`mini_agent.evolution.unified_task_scheduler`）
    的只读预览端点：三条通道各自 `poll_due()` 的原始快照 + 一份"建议执行
    顺序"（`suggest_order()`，默认权重全 1.0，不偏向任何通道）。

    与 `GET /v1/self/scheduling_overview`（P4）的区别：P4 展示的是"运行中/
    排队中/跳过次数"这类聚合计数，本端点展示的是"如果现在要决定谁先执行，
    统一调度层会给出什么建议"——是 P5 后续步骤（接管仲裁裁决/实际派发）
    的预览，**本端点不触发、不影响任何实际执行**，纯读取。

    返回结构：
    {
      "channels": {
        "goal": [{"source","task_id","title","priority","due_at","resource_estimate","extra"}, ...],
        "cron": [...],
        "goal_cycle": [...],
      },
      "suggested_order": [ 同上字段的任务列表，跨通道合并排序 ],
      "slot_allocation": {
        "unified_arbitration_enabled": bool,
        "degraded_total_slots": int,
        "channel_weights": {"goal": float, "cron": float},
        "reserved_min_cron": int,
        "allocation": {"goal": int, "cron": int},
      },
    }

    `slot_allocation`（[P5 第 3 步]）展示的是"如果当前处于 degraded 状态，
    `allocate_weighted_slots()` 会给两条通道分配多少并发槽位"——按当前
    配置（`scheduler.channel_weights`/`degraded_total_slots`/
    `cron.reserved_min_concurrent`）计算，与 `unified_arbitration_enabled`
    是否真正开启无关（开关关闭时这里仍展示"如果开启会怎样"，方便在
    正式打开开关前先观察计算结果是否符合预期，与 P5 第 1-2 步"先上线
    观察排序结果"的思路一致）。**本字段仍是纯展示**——是否真的按这份
    分配裁决，取决于 `unified_arbitration_enabled` 开关本身，本端点不
    修改任何配置、不触发任何实际派发。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    result: dict = {
        "channels": {"goal": [], "cron": [], "goal_cycle": []},
        "suggested_order": [],
        "slot_allocation": {
            "unified_arbitration_enabled": False,
            "degraded_total_slots": 0,
            "channel_weights": {},
            "reserved_min_cron": 0,
            "allocation": {},
        },
    }

    al = http_server.autonomous_loop
    paths = getattr(al, "_paths", None) if al is not None else None
    if paths is None:
        try:
            from mini_agent.storage.paths import AgentPaths
            self_agent = http_server.bridge.agent
            _cfg = getattr(self_agent, "cfg", None) if self_agent else None
            if _cfg is not None and getattr(_cfg, "project_root", None) is not None:
                paths = AgentPaths(_cfg.project_root)
        except Exception:
            pass

    goal_backlog = None
    if paths is not None:
        try:
            from mini_agent.perception.goal_backlog import load_goal_backlog
            goal_backlog = load_goal_backlog(paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_unified_scheduler_preview.goal_backlog')

    cron_scheduler = getattr(al, "_cron_scheduler", None) if al is not None else None
    if cron_scheduler is None:
        cron_scheduler = getattr(http_server.bridge, "_cron_scheduler", None)

    try:
        from mini_agent.evolution.unified_task_scheduler import allocate_weighted_slots
        _cfg_for_alloc = getattr(http_server.bridge.agent, "cfg", None) if http_server.bridge.agent else None
        scheduler_cfg = getattr(_cfg_for_alloc, "scheduler", None) if _cfg_for_alloc is not None else None
        cron_cfg = getattr(_cfg_for_alloc, "cron", None) if _cfg_for_alloc is not None else None
        total_slots = getattr(scheduler_cfg, "degraded_total_slots", 2) if scheduler_cfg is not None else 2
        weights = (getattr(scheduler_cfg, "channel_weights", None) or {}) if scheduler_cfg is not None else {}
        reserved_min_cron = getattr(cron_cfg, "reserved_min_concurrent", 1) if cron_cfg is not None else 1
        goal_weight = weights.get("goal", 1.0)
        cron_weight = weights.get("cron", 1.0)
        allocation = allocate_weighted_slots(
            total_slots,
            {"goal": goal_weight, "cron": cron_weight},
            reserved_min={"cron": reserved_min_cron},
        )
        result["slot_allocation"] = {
            "unified_arbitration_enabled": bool(getattr(scheduler_cfg, "unified_arbitration_enabled", False)),
            "degraded_total_slots": total_slots,
            "channel_weights": {"goal": goal_weight, "cron": cron_weight},
            "reserved_min_cron": reserved_min_cron,
            "allocation": allocation,
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_unified_scheduler_preview.slot_allocation')

    try:
        from mini_agent.evolution.unified_task_scheduler import build_default_scheduler
        scheduler = build_default_scheduler(goal_backlog=goal_backlog, cron_scheduler=cron_scheduler, paths=paths)
        by_channel = scheduler.poll_all()
        for name, tasks in by_channel.items():
            result["channels"][name] = [
                {
                    "source": t.source, "task_id": t.task_id, "title": t.title,
                    "priority": t.priority, "due_at": t.due_at,
                    "resource_estimate": t.resource_estimate, "extra": t.extra,
                }
                for t in tasks
            ]
        result["suggested_order"] = [
            {
                "source": t.source, "task_id": t.task_id, "title": t.title,
                "priority": t.priority, "due_at": t.due_at,
                "resource_estimate": t.resource_estimate, "extra": t.extra,
            }
            for t in scheduler.suggest_order()
        ]
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_self_unified_scheduler_preview.scheduler')

    return result


@router.post("/self/execution_model/force_reap")
async def post_self_execution_model_force_reap(request: Request):
    """POST /v1/self/execution_model/force_reap —
    [next_doc/kanban_execution_visibility_and_control_plan.md 阶段 B]
    看板"🚨 立即回收"按钮：不必等 watchdog 下一次 tick，立刻对指定链路
    跑一次卡死回收扫描。body 可选 `{"target": "cron" | "objective_step"
    | "isolated_pool" | "all"}`，默认 "all"。

    注意：这不是"无视阈值强制回收正在正常运行的任务"——cron/
    objective_step 两条链路仍然按各自配置的超时阈值判定，只是"现在立刻
    跑一次扫描"而不是等下一次 tick；isolated_pool 是共享池的整体事件，
    传 force=True 跳过超时判定，直接按当前 in-flight 数量判断是否需要
    重建，因为它本身就是"看板管理员怀疑池子卡死、想立刻处理"这个语义。

    返回 `{"reaped": {"cron_job": [...], "objective_step": [...],
    "isolated_pool": {"stale_turn_ids": [...], "rebuilt": bool}}}`
    （未涉及/未启用的链路对应字段为空列表或 rebuilt=False）。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    target = "all"
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("target"):
            target = str(body["target"])
    except Exception:
        pass  # 允许空 body，按默认 target="all" 处理

    result: dict = {"reaped": {"cron_job": [], "objective_step": [], "isolated_pool": {"stale_turn_ids": [], "rebuilt": False}}}
    try:
        if target in ("all", "cron"):
            cron_scheduler = getattr(http_server.bridge, "_cron_scheduler", None)
            if cron_scheduler is not None:
                try:
                    result["reaped"]["cron_job"] = cron_scheduler.reap_stale_jobs()
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.routes.force_reap.cron')

        if target in ("all", "objective_step"):
            objective_executor = getattr(http_server.bridge, "_objective_executor", None)
            if objective_executor is not None:
                try:
                    result["reaped"]["objective_step"] = objective_executor.reap_stale_steps()
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.routes.force_reap.objective_step')

        if target in ("all", "isolated_pool"):
            isolated_runner = getattr(http_server, "_objective_isolated_runner", None)
            if isolated_runner is not None:
                try:
                    result["reaped"]["isolated_pool"] = isolated_runner.check_health(force=True)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.routes.force_reap.isolated_pool')
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.post_self_execution_model_force_reap')

    return result



@router.get("/self/config")
async def get_self_config(request: Request):
    """GET /v1/self/config — [kanban_config_management_plan.md] 只读返回
    agent_config.json 的分类字段目录状态（每个字段：分类归属、当前生效值、
    默认值、是否被显式配置过、是否敏感），供看板"⚙️ 配置"tab 展示。纯读取，
    不修改任何文件。

    返回结构：
    {
      "config_path": str,       # agent_config.json 的绝对路径
      "categories": [
        {"id": str, "label": str, "icon": str,
         "fields": [{"json_key", "label", "type", "value", "default",
                     "customized", "sensitive"}, ...]},
        ...
      ]
    }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    from mini_agent.config import config_catalog as _cc
    from mini_agent.config.loader import _load_config_file

    self_agent = http_server.bridge.agent
    cfg = getattr(self_agent, "cfg", None) if self_agent else None
    project_root = getattr(cfg, "project_root", None) if cfg is not None else None
    if cfg is None or project_root is None:
        raise HTTPException(status_code=503, detail="config not available")

    config_path = project_root / "agent_config.json"
    raw_file_cfg = _load_config_file(config_path) if config_path.exists() else {}
    categories = _cc.build_status(cfg, raw_file_cfg)
    return {"config_path": str(config_path), "categories": categories}


@router.patch("/self/config")
async def patch_self_config(request: Request):
    """PATCH /v1/self/config — [kanban_config_management_plan.md] 批量更新
    agent_config.json 里的若干字段。

    Body: {"updates": [{"json_key": str, "value": Any}, ...]}

    只接受配置字段目录（config_catalog.KNOWN_FIELDS）里已收录、且非敏感的
    json_key；出现任何一条不认识/敏感的 key，整批全部拒绝、不写入文件（要么
    全部生效要么都不生效，不产生"部分生效"的中间状态）。写入用临时文件 +
    os.replace 原子替换，避免写到一半被打断导致 agent_config.json 损坏。

    注意：多数配置项需要重启 agent 进程才会生效（AppConfig 目前是进程启动
    时一次性加载，本接口不做热加载），响应里通过 "restart_required": true
    固定提示，具体判断交给前端提示文案。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    body = await request.json()
    updates = body.get("updates") or []
    if not isinstance(updates, list) or not updates:
        raise HTTPException(status_code=400, detail="updates 不能为空")

    from mini_agent.config import config_catalog as _cc
    from mini_agent.config.loader import _load_config_file

    self_agent = http_server.bridge.agent
    cfg = getattr(self_agent, "cfg", None) if self_agent else None
    project_root = getattr(cfg, "project_root", None) if cfg is not None else None
    if cfg is None or project_root is None:
        raise HTTPException(status_code=503, detail="config not available")

    config_path = project_root / "agent_config.json"
    raw_file_cfg = _load_config_file(config_path) if config_path.exists() else {}

    try:
        new_raw = _cc.apply_updates(raw_file_cfg, updates)
    except _cc.ConfigUpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        _cc.write_config_file(config_path, new_raw)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.patch_self_config')
        raise HTTPException(status_code=500, detail=f"写入配置文件失败：{_mini_agent_exc}")

    categories = _cc.build_status(cfg, new_raw)
    # build_status() 的 "value" 字段读的是当前进程里正在跑的 cfg（内存态），
    # PATCH 只改了磁盘文件、没有做热加载，两者这时候是不一致的——直接展示
    # 内存态旧值会让人以为"写了但没生效"。这里用本次提交的值覆盖一下这些
    # 字段的展示值（只影响响应展示，不影响实际生效时机，生效仍然要等重启，
    # 见 restart_required 提示）。
    submitted = {u["json_key"]: u.get("value") for u in updates if "json_key" in u}
    for cat in categories:
        for field_row in cat["fields"]:
            if field_row["json_key"] in submitted and not field_row["sensitive"]:
                field_row["value"] = submitted[field_row["json_key"]]

    return {
        "config_path": str(config_path),
        "categories": categories,
        "restart_required": True,
    }


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


@router.get("/self/error_log_stats")
async def get_error_log_stats(
    request: Request,
    scope: str = Query("all", description="all=全部记录；today=仅当天"),
    exclude_tool_executor: bool = Query(
        False, description="是否剔除 where 以 mini_agent.tool_executor 开头的记录"
    ),
):
    """全局错误日志（~/.agent/logs/error.jsonl）的错误类型分布统计。

    供看板"📛 错误日志"标签页使用。日志文件是进程级全局的（不区分
    project_root/session），所以这里不走 `_bridge(request)`，直接调用
    `mini_agent.errors.error_log_stats()`，只做登录态校验。
    """
    _require_owner(request)

    if scope not in ("all", "today"):
        raise HTTPException(status_code=400, detail="scope 仅支持 all / today")

    from mini_agent.errors import error_log_stats

    return error_log_stats(scope=scope, exclude_tool_executor=exclude_tool_executor)


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
      - loop_active：[看板诊断改进] AutonomousLoop 是否真的挂在 daemon 上在跑。
        autonomy_level 读的是 self_profile.json 里的配置值，跟"tick 有没有
        真的在跑"是两件事——没启动 daemon、或 daemon 启动时没注入
        AutonomousLoop，autonomy_level 配置值可以是 maintenance/autonomous，
        但这里恒为 False，Objective 永远不会被执行。这是排查"为什么加了
        目标 agent 却不去做"的第一个该看的字段。
      - has_actionable_work：[看板诊断改进] GoalBacklog 里是否存在
        status=active 的 Objective（Goal 本身不算，得先拆出 Objective）。
      - objective_slots：[看板诊断改进] ObjectiveExecutor 并发槽位
        {running, max}——槽位占满时新 Objective 只能排队等待。
      - gating：[看板诊断改进] ResourceArbiter.diagnose()，逐条列出预算/
        挫败感/用户在场三条门控规则的通过情况和具体数值。
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
        "loop_active": False,
        "has_actionable_work": False,
        "objective_slots": None,
        "gating": None,
    }

    al = http_server.autonomous_loop
    result["loop_active"] = al is not None
    if al is not None:
        try:
            result["autonomy_level"] = al._get_autonomy_level()
            result["next_tick_in"] = round(max(0.0, al._last_tick_at + al._tick_interval - time.time()), 1)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes')
            pass

        # GoalBacklog：是否存在可执行的 Objective
        gb = getattr(al, "_goal_backlog", None)
        if gb is not None:
            try:
                result["has_actionable_work"] = gb.has_actionable_work()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

        # ResourceArbiter 门控诊断
        try:
            from mini_agent.evolution.resource_arbiter import ResourceArbiter
            paths = getattr(al, "_paths", None)
            cfg = getattr(al, "_cfg", None)
            if paths is not None and cfg is not None:
                result["gating"] = ResourceArbiter(paths, cfg).diagnose()
                # [daemon 稳定性与用户体验改进方案 P0-4] 时间线记录已经下沉到
                # ResourceArbiter.gating_state() 内部——状态真正变化的那一刻
                # 就落盘，不再依赖这个只读接口被轮询到。gating_state() 由
                # AutonomousLoop 主循环每个 tick 调用，覆盖"没人打开看板"的
                # 场景。这里不再重复调用 record_gating_transition()，避免同
                # 一次状态变化被两条路径分别判断一遍（虽然去重逻辑本身是
                # 幂等的，但语义上记录点应该只有一个）。
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
                        # [P2/P3] 供看板展示排队优先级，帮助解释"同时到期
                        # 时谁先跑"。
                        "priority": j.priority,
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
                from mini_agent.evolution.objective_executor import MAX_CONCURRENT_OBJECTIVES
                # [Track K] 优先展示自适应计算后的生效上限；ObjectiveExecutor
                # 未提供 effective_max_concurrent()（理论上不会，防御性
                # 兼容）或计算异常时，退化为展示改造前的静态常量。
                try:
                    effective_max = oe.effective_max_concurrent()
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                    effective_max = MAX_CONCURRENT_OBJECTIVES
                result["objective_slots"] = {
                    "running": oe.running_count(),
                    "max": effective_max,
                    "static_cap": MAX_CONCURRENT_OBJECTIVES,
                }
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes')
                pass

    return result


@router.get("/autonomous/gating_history")
async def get_gating_history(request: Request, limit: int = Query(50, ge=1, le=200)):
    """
    GET /v1/autonomous/gating_history?limit=50

    [调度统一化 + 看板可观测性改进方案 P5] 返回 ResourceArbiter 三态门控
    （full/degraded/blocked）最近的状态变化时间线，按时间正序（旧→新）。
    每条记录只在状态相对上一条发生变化时才会存在（见
    evolution/resource_arbiter.py::record_gating_transition 的去重说明），
    不是每次轮询都记一条，避免时间线被刷成"仲裁状态检查日志"。

    供看板"🗓️ 全局日程" tab 展示"何时从 full 变成 degraded/blocked、
    何时恢复"，配合同一个 tab 里的 cron job 到期时间、recurring goal
    下次触发一起看，定位"为什么现在没有自主任务在跑"这类问题时不用
    再去翻日志文件。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.evolution.resource_arbiter import read_gating_history, gating_ratio_summary
        al = http_server.autonomous_loop
        paths = getattr(al, "_paths", None) if al is not None else None
        if paths is None:
            return {"history": []}
        history = read_gating_history(paths, limit=limit)
        # [kanban_perception_gaps_improvement_plan.md 方向 C] 顺带在同一个
        # 响应里附一份"过去 7 天三态占比"的聚合摘要——数据来源和调用方跟
        # 逐条时间线完全一致，没必要为这一个数字单独拆一次请求。计算失败
        # 时该字段整体缺省为 None，不影响 history 本身的返回。
        ratio_summary = None
        try:
            ratio_summary = gating_ratio_summary(paths, window_days=7.0)
        except Exception as _mini_agent_exc2:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc2, where='mini_agent.api.routes.get_gating_history.ratio_summary')
        return {"history": history, "ratio_summary": ratio_summary}
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_gating_history')
        return {"history": [], "ratio_summary": None}


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
        # [BUGFIX] 之前这里用 active_goals()/active_objectives()，只返回
        # status=="active" 的节点，导致看板的"暂停/已完成/已放弃"三列永远
        # 拿不到数据——不管 goals.json 里实际有多少这几种状态的目标，接口
        # 都会在返回前就把它们过滤掉，看板显示内容因此跟 goals.json 实际
        # 内容对不上。看板是纯展示/管理场景，需要看到全部状态，过滤应该
        # 交给需要"只关心 active"的调用方（如 AutonomousLoop）自己去调用
        # active_goals()/active_objectives()，这里改为返回全量节点。
        all_nodes = backlog.all_nodes()
        objectives = [n.to_dict() for n in all_nodes if n.is_objective]

        # [P1 新增] Objective 通过 work_thread_ref 关联 work_index.json 里的
        # WorkThread，那边的 cumulative_progress/next_suggested 才是"实际做到
        # 哪一步了"的动态记录（progress_notes 需要 agent 手动回写，经常是空的
        # 或者滞后）。看板卡片之前只显示 progress_notes，看起来永远没进展。
        # 这里把关联 WorkThread 的这两个字段一并带出来，看板不用再单独发
        # 一次请求、也不需要新增一个 work_threads 接口。
        try:
            from mini_agent.perception.workdir_knowledge import load_work_index
            threads_by_id = {t.id: t for t in load_work_index(paths)}
            for obj in objectives:
                ref = obj.get("work_thread_ref")
                thread = threads_by_id.get(ref) if ref else None
                if thread is not None:
                    obj["work_thread_progress"] = thread.cumulative_progress
                    obj["work_thread_next_suggested"] = thread.next_suggested
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.list_goals.work_thread_enrich')

        return {
            "goals":      [n.to_dict() for n in all_nodes if n.is_goal],
            "objectives": objectives,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/goals/similar_confirmed_specs")
async def get_similar_confirmed_goal_specs(
    request: Request,
    title: str = Query(""),
    description: str = Query(""),
    exclude_goal_id: str = Query(""),
):
    """GET /v1/goals/similar_confirmed_specs?title=&description= —
    [cross_goal_experience_reuse_plan.md] 只读：在已确认执行规范的历史
    Goal 里找相似候选，附对方的 GoalExecutionSpec 摘要，供创建新 Goal 时
    自愿参考；不做任何自动应用。title/description 都为空时返回空列表。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.goal_backlog import load_goal_backlog
        from mini_agent.perception.cross_goal_reference import find_similar_confirmed_goals

        self_agent = http_server.bridge.agent
        project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
        if not project_root:
            return {"candidates": []}
        paths = AgentPaths(project_root)
        backlog = load_goal_backlog(paths)
        candidates = find_similar_confirmed_goals(
            backlog, title, description, paths=paths,
            exclude_goal_id=exclude_goal_id or None,
        )
        return {"candidates": candidates}
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_similar_confirmed_goal_specs')
        return {"candidates": []}


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
            # [goal-provenance-guide.md] 这是 HTTP API 调用（看板"新建
            # Goal"表单是当前唯一的真实调用方），显式标记 "user"——不落到
            # add_goal() 内部 thread-local 兜底逻辑，因为处理 HTTP 请求的
            # 线程本来就不是 AgentRunner 的轮次处理线程，兜底本身也会是
            # "user"，这里写出来只是让语义更自解释。允许调用方通过 body
            # 显式覆盖（比如未来某个脚本代表 cron 直接调这个 API），默认
            # 仍然是 "user"。
            source_initiator=body.get("source_initiator", "user"),
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
        # [看板 Goal 编辑功能，配套 self_diagnosis_feedback_loop_deepening_plan.md
        # 看板改造新增] 允许看板直接改标题/描述，此前只能改 status/priority/
        # progress_notes，标题写错或描述过时时只能删了重建，丢失历史关联。
        if "title" in body:
            new_title = (body["title"] or "").strip()
            if new_title:
                fields["title"] = new_title
        if "description" in body:
            fields["description"] = body["description"] or ""

        updated = backlog.update_fields(goal_id, **fields)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")

        # [看板与自主性改进方案 Track B 完整版] 反向同步：用户在看板上手动
        # 把 GoalNode 状态改成非"运行中"（active）时，若对应 objective 还有
        # 一个 running/pending 的 execution 在跑，driv 它 cancel()——不能让
        # 看板显示"已放弃/已暂停"但后台 execution 还在继续消耗并发槽位。
        # 正向同步（execution 完成/失败/取消时回写 GoalNode.status）由
        # ObjectiveExecutor._sync_goal_status() 负责，这里只处理反方向，
        # 两个方向都只在各自触发点单向写入，不会互相覆盖。
        new_status = body.get("status")
        if new_status and new_status != "active":
            try:
                oe = getattr(http_server.bridge, "_objective_executor", None)
                al = getattr(http_server, "autonomous_loop", None)
                if oe is None and al is not None:
                    oe = getattr(al, "_objective_executor", None)
                if oe is not None:
                    running_exec_id = oe.find_running_execution_by_objective(goal_id)
                    if running_exec_id:
                        oe.cancel(running_exec_id, sync_goal_status=False)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.routes.update_goal')

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


# ── 周期性 Goal 绑定/解绑/跳过（goal_cron_visibility_and_intervention_
# improvement_plan.md Track A/B）────────────────────────────────────────────
# 三个端点都直接复用 evolution/goal_cron_bridge.py 里已有的函数，不重复实现
# 绑定/触发业务逻辑——REST 层只是把 CLI 已有的 /agent goals recur|unrecur
# 能力暴露给看板。

def _goal_backlog_and_scheduler(request: Request):
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import load_goal_backlog
    self_agent = http_server.bridge.agent
    project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
    if not project_root:
        raise HTTPException(status_code=503, detail="project_root not configured")
    paths = AgentPaths(project_root)
    backlog = load_goal_backlog(paths)
    scheduler = _get_cron_scheduler(http_server)
    return backlog, scheduler


def _goal_backlog_only(request: Request):
    """[goal_execution_spec_generation_plan.md §6.1/§6.3/§6.4] 与
    `_goal_backlog_and_scheduler()` 的区别：不解析 CronScheduler。执行规范
    的生成/修订/确认/整体关闭判定几个端点都不涉及 cron job 读写，强行复用
    `_goal_backlog_and_scheduler()` 会因为测试/部分嵌入场景下
    `http_server.autonomous_loop`/`bridge._cron_scheduler` 缺失而无谓报错——
    这里单独拆一个只解析 GoalBacklog 的轻量版本，两者都基于同一份
    project_root 解析逻辑，只是要不要额外解析 scheduler 的区别。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import load_goal_backlog
    self_agent = http_server.bridge.agent
    project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
    if not project_root:
        raise HTTPException(status_code=503, detail="project_root not configured")
    paths = AgentPaths(project_root)
    backlog = load_goal_backlog(paths)
    return backlog


@router.post("/goals/{goal_id}/recur")
async def recur_goal(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/recur — 把一个已有 Goal 声明为周期性。
    Body: { "schedule": str, "task_template": Optional[str] }
    等价于 CLI 的 `/agent goals recur <id> <schedule> [task]`。
    """
    body = await request.json()
    schedule = (body.get("schedule") or "").strip()
    if not schedule:
        raise HTTPException(status_code=400, detail="schedule is required")
    backlog, scheduler = _goal_backlog_and_scheduler(request)
    try:
        from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
        job = make_goal_recurring(
            backlog, scheduler, goal_id, schedule,
            task_template=body.get("task_template") or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    goal = backlog.get(goal_id)
    return {"goal": goal.to_dict() if goal else None, "cron_job": job.to_dict()}


@router.post("/goals/{goal_id}/unrecur")
async def unrecur_goal(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/unrecur — 停止周期性推进（不删 Goal/cron job）。
    等价于 CLI 的 `/agent goals unrecur <id>`。
    """
    backlog, scheduler = _goal_backlog_and_scheduler(request)
    from mini_agent.evolution.goal_cron_bridge import stop_goal_recurrence
    ok = stop_goal_recurrence(backlog, scheduler, goal_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found or not recurring")
    goal = backlog.get(goal_id)
    return {"goal": goal.to_dict() if goal else None}


@router.post("/goals/{goal_id}/skip_next_cycle")
async def skip_goal_next_cycle(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/skip_next_cycle — 跳过下一次周期性触发，
    但保持 recurring=True 不变（区别于 unrecur）。见
    next_doc/goal_cron_visibility_and_intervention_improvement_plan.md §3。
    """
    backlog, _scheduler = _goal_backlog_and_scheduler(request)
    goal = backlog.get(goal_id)
    if goal is None or not goal.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    if not goal.recurring:
        raise HTTPException(status_code=400, detail="Goal is not recurring")
    updated = backlog.update_fields(goal_id, skip_next_cycle=True)
    return {"goal": updated.to_dict() if updated else None}


@router.post("/goals/{goal_id}/lightweight_next_cycle")
async def lightweight_goal_next_cycle(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/lightweight_next_cycle — 下一次周期性触发
    仍然照常执行，但要求"从简"（不引入新方案/不做结构性变更），跟
    skip_next_cycle（完全不跑）是不同粒度的干预。见
    next_doc/goal_cron_task_optimization_holistic_plan.md 方向 C。
    """
    backlog, _scheduler = _goal_backlog_and_scheduler(request)
    goal = backlog.get(goal_id)
    if goal is None or not goal.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    if not goal.recurring:
        raise HTTPException(status_code=400, detail="Goal is not recurring")
    updated = backlog.update_fields(goal_id, next_cycle_lightweight=True)
    return {"goal": updated.to_dict() if updated else None}


@router.post("/goals/{goal_id}/feedback")
async def add_goal_feedback(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/feedback — [goal_cron_feedback_and_output_
    policy_plan.md 3.5] 持久化提意见，合入该节点的 description，此后所有基于
    这个 Goal/Objective 派生的执行都会带着这条意见。若该节点是绑定了周期性
    CronJob 的 Goal，会自动双向同步到对应 CronJob。
    Body: { "text": str }
    返回更新后的节点摘要，供前端立即刷新。
    """
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    backlog, _scheduler = _goal_backlog_and_scheduler(request)
    node = backlog.get(goal_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    ok = backlog.add_user_feedback(goal_id, text)
    if not ok:
        raise HTTPException(status_code=500, detail="add_user_feedback failed")
    updated = backlog.get(goal_id)
    return {"goal": updated.to_dict() if updated else None}


# ── Goal 执行规范（goal_execution_spec_generation_plan.md §6.1/§6.3/§6.4）───
# 看板"⏰ 周期性设置"/"➕ 新建目标"的草稿生成/反馈迭代/确认/查看/整体关闭
# 手动重判，五个端点直接复用 CLI（/agent goals spec ...）已经验证过的同一套
# perception/goal_execution_spec.py 模块，行为对称——REST 层只是把 CLI 能力
# 暴露给看板，不重复实现生成/确认逻辑。

def _spec_paths(request: Request) -> "AgentPaths":
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    from mini_agent.storage.paths import AgentPaths
    self_agent = http_server.bridge.agent
    project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
    if not project_root:
        raise HTTPException(status_code=503, detail="project_root not configured")
    return AgentPaths(project_root)


@router.get("/goal_execution_spec_templates")
async def list_goal_execution_spec_templates(
    request: Request, goal_title: str = Query(default=""), goal_description: str = Query(default=""),
):
    """GET /v1/goal_execution_spec_templates — 模板库摘要列表（方案 §7），
    供看板"从模板起步"下拉框使用。传 `goal_title`/`goal_description` 时
    额外返回 `suggested_template_id`（关键词粗略匹配，见
    `goal_execution_spec.suggest_template()`），前端据此默认预选、允许
    用户改选或选"不用模板"——匹配不到时为 `null`。"""
    _require_owner(request)
    from mini_agent.perception.goal_execution_spec import list_templates, suggest_template
    suggested = suggest_template(goal_title, goal_description) if (goal_title or goal_description) else None
    return {"templates": list_templates(), "suggested_template_id": suggested}


@router.get("/goals/{goal_id}/execution_spec")
async def get_goal_execution_spec(goal_id: str, request: Request):
    """GET /v1/goals/{goal_id}/execution_spec — 查看当前执行规范（草稿或已
    确认版本），对应 CLI `/agent goals spec show`。没有生成过时返回
    `{"spec": None}`，不是 404——"还没生成"是合法状态，不是错误。"""
    paths = _spec_paths(request)
    from mini_agent.perception.goal_execution_spec import load_spec
    spec = load_spec(paths, goal_id)
    return {"spec": spec.to_dict() if spec else None}


@router.post("/goals/{goal_id}/execution_spec/generate")
async def generate_goal_execution_spec(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/execution_spec/generate — 生成第 1 版草稿
    （不确认，不影响执行），对应 CLI `/agent goals spec generate`。
    Body: { "schedule": str?, "task_template": str?, "template_id": str?,
            "from_history": bool?, "mode": "llm"|"agent"|"auto"? }
    `mode` 不传时回退配置文件 `goal_execution_spec.builder_mode`（默认
    "auto"），传了非法值时同样回退（`GoalExecutionSpecBuilder.__init__`
    已有校验，这里不重复）——单次调用覆盖，不修改配置文件。响应体新增
    `effective_path`（"llm"/"agent"，这次实际走的路径），供前端展示
    "这份草稿是否读取过项目内容"。[goal_execution_spec_generation_plan.md
    §3 输入源 1 / implementation_record.md §7.5 未实施清单第 2 条]
    """
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    body = await request.json() if await request.body() else {}
    paths = _spec_paths(request)
    try:
        from mini_agent.config import load_config
        from mini_agent.perception import goal_execution_spec as ges
        from mini_agent.evolution import output_workspace

        cfg = load_config()
        history_manifests = None
        if body.get("from_history"):
            base_dir = output_workspace.goal_output_base_dir(paths, goal_id)
            m = output_workspace.read_latest_manifest(base_dir)
            history_manifests = [m] if m else None

        builder = ges.GoalExecutionSpecBuilder(cfg, mode=body.get("mode") or None)
        spec = builder.build_draft(
            goal_id, node.title, node.description,
            schedule=body.get("schedule") or None,
            task_template=body.get("task_template") or None,
            template_id=body.get("template_id") or None,
            history_manifests=history_manifests,
        )
        ges.save_spec(paths, goal_id, spec)
    except HTTPException:
        raise
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.generate_goal_execution_spec')
        raise HTTPException(status_code=500, detail=f"生成执行规范失败：{e}")
    return {"spec": spec.to_dict(), "effective_path": builder.last_effective_path}


@router.post("/goals/{goal_id}/execution_spec/revise")
async def revise_goal_execution_spec(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/execution_spec/revise — 基于反馈 + 字段级
    锁定重新生成（方案 §6.2），对应看板「🔄 补充意见重新生成」按钮。
    Body: { "feedback": str, "locked_fields": [str, ...]?, "mode": "llm"|
            "agent"|"auto"? }
    `mode` 用法与 generate 端点一致（单次覆盖，不传时回退配置默认值）。
    响应体同样新增 `effective_path`。
    """
    body = await request.json()
    feedback = (body.get("feedback") or "").strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="feedback is required")
    paths = _spec_paths(request)
    from mini_agent.perception import goal_execution_spec as ges
    prior = ges.load_spec(paths, goal_id)
    if prior is None:
        raise HTTPException(status_code=404, detail=f"该 Goal 还没有生成过执行规范草稿：{goal_id}")
    try:
        from mini_agent.config import load_config
        builder = ges.GoalExecutionSpecBuilder(load_config(), mode=body.get("mode") or None)
        spec = builder.revise(prior, feedback, locked_fields=body.get("locked_fields"))
        ges.save_spec(paths, goal_id, spec)
    except HTTPException:
        raise
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.revise_goal_execution_spec')
        raise HTTPException(status_code=500, detail=f"修订执行规范失败：{e}")
    return {"spec": spec.to_dict(), "effective_path": builder.last_effective_path}


@router.post("/goals/{goal_id}/execution_spec/confirm")
async def confirm_goal_execution_spec(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/execution_spec/confirm — 确认并冻结当前草稿
    （下次触发即生效），对应 CLI `/agent goals spec confirm`。看板"✅ 确认
    并设为周期性"/"✅ 确认执行规范"按钮在这一步之后紧接着调用
    `recur_goal`（周期性场景）或什么都不做（一次性 Goal 场景）——两种场景
    共用同一个确认端点，`recur` 与 `confirm` 解耦成两次独立请求，任一失败
    都不会让另一半处于不一致的"半成功"状态。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception import goal_execution_spec as ges
    spec = ges.load_spec(paths, goal_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"该 Goal 还没有生成过执行规范草稿：{goal_id}")
    try:
        ges.GoalExecutionSpecBuilder.confirm(spec)
        ges.save_spec(paths, goal_id, spec)
        backlog.update_fields(goal_id, execution_spec_confirmed=True)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.confirm_goal_execution_spec')
        raise HTTPException(status_code=500, detail=f"确认执行规范失败：{e}")
    updated = backlog.get(goal_id)
    return {"spec": spec.to_dict(), "goal": updated.to_dict() if updated else None}


@router.post("/goals/{goal_id}/execution_spec/close_check")
async def close_check_goal_execution_spec(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/execution_spec/close_check — 手动（重新）
    触发一次"整体是否可以关闭"判定，对应 CLI `/agent goals spec
    close-check`。前置条件不满足时返回 `outcome: null`，不算错误。
    Body: { "use_agent": bool? } —— [implementation_record.md §11 后续
    建议顺序第 2 条] 单次覆盖是否走受限 Agent 路径判定，不传（或传
    `null`）时回退配置文件 `overall_completion_use_agent`，不修改配置
    文件，与 generate/revise 端点的 `mode` 单次覆盖同一风格。返回的
    `goal.overall_completion_last_check` 里带 `used_agent`，供前端展示
    这次实际走的是哪条路径。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    if node.status != "active":
        return {"outcome": None, "reason": f"Goal 当前状态为 {node.status!r}，不是 active，跳过判定。"}
    body = await request.json() if await request.body() else {}
    use_agent = body.get("use_agent")
    if use_agent is not None:
        use_agent = bool(use_agent)
    try:
        from mini_agent.config import load_config
        outcome = backlog.maybe_close_goal_by_overall_criteria(goal_id, load_config(), use_agent=use_agent)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.close_check_goal_execution_spec')
        raise HTTPException(status_code=500, detail=f"整体完成判定失败：{e}")
    updated = backlog.get(goal_id)
    return {"outcome": outcome, "goal": updated.to_dict() if updated else None}


# ── Goal 执行阶段（goal_execution_phase_improvement_plan.md Stage C）────────
# 看板"阶段徽章"展示 + 手动切换，直接复用 perception/execution_phase.py，
# 与 execution_spec 一节风格对称：REST 层只是把 CLI（/agent goals phase）
# 能力暴露给看板，不重复实现判定逻辑。

@router.get("/goals/{goal_id}/execution_phase")
async def get_goal_execution_phase(goal_id: str, request: Request):
    """GET /v1/goals/{goal_id}/execution_phase — 查看当前执行阶段状态
    （mode/locked/stability_score/mode_history），对应 CLI
    `/agent goals phase show`。没有 phase 文件时返回默认状态
    （mode="auto", locked=False），不是 404——"还没手动设置过"是合法状态。
    """
    paths = _spec_paths(request)
    from mini_agent.perception import execution_phase as ep
    state = ep.load_phase(paths, goal_id)
    return {"phase": state.to_dict()}


@router.post("/goals/{goal_id}/execution_phase")
async def set_goal_execution_phase(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/execution_phase — 手动切换执行阶段，对应
    CLI `/agent goals phase set`。Body: { "mode": "explore"|"converge"|
    "stable"|"tidy"|"auto", "lock": bool? }。`lock` 不传时沿用 CLI 同样的
    默认规则：非 auto 隐式锁定，auto 隐式解锁。
    """
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    body = await request.json()
    mode = body.get("mode")
    if not mode:
        raise HTTPException(status_code=400, detail="mode is required")
    lock = body.get("lock")
    if lock is not None:
        lock = bool(lock)
    paths = _spec_paths(request)
    from mini_agent.perception import execution_phase as ep
    try:
        state = ep.set_mode(paths, goal_id, mode, lock=lock, reason="kanban_set")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.set_goal_execution_phase')
        raise HTTPException(status_code=500, detail=f"切换执行阶段失败：{e}")
    return {"phase": state.to_dict()}


@router.post("/goals/{goal_id}/execution_phase/unlock")
async def unlock_goal_execution_phase(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/execution_phase/unlock — 解除锁定，交回
    自动判定，对应 CLI `/agent goals phase unlock`。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception import execution_phase as ep
    try:
        state = ep.unlock_mode(paths, goal_id)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.unlock_goal_execution_phase')
        raise HTTPException(status_code=500, detail=f"解除锁定失败：{e}")
    return {"phase": state.to_dict()}


@router.get("/goals/{goal_id}/cycle_diagnostics")
async def get_goal_cycle_diagnostics(goal_id: str, request: Request):
    """GET /v1/goals/{goal_id}/cycle_diagnostics —
    [goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1]
    跨轮次诊断报告：聚合阶段/健康告警/cron 状态/最近轮次产出/机制说明，
    回答"这个 Goal 整体跑得怎么样"，对应 CLI `/agent goals diagnose`。
    纯只读聚合，不修改任何状态。Goal 不存在时返回 404。
    """
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
    try:
        report = build_cycle_diagnostics(paths, backlog, goal_id)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.get_goal_cycle_diagnostics')
        raise HTTPException(status_code=500, detail=f"生成诊断报告失败：{e}")
    return {"diagnostics": report.to_dict()}


# ── Stage 2: 交互式调优（草案 → 确认 → 应用）──────────────────────────────
# [goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md §3.3]

def _cron_scheduler_readonly(paths: "AgentPaths"):
    from mini_agent.evolution.cron_scheduler import load_cron_scheduler
    return load_cron_scheduler(paths)


@router.post("/goals/{goal_id}/tuning_proposals")
async def create_tuning_proposal(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/tuning_proposals — 生成一份调优草案。
    Body: { "changes": [{"param": str, "to": any, "reason": str?}], "source": str? }
    `source` 默认 "user_request"；规则触发的建议请改用
    `POST /v1/goals/{goal_id}/tuning_proposals/suggest`。
    `param` 不在白名单内时返回 400，不生成任何草案。
    """
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    body = await request.json()
    changes = body.get("changes")
    if not changes:
        raise HTTPException(status_code=400, detail="changes is required and must be non-empty")
    source = body.get("source", "user_request")
    paths = _spec_paths(request)
    from mini_agent.perception import cycle_tuning as ct
    try:
        proposal = ct.build_tuning_proposal(goal_id, changes, source=source)
    except ct.WhitelistViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ct.save_proposal(paths, proposal)
    return {"proposal": proposal.to_dict()}


@router.post("/goals/{goal_id}/tuning_proposals/suggest")
async def suggest_tuning_proposal(goal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/tuning_proposals/suggest — 基于诊断报告的
    规则信号生成候选草案（不含 LLM），命中信号才会生成并落盘草案；没有
    命中时返回 `{"proposal": null}`，不是错误。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
    from mini_agent.perception import cycle_tuning as ct
    report = build_cycle_diagnostics(paths, backlog, goal_id)
    suggestion = ct.suggest_tuning_from_diagnostics(report)
    if suggestion is None:
        return {"proposal": None}
    ct.save_proposal(paths, suggestion)
    return {"proposal": suggestion.to_dict()}


@router.get("/goals/{goal_id}/tuning_proposals")
async def list_tuning_proposals(goal_id: str, request: Request):
    """GET /v1/goals/{goal_id}/tuning_proposals — 列出历史草案（含状态）。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception import cycle_tuning as ct
    proposals = ct.list_proposals(paths, goal_id)
    return {"proposals": [p.to_dict() for p in proposals]}


@router.post("/goals/{goal_id}/tuning_proposals/{proposal_id}/confirm")
async def confirm_tuning_proposal_route(goal_id: str, proposal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/tuning_proposals/{proposal_id}/confirm —
    确认草案本身，此时仍未生效。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception import cycle_tuning as ct
    try:
        proposal = ct.confirm_tuning_proposal(paths, goal_id, proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"proposal": proposal.to_dict()}


@router.post("/goals/{goal_id}/tuning_proposals/{proposal_id}/apply")
async def apply_tuning_proposal_route(goal_id: str, proposal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/tuning_proposals/{proposal_id}/apply —
    应用已确认的草案，逐项调用白名单参数对应的既有修改入口。某一项失败
    不影响其它项，失败详情在返回的 `apply_results` 里逐条列出。"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    from mini_agent.perception import cycle_tuning as ct
    cs = _cron_scheduler_readonly(paths)
    spec_builder_cfg = None
    try:
        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        spec_builder_cfg = getattr(self_agent, "cfg", None) if self_agent else None
        if spec_builder_cfg is None:
            from mini_agent.config import load_config
            spec_builder_cfg = load_config()
    except Exception:
        spec_builder_cfg = None
    try:
        proposal = ct.apply_tuning_proposal(
            paths, backlog, cs, goal_id, proposal_id, spec_builder_cfg=spec_builder_cfg,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.api.routes.apply_tuning_proposal_route')
        raise HTTPException(status_code=500, detail=f"应用调优草案失败：{e}")
    return {"proposal": proposal.to_dict()}


@router.post("/goals/{goal_id}/tuning_proposals/{proposal_id}/reject")
async def reject_tuning_proposal_route(goal_id: str, proposal_id: str, request: Request):
    """POST /v1/goals/{goal_id}/tuning_proposals/{proposal_id}/reject —
    拒绝草案，作废，不产生任何实际改动。Body 可选: {"reason": str}"""
    backlog = _goal_backlog_only(request)
    node = backlog.get(goal_id)
    if node is None or not node.is_goal:
        raise HTTPException(status_code=404, detail=f"Goal '{goal_id}' not found")
    paths = _spec_paths(request)
    reason = ""
    try:
        body = await request.json()
        reason = body.get("reason", "") if body else ""
    except Exception:
        reason = ""
    from mini_agent.perception import cycle_tuning as ct
    try:
        proposal = ct.reject_tuning_proposal(paths, backlog, goal_id, proposal_id, reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"proposal": proposal.to_dict()}


# ── Objective 执行操作（看板与自主性改进方案 Track D）────────────────────────
# 给 ObjectiveExecutor 已有的状态机加几个转换入口：终止 / 手动重试当前步 /
# 插一句话补充上下文。都是"事实来源仍是 ObjectiveExecutor"的操作——不直接
# 改 GoalNode.status，由 ObjectiveExecutor 内部的 _sync_goal_status()
# （Track B）单向回写。

def _objective_executor_or_404(request: Request):
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    al = http_server.autonomous_loop
    oe = getattr(al, "_objective_executor", None) if al is not None else None
    if oe is None:
        oe = getattr(http_server.bridge, "_objective_executor", None)
    if oe is None:
        raise HTTPException(status_code=503, detail="ObjectiveExecutor not available")
    return oe


@router.post("/objectives/{execution_id}/cancel")
async def cancel_objective(request: Request, execution_id: str):
    """POST /v1/objectives/{execution_id}/cancel — 终止一个正在运行的 Objective
    执行：立即释放并发槽位，不再重试；对应 GoalNode.status 会同步变为
    "cancelled"（见 ObjectiveExecutor._on_objective_cancelled）。"""
    oe = _objective_executor_or_404(request)
    ok = oe.cancel(execution_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"execution {execution_id!r} not found or already finished",
        )
    return {"ok": True}


@router.post("/objectives/{execution_id}/pause")
async def pause_objective(request: Request, execution_id: str):
    """POST /v1/objectives/{execution_id}/pause — [daemon_stability_and_
    ux_improvement_plan.md P1-5] 用户主动暂停一个正在运行/因公平性暂停的
    Objective execution：不释放已完成 step 的进度，也不重新拆解，只是
    不再提交下一步，等用户显式调用 /resume 才继续。如果当前 step 正在
    执行，暂停会在这一步跑完后才真正生效（不会打断正在跑的 step），
    这段等待期内 execution 的 status 仍是 "running"。"""
    oe = _objective_executor_or_404(request)
    ok = oe.request_pause(execution_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"execution {execution_id!r} not found or not pausable in its current state",
        )
    return {"ok": True}


@router.post("/objectives/{execution_id}/resume")
async def resume_objective(request: Request, execution_id: str):
    """POST /v1/objectives/{execution_id}/resume — 恢复一个被用户主动暂停
    （paused_by_user）的 Objective execution：从断点（current_step_idx）
    重新提交，不重新拆解、不丢失已完成 step 的进度。"""
    oe = _objective_executor_or_404(request)
    ok = oe.resume_user_pause(execution_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"execution {execution_id!r} not found or not in paused_by_user state",
        )
    return {"ok": True}


@router.post("/objectives/{execution_id}/retry")
async def retry_objective_step(request: Request, execution_id: str):
    """POST /v1/objectives/{execution_id}/retry — 手动触发当前 step 重新
    提交，不检查是否超时，随时可调用（区别于 reap_stale_steps() 的自动
    超时重试）。"""
    oe = _objective_executor_or_404(request)
    ok = oe.retry_current_step(execution_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"execution {execution_id!r} not found or has no retryable current step",
        )
    return {"ok": True}


@router.post("/objectives/{execution_id}/steps/{step_index}/edit")
async def edit_objective_step(request: Request, execution_id: str, step_index: int):
    """POST /v1/objectives/{execution_id}/steps/{step_index}/edit
    Body: { "result_summary"?: str, "artifacts"?: list[str] }

    [daemon_stability_and_ux_improvement_plan.md P2-10] 编辑一个已完成
    step 的产出并继续，不重新执行该 step 本身——只把用户修正后的结果写回
    去，后续 step 会读到修正后的版本作为"前序步骤结果"继续执行。与
    /reset（整步重做）是互补关系：这一步基本做对了、只是描述有点小问题，
    不需要重跑模型，改一下继续就行。只对 status == "done" 的历史 step
    生效。"""
    oe = _objective_executor_or_404(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    result_summary = body.get("result_summary")
    artifacts = body.get("artifacts")
    if result_summary is not None and not isinstance(result_summary, str):
        raise HTTPException(status_code=400, detail="result_summary must be a string")
    if artifacts is not None and not isinstance(artifacts, list):
        raise HTTPException(status_code=400, detail="artifacts must be a list of strings")
    ok = oe.edit_step_result(execution_id, step_index, result_summary=result_summary, artifacts=artifacts)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=(
                f"execution {execution_id!r} not found, step_index {step_index} out of range, "
                "step is not in 'done' status, or no changes provided"
            ),
        )
    return {"ok": True}


@router.post("/objectives/{execution_id}/steps/{step_index}/reset")
async def reset_objective_step(request: Request, execution_id: str, step_index: int):
    """POST /v1/objectives/{execution_id}/steps/{step_index}/reset
    Body（可选）: { "reason": str }

    [daemon_autonomous_state_recovery_plan.md 阶段二] 手动把某一步（可以是
    已经"done"但事后发现结果有问题的步骤）打回 pending 重做：清空该步骤及
    其之后所有步骤的既有进度（这些进度可能是基于被污染上下文产生的），
    并在重新提交时明确告诉模型"前序结果已作废"。区别于 /retry ——/retry
    只能重试"当前仍卡着的" step，/reset 可以回退到任意历史 step。"""
    oe = _objective_executor_or_404(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip() if isinstance(body, dict) else ""
    ok = oe.reset_step(execution_id, step_index, reason)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"execution {execution_id!r} not found or step_index {step_index} out of range",
        )
    return {"ok": True}


@router.post("/objectives/{execution_id}/guidance")
async def inject_objective_guidance(request: Request, execution_id: str):
    """POST /v1/objectives/{execution_id}/guidance
    Body: { "message": str }
    把用户的一句话作为补充上下文塞进下一次提交当前 step 的 prompt。若希望
    立即生效（而不是等当前 step 跑完/超时后才用上），需要配合调用
    /retry 让当前 step 重新提交。"""
    oe = _objective_executor_or_404(request)
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    ok = oe.inject_guidance(execution_id, message)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"execution {execution_id!r} not found or has no current step",
        )
    return {"ok": True}


# ── 执行细节可钻取（看板与自主性改进方案 Track E）────────────────────────────

def _format_history_entry_for_trace(entry: dict) -> Optional[dict]:
    """把一条 active history 条目（含 _type）转成 trace 展示用的精简结构。
    只保留跟"这一步到底干了什么"相关的类型；压缩/摘要/提醒类内部记录
    不是"这一步做了什么"的一部分，过滤掉以免干扰阅读。"""
    etype = entry.get("_type")
    if etype not in ("user_input", "assistant_reply", "tool_result"):
        return None
    content = entry.get("content")
    if etype == "assistant_reply" and isinstance(content, list):
        # assistant 回复可能是 [{"type":"text",...}, {"type":"tool_use",...}, ...] 混合块
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append({"kind": "text", "text": str(block.get("text", ""))[:2000]})
            elif btype == "tool_use":
                parts.append({
                    "kind": "tool_call",
                    "tool_name": block.get("name", ""),
                    "tool_input": block.get("input", {}),
                })
        return {"type": "assistant_reply", "parts": parts}
    if etype == "tool_result":
        # render_tool_results() 拼装出的内容，结构随 provider 略有差异，
        # 这里不深究内部格式，直接把整体内容转成文本摘要展示。
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)[:4000]
        return {"type": "tool_result", "text": str(text)[:4000]}
    if etype == "user_input":
        return {"type": "user_input", "text": str(content)[:2000]}
    return None


def _locate_step_history_entries(hist_mgr, submitted_message: str) -> Optional[list[dict]]:
    """[Track E 提取为公用函数，供 Track G 深化复用] 在 agent 的 active
    history 里定位 `submitted_message` 对应的那一段原始记录（未格式化），
    即从匹配到的 `user_input` 条目开始，到下一条 `user_input`（或历史
    末尾）为止的所有条目。逻辑与 get_objective_step_trace() 里原来内联
    的匹配代码完全一致，这里只是抽出来供 Track G 的"从 tool_call 里提取
    产出物路径"复用，避免维护两份几乎一样的匹配代码。

    返回 None 表示没有找到匹配（历史里没有这条记录，或被压缩/归档——
    压缩场景下调用方可以改用 `_locate_entries_in_list(hist_mgr.raw_history.
    entries, submitted_message)` 兜底，见 `get_objective_step_trace()` 第
    十一轮的改动）。
    """
    return _locate_entries_in_list(hist_mgr.history, submitted_message)


def _locate_entries_in_list(history: list[dict], submitted_message: str) -> Optional[list[dict]]:
    """[Track E 第十一轮 · compact 边界情况修复] `_locate_step_history_entries`
    的通用版本：不绑定 `hist_mgr.history`，可直接传入任意条目列表（active
    history 或 `hist_mgr.raw_history.entries`），供 compact 之后从 raw
    history 里兜底查找同一个 step 时复用同一份匹配逻辑，不重复实现。
    """
    start_idx = None
    for i, entry in enumerate(history):
        if entry.get("_type") == "user_input" and entry.get("content") == submitted_message:
            start_idx = i
    # 取最后一次匹配，见 get_objective_step_trace() 里的同一段注释：
    # submitted_message 每次重新提交都会被覆盖为最新内容，重试多次时只有
    # 最新一次会命中，"最后一次匹配"正好对应最新这次尝试。
    if start_idx is None:
        return None

    end_idx = len(history)
    for j in range(start_idx + 1, len(history)):
        if history[j].get("_type") == "user_input":
            end_idx = j
            break
    return history[start_idx:end_idx]


# [看板与自主性改进方案 Track G 深化] 这些工具的 tool_input 里有明确的
# `path` 字段指向被写入/修改的文件本身——与
# perception/artifact_detector.py 里 `_PATH_ARG_TOOLS` 保持同一份工具名单
# （那边是为了"产出物预览看板"侦测图片/文档类文件，这里是为了"跨步骤传递
# 产出路径"，目的不同所以不复用同一个常量，但工具名单没有理由不一致——
# 如果以后新增/改名了写文件类工具，两处都要同步维护）。
_ARTIFACT_TOOL_PATH_KEYS = ("path", "file_path", "target_file", "filepath")
_ARTIFACT_WRITE_TOOL_NAMES = frozenset({
    "write_file", "create_file", "patch_file", "patch_file_simple",
})


def _extract_tool_write_paths(raw_entries: list[dict]) -> list[str]:
    """[Track G 深化] 从一段原始 history 条目（`_locate_step_history_entries`
    的返回值）里扫描 assistant_reply 中的 tool_use 块，提取写文件类工具
    调用的路径参数。

    这是方案原文"待细化项 2"里标注的更可靠做法——不依赖模型在回复文本里
    自觉声明 `[ARTIFACTS] ...` 标记，而是直接从工具调用的结构化入参里拿
    真实路径，只要模型确实调用了写文件工具就一定能拿到，不存在"模型忘了
    按格式声明"的情况。

    按出现顺序去重返回；扫描不到任何写文件类工具调用时返回空列表（调用方
    据此决定是否退化到 `[ARTIFACTS]` 正则解析）。
    """
    paths: list[str] = []
    for entry in raw_entries:
        if entry.get("_type") != "assistant_reply":
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _ARTIFACT_WRITE_TOOL_NAMES:
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            for key in _ARTIFACT_TOOL_PATH_KEYS:
                p = tool_input.get(key)
                if p:
                    paths.append(str(p))
                    break
    return list(dict.fromkeys(paths))  # 去重且保序


@router.get("/objectives/{execution_id}/steps/{step_index}/trace")
async def get_objective_step_trace(request: Request, execution_id: str, step_index: int):
    """GET /v1/objectives/{execution_id}/steps/{step_index}/trace

    [看板与自主性改进方案 Track E] 返回某个 step 实际执行过程中的完整
    tool_call/tool_result 序列，而不只是 result_summary 截断到的 200~500
    字摘要。

    实现方式：Objective 的每个 step 都是提交到主 agent 会话（与普通聊天
    共用同一个 bridge/history）里的一次 run_turn()；`ExecutionStep.
    submitted_message` 保存了当次提交时拼装的完整 prompt 文本（见
    ObjectiveExecutor._submit_step），这段文本在 agent 的 active history
    里对应且仅对应一条 `_type=="user_input"` 记录（拼装内容包含 Objective
    标题 + 步骤序号，实际不会与其他消息重复）。据此在历史里做精确匹配，
    定位到该 step 对应的这条 user_input，再截取到下一条 user_input（或
    历史末尾）之间的所有条目，即为这一步实际发生的完整过程。

    局限（据实说明，不掩盖）：
    - [第十一轮已修复] 若该 step 因为压缩（compact）被从 active history
      里移除，此前会直接返回 `entries: []`；现在改为在 active history
      找不到匹配时，退化查询 `hist_mgr.raw_history`（raw history 只追加
      不压缩，`.agent/sessions/<id>/raw_history.jsonl` 完整保留了压缩前
      的全部记录），能定位到的话正常返回 entries，并在响应里加一个
      `from_raw_history: true` 标记，供看板提示"这是从压缩前的历史记录里
      找回的"。raw history 里也找不到（比如 step 从未真正提交过，或者
      是极旧的 session 且 raw_history.jsonl 文件本身已被外部清理）时，
      才真正退化为空列表 + 提示。
    - 只针对"当前仍能访问到 agent 实例"的场景（单用户模式下的主 bridge，
      或多用户模式下能定位到的 session）；找不到 agent/history 时同样
      退化为空列表 + 提示，而不是报错。多 session 场景下如果 Objective
      执行不再统一走单一主 bridge（即当前实现假设的前提），trace 提取
      需要能先定位到正确的 session/agent 再复用这里的逻辑——这部分维持
      现状未做，标注在实施记录"未完成/待续"里。
    """
    oe = _objective_executor_or_404(request)
    ex = oe.get_execution(execution_id)
    if ex is None:
        raise HTTPException(status_code=404, detail=f"execution {execution_id!r} not found")
    if not (0 <= step_index < len(ex.steps)):
        raise HTTPException(status_code=404, detail=f"step_index {step_index} out of range")
    step = ex.steps[step_index]

    def _empty(note: str) -> dict:
        return {
            "execution_id": execution_id,
            "step_index": step_index,
            "description": step.description,
            "status": step.status,
            "entries": [],
            "note": note,
            "from_raw_history": False,
        }

    if not step.submitted_message:
        return _empty("这一步还未提交过（或数据里没有保存提交文本），暂无可展示的执行过程。")

    http_server = getattr(request.app.state, "http_server", None)
    agent = getattr(http_server.bridge, "agent", None) if http_server is not None else None
    hist_mgr = getattr(agent, "_hist", None) if agent is not None else None
    if hist_mgr is None:
        return _empty("当前无法访问 agent 会话历史，暂不支持查看执行细节。")

    raw_entries = _locate_step_history_entries(hist_mgr, step.submitted_message)
    from_raw_history = False
    if raw_entries is None:
        # [第十一轮] active history 里没找到，大概率是已被 compact 压缩掉了——
        # 退化查询 raw_history（只追加、永不压缩），仍按同一份匹配逻辑找
        # 最后一次命中。raw_history 本身规模可能很大，但只在"active history
        # 未命中"这一少见路径才会触发全量扫描，日常一次看板点击不受影响。
        try:
            raw_history_entries = hist_mgr.raw_history.entries
        except Exception:
            raw_history_entries = []
        raw_entries = _locate_entries_in_list(raw_history_entries, step.submitted_message)
        if raw_entries is not None:
            from_raw_history = True
    if raw_entries is None:
        return _empty("在当前会话历史里没有找到这一步对应的记录（可能已被压缩/归档，且原始日志里也未找到），暂不支持查看执行细节。")

    entries = []
    for entry in raw_entries:
        formatted = _format_history_entry_for_trace(entry)
        if formatted is not None:
            entries.append(formatted)

    return {
        "execution_id": execution_id,
        "step_index": step_index,
        "description": step.description,
        "status": step.status,
        "entries": entries,
        "note": "（从压缩前的历史记录里找回，事件时间可能较早）" if from_raw_history else "",
        "from_raw_history": from_raw_history,
    }


# ── 进化提案分级自治（看板与自主性改进方案 Track I）──────────────────────────
# 第七轮实施记录里"未完成/待续"的看板可视化半成品：给 CLI 已有的
# `/evolution proposals` / `/evolution merge` 补上等价的 REST 端点，直接复用
# `classify_proposal_risk()` / `StateRepo.merge_branch()`，不重新设计核心逻辑
# （与 cli/commands/evolution.py::_handle_proposals()/_handle_merge() 的判断
# 逻辑保持一致，只是把 rich 表格输出换成 JSON）。

def _evolution_state_repo(request: Request):
    """定位当前 agent 项目所在仓库的 StateRepo，供进化提案端点复用。

    与 `/evolution` slash 命令一致：固定指向 `agent.cfg.project_root`，
    不支持指定别的仓库路径（不是通用 git 客户端）。
    """
    from mini_agent.evolution.state_repo import StateRepo, StateRepoError

    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    self_agent = getattr(http_server.bridge, "agent", None)
    project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
    if not project_root:
        raise HTTPException(status_code=503, detail="project_root not configured")
    try:
        return StateRepo(project_root)
    except StateRepoError as e:
        raise HTTPException(status_code=503, detail=f"Failed to open StateRepo: {e}")


@router.get("/evolution/proposals")
async def list_evolution_proposals(request: Request):
    """GET /v1/evolution/proposals — 列出所有 `evolve/*` 提案分支及风险分级。

    逐条返回 `classify_proposal_risk()` 的结果（`ProposalRisk.to_dict()`），
    与 `/evolution proposals` 命令行输出的判断逻辑完全一致，供看板"进化
    提案" tab 渲染列表 + risk 徽标，决定是否展示"一键合并"按钮。
    """
    from mini_agent.evolution.proposal_risk import classify_proposal_risk

    repo = _evolution_state_repo(request)
    branches = repo.list_branches(prefix="evolve/")
    items = [classify_proposal_risk(repo, branch).to_dict() for branch in branches]
    return {"items": items, "count": len(items)}


@router.get("/evolution/proposals/{branch:path}/diff")
async def get_evolution_proposal_diff(request: Request, branch: str):
    """GET /v1/evolution/proposals/{branch}/diff — 该提案分支相对基准分支的
    unified diff 全文（`StateRepo.diff()` 已有现成方法，这里只是接线），
    供看板"进化提案" tab 展开查看改动内容，不需要跳回命令行。
    `branch` 用 `:path` 转换器是因为提案分支名固定带 `/`
    （例如 `evolve/2026-07-20-skill-foo`），普通路径参数会在第一个 `/`
    处截断。
    """
    repo = _evolution_state_repo(request)
    if branch not in repo.list_branches():
        raise HTTPException(status_code=404, detail=f"branch {branch!r} not found")
    base = repo.current_branch() or "HEAD"
    diff_text = repo.diff(base, branch)
    return {"branch": branch, "base": base, "diff": diff_text}


@router.post("/evolution/proposals/{branch:path}/merge")
async def merge_evolution_proposal(request: Request, branch: str):
    """POST /v1/evolution/proposals/{branch}/merge — 一键合并提案分支。

    Body（可选）: `{"force": bool}`。行为与 `/evolution merge <branch>
    [--force]` 完全一致：risk=low 时直接合并；risk=high 时默认拒绝并在
    错误信息里给出判定依据，需要显式传 `force: true` 才会合并——`force`
    本身仍然是一次人工决定（看板侧应该用一个需要二次确认的入口去调用
    这个参数，而不是让它成为默认行为），不代表跳过了"人工审核"这件事
    本身。
    """
    from mini_agent.evolution.state_repo import StateRepoError
    from mini_agent.evolution.proposal_risk import classify_proposal_risk

    repo = _evolution_state_repo(request)
    if branch not in repo.list_branches():
        raise HTTPException(status_code=404, detail=f"branch {branch!r} not found")

    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    force = bool(body.get("force", False))

    result = classify_proposal_risk(repo, branch)
    if result.risk != "low" and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"branch {branch!r} is risk={result.risk!r}, "
                            "refusing to merge without explicit force=true",
                "risk": result.to_dict(),
            },
        )

    try:
        commit_hash = repo.merge_branch(branch)
    except StateRepoError as e:
        raise HTTPException(status_code=409, detail=f"Merge failed: {e}")

    return {
        "ok": True,
        "branch": branch,
        "merged_into": repo.current_branch(),
        "commit": commit_hash,
        "risk": result.risk,
    }


# ── 全局待办通知中心（看板与自主性改进方案 Track A）──────────────────────────

@router.get("/inbox")
async def get_inbox(request: Request):
    """GET /v1/inbox — 跨所有 session 聚合"待办列表"：
      - pending 权限审批请求（不再局限于"当前最近活跃 session"，而是
        遍历 SessionAgentPool 里所有活跃 session；单用户模式下只有一个
        bridge，等价于原有 /v1/permissions/pending）
      - pending 通用交互请求（同上）
      - 执行失败的 Objective（ObjectiveExecutor.status == "failed"）

    每项统一返回 {type, session_id, objective_id?, summary, created_at,
    req_id?, execution_id?}，供看板顶栏渲染待办徽标/下拉列表，点击后
    跳转到对应 session 并定位到该请求。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    items: list[dict] = []

    # ── 权限 / 交互请求：遍历所有活跃 session 的 bridge ──────────────────
    pool = _session_pool(request)
    if pool is not None:
        bridges = [(e.session_id, e.bridge) for e in pool.list_entries() if e.bridge is not None]
    else:
        bridges = [(getattr(http_server.bridge, "session_id", None), http_server.bridge)]

    seen_bridge_ids = set()
    for sid, bridge in bridges:
        if bridge is None or id(bridge) in seen_bridge_ids:
            continue
        seen_bridge_ids.add(id(bridge))
        try:
            for p in bridge.permission_gate.list_pending():
                items.append({
                    "type": "permission",
                    "session_id": sid,
                    "req_id": p.get("req_id") or p.get("id"),
                    "summary": p.get("summary") or p.get("tool_name") or "待审批的权限请求",
                    "created_at": p.get("created_at"),
                })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_inbox.permissions')
        try:
            for it in bridge.interaction_gate.list_pending():
                data = it.get("data") or {}
                summary = data.get("prompt") or data.get("question") or f"待回答的交互请求（{it.get('kind', '?')}）"
                items.append({
                    "type": "interaction",
                    "session_id": sid,
                    "req_id": it.get("req_id"),
                    "summary": summary,
                    "created_at": it.get("created_at"),
                })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_inbox.interactions')

    # ── 失败/卡住的 Objective ────────────────────────────────────────────
    al = http_server.autonomous_loop
    oe = getattr(al, "_objective_executor", None) if al is not None else None
    if oe is None:
        oe = getattr(http_server.bridge, "_objective_executor", None)
    if oe is not None:
        try:
            for ex in oe.get_status_summary():
                if ex.get("status") == "failed":
                    items.append({
                        "type": "objective_failed",
                        "session_id": None,
                        "objective_id": ex.get("objective_id"),
                        "execution_id": ex.get("execution_id"),
                        "summary": f"「{ex.get('title')}」执行失败：{ex.get('progress_notes') or '未知原因'}",
                        "created_at": ex.get("finished_at"),
                    })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_inbox.objectives')

    # [汇报独立存储 变更] 外部输入网关的 notify_only 告警不再聚合进全局待办中心。
    # 理由：这类告警已经有专门的展示入口——看板"外部输入网关"tab 的
    # "🔔 待处理告警"面板（/v1/external_input/alerts）——混进
    # "跨会话待办"会让语义完全不同的两件事（"需要你审批/回答才能继续
    # 执行"vs"外部世界发生了一件事，仅供知悉"）显示在同一个列表里。
    # watchlist_report 分级汇报同理，也已经拆到独立的
    # /v1/notifications/pending，见 notification/reports_store.py。

    items.sort(key=lambda it: it.get("created_at") or 0, reverse=True)
    return {"items": items, "count": len(items)}


@router.post("/inbox/external_alerts/{alert_id}/ack")
async def ack_external_alert(request: Request, alert_id: str):
    """POST /v1/inbox/external_alerts/{alert_id}/ack — 标记一条外部输入
    notify_only 告警为已处理，之后不再出现在 /v1/inbox 聚合结果里。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    proj_root = getattr(http_server.bridge.agent.cfg, "project_root", None) if http_server.bridge.agent else None
    if proj_root is None:
        raise HTTPException(status_code=503, detail="project_root not available")

    from mini_agent.external_input.policy import acknowledge_alert
    from mini_agent.storage.paths import AgentPaths
    ok = acknowledge_alert(AgentPaths(proj_root), alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"External alert {alert_id!r} not found or already acknowledged")
    return {"ok": True}


# ── watchlist_report 分级汇报专用端点（P8，独立于外部输入网关告警）────────
#
#   GET  /v1/notifications/pending           待处理汇报（分页，含完整 detail 正文）
#   POST /v1/notifications/pending/{id}/ack  标记一条汇报为已读
#
# 存储在独立的 notification/reports.jsonl，跟 external_input/alerts.jsonl
# 彻底分开，也不再出现在 /v1/inbox 全局待办中心里，见
# notification/reports_store.py 模块 docstring。

@router.get("/notifications/pending")
async def get_pending_reports(request: Request, limit: int = 20, offset: int = 0):
    """GET /v1/notifications/pending?limit=20&offset=0 — 分页返回未读的
    watchlist_report 汇报，每条都带完整 `detail`（汇报正文，含命中明细），
    供看板"关注与通知"tab 的"📋 待处理汇报"面板展开显示。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    proj_root = getattr(http_server.bridge.agent.cfg, "project_root", None) if http_server.bridge.agent else None
    if proj_root is None:
        raise HTTPException(status_code=503, detail="project_root not available")

    from mini_agent.notification.reports_store import list_pending_reports, count_pending_reports
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(proj_root)
    total = count_pending_reports(paths)
    reports = list_pending_reports(paths, limit=limit, offset=offset)
    return {
        "reports": reports,
        "total": total,
        "has_more": offset + len(reports) < total,
    }


@router.post("/notifications/pending/{report_id}/ack")
async def ack_pending_report(request: Request, report_id: str):
    """POST /v1/notifications/pending/{report_id}/ack — 标记一条
    watchlist_report 汇报为已读。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    proj_root = getattr(http_server.bridge.agent.cfg, "project_root", None) if http_server.bridge.agent else None
    if proj_root is None:
        raise HTTPException(status_code=503, detail="project_root not available")

    from mini_agent.notification.reports_store import acknowledge_report
    from mini_agent.storage.paths import AgentPaths
    ok = acknowledge_report(AgentPaths(proj_root), report_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found or already acknowledged")
    return {"ok": True}


# ── 外部输入网关 REST API（看板"🔌 外部输入"面板，P6）────────────────────────
#
#   GET  /v1/external_input/sources         已配置的 source 列表 + 运行时健康度
#   POST /v1/external_input/sources/reload  热重载 sources.yaml（先校验再生效）
#   GET  /v1/external_input/policies        policies.yaml 里的路由规则（只读）
#   GET  /v1/external_input/events          最近的 external.* 事件流水（不消费游标）
#
# 均为 owner-only 端点，对齐设计文档 §6："供人工核对路由是否符合预期"。
# 不提供在线编辑 sources.yaml/policies.yaml 内容本身的写端点——YAML 还是
# 直接编辑文件；但"改完文件后不需要重启 daemon 就能生效"这件事本身足够
# 高频、且需要"先校验再生效"的保护逻辑，值得单独开一个 reload 端点，
# 而不是让使用者每次改配置都去重启 daemon。

def _project_root_or_503(http_server) -> Path:
    proj_root = getattr(http_server.bridge.agent.cfg, "project_root", None) if http_server.bridge.agent else None
    if proj_root is None:
        raise HTTPException(status_code=503, detail="project_root not available")
    return proj_root


@router.get("/external_input/sources")
async def list_external_input_sources(request: Request):
    """GET /v1/external_input/sources — 已配置 source 的类型/状态/上次轮询
    时间/健康度。健康度数据来自 daemon 内正在跑的 GatewayPoller 实例
    （`HttpServer._build_autonomous_loop` 里构造并 start()）；非 daemon
    模式或该实例构造失败时，退化为只读 sources.yaml 配置本身（健康字段
    全部为 null），不因为轮询线程不可用就让整个端点报错。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.config import load_sources_config
    paths = AgentPaths(proj_root)

    try:
        configs = load_sources_config(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.list_external_input_sources')
        configs = []

    poller = getattr(http_server.bridge, "_external_input_poller", None)
    health_by_id = poller.get_all_health() if poller is not None else {}

    sources = []
    for cfg in configs:
        health = health_by_id.get(cfg.id, {})
        sources.append({
            "id": cfg.id,
            "type": cfg.type,
            "enabled": cfg.enabled,
            "interval_seconds": cfg.interval_seconds,
            "is_running": poller.is_running(cfg.id) if poller is not None else None,
            "last_poll_ts": health.get("last_poll_ts"),
            "consecutive_failures": health.get("consecutive_failures", 0),
            "circuit_open": health.get("circuit_open", False),
            "last_error": health.get("last_error"),
        })
    return {
        "sources": sources,
        "poller_available": poller is not None,
    }


@router.post("/external_input/sources/reload")
async def reload_external_input_sources(request: Request):
    """POST /v1/external_input/sources/reload — 热重载 sources.yaml，
    不需要重启 daemon。

    先对新配置里"新增/被修改"的条目做一次可用性检测（类型是否注册 + 真实
    试跑一次 poll()），全部通过才真正切换配置、重启受影响 source 的
    轮询线程；只要有一条没通过，整体拒绝，旧配置继续照常运行。
    成功或失败都会各发布一条 `external.gateway.*` 事件（默认 notify_only
    落点），因此看板"待处理告警"/"最近事件流水"也会看到同一条提示；
    这里额外把结构化结果原样返回，方便前端在按下按钮后立刻展示，不用
    等下一次事件流水刷新。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    poller = getattr(http_server.bridge, "_external_input_poller", None)
    if poller is None:
        raise HTTPException(
            status_code=503,
            detail="GatewayPoller 当前不可用（非 daemon 模式，或启动时构造失败）",
        )

    try:
        result = poller.reload()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.reload_external_input_sources')
        raise HTTPException(status_code=500, detail=str(_mini_agent_exc))
    return result


@router.get("/external_input/policies")
async def list_external_input_policies(request: Request):
    """GET /v1/external_input/policies — policies.yaml 里的路由规则列表
    （只读，按文件里的顺序返回，与 `decide_action()` "首个匹配规则生效"
    的语义一致，方便使用者在看板上确认规则优先级）。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.policy import PoliciesConfigError, load_policies
    paths = AgentPaths(proj_root)

    try:
        rules = load_policies(paths)
    except PoliciesConfigError as exc:
        return {"rules": [], "_error": str(exc)}

    return {
        "rules": [
            {"match": r.match, "action": r.action, "enqueue": r.enqueue}
            for r in rules
        ]
    }


@router.get("/external_input/events")
async def list_external_input_events(request: Request, limit: int = 50, offset: int = 0):
    """GET /v1/external_input/events?limit=50&offset=0 — 最近的 external.*
    事件流水，倒序（最新的在前面）。

    直接尾读 `system_events.jsonl` 并按 `event_type` 前缀过滤，**不**走
    `poll_since()`/消费游标——这是给人看的"最近发生了什么"展示，不是一个
    消费者，不该跟 `soft_goal_deriver`/`external_input_policy` 等真实消费
    者抢游标或互相干扰。`limit` 上限 200，避免看板一次性请求把整份
    events.jsonl（可能包含大量非 external.* 事件）读回来。

    `offset`（配合看板"⬇️ 加载更多"分页）：跳过排序后最靠前的 `offset`
    条 external.* 事件，再取 `limit` 条；`offset=0` 时行为与改动前完全
    一致。响应新增 `has_more`：还有没有扫描到但未返回的更早事件。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(proj_root)
    p = paths.system_events
    events: list[dict] = []
    has_more = False
    if p.exists():
        try:
            import json as _json
            lines = p.read_text(encoding="utf-8").splitlines()
            # 从文件尾部往前扫，跳过前 offset 条、再收够 limit 条 external.*
            # 就停，避免大文件全量反序列化——跟
            # external_input/policy.py::list_pending_alerts() 里"体量不大就
            # 全量扫描"的取舍不同，这里 events.jsonl 体量可能很大（承载了
            # 所有子系统的事件，不只是外部输入），值得做这个优化。多扫一条
            # 用来判断 has_more，不计入返回结果。
            skipped = 0
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                if not str(d.get("event_type", "")).startswith("external."):
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                if len(events) >= limit:
                    has_more = True
                    break
                events.append(d)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.routes.list_external_input_events')
    return {"events": events, "has_more": has_more}


@router.get("/external_input/alerts")
async def list_external_input_alerts_paginated(request: Request, limit: int = 20, offset: int = 0):
    """GET /v1/external_input/alerts?limit=20&offset=0 — 分页返回未处理的
    notify_only 告警（`alerts.jsonl`），倒序（最新的在前面）。

    专用于看板"🔌 外部输入"tab 的"待处理告警"面板分页展示——跟 /v1/inbox
    是两回事：/v1/inbox 聚合了权限/交互/失败 Objective/外部告警等多种
    待办类型给顶栏徽标用，不适合在那里加分页语义；这里只服务这一个面板，
    独立分页不影响 /v1/inbox 现有调用方。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.policy import count_pending_alerts, list_pending_alerts
    paths = AgentPaths(proj_root)

    alerts = list_pending_alerts(paths, limit=limit, offset=offset)
    total = count_pending_alerts(paths)
    return {"alerts": alerts, "total": total, "has_more": offset + len(alerts) < total}


# ── 外部输入网关可观测性（成功率/延迟趋势，见改造方案 §3）────────────────────

@router.get("/external_input/health_history")
async def get_external_input_health_history(
    request: Request, source_id: Optional[str] = None, since_days: int = 7,
):
    """GET /v1/external_input/health_history?source_id=&since_days=7 —
    返回 `poll_history.summarize_poll_history()` 的聚合结果：`source_id`
    留空则返回全部 source 各自的聚合，传入则只返回该 source 的聚合
    （total_polls/success_rate/avg_duration_ms/p50/p95/按天时间序列）。
    纯只读聚合查询，不消费游标、不改变任何状态，可以被高频调用（看板
    刷新）而没有副作用。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)
    since_days = max(1, min(since_days, 90))

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.poll_history import summarize_poll_history
    paths = AgentPaths(proj_root)

    try:
        result = summarize_poll_history(paths, source_id=source_id, since_days=since_days)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_external_input_health_history')
        raise HTTPException(status_code=500, detail=str(_mini_agent_exc))
    return result


# ── 长期归档 / 回顾式查询（§4）────────────────────────────────────────────

@router.get("/archive/query")
async def query_archive_records(
    request: Request,
    category: str,
    since: str,
    until: str,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """GET /v1/archive/query?category=external_input&since=2026-06&until=2026-06&keyword=agent&limit=50&offset=0

    `category`：external_input / notification（对应归档子目录）；
    `since`/`until`：自然月粒度（"YYYY-MM"）；`keyword`：对 title/detail
    做简单子串匹配（不区分大小写）。归档数据只读，本端点不提供任何写操作。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.archive.gc import query_archive
    paths = AgentPaths(proj_root)

    try:
        result = query_archive(
            paths, category=category, since=since, until=until,
            keyword=keyword, limit=limit, offset=offset,
        )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.query_archive_records')
        raise HTTPException(status_code=500, detail=str(_mini_agent_exc))
    return result


# ── "新颖重要事件"受控出口（独立通道，不进 /v1/inbox，见改造方案 §2）──────

@router.get("/external_input/novelty_candidates")
async def list_novelty_candidates(request: Request, limit: int = 20, offset: int = 0):
    """GET /v1/external_input/novelty_candidates?limit=20&offset=0 —
    分页返回待确认的新颖信号候选（status=pending）。明确不聚合进
    /v1/inbox——这是独立通道，语义是"系统主动发现的、可能值得开一个新
    方向的建议"，跟"待办中心"/"待处理告警"/"待处理汇报"三个既有面板
    都不是一回事。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.novelty_judge import (
        count_pending_novelty_candidates, list_pending_novelty_candidates,
    )
    paths = AgentPaths(proj_root)
    total = count_pending_novelty_candidates(paths)
    candidates = list_pending_novelty_candidates(paths, limit=limit, offset=offset)
    return {"candidates": candidates, "total": total, "has_more": offset + len(candidates) < total}


# ── 外部知识反馈闭环计划 P1-P5 只读汇总（供看板一次性拉取展示）──────────

@router.get("/evolution/feedback_loop_summary")
async def get_feedback_loop_summary(request: Request):
    """GET /v1/evolution/feedback_loop_summary — 只读汇总
    next_doc/external_knowledge_feedback_loop_improvement_plan.md
    P1-P5 五个模块各自的当前状态，供看板一次性拉取展示，不需要看板前端
    分别拼五个请求。任何一路读取失败都单独 try/except 隔离，不影响其余
    四路（跟这些模块自身"单点失败不阻塞其余"的既有风格一致）。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)

    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(proj_root)

    result: dict = {}

    # P1：候选队列过期巡检——novelty_candidates.jsonl 里各状态计数
    try:
        import json as _json
        p1 = {"pending": 0, "expired": 0, "confirmed": 0, "dismissed": 0}
        log_path = paths.notification_novelty_candidates
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                status = rec.get("status", "pending")
                p1[status] = p1.get(status, 0) + 1
        result["candidate_queue_triage"] = p1
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_feedback_loop_summary.p1')
        result["candidate_queue_triage"] = {"_error": str(_mini_agent_exc)}

    # P2：wiki 利用率审计——最近一次落盘的 usage_stats.json
    try:
        from mini_agent.evolution.wiki_utility_audit import load_wiki_usage_stats
        stats = load_wiki_usage_stats(paths)
        top_used = sorted(
            stats.items(), key=lambda kv: -kv[1].get("hit_count", 0)
        )[:10]
        result["wiki_utility_audit"] = {
            "total_pages_with_stats": len(stats),
            "top_used": [{"page_id": pid, **s} for pid, s in top_used],
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_feedback_loop_summary.p2')
        result["wiki_utility_audit"] = {"_error": str(_mini_agent_exc)}

    # P3：阈值自校准——当前生效阈值 + 最近调整历史
    try:
        from mini_agent.evolution.relevance_threshold_calibration import load_calibration_state
        cal = load_calibration_state(paths)
        result["relevance_threshold_calibration"] = {
            "current_threshold": cal.current_threshold,
            "created_at": cal.created_at,
            "last_calibrated_at": cal.last_calibrated_at,
            "history": (cal.history or [])[-5:],
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_feedback_loop_summary.p3')
        result["relevance_threshold_calibration"] = {"_error": str(_mini_agent_exc)}

    # P4a：外部趋势 x 能力薄弱点候选
    try:
        from mini_agent.evolution.external_trend_capability_link import load_external_trend_candidates
        candidates = load_external_trend_candidates(paths)
        result["external_trend_capability_link"] = {
            "candidate_count": len(candidates),
            "candidates": [c.to_dict() for c in candidates[:10]],
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_feedback_loop_summary.p4a')
        result["external_trend_capability_link"] = {"_error": str(_mini_agent_exc)}

    # P4b：生态定位扫描——种子轮转游标 + 已落盘的 external_ecosystem 页面数
    try:
        import json as _json
        state_path = paths.external_input_ecosystem_positioning_state
        rotation = {}
        if state_path.exists():
            rotation = _json.loads(state_path.read_text(encoding="utf-8"))
        from mini_agent.wiki.stats import compute_stats
        wiki_stats = compute_stats(paths)
        result["ecosystem_positioning_scan"] = {
            "rotation_offset": rotation.get("offset", 0),
            "last_run_at": rotation.get("last_run_at"),
            "ecosystem_pages_count": wiki_stats.by_source_kind.get("external_ecosystem", 0),
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_feedback_loop_summary.p4b')
        result["ecosystem_positioning_scan"] = {"_error": str(_mini_agent_exc)}

    # P5：月度战略回顾——最新一期文档内容 + 已产出的月份列表
    try:
        d = paths.monthly_trend_retrospective_dir
        months = sorted(p.stem for p in d.glob("*.md")) if d.exists() else []
        latest_content = ""
        if months:
            latest_content = paths.monthly_trend_retrospective_path(months[-1]).read_text(encoding="utf-8")
        result["monthly_trend_retrospective"] = {
            "months": months,
            "latest_month": months[-1] if months else None,
            "latest_content": latest_content,
        }
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_feedback_loop_summary.p5')
        result["monthly_trend_retrospective"] = {"_error": str(_mini_agent_exc)}

    return result


# ── hybrid_exec（脚本/LLM/Agent 混合执行系统）只读汇总 ────────────────
# next_doc/hybrid_exec_design_plan.md §6/§8 P4：供看板一次性拉取展示各
# task_id 当前的脚本仓库状态（active 版本/成功率）+ run 统计汇总，不需要
# 看板前端分别拼多个请求，也不需要看板知道存储细节。

@router.get("/hybrid_exec/summary")
async def get_hybrid_exec_summary(request: Request):
    """GET /v1/hybrid_exec/summary — 只读汇总所有 hybrid_exec task_id
    的脚本仓库状态 + run 统计，供看板展示。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)

    try:
        from mini_agent.hybrid_exec.kanban_summary import build_kanban_summary
        return build_kanban_summary(proj_root)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.get_hybrid_exec_summary')
        return {"tasks": [], "_error": str(_mini_agent_exc)}


@router.post("/external_input/novelty_candidates/{candidate_id}/confirm")
async def confirm_novelty_candidate_endpoint(request: Request, candidate_id: str):
    """POST /v1/external_input/novelty_candidates/{id}/confirm — 确认：
    创建一个新 Goal，标记 status=confirmed。这是唯一允许创建新 Goal 的
    入口，且只能由用户手动点击触发，不存在任何自动确认路径。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.novelty_judge import confirm_novelty_candidate
    paths = AgentPaths(proj_root)

    node = confirm_novelty_candidate(paths, candidate_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Novelty candidate {candidate_id!r} not found or already processed")
    return {"ok": True, "goal_id": node.id, "goal_title": node.title}


@router.post("/external_input/novelty_candidates/{candidate_id}/dismiss")
async def dismiss_novelty_candidate_endpoint(request: Request, candidate_id: str):
    """POST /v1/external_input/novelty_candidates/{id}/dismiss — 忽略：
    标记 status=dismissed，不创建 Goal，纯粹是"我看过了，不需要"。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)
    proj_root = _project_root_or_503(http_server)

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.external_input.novelty_judge import dismiss_novelty_candidate
    paths = AgentPaths(proj_root)

    ok = dismiss_novelty_candidate(paths, candidate_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Novelty candidate {candidate_id!r} not found or already processed")
    return {"ok": True}


# ── Cron Jobs REST API ────────────────────────────────────────────────────────

@router.get("/cron/jobs")
async def list_cron_jobs(request: Request):
    """GET /v1/cron/jobs — 列出所有 cron job。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    # [看板删除 cron 后刷新又出现 bugfix] 与 add/update/delete 三个路由统一
    # 走 _get_cron_scheduler() 的兜底顺序（bridge._cron_scheduler 优先，
    # 其次 autonomous_loop._cron_scheduler）——此前这里是本地手写的兜底
    # 逻辑，add/update/delete 三个路由却各自只取 bridge._cron_scheduler、
    # 没有这个兜底。当 bridge._cron_scheduler 为 None 而实际调度器挂在
    # autonomous_loop 上时，GET 能读到 job 列表，但 DELETE 会因为
    # cs is None 直接 503 失败（或者两者拿到的根本不是同一个调度器实例，
    # 删除操作作用在错误的实例上）——表现出来就是"看板点删除，刷新后
    # cron job 又出现了"。统一入口后，四个路由永远读写同一个调度器对象。
    cs = _get_cron_scheduler(http_server)
    if cs is None:
        return {"jobs": [], "note": "CronScheduler not available (daemon mode required)"}

    jobs = cs.list_jobs()
    return {
        "jobs": [
            {
                **j.to_dict(),
                "next_run_str": j.next_run_str(),
                # [kanban_execution_visibility_and_control_plan.md 阶段 B]
                # 区分 "not_running"/"queued"/"running"，看板据此把
                # "正在执行" 和 "排队等待" 分开展示，不再混为一谈。
                "execution_phase": cs.execution_phase(j.id),
            }
            for j in jobs
        ]
    }


@router.post("/cron/jobs")
async def add_cron_job(request: Request):
    """
    POST /v1/cron/jobs
    Body: { "name": str, "schedule": str, "task_template": str, "description": str,
            "priority": int (可选，缺省按 add_job() 的 run_mode 规则决定) }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    # 统一走 _get_cron_scheduler()，理由见 list_cron_jobs() 中的说明。
    cs = _get_cron_scheduler(http_server)
    if cs is None:
        raise HTTPException(status_code=503, detail="CronScheduler not available")

    body = await request.json()
    name = body.get("name", "").strip()
    schedule = body.get("schedule", "").strip()
    task_template = body.get("task_template", "").strip()
    if not name or not schedule or not task_template:
        raise HTTPException(status_code=400, detail="name, schedule, task_template are required")

    try:
        add_kwargs = dict(
            name=name,
            schedule=schedule,
            task_template=task_template,
            description=body.get("description", ""),
        )
        if "priority" in body:
            add_kwargs["priority"] = body["priority"]
        job = cs.add_job(**add_kwargs)
        return {"job": {**job.to_dict(), "next_run_str": job.next_run_str()}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, request: Request):
    """
    PUT /v1/cron/jobs/{job_id}
    Body: { "enabled": bool, "schedule": str, "priority": int }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    # 统一走 _get_cron_scheduler()，理由见 list_cron_jobs() 中的说明。
    cs = _get_cron_scheduler(http_server)
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
        if "priority" in body:
            cs.update_priority(job_id, body["priority"])
        job = cs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return {"job": {**job.to_dict(), "next_run_str": job.next_run_str()}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str, request: Request):
    """DELETE /v1/cron/jobs/{job_id} — 彻底删除一个 cron job。

    系统内置 job（id 以 "sys:" 开头）不可删除，只能禁用——与
    CronScheduler.remove_job() 内部的保护逻辑保持一致，这里提前给出
    更明确的 400 错误信息，而不是让前端只拿到一个笼统的 "删除失败"。
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    # 统一走 _get_cron_scheduler()，理由见 list_cron_jobs() 中的说明——
    # 这也是本次 bugfix 的核心：之前这里只取 bridge._cron_scheduler，
    # 与 GET /cron/jobs 的兜底顺序不一致，导致 bridge._cron_scheduler
    # 为 None 时删除请求要么 503 失败、要么（若两处解析出不同的调度器
    # 实例）删除作用在错误实例上，看板刷新后 job 又出现。
    cs = _get_cron_scheduler(http_server)
    if cs is None:
        raise HTTPException(status_code=503, detail="CronScheduler not available")

    job = cs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if getattr(job, "is_system", False):
        raise HTTPException(
            status_code=400,
            detail=f"系统内置 job '{job_id}' 不可删除，只能禁用（PUT /v1/cron/jobs/{job_id} enabled=false）",
        )

    try:
        ok = cs.remove_job(job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not ok:
        raise HTTPException(status_code=500, detail="Job delete failed")
    return {"deleted": True, "job_id": job_id}


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


@router.post("/cron/jobs/{job_id}/feedback")
async def add_cron_job_feedback(job_id: str, request: Request):
    """POST /v1/cron/jobs/{job_id}/feedback — [goal_cron_feedback_and_output_
    policy_plan.md 3.5] 持久化提意见，写入 description/task_template（及
    dedicated 模式下的 prompt.md）。若该 job 绑定了 Goal（run_mode=goal_cycle），
    自动双向同步到对应 Goal。
    Body: { "text": str }
    """
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    _require_owner(request)

    cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is None:
        raise HTTPException(status_code=503, detail="CronScheduler not available")

    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    job = cs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    ok = cs.add_user_feedback(job_id, text)
    if not ok:
        raise HTTPException(status_code=500, detail="add_user_feedback failed")
    job = cs.get(job_id)
    return {"job": {**job.to_dict(), "next_run_str": job.next_run_str()} if job else None}


# ── Cron Job Workspace REST API（专属执行机制：进度/日志/prompt/卡死恢复）───
#
#   GET   /v1/cron/jobs/{id}/workspace        state + config + 最近执行列表
#   GET   /v1/cron/jobs/{id}/prompt           读取用户可编辑的 prompt.md
#   PUT   /v1/cron/jobs/{id}/prompt           修改 prompt.md
#   GET   /v1/cron/jobs/{id}/runs/{run_id}    某次执行的完整事件流
#   POST  /v1/cron/jobs/{id}/reset            把卡死(needs_human_review)的
#                                              job 重置回 idle，清空 progress

def _get_cron_scheduler(http_server):
    """统一获取 CronScheduler，与 list_cron_jobs() 保持一致的兜底顺序：
    优先 bridge._cron_scheduler，其次 autonomous_loop._cron_scheduler。"""
    cs = getattr(http_server.bridge, "_cron_scheduler", None)
    if cs is None:
        al = http_server.autonomous_loop
        cs = getattr(al, "_cron_scheduler", None) if al else None
    return cs


def _get_cron_paths(request: Request):
    """取 AgentPaths，用于定位 .agent/cron_jobs/。复用现有 self_agent.cfg.project_root
    获取方式（见本文件其它 self_profile/goal_backlog 相关端点）。"""
    from mini_agent.storage.paths import AgentPaths
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    self_agent = getattr(http_server.bridge, "agent", None)
    project_root = getattr(getattr(self_agent, "cfg", None), "project_root", None)
    if project_root is None:
        raise HTTPException(status_code=503, detail="project_root not available")
    return http_server, AgentPaths(project_root)


@router.get("/cron/jobs/{job_id}/workspace")
async def get_cron_job_workspace(job_id: str, request: Request):
    """GET /v1/cron/jobs/{job_id}/workspace — 该 job 的 state/config/最近执行列表。
    看板"Cron Jobs" tab 的主要数据源，job_id 传原始 id（如 "sys:consolidation"
    或 "user:ab12cd34"），CronJobWorkspace 内部会处理成文件系统安全的目录名。"""
    _require_owner(request)
    http_server, paths = _get_cron_paths(request)

    cs = _get_cron_scheduler(http_server)
    job = cs.get(job_id) if cs is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
    ws = CronJobWorkspace(paths, job_id)
    ws.ensure(default_task_template=job.task_template)
    state = ws.read_state()
    config = ws.read_config()
    is_running = cs.is_job_running(job_id) if cs is not None else False

    return {
        "job_id": job_id,
        "state": state.to_dict(),
        "config": config.to_dict(),
        "is_running": is_running,
        "recent_runs": ws.recent_runs(limit=10),
    }


@router.get("/cron/jobs/{job_id}/prompt")
async def get_cron_job_prompt(job_id: str, request: Request):
    """GET /v1/cron/jobs/{job_id}/prompt — 读取用户可编辑的 prompt.md 原文。"""
    _require_owner(request)
    http_server, paths = _get_cron_paths(request)

    cs = _get_cron_scheduler(http_server)
    job = cs.get(job_id) if cs is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
    ws = CronJobWorkspace(paths, job_id)
    ws.ensure(default_task_template=job.task_template)
    return {"job_id": job_id, "prompt": ws.read_prompt()}


@router.put("/cron/jobs/{job_id}/prompt")
async def update_cron_job_prompt(job_id: str, request: Request):
    """
    PUT /v1/cron/jobs/{job_id}/prompt
    Body: { "prompt": str }
    覆盖写入 prompt.md，下次该 job 触发时立即生效（无需重启 daemon）。
    """
    _require_owner(request)
    http_server, paths = _get_cron_paths(request)

    cs = _get_cron_scheduler(http_server)
    job = cs.get(job_id) if cs is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    body = await request.json()
    prompt = body.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must be a non-empty string")

    from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
    ws = CronJobWorkspace(paths, job_id)
    ws.ensure(default_task_template=job.task_template)
    try:
        ws.prompt_path.write_text(prompt, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"job_id": job_id, "prompt": prompt}


@router.get("/cron/jobs/{job_id}/runs/{run_id}")
async def get_cron_job_run_events(job_id: str, run_id: str, request: Request):
    """GET /v1/cron/jobs/{job_id}/runs/{run_id} — 某次执行的完整逐步事件流，
    供看板"回放"某次 cron 执行的诊断视图（每步输出摘要/是否触发卡死恢复/
    超时/异常等）。"""
    _require_owner(request)
    http_server, paths = _get_cron_paths(request)

    from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
    ws = CronJobWorkspace(paths, job_id)
    events = ws.read_run_events(run_id)
    return {"job_id": job_id, "run_id": run_id, "events": events}


@router.post("/cron/jobs/{job_id}/reset")
async def reset_cron_job_workspace(job_id: str, request: Request):
    """
    POST /v1/cron/jobs/{job_id}/reset
    把处于 needs_human_review（卡死判定 GIVE_UP / 单步异常）状态的 job
    人工介入确认后重置为 idle，同时清空 progress_summary（放弃续接，
    下次触发从头开始）。正在执行中（is_job_running）的 job 拒绝重置，
    避免和后台线程写 state.json 产生竞争。
    """
    _require_owner(request)
    http_server, paths = _get_cron_paths(request)

    cs = _get_cron_scheduler(http_server)
    job = cs.get(job_id) if cs is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if cs is not None and cs.is_job_running(job_id):
        raise HTTPException(status_code=409, detail="Job is currently running, cannot reset")

    from mini_agent.evolution.cron_job_workspace import CronJobWorkspace, CronJobState
    ws = CronJobWorkspace(paths, job_id)
    ws.ensure(default_task_template=job.task_template)
    ws.write_state(CronJobState())
    return {"job_id": job_id, "state": CronJobState().to_dict()}


# ── Workflow REST API（workflow机制改进计划（P7）一）───────────────────────
#
# 这批端点是 workflow/tools.py 里同名 @tool 工具的"薄封装"：真正的状态机
# 逻辑都在 workflow/api_helpers.py 的纯函数里，两边共用，不重复维护。
# 鉴权沿用 owner-only（与 cron jobs 一致）。

def _workflow_cfg(request: Request):
    """取出 AppConfig，用于调用 workflow/api_helpers.py 里的纯函数。"""
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    agent = getattr(http_server.bridge, "agent", None)
    cfg = getattr(agent, "cfg", None) if agent else None
    if cfg is None:
        raise HTTPException(status_code=503, detail="AppConfig not available")
    return cfg


def _workflow_api_error_to_http(e) -> HTTPException:
    from mini_agent.workflow.api_helpers import WorkflowApiError
    if not isinstance(e, WorkflowApiError):
        return HTTPException(status_code=500, detail=str(e))
    status_map = {
        "not_found": 404,
        "no_pending": 409,
        "not_active": 409,
        "bad_snapshot": 422,
        "bad_step": 422,
        "cyclic": 422,
    }
    return HTTPException(status_code=status_map.get(e.code, 400), detail=e.message)


@router.get("/workflows")
async def list_workflows_route(request: Request):
    """GET /v1/workflows — 列出已保存的工作流。"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    return {"workflows": api_helpers.list_workflows(cfg)}


@router.get("/workflows/{name}")
async def get_workflow_yaml_route(name: str, request: Request):
    """GET /v1/workflows/{name} — 查看 YAML 定义。"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    try:
        yaml_str = api_helpers.get_workflow_yaml(cfg, name)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"name": name, "yaml": yaml_str}


@router.post("/workflows/{name}/steps/{step_id}/patch")
async def patch_workflow_step_route(name: str, step_id: str, request: Request):
    """
    POST /v1/workflows/{name}/steps/{step_id}/patch — 单步编辑（改进方案 §4.2）。
    Body: {"patch": {"prompt": "...", "timeout": 120, ...}}
    只修改已保存工作流定义里某个 step 的部分字段，不用重贴整份 YAML；
    改动落盘到工作流本体，后续所有执行都受益。配合
    `resume_workflow_run(force_rerun_from=step_id)` 使用可以只重跑这一步及下游。
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    body = await request.json() if await request.body() else {}
    patch_dict = body.get("patch") or {}
    try:
        outcome = api_helpers.patch_workflow_step(cfg, name, step_id, patch_dict)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"patched": True, "step_id": step_id, **outcome}


@router.post("/workflows/{name}/preview")
async def preview_workflow_route(name: str, request: Request):
    """
    POST /v1/workflows/{name}/preview — dry-run 预览，不实际执行。
    Body: {"inputs": {...}}
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    body = await request.json() if await request.body() else {}
    inputs = body.get("inputs") or {}
    try:
        return api_helpers.preview_workflow(cfg, name, inputs)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)


@router.get("/workflows/{name}/stats")
async def get_workflow_stats_route(name: str, request: Request):
    """
    GET /v1/workflows/{name}/stats — 汇总历史执行统计（P9-1a
    workflow_system_next_directions.md §1.2a）：成功率、各步骤平均耗时/
    评分/重试率、condition 命中率。纯读取聚合，不涉及执行。
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    return api_helpers.get_workflow_stats(cfg, name)


@router.post("/workflows/{name}/run")
async def run_workflow_route(name: str, request: Request):
    """
    POST /v1/workflows/{name}/run
    Body: {"inputs": {...}, "background": true}
    background 语义同 run_workflow 工具：含 require_approval 步骤时强制后台执行。
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers

    body = await request.json() if await request.body() else {}
    inputs = body.get("inputs") or {}
    background = body.get("background")
    force_serial = body.get("force_serial")
    require_all_inputs_upfront = bool(body.get("require_all_inputs_upfront") or False)
    output_export_dir = body.get("output_export_dir") or None

    try:
        outcome = api_helpers.start_workflow_run(
            cfg, name, inputs, background,
            force_serial=force_serial,
            require_all_inputs_upfront=require_all_inputs_upfront,
            output_export_dir=output_export_dir,
        )
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)

    if outcome["mode"] == "sync":
        result = outcome["result"]
        return {
            "mode": "sync",
            "workflow_session_id": result.workflow_session_id,
            "status": result.status,
            "total_duration": result.total_duration,
            "final_output": result.final_output,
            "output_dir": result.output_dir,
            "output_export_result": result.output_export_result,
            "step_results": [sr.to_dict() for sr in result.step_results],
        }
    return {
        "mode": "async",
        "workflow_session_id": outcome["workflow_session_id"],
        "output_dir": outcome["output_dir"],
        "has_approval_step": outcome["has_approval_step"],
        "output_export_dir": output_export_dir,
    }


@router.get("/workflow_runs")
async def list_workflow_runs_route(request: Request, name: Optional[str] = Query(default=None)):
    """GET /v1/workflow_runs — 列出所有执行记录（?name= 可过滤）。"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    return {"runs": api_helpers.list_workflow_runs(cfg, name)}


@router.get("/workflow_runs/{run_id}")
async def get_workflow_run_detail_route(run_id: str, request: Request):
    """GET /v1/workflow_runs/{id} — 单次执行详情。"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    try:
        return api_helpers.get_workflow_run_detail(cfg, run_id)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)


@router.get("/workflow_runs/{run_id}/events")
async def get_workflow_run_events_route(
    run_id: str, request: Request, since_line: int = Query(default=0)
):
    """GET /v1/workflow_runs/{id}/events — events.jsonl 增量拉取。"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    return api_helpers.read_workflow_run_events(cfg, run_id, since_line)


@router.post("/workflow_runs/{run_id}/pause")
async def pause_workflow_run_route(run_id: str, request: Request):
    """POST /v1/workflow_runs/{id}/pause"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    try:
        api_helpers.pause_workflow_run(cfg, run_id)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"paused": True, "workflow_session_id": run_id}


@router.post("/workflow_runs/{run_id}/cancel")
async def cancel_workflow_run_route(run_id: str, request: Request):
    """POST /v1/workflow_runs/{id}/cancel"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    try:
        api_helpers.cancel_workflow_run(cfg, run_id)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"cancelled": True, "workflow_session_id": run_id}


@router.post("/workflow_runs/{run_id}/mark_interrupted")
async def mark_workflow_run_interrupted_route(run_id: str, request: Request):
    """
    POST /v1/workflow_runs/{run_id}/mark_interrupted — 孤儿运行修复：把一条
    daemon 重启/崩溃后遗留的、磁盘状态仍是 running/paused/awaiting_approval
    但进程内已无活跃控制的执行记录，直接改判为 cancelled。仅在确实是孤儿
    记录时才允许操作，见 api_helpers.mark_workflow_run_interrupted。
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    try:
        return api_helpers.mark_workflow_run_interrupted(cfg, run_id)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)


@router.post("/workflow_runs/{run_id}/resume")
async def resume_workflow_run_route(run_id: str, request: Request):
    """
    POST /v1/workflow_runs/{id}/resume
    Body: {"background": true, "force_rerun_from": "step_id"}
    force_rerun_from 为单步编辑续跑场景：配合 .../steps/{step_id}/override 先改输出，
    再传同一个 step_id 触发"该 step 之后全部重跑"。
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers

    body = await request.json() if await request.body() else {}
    background = body.get("background")
    force_rerun_from = body.get("force_rerun_from")

    try:
        outcome = api_helpers.resume_workflow_run(cfg, run_id, background, force_rerun_from)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)

    if outcome["mode"] == "sync":
        result = outcome["result"]
        return {
            "mode": "sync",
            "workflow_session_id": result.workflow_session_id,
            "status": result.status,
            "final_output": result.final_output,
        }
    return {"mode": "async", "workflow_session_id": run_id}


@router.post("/workflow_runs/{run_id}/approve")
async def approve_workflow_step_route(run_id: str, request: Request):
    """POST /v1/workflow_runs/{id}/approve"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    try:
        step_id = api_helpers.approve_workflow_step(cfg, run_id)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"approved": True, "step_id": step_id, "workflow_session_id": run_id}


@router.post("/workflow_runs/{run_id}/reject")
async def reject_workflow_step_route(run_id: str, request: Request):
    """POST /v1/workflow_runs/{id}/reject — Body: {"reason": str}"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    body = await request.json() if await request.body() else {}
    reason = body.get("reason", "")
    try:
        step_id = api_helpers.reject_workflow_step(cfg, run_id, reason)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"rejected": True, "step_id": step_id, "workflow_session_id": run_id}


@router.post("/workflow_runs/{run_id}/input")
async def provide_workflow_input_route(run_id: str, request: Request):
    """POST /v1/workflow_runs/{id}/input — Body: {"text": str}"""
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    body = await request.json() if await request.body() else {}
    text = body.get("text", "")
    try:
        step_id = api_helpers.provide_workflow_step_input(cfg, run_id, text)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"provided": True, "step_id": step_id, "workflow_session_id": run_id}


@router.post("/workflow_runs/{run_id}/steps/{step_id}/override")
async def override_workflow_step_output_route(run_id: str, step_id: str, request: Request):
    """
    POST /v1/workflow_runs/{id}/steps/{step_id}/override
    Body: {"output": str}
    人工编辑已完成 step 的输出（单步编辑续跑第一步，第二步用
    POST .../resume 传 force_rerun_from=step_id 触发下游重跑）。
    """
    cfg = _workflow_cfg(request)
    _require_owner(request)
    from mini_agent.workflow import api_helpers
    body = await request.json() if await request.body() else {}
    output = body.get("output", "")
    try:
        api_helpers.override_step_output(cfg, run_id, step_id, output)
    except api_helpers.WorkflowApiError as e:
        raise _workflow_api_error_to_http(e)
    return {"overridden": True, "step_id": step_id, "workflow_session_id": run_id}


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
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.routes.perception_browser_stop')
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


# ── 主动推荐与数字分身机制设计方案：日报 / 推荐 / 决策画像 只读端点 ─────────────
# 供 Kanban 看板等前端展示用，均为只读；生成/刷新仍分别走 cron job 或
# /digest daily、/next refresh、/decision_profile update 命令，这里不重复触发。

def _get_paths_for_request(request: Request) -> "AgentPaths":
    http_server = getattr(request.app.state, "http_server", None)
    if http_server is None:
        raise HTTPException(status_code=503, detail="HttpServer not available")
    from mini_agent.storage.paths import AgentPaths
    self_agent = http_server.bridge.agent
    project_root = getattr(self_agent.cfg, "project_root", None) if self_agent else None
    if not project_root:
        raise HTTPException(status_code=503, detail="project_root not configured")
    return AgentPaths(project_root)


# ── P7：看板只读展示端点 ──────────────────────────────────────────────────────
# 设计背景见 next_doc/watchlist_notification_goal_design.md §6 P7。三个端点
# 全部是只读、无副作用（GET only），配置文件本身仍然只能靠用户手改 yaml——
# 这里不提供写接口，跟设计文档"看板展示"这条范围保持一致，不引入配置
# 编辑器。

@router.get("/notification/watchlist")
async def get_notification_watchlist(request: Request):
    """GET /v1/notification/watchlist — 只读返回 watchlist.yaml 里配置的
    全部关注对象条目（含 enabled=false 的，方便看板区分"已配置但暂停"和
    "从未配置过"两种情况）。watchlist.yaml 不存在时返回空列表，不是错误——
    这是全新项目的正常初始状态。"""
    _require_owner(request)
    paths = _get_paths_for_request(request)
    try:
        from dataclasses import asdict
        from mini_agent.external_input.watchlist import load_watchlist_config
        items = load_watchlist_config(paths)
        return {"items": [asdict(item) for item in items]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/notification/report_tiers")
async def get_notification_report_tiers(request: Request):
    """GET /v1/notification/report_tiers — 只读返回 report_tiers.yaml 里
    配置的全部 tier，附带每个 tier 对应 cron job 的运行时信息（下次触发
    时间/是否 enabled），以及 tier_state.json 里的空转计数（§9.2 #7）。
    cron_scheduler 拿不到（比如 daemon 尚未完全启动）时 job 相关字段
    退化为 None，不影响 tier 配置本身的展示。"""
    _require_owner(request)
    paths = _get_paths_for_request(request)
    try:
        from dataclasses import asdict
        from mini_agent.external_input.report_tiers import load_report_tiers_config, _load_tier_state
        tiers = load_report_tiers_config(paths)
        tier_state = _load_tier_state(paths)

        http_server = getattr(request.app.state, "http_server", None)
        cron_scheduler = _get_cron_scheduler(http_server) if http_server is not None else None
        jobs_by_id = {}
        if cron_scheduler is not None:
            jobs_by_id = {j.id: j for j in cron_scheduler.list_jobs()}

        result = []
        for tier in tiers:
            entry = asdict(tier)
            job = jobs_by_id.get(tier.job_id)
            entry["job_id"] = tier.job_id
            entry["job_enabled"] = job.enabled if job else None
            entry["next_run_str"] = job.next_run_str() if job else None
            entry["idle_streak"] = (tier_state.get(tier.id) or {}).get("idle_streak", 0)
            result.append(entry)
        return {"tiers": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/notification/dispatch_log")
async def get_notification_dispatch_log(request: Request, limit: int = Query(50, ge=1, le=500)):
    """GET /v1/notification/dispatch_log?limit=50 — 只读返回最近 N 条
    NotificationDispatcher 的发送记录（`dispatch_log.jsonl`，见
    `notification/dispatcher.py::_append_dispatch_log`），倒序（最新的在
    前面）。文件不存在/为空时返回空列表。响应新增 `has_more`：文件里是否
    还有比这 `limit` 条更早的记录（供看板"⬇️ 加载更多"分页按钮判断是否
    还要展示）。"""
    _require_owner(request)
    paths = _get_paths_for_request(request)
    try:
        p = paths.notification_dispatch_log
        if not p.exists():
            return {"entries": [], "has_more": False}
        raw_lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        entries = []
        for line in raw_lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
        entries.reverse()
        return {"entries": entries, "has_more": len(raw_lines) > limit}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/digest/pending_startup")
async def get_pending_startup_digest(request: Request):
    """GET /v1/digest/pending_startup — daemon connected 客户端专用。

    [新增] 之前只有本地直连模式（cli/repl.py::run_repl）在启动时会调用
    _print_startup_digest_and_advisor() 打印一行"未读日报"摘要，因为它
    直接持有本地 Agent 对象，能直接读 paths / cfg。daemon 模式下的
    run_connected_repl() 只有一个走 HTTP 的 DaemonClient，没有本地 Agent，
    之前完全没有对接这块——导致 daemon 客户端连接时永远看不到这行提示。

    这里补一个只读端点：复用 evolution/daily_digest.py::load_pending_digest()
    读取"最近一份 shown_at 为空的日报"，不做任何生成/刷新（生成仍然只由
    cron job 负责），也不在这里标记为已读——标记动作交给下面的
    POST /digest/pending_startup/ack，由客户端确认真正打印出来之后再调用，
    避免"服务端一返回就标记已读，但客户端因为网络问题没显示出来"的丢失。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution.daily_digest import load_pending_digest
        data = load_pending_digest(paths)
        return {"digest": data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/digest/pending_startup/ack")
async def ack_pending_startup_digest(request: Request):
    """POST /v1/digest/pending_startup/ack {"day": "YYYY-MM-DD"}

    客户端把 GET /digest/pending_startup 返回的日报实际打印出来之后，
    调用这个端点把对应日期标记为 shown_at，避免下次连接（或其它客户端
    连接）时重复展示同一份日报。
    """
    _require_owner(request)
    try:
        body = await request.json()
        day = (body or {}).get("day")
        if not day:
            raise HTTPException(status_code=400, detail="missing 'day'")
        paths = _get_paths_for_request(request)
        from mini_agent.evolution.daily_digest import mark_shown
        mark_shown(paths, day)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/digest/daily")
async def get_daily_digest(request: Request, date: Optional[str] = Query(None)):
    """GET /v1/digest/daily?date=YYYY-MM-DD — 读取（不生成）某天的融合日报。
    date 缺省为最近一份已生成的日报；都没有时返回 {"digest": None}。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        if date:
            json_path = paths.daily_reports_dir / f"{date}.json"
            if not json_path.exists():
                return {"digest": None}
            import json as _json
            return {"digest": _json.loads(json_path.read_text(encoding="utf-8"))}

        # 未指定日期：取目录里最新的一份
        d = paths.daily_reports_dir
        if not d.exists():
            return {"digest": None}
        files = sorted(d.glob("*.json"))
        if not files:
            return {"digest": None}
        import json as _json
        return {"digest": _json.loads(files[-1].read_text(encoding="utf-8"))}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/next_actions")
async def get_next_actions(request: Request):
    """GET /v1/next_actions — 读取当前落盘的主动推荐候选（不重新计算）。
    对应设计方案第 4.2 节，/next 命令的只读版本。
    """
    _require_owner(request)
    try:
        from mini_agent.evolution.next_action_advisor import load_pending_next_actions
        paths = _get_paths_for_request(request)
        p = paths.next_actions_path
        if not p.exists():
            return {"next_actions": None}
        import json as _json
        return {"next_actions": _json.loads(p.read_text(encoding="utf-8"))}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/decision_profile")
async def get_decision_profile(request: Request):
    """GET /v1/decision_profile — 读取当前决策画像（Markdown 原文 + 结构化模式列表）。
    对应设计方案第 4.4 节。画像不存在时返回 {"exists": false}。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        md_path = paths.user_value_profile_path
        if not md_path.exists():
            return {"exists": False, "markdown": None, "patterns": []}
        markdown = md_path.read_text(encoding="utf-8")
        patterns = []
        try:
            import json as _json
            state = _json.loads(paths.decision_profile_state_path.read_text(encoding="utf-8"))
            patterns = state.get("patterns", [])
        except Exception:
            pass
        return {"exists": True, "markdown": markdown, "patterns": patterns}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── 成长顾问 Growth Advisor 看板端点 ────────────────────────────────────────
# [next_doc/growth_advisor_design.md] P1 里程碑："看板 tab 上线"。
# 与上面的 /notification/watchlist 系列一样走只读展示 + 少量显式动作
# （accept/dismiss/scan）的模式，不在 API 层做任何自动推送判断——推送节奏
# 完全由 cron job + GrowthAdvisorConfig.notification_* 控制，这里只负责
# 把当前状态如实透出给看板。

@router.get("/growth/summary")
async def get_growth_summary(request: Request):
    """GET /v1/growth/summary — 返回当前候选队列（pending 优先）+ 已生成的
    调研报告列表 + 月度复盘统计 + 首次触达提示是否已展示过 + 诊断快照
    （配置/信号扫描命中情况/记忆条目数，供用户自查"为什么候选一直是 0"），
    供看板"🌱 成长顾问"tab 一次性渲染。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.memory_store import MemoryStore
        from mini_agent.profile import UserProfileManager

        backlog = ga.GrowthBacklog(paths)
        candidates = backlog.load_all()
        reports = ga.list_reports(paths)
        retro = ga.monthly_retrospective_summary(paths)

        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        if cfg is None:
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()
        profile = UserProfileManager(paths).load()
        # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
        # 方向一] 之前这里是 `MemoryStore(paths)`——把整个 AgentPaths 实例
        # 当路径传了进去，静默降级为空记忆列表，导致诊断面板"记忆总条数"
        # 永远是 0，跟健康度趋势里 cron 任务记的真实条数对不上。
        from mini_agent.perception.memory_factory import build_default_memory_store
        store = build_default_memory_store(paths)
        profile_cfg = getattr(self_agent.cfg, "profile", None) if self_agent else None
        # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2
        # 第二步] 跟 `/growth/align` 的 `goal_alignment_llm_enabled` 同款
        # opt-in 约定：只有配置开启且拿得到 agent 上下文时才传 llm_helper，
        # `diagnostics_snapshot()` 内部再按 `feedback_pattern_llm_enabled`
        # 决定要不要真的触发那次归纳调用。
        llm_helper = None
        if self_agent is not None and getattr(cfg, "feedback_pattern_llm_enabled", False):
            helper = getattr(self_agent, "llm_helper", None)
            if helper is not None:
                llm_helper = lambda prompt: helper.ask(prompt)
        diagnostics = ga.diagnostics_snapshot(
            paths, cfg, profile, store, profile_cfg=profile_cfg, llm_helper=llm_helper,
        )

        cs = _get_cron_scheduler(http_server) if http_server else None
        if cs is not None:
            jobs_by_id = {j.id: j for j in cs.list_jobs()}
            # [next_doc/memory_backfill_and_profile_update_plan.md 看板展示]
            # 把 sys:memory_backfill_scan 也一并透出，跟 diagnostics.memory.
            # backfill_candidates_count 搭配展示——看板能同时看到"还有多少
            # 候选没处理"和"上一次自动回填是什么时候跑的"。
            diagnostics["cron_jobs"] = {
                jid: {
                    "enabled": j.enabled,
                    "last_run_at": j.last_run_at,
                    "next_run_at": j.next_run_at,
                    "run_count": j.run_count,
                    "consecutive_skip_count": j.consecutive_skip_count,
                }
                for jid, j in jobs_by_id.items()
                if jid in (ga.JOB_ID_DAILY, ga.JOB_ID_MONTHLY, "sys:memory_backfill_scan")
            }
        else:
            diagnostics["cron_jobs"] = {"_note": "CronScheduler not available (daemon mode required)"}

        return {
            "candidates": [c.to_dict() for c in candidates],
            "reports": [r.to_dict() for r in reports],
            "retrospective": retro,
            "first_touch_notice_shown": ga.first_touch_notice_shown(paths),
            "diagnostics": diagnostics,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/first_touch_ack")
async def post_growth_first_touch_ack(request: Request):
    """POST /v1/growth/first_touch_ack — 看板展示过首次触达提示后调用，
    跨会话持久化"已经提示过"，避免每次打开看板都重新弹一次（方案第 8 节
    第 1 条：知情权不能省略，但也不能变成每次都打断）。幂等：重复调用
    不会报错，也不会重置已记录的展示时间。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        ga.mark_first_touch_notice_shown(paths)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/scan")
async def post_growth_scan(request: Request):
    """POST /v1/growth/scan — 手动触发一轮信号扫描 + 候选生成 + Top-N 调研
    报告（等价于 CLI `/growth scan` / cron sys:growth_advisor_daily 的内容），
    供看板"立即为我看看"按钮调用。规则式实现，不依赖 LLM，可安全同步执行
    （信号扫描本身；候选/报告生成阶段是否走 LLM 由各自的 opt-in 开关决定）。

    [BUGFIX] `run_daily_cycle()` 内部把"信号扫描"（写 profile.derived，
    包含诊断面板展示的 last_scan_at）、"候选生成"、"报告生成"（可能走
    LLM，耗时更长、更容易失败）串在一条调用链里，而 profile 的落盘此前
    统一放在整条链跑完之后——如果候选生成/报告生成阶段抛异常（比如
    report_quality_llm_enabled 开着但 LLM 调用超时/出错），整个请求会
    500，profile.save() 根本不会被执行，连"已经成功完成的信号扫描"这部分
    结果都跟着丢了：诊断面板会一直显示"还没有扫描记录"，即使实际上扫描
    本身是成功的。现在无论后面阶段是否失败都会尝试落盘 profile——正常
    路径 mgr.save() 照旧，异常路径在转换成 HTTPException 之前先补一次
    mgr.save()，至少诊断面板能如实反映"扫描确实跑过"。
    """
    _require_owner(request)
    paths = _get_paths_for_request(request)
    from mini_agent.evolution import growth_advisor as ga
    from mini_agent.perception.memory_store import MemoryStore
    from mini_agent.profile import UserProfileManager

    mgr = UserProfileManager(paths)
    profile = mgr.load()
    try:
        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        if cfg is None:
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()

        # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
        # 方向一] 同上：改用统一的工具函数，避免手动扫描在空记忆列表上跑，
        # 导致 0 命中、LLM 信号增强永远因"未匹配记忆数不足"被跳过。
        from mini_agent.perception.memory_factory import build_default_memory_store
        store = build_default_memory_store(paths)
        llm_helper = None
        if self_agent is not None and getattr(cfg, "llm_signal_augment_enabled", False):
            helper = getattr(self_agent, "llm_helper", None)
            if helper is not None:
                llm_helper = lambda prompt, _h=helper: _h.ask(prompt)
        # [next_doc/growth_advisor_cron_search_and_status_history_plan.md
        # 方向一] 同 CLI `/growth scan`：传入 web_search_fn 只是让能力
        # 可用，是否真正触发仍由 cfg.cron_triggered_active_search_enabled
        # 这个显式开关决定。
        from mini_agent.tools.builtin import web_search as _web_search_fn
        result = ga.run_daily_cycle(
            paths, cfg, profile, store,
            llm_helper=llm_helper, web_search_fn=_web_search_fn,
        )
        mgr.save()
        # [kanban_perception_gaps_improvement_plan.md 方向 D.1] 复用这个
        # 既有的每日调用点，顺带记一条 Objective 完成率快照——不新增独立
        # 线程/cron。best-effort：跟成长顾问本身的信号扫描是两件不相关的
        # 事，快照记录失败绝不能让本次成长顾问扫描的结果也跟着 500。
        try:
            from mini_agent.evolution.objective_trend import record_objective_completion_snapshot
            record_objective_completion_snapshot(paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.api.routes.post_growth_scan.objective_trend_snapshot")
        return result
    except HTTPException:
        raise
    except Exception as e:
        # 信号扫描阶段（`ga.growth_signal_scan` 内部）已经把结果写进了
        # 上面 load() 出来的同一个 profile 对象；即使异常发生在后面的候选/
        # 报告生成阶段，这里也要把已经完成的那部分落盘，不能让整轮请求
        # 500 就把"扫描确实跑过"这个事实也一并丢掉。落盘本身失败（磁盘满/
        # 权限问题等）不覆盖原始异常，只做尽力而为。
        try:
            mgr.save()
        except Exception as save_exc:
            from mini_agent.errors import log_exception
            log_exception(save_exc, where="mini_agent.api.routes.post_growth_scan.fallback_save")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/candidates/{candidate_id}/{action}")
async def post_growth_candidate_action(request: Request, candidate_id: str, action: str):
    """POST /v1/growth/candidates/{id}/accept|dismiss — 看板上对单个候选的
    显式反馈动作，写入 GrowthFeedbackLedger（供后续置信度调优参考）。

    [反馈粒度细化] dismiss 时可选传一个 JSON body `{"reason": "..."}`，
    取值见 `growth_advisor._VALID_DISMISS_REASONS`；不传 body 或不传
    `reason` 字段都等价于 `reason=None`（记为 unspecified，行为与此前
    版本完全一致）。accept 动作忽略 body 内容。
    """
    _require_owner(request)
    if action not in ("accept", "dismiss"):
        raise HTTPException(status_code=400, detail="action must be accept or dismiss")
    try:
        reason = None
        if action == "dismiss":
            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                reason = body.get("reason") or None

        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        if reason is not None and reason not in ga._VALID_DISMISS_REASONS:
            raise HTTPException(status_code=400, detail=f"invalid dismiss reason: {reason}")

        status = ga.STATUS_ACCEPTED if action == "accept" else ga.STATUS_DISMISSED
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.set_status(candidate_id, status)
        if cand is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        ga.GrowthFeedbackLedger(paths).record(candidate_id, status, reason=reason)
        response: dict = {"ok": True, "candidate": cand.to_dict()}

        # [采纳即启动] action == accept 且配置开启（默认开启）时，自动
        # 衔接"生成报告 → 落地为 Goal → 生成并确认执行规范 → 绑定周期性"
        # 全部后续步骤，不需要用户再逐步点。任一后续步骤失败都不影响
        # accept 本身已经成功——失败信息放进 `pursuit.errors`，看板据此
        # 尽力而为地提示，而不是让这个请求整体 500。
        if action == "accept":
            http_server = getattr(request.app.state, "http_server", None)
            self_agent = getattr(http_server.bridge, "agent", None) if http_server else None
            growth_cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
            if growth_cfg is None:
                from mini_agent.config.models import GrowthAdvisorConfig
                growth_cfg = GrowthAdvisorConfig()
            if getattr(growth_cfg, "auto_pursue_on_accept", True):
                try:
                    from mini_agent.perception.goal_backlog import GoalBacklog
                    goal_backlog = GoalBacklog(paths)
                    cron_scheduler = _get_cron_scheduler(http_server) if http_server else None
                    pursuit = ga.auto_pursue_candidate(
                        paths, cand, goal_backlog=goal_backlog,
                        cron_scheduler=cron_scheduler, cfg=growth_cfg,
                    )
                    response["pursuit"] = {
                        "goal": pursuit["goal"].to_dict() if pursuit.get("goal") else None,
                        "cron_job": pursuit["cron_job"].to_dict() if pursuit.get("cron_job") else None,
                        "report_generated": pursuit.get("report_generated", False),
                        "errors": pursuit.get("errors", []),
                    }
                    refreshed = backlog.get(candidate_id)
                    if refreshed is not None:
                        response["candidate"] = refreshed.to_dict()
                except Exception as e:
                    from mini_agent.errors import log_exception
                    log_exception(e, where="mini_agent.api.routes.post_growth_candidate_action.auto_pursue")
                    response["pursuit"] = {"errors": [f"自动持续调研触发失败：{e}"]}

        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/followups")
async def get_growth_followups(request: Request):
    """GET /v1/growth/followups — 返回已采纳、满足回访窗口
    （`GrowthAdvisorConfig.followup_review_days`，默认 30 天）且尚未回访
    过的候选列表，供看板渲染"这个方向后续有没有推进？"回访卡片。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        if cfg is None:
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()
        # [growth_advisor_goal_cron_integration_plan.md 阶段 C] 尽力附带
        # GoalBacklog，让已关联 Goal 的候选优先用 Goal 真实状态判断是否
        # 该展示回访卡片；拿不到时两个函数自动退化为原有的 memory 证据数
        # 走势逻辑，不影响接口可用性。
        try:
            from mini_agent.perception.goal_backlog import GoalBacklog
            goal_backlog = GoalBacklog(paths)
        except Exception:
            goal_backlog = None
        candidates = ga.pending_followups(paths, cfg, goal_backlog=goal_backlog)
        return {
            "followups": [
                {
                    **c.to_dict(),
                    "question_hint": ga.followup_question_hint(
                        paths, c, cfg=cfg, goal_backlog=goal_backlog
                    ),
                }
                for c in candidates
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/followups/{candidate_id}/{outcome}")
async def post_growth_followup_record(request: Request, candidate_id: str, outcome: str):
    """POST /v1/growth/followups/{id}/progressed|stalled — 回答一次回访，
    结果写回候选并追加到 GrowthFeedbackLedger（供后续置信度调权参考）。"""
    _require_owner(request)
    if outcome not in ("progressed", "stalled"):
        raise HTTPException(status_code=400, detail="outcome must be progressed or stalled")
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        cand = ga.record_followup(paths, candidate_id, outcome)
        if cand is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return {"ok": True, "candidate": cand.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/keywords")
async def post_growth_keyword_add(request: Request):
    """POST /v1/growth/keywords — 用户在看板上手动添加一个自定义关键词
    主题，body: {"topic": str, "keywords": str | list[str]}（字符串按逗号/
    顿号/换行切分）。直接标记为已确认（`confirmed_by_user=True`）。"""
    _require_owner(request)
    try:
        body = await request.json()
        topic = str(body.get("topic") or "").strip()
        keywords = body.get("keywords") or []
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.profile import UserProfileManager

        mgr = UserProfileManager(paths)
        profile = mgr.load()
        entry = ga.add_custom_topic_keyword(profile, topic, keywords)
        mgr.save()
        return {"ok": True, "topic": topic, "entry": entry}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/keywords/{topic}/confirm")
async def post_growth_keyword_confirm(request: Request, topic: str):
    """POST /v1/growth/keywords/{topic}/confirm — 把一个系统学到、待确认
    的主题标记为已确认（看板"✅ 保留"按钮）。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.profile import UserProfileManager

        mgr = UserProfileManager(paths)
        profile = mgr.load()
        changed = ga.confirm_topic_keyword(profile, topic)
        if changed:
            mgr.save()
        return {"ok": True, "changed": changed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/keywords/{topic}/remove")
async def post_growth_keyword_remove(request: Request, topic: str):
    """POST /v1/growth/keywords/{topic}/remove — 删除自定义主题，或隐藏
    一个内置主题（看板"❌ 删除"/"🙈 隐藏"按钮）。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.profile import UserProfileManager

        mgr = UserProfileManager(paths)
        profile = mgr.load()
        changed = ga.remove_topic_keyword(profile, topic)
        if changed:
            mgr.save()
        return {"ok": True, "changed": changed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/keywords/{topic}/restore")
async def post_growth_keyword_restore(request: Request, topic: str):
    """POST /v1/growth/keywords/{topic}/restore — 恢复一个被隐藏的内置
    主题（P4-7，`remove` 的对称操作）。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.profile import UserProfileManager

        mgr = UserProfileManager(paths)
        profile = mgr.load()
        changed = ga.restore_builtin_topic_keyword(profile, topic)
        if changed:
            mgr.save()
        return {"ok": True, "changed": changed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/reports/refresh_candidates")
async def get_growth_reports_refresh_candidates(request: Request):
    """GET /v1/growth/reports/refresh_candidates — 返回"生成之后证据又
    显著增长、值得提示用户刷新一下"的报告列表（P4-4 增量刷新）。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        if cfg is None:
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()
        # [growth_advisor_autonomy_deepening_plan.md 方向 A1] 已经落地成
        # Goal 且绑定了周期性执行的候选，素材由 growth_pursuit 自动接管，
        # 不再需要"报告刷新"这条独立路径；拿不到 GoalBacklog 时优雅退化
        # 成不过滤（等价于改动前的行为），不因为这一步失败而影响主功能。
        goal_backlog = None
        try:
            from mini_agent.perception.goal_backlog import GoalBacklog
            goal_backlog = GoalBacklog(paths)
        except Exception:
            goal_backlog = None
        # [growth_advisor_autonomous_search_and_material_improvement_
        # plan.md 方向"外部世界变化驱动的刷新"] 只有配置开启时才多付
        # 一次 profile 加载 + 比对的成本，关闭时（默认）行为与改动前
        # 完全一致。
        profile = None
        if getattr(cfg, "report_external_drift_refresh_enabled", False):
            try:
                from mini_agent.profile import UserProfileManager
                profile = UserProfileManager(paths).load()
            except Exception:
                profile = None
        return {
            "refresh_candidates": ga.reports_needing_refresh(
                paths, cfg, goal_backlog=goal_backlog, profile=profile,
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/pursuits")
async def get_growth_pursuits(request: Request):
    """GET /v1/growth/pursuits — [growth_advisor_autonomy_deepening_plan.md
    方向 D1] 聚合"哪些方向正在被自主持续调研"：已采纳且关联了 Goal 的
    候选，逐条附上该 Goal 的周期性执行状态（下次执行时间/已跑轮数）和
    饱和度信号，供看板渲染"🔄 正在自主推进"总览，不需要用户跳到
    「🎯 目标」tab 理解 Goal/Cron 的内部机制。

    纯只读聚合，跨 GrowthBacklog + GoalBacklog + CronScheduler +
    growth_state.json 四个既有数据源拼装，不新增持久化。CronScheduler
    在非 daemon 模式下可能拿不到，此时对应条目的 `next_run_at`/
    `run_count` 为 `None`，不影响其余字段返回。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.goal_backlog import GoalBacklog

        backlog = ga.GrowthBacklog(paths)
        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()

        http_server = getattr(request.app.state, "http_server", None)
        cs = _get_cron_scheduler(http_server) if http_server else None
        jobs_by_id = {j.id: j for j in cs.list_jobs()} if cs is not None else {}

        pursuits = []
        for c in backlog.load_all():
            if not c.linked_goal_id:
                continue
            goal = goal_backlog.get(c.linked_goal_id)
            if goal is None:
                continue
            job = jobs_by_id.get(goal.recurrence_cron_job_id) if goal.recurrence_cron_job_id else None
            saturation = ga.get_pursuit_saturation(paths, goal.id)
            # [方向 C2] 还没被推送出去的"本轮新增摘要"，只读展示，不清空队列
            # （真正清空由下一次实际推送时触发，见 growth_advisor.py）。
            pending_digest = [
                d for d in ga.peek_pending_pursuit_digests(paths) if d.get("goal_id") == goal.id
            ]
            # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 1]
            # 素材参与度：素材已经比用户上次点开查看时新了几轮，供看板
            # 展示"距你上次查看已经过了 N 轮新内容"，纯只读聚合。
            engagement = ga.get_pursuit_material_engagement(paths, goal.id, goal.cycle_count)
            pursuits.append({
                "candidate_id": c.candidate_id,
                "title": c.title,
                "goal_id": goal.id,
                "goal_title": goal.title,
                "recurring": goal.recurring,
                "cycle_count": goal.cycle_count,
                "schedule": job.schedule if job else None,
                "next_run_at": job.next_run_at if job else None,
                "last_run_at": job.last_run_at if job else None,
                "run_count": job.run_count if job else None,
                "cron_enabled": job.enabled if job else None,
                "saturation": saturation,
                "pending_digest": pending_digest,
                "engagement": engagement,
                # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
                # 方向 6] 调研风格标记，落地时判定、`None` 表示未分类
                # （旧 Goal 或非自动推进路径），纯展示用途。
                "pursuit_style": getattr(goal, "growth_pursuit_style", None),
            })
        return {"pursuits": pursuits}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/pursuits/portfolio_summary")
async def get_growth_pursuits_portfolio_summary(request: Request):
    """GET /v1/growth/pursuits/portfolio_summary —
    [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 4] 多方向
    并行推进时的全局视角摘要：聚合饱和度信号（方向 B2）+ 参与度信号
    （方向 1），回答"我现在该先看哪几个方向"。纯只读聚合，不产生新的
    持久化，跟 `/growth/pursuits` 一样按需拉取（看板在展开分区时才
    请求一次，不放进默认响应）。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.goal_backlog import GoalBacklog

        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()

        http_server = getattr(request.app.state, "http_server", None)
        self_agent = getattr(http_server.bridge, "agent", None) if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        threshold = getattr(cfg, "pursuit_long_unviewed_threshold", None)
        if not threshold:
            from mini_agent.config.models import GrowthAdvisorConfig
            threshold = GrowthAdvisorConfig().pursuit_long_unviewed_threshold

        return ga.pursuits_portfolio_summary(paths, goal_backlog, long_unviewed_threshold=threshold)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/pursuits/related_directions")
async def get_growth_pursuits_related_directions(request: Request):
    """GET /v1/growth/pursuits/related_directions —
    [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 规划维度候选]
    多方向并行推进时的关联信号：哪些正在自主推进的方向，内容上跟另一
    个方向有共现关键词，值得互相参考。纯只读聚合，不产生新的持久化，
    跟 `/growth/pursuits/portfolio_summary` 一样按需拉取。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.goal_backlog import GoalBacklog
        from mini_agent.profile import UserProfileManager

        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()
        profile = UserProfileManager(paths).load()

        return {"relations": ga.related_pursuit_directions(paths, goal_backlog, profile=profile)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/pursuits/{goal_id}/view_material")
async def post_growth_pursuit_view_material(request: Request, goal_id: str):
    """POST /v1/growth/pursuits/{goal_id}/view_material —
    [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 1] 看板
    "📄 素材"按钮点击时调用，记一次"用户查看时素材处于第几轮"的轻量
    埋点，供 `/growth/pursuits` 的 `engagement` 字段计算"距上次查看过了
    几轮"。只需要 `goal_id`，当前轮次由后端从 GoalBacklog 读取（避免
    信任前端传来的轮次，也避免前端要额外拼一次请求体）。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.goal_backlog import GoalBacklog

        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()
        goal = goal_backlog.get(goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail=f"Goal 不存在：{goal_id}")
        return ga.record_pursuit_material_view(paths, goal_id, goal.cycle_count)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/align")
async def get_growth_align(request: Request):
    """GET /v1/growth/align — [growth_advisor_goal_cron_integration_
    plan.md 阶段 A] 兴趣方向 ⇄ Goal 对齐分析，CLI `/growth align` 的
    API 对应端点。纯只读聚合（除非开启 `goal_alignment_llm_enabled`
    且拿得到 agent 上下文，此时会额外触发一次 LLM 语义匹配，见
    `goal_growth_alignment()` docstring）。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.profile import UserProfileManager
        from mini_agent.perception.goal_backlog import GoalBacklog

        profile = UserProfileManager(paths).load()
        http_server = getattr(request.app.state, "http_server", None)
        self_agent = getattr(http_server.bridge, "agent", None) if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        if cfg is None:
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()

        llm_helper = None
        if self_agent is not None and getattr(cfg, "goal_alignment_llm_enabled", False):
            helper = getattr(self_agent, "llm_helper", None)
            if helper is not None:
                llm_helper = lambda prompt: helper.ask(prompt)

        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()
        return ga.goal_growth_alignment(paths, profile, cfg=cfg, goal_backlog=goal_backlog, llm_helper=llm_helper)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/align/adopt_all")
async def post_growth_align_adopt_all(request: Request):
    """POST /v1/growth/align/adopt_all — [growth_advisor_autonomy_
    deepening_plan.md 方向 A3] 批量落地对齐分析里"有兴趣但没建目标"的
    方向，复用 `auto_pursue_candidate()` 整条链路（生成报告 → 落地成
    Goal → 生成并确认执行规范 → 绑定周期性）。单次最多处理
    `growth_advisor.goal_alignment_adopt_all_max_batch`（默认 3）条，
    避免看板"全部采纳"按钮一次点击就意外触发过多 LLM 调用；剩余条目
    （`remaining_count`）留到下一次调用继续处理，不会丢失。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.profile import UserProfileManager
        from mini_agent.perception.goal_backlog import GoalBacklog

        profile = UserProfileManager(paths).load()
        http_server = getattr(request.app.state, "http_server", None)
        self_agent = getattr(http_server.bridge, "agent", None) if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        if cfg is None:
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()

        llm_helper = None
        if self_agent is not None and getattr(cfg, "goal_alignment_llm_enabled", False):
            helper = getattr(self_agent, "llm_helper", None)
            if helper is not None:
                llm_helper = lambda prompt: helper.ask(prompt)

        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()
        cron_scheduler = _get_cron_scheduler(http_server) if http_server else None
        result = ga.batch_adopt_unmatched_interests(
            paths, cfg, profile, goal_backlog=goal_backlog,
            cron_scheduler=cron_scheduler, llm_helper=llm_helper,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/align/confirm_match")
async def post_growth_align_confirm_match(request: Request):
    """POST /v1/growth/align/confirm_match — [growth_advisor_autonomy_
    deepening_plan_v2.md 方向 2] 把一条 `llm_suggested_matches` 里的
    建议确认成正式关联：把 `topic` 对应候选的 `linked_goal_id` 指向
    请求体里的 `goal_id`（不新建 Goal）。请求体：{"topic": str, "goal_id": str}。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.goal_backlog import GoalBacklog

        body = await request.json()
        topic = (body or {}).get("topic")
        goal_id = (body or {}).get("goal_id")
        if not topic or not goal_id:
            raise HTTPException(status_code=400, detail="topic 和 goal_id 均为必填。")

        goal_backlog = GoalBacklog(paths)
        goal_backlog.load()
        return ga.confirm_llm_suggested_match(paths, topic, goal_id, goal_backlog=goal_backlog)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_growth_health_trend(request: Request):
    """GET /v1/growth/health_trend — [next_doc/growth_advisor_improvement_plan_v4.md
    方向三 N1] 返回全局健康度快照序列（最近若干天，按时间正序），供看板
    画折线图。独立于 `/growth/summary`：趋势数据不需要每次打开 tab 都拉
    取，看板在用户展开"健康度趋势"区块时才请求，减少默认加载的数据量。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        limit = 30
        try:
            raw_limit = request.query_params.get("limit")
            if raw_limit is not None:
                limit = max(1, int(raw_limit))
        except (TypeError, ValueError):
            limit = 30
        return {"health_trend": ga.health_trend_series(paths, limit=limit)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/pursuits/{goal_id}/saturation_trend")
async def get_growth_pursuit_saturation_trend(request: Request, goal_id: str):
    """GET /v1/growth/pursuits/{goal_id}/saturation_trend — [growth_advisor_
    autonomy_deepening_plan_v2.md 方向 3] 返回某个正在自主推进的方向
    （Goal）最近若干轮的"是否低增量"时间序列，供看板展开某个方向时画一条
    简单走势（跟 `/growth/health_trend` 一样是按需拉取，不放进
    `/growth/pursuits` 的默认响应，避免每次打开 tab 都拉取历史数据）。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        limit = 30
        try:
            raw_limit = request.query_params.get("limit")
            if raw_limit is not None:
                limit = max(1, int(raw_limit))
        except (TypeError, ValueError):
            limit = 30
        return {"saturation_trend": ga.get_pursuit_saturation_trend(paths, goal_id, limit=limit)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/candidates/{candidate_id}/timeline")
async def get_growth_candidate_timeline(request: Request, candidate_id: str):
    """GET /v1/growth/candidates/{id}/timeline —
    [next_doc/growth_advisor_active_search_and_lifecycle_plan.md 方向二]
    返回该候选所属主题（按 dedupe_key）的完整成长轨迹事件列表（按时间
    正序），供看板画时间轴。跟 `/growth/health_trend` 一样是按需拉取的
    独立端点，不挤进 `/growth/summary` 的默认 payload。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        backlog = ga.GrowthBacklog(paths)
        candidate = backlog.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"候选不存在：{candidate_id}")

        goal_backlog = None
        try:
            from mini_agent.perception.goal_backlog import GoalBacklog
            http_server = getattr(request.app.state, "http_server", None)
            self_agent = http_server.bridge.agent if http_server is not None else None
            cfg = getattr(self_agent, "cfg", None) if self_agent else None
            project_root = getattr(cfg, "project_root", None) if cfg is not None else None
            if project_root is not None:
                goal_backlog = GoalBacklog(paths)
                goal_backlog.load()
        except Exception:
            goal_backlog = None

        events = ga.growth_topic_lifecycle(paths, candidate.dedupe_key(), goal_backlog=goal_backlog)
        return {"topic": candidate.title, "events": events}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/objectives/completion_trend")
async def get_objectives_completion_trend(request: Request, limit: int = Query(30, ge=1, le=200)):
    """GET /v1/objectives/completion_trend — [kanban_perception_gaps_
    improvement_plan.md 方向 D.1] Objective 完成率每日快照序列（最近若干
    天，按时间正序）：每天完成/失败的 Objective 数、平均重试次数、当前
    活跃 Objective 数。快照由 `POST /v1/growth/scan`（cron
    `sys:growth_advisor_daily` 每日调用）顺带记录，本端点只读，不触发
    任何计算。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution.objective_trend import objective_completion_trend_series
        return {"completion_trend": objective_completion_trend_series(paths, limit=limit)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/candidates/{candidate_id}/report/refresh")
async def post_growth_candidate_refresh_report(request: Request, candidate_id: str):
    """POST /v1/growth/candidates/{id}/report/refresh — 用新的（更多的）
    证据为该候选重新生成一份调研报告，替换候选当前挂着的报告；旧报告仍
    保留在历史记录里，不会被删除。是否使用 LLM 起草正文由
    `GrowthAdvisorConfig.report_quality_llm_enabled` 决定，与手动触发的
    `/growth/scan` 保持一致的判断逻辑。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        cfg = getattr(self_agent.cfg, "growth_advisor", None) if self_agent else None
        llm_helper = None
        if (
            self_agent is not None
            and cfg is not None
            and getattr(cfg, "report_quality_llm_enabled", False)
        ):
            helper = getattr(self_agent, "llm_helper", None)
            if helper is not None:
                llm_helper = lambda prompt, _h=helper: _h.ask(prompt)
        report = ga.refresh_growth_report(paths, candidate_id, llm_helper=llm_helper)
        if report is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return {"ok": True, "report": report.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/candidates/{candidate_id}/adopt_goal")
async def post_growth_candidate_adopt_goal(request: Request, candidate_id: str):
    """POST /v1/growth/candidates/{id}/adopt_goal — 把一个候选"落地"成
    GoalBacklog 里的一个 Goal 节点，交给 Goal/Cron 体系持续推进——这是
    用户采纳一个方向之后，真正让成长顾问"接着往下调研、收集素材"的
    衔接点（CLI `/growth adopt-goal <id>` 此前唯一入口，这里补上看板/
    API 路径，行为完全复用同一个 `adopt_candidate_as_goal()`）。

    要求候选已有调研报告（`report_id` 非空），否则 400；候选如果还是
    `pending` 会顺带流转成 `accepted`。只创建 Goal 本身，**不自动绑定
    周期性**——是否设为周期性执行、要不要顺手生成一份
    [Goal 执行规范](../../../docs/goal-execution-spec-guide.md）仍然是
    用户在 Goal 管理里显式决定的下一步，成长顾问不代管 Goal 生命周期。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga
        from mini_agent.perception.goal_backlog import GoalBacklog

        backlog = ga.GrowthBacklog(paths)
        candidate = next((c for c in backlog.load_all() if c.candidate_id == candidate_id), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        if not candidate.report_id:
            raise HTTPException(
                status_code=400,
                detail="该候选还没有调研报告，请先生成报告后再落地成目标",
            )

        goal_backlog = GoalBacklog(paths)
        goal = ga.adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
        return {"ok": True, "goal": goal.to_dict()}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/reports/{report_id}")
async def get_growth_report_body(request: Request, report_id: str):
    """GET /v1/growth/reports/{id} — 返回某份调研报告的 Markdown 正文。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        report = ga.get_report_by_id(paths, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        from pathlib import Path
        body_path = Path(report.body_path)
        body = body_path.read_text(encoding="utf-8") if body_path.exists() else ""
        d = report.to_dict()
        d["body"] = body
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/growth/candidates/{candidate_id}/material/generate")
async def post_growth_candidate_generate_material(request: Request, candidate_id: str):
    """POST /v1/growth/candidates/{id}/material/generate —
    [growth_advisor_autonomous_search_and_material_improvement_plan.md
    方向"报告与学习素材分层"] 为该候选生成一份『学习素材』（学习路径 +
    资源清单 + 第一个可执行任务），跟调研报告是两份独立产物、独立的
    生成入口——不要求候选已经有报告；如果已经有报告，会复用报告的
    摘要作为素材背景（见 `generate_learning_material()` 的 `report`
    参数说明）。已经生成过素材的候选重复调用会再生成一份新的（跟
    `report/refresh` 的"替换"语义不同，素材索引只追加，多次生成的
    历史都保留，候选上挂着的 `material_id` 会指向最新一份）。
    """
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        backlog = ga.GrowthBacklog(paths)
        candidate = next((c for c in backlog.load_all() if c.candidate_id == candidate_id), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")

        http_server = getattr(request.app.state, "http_server", None)
        self_agent = http_server.bridge.agent if http_server else None
        llm_helper = None
        if self_agent is not None:
            helper = getattr(self_agent, "llm_helper", None)
            if helper is not None:
                llm_helper = lambda prompt, _h=helper: _h.ask(prompt)

        report = ga.get_report_by_id(paths, candidate.report_id) if candidate.report_id else None
        material = ga.generate_learning_material(
            paths, candidate, llm_helper=llm_helper, report=report,
        )
        return {"ok": True, "material": material.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/growth/materials/{material_id}")
async def get_growth_material_body(request: Request, material_id: str):
    """GET /v1/growth/materials/{id} — 返回某份学习素材的 Markdown 正文
    及结构化字段（`learning_path`/`resources`/`first_task`）。"""
    _require_owner(request)
    try:
        paths = _get_paths_for_request(request)
        from mini_agent.evolution import growth_advisor as ga

        material = ga.get_material_by_id(paths, material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="material not found")
        from pathlib import Path
        body_path = Path(material.body_path)
        body = body_path.read_text(encoding="utf-8") if body_path.exists() else ""
        d = material.to_dict()
        d["body"] = body
        return d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
