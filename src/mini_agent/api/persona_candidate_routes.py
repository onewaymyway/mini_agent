"""api/persona_candidate_routes.py — 候选人设/能力自动检测 HTTP API。

设计背景见 next_doc/persona_candidate_autoscan_plan.md §5、§8 待确认问题
2（倾向新开独立文件而不是塞进 capability_routes.py：`capability_routes.py`
已经不小，且这是一个概念上独立的子系统，参照 `growth_advisor.py` 和
`capability_learning.py` 本来就是分开的两个模块这一先例）。

`_get_paths`/`_get_llm_helper` 与 `capability_routes.py` 同款写法；
`_require_owner`/`_async_jobs` 与 `api/routes.py` 同款写法——这两个都是
几行的薄封装，独立复制一份而不是从 routes.py（7000+ 行超大文件）import，
避免引入不必要的耦合/循环 import 风险，和 `capability_routes.py` 顶部
"独立成文件、独立成一个 APIRouter" 的取舍一致。

已挂载到 api/server.py（`app.include_router(persona_candidate_router)`）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mini_agent.evolution.persona_candidates import (
    PersonaCandidateStore,
    accept_candidate,
    dismiss_candidate,
    scan_persona_candidates,
)
from mini_agent.storage.paths import AgentPaths

persona_candidate_router = APIRouter(prefix="/v1/capability/persona_candidates")


def _get_paths(request: Request) -> AgentPaths:
    http_server = request.app.state
    bridge = getattr(http_server, "bridge", None)
    agent = getattr(bridge, "agent", None) if bridge is not None else None
    cfg = getattr(agent, "cfg", None) if agent is not None else None
    project_root = getattr(cfg, "project_root", None) if cfg is not None else None
    if project_root is None:
        raise HTTPException(status_code=503, detail="project_root 未就绪，Agent 可能尚未初始化")
    return AgentPaths(project_root)


def _get_llm_helper(request: Request):
    http_server = request.app.state
    bridge = getattr(http_server, "bridge", None)
    agent = getattr(bridge, "agent", None) if bridge is not None else None
    helper = getattr(agent, "llm_helper", None) if agent is not None else None
    if helper is None:
        return None
    return lambda prompt: helper.ask(prompt)


def _get_persona_candidate_cfg(request: Request):
    http_server = request.app.state
    bridge = getattr(http_server, "bridge", None)
    agent = getattr(bridge, "agent", None) if bridge is not None else None
    cfg = getattr(agent.cfg, "persona_candidates", None) if agent is not None else None
    if cfg is not None:
        return cfg
    from mini_agent.config.models import PersonaCandidateConfig
    return PersonaCandidateConfig()


def _require_owner(request: Request) -> None:
    """单用户模式（`role_store` 为 None）下直接放行，与
    `api/routes.py::_require_owner` 同款判断，见该函数文档字符串。"""
    user_ctx = getattr(request.state, "user_ctx", None)
    if user_ctx is None:
        return
    if not getattr(user_ctx, "is_owner", False):
        raise HTTPException(status_code=403, detail="Owner only")


def _async_jobs(request: Request):
    return request.app.state.async_jobs


# ── 请求体模型 ───────────────────────────────────────────────────────────


class DismissCandidateBody(BaseModel):
    reason: Optional[str] = None


# ── 端点 ─────────────────────────────────────────────────────────────────


@persona_candidate_router.get("")
def list_persona_candidates(request: Request, status: Optional[str] = None):
    """GET /v1/capability/persona_candidates — 列出候选（默认只返回
    pending，加 `status` 查询参数可查其它状态，对齐 `/growth/candidates`
    现有风格）。"""
    store = PersonaCandidateStore(_get_paths(request))
    return {"candidates": [c.to_dict() for c in store.list_candidates(status=status or "pending")]}


@persona_candidate_router.post("/scan")
def scan_candidates(request: Request):
    """POST /v1/capability/persona_candidates/scan — 触发一次扫描；因为
    要调用 LLM（提炼 + 逐条判重），走异步任务返回 `{"job_id", "key"}`，
    前端用 `run_async_job()` 轮询（同 `growth_scan`，见方案 §5）。
    `PersonaCandidateConfig.enabled=False`（默认）时直接返回空结果，不
    发起任何 LLM 调用。"""
    _require_owner(request)
    paths = _get_paths(request)
    cfg = _get_persona_candidate_cfg(request)
    llm_helper = _get_llm_helper(request)

    def _do_scan() -> dict:
        if not getattr(cfg, "enabled", False):
            return {"candidates": [], "skipped": "persona_candidates.enabled=False"}
        from mini_agent.profile import UserProfileManager

        profile = UserProfileManager(paths).load()
        created = scan_persona_candidates(paths, cfg, profile, llm_helper)
        return {"candidates": [c.to_dict() for c in created]}

    key = "persona_candidate_scan"
    job_id = _async_jobs(request).start(_do_scan, key=key)
    return {"job_id": job_id, "key": key}


@persona_candidate_router.post("/{candidate_id}/accept")
def accept_persona_candidate(request: Request, candidate_id: str):
    """采纳一条候选：创建一条 `target_type="persona"` 的空大纲 Track，
    是否/何时补充大纲、正式发布留给用户在 Track 详情页决定（方案 §7）。"""
    _require_owner(request)
    result = accept_candidate(_get_paths(request), candidate_id)
    if result is None:
        raise HTTPException(status_code=404, detail="candidate not found or already decided")
    return result


@persona_candidate_router.post("/{candidate_id}/dismiss")
def dismiss_persona_candidate(request: Request, candidate_id: str, body: DismissCandidateBody):
    """忽略一条候选，`reason` 复用 growth_advisor 的 DISMISS_REASON_*
    常量语义（不强制传值，默认记为 unspecified）。"""
    _require_owner(request)
    candidate = dismiss_candidate(_get_paths(request), candidate_id, reason=body.reason)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found or already decided")
    return candidate.to_dict()
