"""api/capability_routes.py — 人设能力自主学习 HTTP API（P1）

设计背景见 next_doc/persona_capability_learning_design.md §7.1。

独立成文件、独立成一个 APIRouter，而不是直接改 api/routes.py（7700+ 行的
超大文件），原因见该文件规模——直接在里面插入几百行新代码风险高、review
成本大，独立文件挂载是更安全的接线方式。

已挂载到 api/server.py（`app.include_router(capability_router)`，紧跟在
主 router 挂载之后），`/v1/capability/*` 端点对外可用。

本文件里的 `_get_paths(request)` 复用了 routes.py 里同样的取 AgentPaths
方式（`http_server.bridge.agent.cfg.project_root`），Track 数据是
project 级（workdir）而不是 session 级——这和设计文档 §3 一致：
CapabilityTrack 是长期持续的人设/方向，不挂在某一次会话下。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mini_agent.evolution.capability_learning import (
    CapabilityLedgerStore,
    CapabilityQuestionStore,
    CapabilityTrackStore,
)
from mini_agent.orchestrator.persona_profiles import (
    get_persona_loader,
    list_personas_for_paths,
    set_persona_wiki_scopes,
)
from mini_agent.storage.paths import AgentPaths

capability_router = APIRouter(prefix="/v1/capability")


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
    """跟 cli/commands/capability_cmd.py::_get_llm_helper 同款约定（也是
    routes.py 里其它几处 opt-in LLM 增强端点的既有模式，见该文件
    `_get_llm_helper` 附近的用法）：把 `agent.llm_helper` 包成
    `Callable[[str], str]`，拿不到就返回 None，调用方退回无 LLM 默认
    路径，不报错。"""
    http_server = request.app.state
    bridge = getattr(http_server, "bridge", None)
    agent = getattr(bridge, "agent", None) if bridge is not None else None
    helper = getattr(agent, "llm_helper", None) if agent is not None else None
    if helper is None:
        return None
    return lambda prompt: helper.ask(prompt)


# ── 请求体模型 ───────────────────────────────────────────────────────────


class CreateTrackBody(BaseModel):
    title: str
    persona_desc: str
    outline_names: Optional[list[str]] = None
    target_type: str = "knowledge"  # knowledge / persona
    wiki_tag: Optional[str] = None
    # §14 P2：outline_names 为空且这个开关为 True 时，用
    # draft_outline_with_llm() 起草初始大纲；拿不到 agent.llm_helper 时
    # 静默退回空大纲，不报错（见 CapabilityTrackStore.create 文档字符串）。
    llm_draft: bool = False


class UpdateTrackBody(BaseModel):
    title: Optional[str] = None
    persona_desc: Optional[str] = None
    outline: Optional[list[dict]] = None
    status: Optional[str] = None
    excluded_keywords: Optional[list[str]] = None
    cadence: Optional[str] = None


class AnswerQuestionBody(BaseModel):
    answer: str


class SetPersonaWikiScopesBody(BaseModel):
    wiki_scopes: list[str]


# ── Track 端点 ───────────────────────────────────────────────────────────


@capability_router.get("/tracks")
def list_tracks(request: Request, status: Optional[str] = None):
    store = CapabilityTrackStore(_get_paths(request))
    return {"tracks": [t.to_dict() for t in store.list_tracks(status=status)]}


@capability_router.post("/tracks")
def create_track(request: Request, body: CreateTrackBody):
    store = CapabilityTrackStore(_get_paths(request))
    llm_helper = _get_llm_helper(request) if (body.llm_draft and not body.outline_names) else None
    track = store.create(
        title=body.title,
        persona_desc=body.persona_desc,
        outline_names=body.outline_names,
        target_type=body.target_type,
        wiki_tag=body.wiki_tag or "",
        llm_helper=llm_helper,
    )
    return track.to_dict()


@capability_router.get("/tracks/{track_id}")
def get_track(request: Request, track_id: str):
    store = CapabilityTrackStore(_get_paths(request))
    track = store.get(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    return track.to_dict()


@capability_router.patch("/tracks/{track_id}")
def update_track(request: Request, track_id: str, body: UpdateTrackBody):
    store = CapabilityTrackStore(_get_paths(request))
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    track = store.update(track_id, **fields)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    return track.to_dict()


@capability_router.delete("/tracks/{track_id}")
def delete_track(request: Request, track_id: str):
    """删除 Track 本身，不级联删除已产出的 wiki 页面（§7.1 设计原则）。"""
    store = CapabilityTrackStore(_get_paths(request))
    ok = store.delete(track_id)
    if not ok:
        raise HTTPException(status_code=404, detail="track not found")
    return {"deleted": True, "track_id": track_id}


@capability_router.get("/tracks/{track_id}/ledger")
def get_track_ledger(request: Request, track_id: str, limit: int = 50):
    store = CapabilityLedgerStore(_get_paths(request))
    return {"entries": [e.to_dict() for e in store.list_for_track(track_id, limit=limit)]}


# ── 异步问答端点 ─────────────────────────────────────────────────────────


@capability_router.get("/questions")
def list_questions(request: Request, status: Optional[str] = None, track_id: Optional[str] = None):
    store = CapabilityQuestionStore(_get_paths(request))
    return {"questions": [q.to_dict() for q in store.list_questions(status=status, track_id=track_id)]}


@capability_router.post("/questions/{question_id}/answer")
def answer_question(request: Request, question_id: str, body: AnswerQuestionBody):
    """用户在看板提交回答——纯写入，立即返回，不等待/不触发 cron 循环
    立即处理（异步语义，见设计文档 §3.3、§9 第 6 条）。"""
    store = CapabilityQuestionStore(_get_paths(request))
    q = store.answer(question_id, body.answer)
    if q is None:
        raise HTTPException(status_code=404, detail="question not found")
    return q.to_dict()


@capability_router.post("/questions/{question_id}/dismiss")
def dismiss_question(request: Request, question_id: str):
    store = CapabilityQuestionStore(_get_paths(request))
    ok = store.dismiss(question_id)
    if not ok:
        raise HTTPException(status_code=404, detail="question not found")
    return {"dismissed": True, "question_id": question_id}


# ── §11.4 知识范围绑定端点 ───────────────────────────────────────────────
#
# 供看板"知识范围绑定"卡片使用：列出所有 persona 及其当前 wiki_scopes，
# 并允许勾选/取消某个 knowledge 型 Track 的 wiki_tag。这里只暴露"设置
# 某个 persona 的完整 wiki_scopes 列表"这一个端点（而不是"添加单个
# tag"/"移除单个 tag"两个端点），把"要不要加/删"的判断留在看板前端——
# 看板已经知道当前 persona 的完整 wiki_scopes，勾选/取消只是本地增删
# 数组后整体提交，避免服务端还要处理并发追加/移除的顺序问题。


@capability_router.get("/personas")
def list_personas_with_scopes(request: Request):
    """列出所有 persona 及其 wiki_scopes，供看板"知识范围绑定"卡片展示。"""
    paths = _get_paths(request)
    personas = list_personas_for_paths(paths)
    return {
        "personas": [
            {
                "name": p.name,
                "display_name": p.display_name,
                "wiki_scopes": p.wiki_scopes,
                "source_path": str(p.source_path) if p.source_path else None,
            }
            for p in personas
        ]
    }


@capability_router.post("/personas/{persona_name}/wiki_scopes")
def update_persona_wiki_scopes(request: Request, persona_name: str, body: SetPersonaWikiScopesBody):
    """整体替换某个 persona 的 wiki_scopes 字段并写回其 .md 文件。

    写回成功后尝试触发已加载的 PersonaLoader.rediscover()，让正在运行的
    agent 立即感知到变更（不强制——拿不到 loader 时静默跳过，下次进程
    重启/下次热加载自然会读到新内容，不影响本次写入结果）。
    """
    paths = _get_paths(request)
    personas = list_personas_for_paths(paths)
    target = next((p for p in personas if p.name == persona_name), None)
    if target is None or target.source_path is None:
        raise HTTPException(status_code=404, detail="persona not found")

    ok = set_persona_wiki_scopes(target.source_path, body.wiki_scopes)
    if not ok:
        raise HTTPException(status_code=400, detail="failed to update persona file (missing frontmatter?)")

    loader = get_persona_loader()
    if loader is not None:
        try:
            loader.rediscover()
        except Exception:
            pass

    return {"name": persona_name, "wiki_scopes": body.wiki_scopes}
