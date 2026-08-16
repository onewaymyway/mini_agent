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
    CapabilityOutlineSuggestionStore,
    CapabilityQuestionStore,
    CapabilityTrackStore,
    accept_outline_suggestion,
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


# ── v0.21 §13.2-f 大纲动态生长建议端点 ─────────────────────────────────
#
# 与上面的异步问答端点是两件独立的事：问答队列的答案是"素材"，这里的
# 建议队列是从素材里提炼出来的"要不要扩展大纲"的产出，两者状态机
# （pending/answered/dismissed/expired 与 pending/accepted/dismissed）
# 也不同，不合并成一个端点组。


@capability_router.get("/suggestions")
def list_outline_suggestions(request: Request, status: Optional[str] = None, track_id: Optional[str] = None):
    store = CapabilityOutlineSuggestionStore(_get_paths(request))
    return {"suggestions": [s.to_dict() for s in store.list_suggestions(status=status, track_id=track_id)]}


@capability_router.post("/suggestions/{suggestion_id}/accept")
def accept_outline_suggestion_endpoint(request: Request, suggestion_id: str):
    """采纳一条建议：追加为大纲新子主题（`accept_outline_suggestion()`
    同时把建议自身标记为 accepted）。建议不存在/已处理过/对应 Track 已被
    删除时统一返回 404——三种情况看板前端都应该提示"刷新后重试"，不需要
    在错误码层面细分。"""
    topic = accept_outline_suggestion(_get_paths(request), suggestion_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="suggestion not found or already processed")
    return {"accepted": True, "suggestion_id": suggestion_id, "topic": topic.to_dict()}


@capability_router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_outline_suggestion(request: Request, suggestion_id: str):
    store = CapabilityOutlineSuggestionStore(_get_paths(request))
    ok = store.dismiss(suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return {"dismissed": True, "suggestion_id": suggestion_id}


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


# ── [persona_capability_learning_design.md §10.3] persona 型 Track 人设草稿 ──
#
# 与上面的 wiki_scopes 端点不同：这三个端点操作的是 persona 型 Track 本身
# （CapabilityTrack.target_type == "persona"），不是已发布的 persona 文件。
# 三个动作严格对应 evolution/capability_learning.py 里的三个纯函数
# （draft_persona_markdown / load_persona_draft / publish_persona_draft），
# HTTP 层只做参数校验和错误码映射，不重复任何业务逻辑——和
# cli/commands/capability_cmd.py 的 `/capability persona ...` 子命令是
# 同一套底层实现的两层接线，行为应保持一致。


@capability_router.post("/tracks/{track_id}/persona/draft")
def draft_persona(request: Request, track_id: str):
    """生成/刷新 persona 型 Track 的人设草稿并落盘，返回草稿全文 +
    完成度摘要。knowledge 型 Track 调用返回 400（这是 target_type
    的语义错误，不是"没找到"，用 400 而不是 404 更准确）。"""
    from mini_agent.evolution.capability_learning import (
        draft_persona_markdown,
        persona_draft_completeness,
        save_persona_draft,
    )

    paths = _get_paths(request)
    track_store = CapabilityTrackStore(paths)
    track = track_store.get(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    if track.target_type != "persona":
        raise HTTPException(status_code=400, detail="track is not target_type=persona")

    questions = CapabilityQuestionStore(paths).list_questions(track_id=track_id)
    markdown_text = draft_persona_markdown(track, questions)
    save_persona_draft(paths, track_id, markdown_text)
    completeness = persona_draft_completeness(track, questions)
    return {"track_id": track_id, "draft": markdown_text, "completeness": completeness}


@capability_router.get("/tracks/{track_id}/persona/draft")
def get_persona_draft(request: Request, track_id: str):
    """读取上一次落盘的人设草稿，不存在返回 404（尚未调用过 draft 端点）。

    连带返回一份完成度摘要（与 POST 端点同款 `persona_draft_completeness`），
    这样看板刷新页面重新拉取已落盘草稿时，也能展示进度条/缺失维度，不需要
    强制用户先点一次「生成/刷新」才能看到完成度——GET 是只读操作，这里的
    completeness 只是基于当前 track/questions 状态重新计算，不涉及任何
    写入，和落盘的草稿文本本身是否已经过期（用户后续又回答了新问题）无关，
    只是"以现在的已知信息看，这份草稿覆盖了多少"。"""
    from mini_agent.evolution.capability_learning import (
        load_persona_draft,
        persona_draft_completeness,
    )

    paths = _get_paths(request)
    track = CapabilityTrackStore(paths).get(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")

    text = load_persona_draft(paths, track_id)
    if text is None:
        raise HTTPException(status_code=404, detail="no draft found for this track yet")

    questions = CapabilityQuestionStore(paths).list_questions(track_id=track_id)
    completeness = persona_draft_completeness(track, questions)
    return {"track_id": track_id, "draft": text, "completeness": completeness}


@capability_router.post("/tracks/{track_id}/persona/publish")
def publish_persona(request: Request, track_id: str):
    """把已落盘的草稿显式发布到项目级 personas 目录（§10.3 第 4 点：
    发布必须是显式用户动作）。没有草稿时返回 400（"状态不满足前置条件"，
    不是 404——track 本身是存在的，缺的是"还没生成过草稿"这个步骤）。"""
    from mini_agent.evolution.capability_learning import publish_persona_draft

    paths = _get_paths(request)
    track = CapabilityTrackStore(paths).get(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    if track.target_type != "persona":
        raise HTTPException(status_code=400, detail="track is not target_type=persona")

    try:
        target_path = publish_persona_draft(paths, track_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"track_id": track_id, "published_path": str(target_path)}


@capability_router.get("/wiki_pages/{page_id}")
def get_capability_wiki_page(request: Request, page_id: str):
    """GET /v1/capability/wiki_pages/{page_id} — 返回某篇 wiki 页面的
    Markdown 正文，供看板"能力大纲覆盖状态"区块里直接查看某个子主题
    关联的 wiki 页面内容（而不是只显示"关联 N 篇 wiki 页面"这个数字）。

    复用 `wiki/index_reader.py::find_page_path()` 按 `page_id` 定位文件
    （文件名固定为 `<page_id>.md`，`wiki/writer.py::write_page()` 写入
    时保证这一约定），风格对齐 `GET /growth/reports/{id}` 读取
    `body_path` 正文的做法。页面不存在（page_id 有误，或对应文件被
    外部删除）时返回 404，不在这个只读端点里做任何隔离区/合规状态
    判断——那是 wiki 检索侧的关注点，这里只负责"page_id 存在就把内容
    原样读出来"。
    """
    from mini_agent.wiki.index_reader import find_page_path

    paths = _get_paths(request)
    md_path = find_page_path(paths, page_id)
    if md_path is None or not md_path.exists():
        raise HTTPException(status_code=404, detail="wiki page not found")
    body = md_path.read_text(encoding="utf-8")
    return {"page_id": page_id, "body": body}
