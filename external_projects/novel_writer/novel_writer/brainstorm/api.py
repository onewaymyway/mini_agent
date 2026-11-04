"""构思工作室对外接口（B1）：统一返回 {ok, data | error}，与 tool_api 的约定一致。

界面只通过这里访问构思项目，不直接碰文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import ideas as I
from novel_writer.brainstorm import spark as S
from novel_writer.brainstorm.workspace import BrainstormWorkspace, default_root, list_brainstorms


def _ok(data: Any = None) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(exc: Exception) -> Dict[str, Any]:
    # 与 tool_api 的约定一致：error 是 {type, message}，界面的 show_error 直接读取 message
    kind = type(exc).__name__
    return {"ok": False, "error": {"type": kind, "message": str(exc)}, "error_type": kind}


def _guard(fn: Callable[[], Any]) -> Dict[str, Any]:
    try:
        return _ok(fn())
    except Exception as exc:  # noqa: BLE001 - 界面需要拿到可显示的错误，而不是异常
        return _err(exc)


def _ws(root: str) -> BrainstormWorkspace:
    ws = BrainstormWorkspace(root)
    ws.meta()                                   # 不是构思项目时抛 BrainstormError
    return ws


def list_projects(base: Optional[str] = None) -> Dict[str, Any]:
    return _guard(lambda: list_brainstorms(Path(base) if base else default_root()))


def create_project(title: str, base: Optional[str] = None) -> Dict[str, Any]:
    def run():
        root = (Path(base) if base else default_root()) / _slug(title)
        ws = BrainstormWorkspace(root)
        ws.create(title)
        return {"root": str(root), "title": title}
    return _guard(run)


def _slug(title: str) -> str:
    import datetime as _dt
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (title or "").strip())[:40] or "构思"
    return f"{safe}_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_project(root: str) -> Dict[str, Any]:
    def run():
        ws = _ws(root)
        return {"meta": ws.meta(), "cards": C.all_modules(ws), "versions": C.list_versions(ws)}
    return _guard(run)


def save_brief(root: str, brief: Dict[str, str]) -> Dict[str, Any]:
    return _guard(lambda: C.put_module(_ws(root), "brief", brief))


def list_ideas(root: str, status: Optional[str] = None) -> Dict[str, Any]:
    return _guard(lambda: I.list_ideas(_ws(root), status))


def add_idea(root: str, text: str, tags: Iterable[str] = ()) -> Dict[str, Any]:
    return _guard(lambda: I.add_idea(_ws(root), text, tags=tags, source="author"))


def update_idea(root: str, idea_id: str, **changes: Any) -> Dict[str, Any]:
    return _guard(lambda: I.update_idea(_ws(root), idea_id, **changes))


def spark(root: str, direction: str = "", count: int = 6, generator=None) -> Dict[str, Any]:
    return _guard(lambda: S.generate(_ws(root), direction, count=count, generator=generator))


def spark_history(root: str) -> Dict[str, Any]:
    return _guard(lambda: S.history(_ws(root)))


def adopt_candidates(root: str, spark_id: str, cids: list) -> Dict[str, Any]:
    return _guard(lambda: S.adopt(_ws(root), spark_id, list(cids)))


# ── B2：对话与提议 ──
from novel_writer.brainstorm import chat as CH  # noqa: E402
from novel_writer.brainstorm import proposals as PR  # noqa: E402


def chat_send(root: str, text: str, mode: str = "追问", runner=None) -> Dict[str, Any]:
    return _guard(lambda: CH.send(_ws(root), text, mode, runner=runner))


def chat_history(root: str, limit: Optional[int] = None) -> Dict[str, Any]:
    return _guard(lambda: CH.history(_ws(root), limit))


def chat_new(root: str) -> Dict[str, Any]:
    """新开一段对话并切换过去（当前对话没有内容时直接沿用）。"""
    return _guard(lambda: CH.new_chat(_ws(root)))


def scope_get(root: str) -> Dict[str, Any]:
    """当前上下文范围（摘要 / 读取文件 / …）与可选项。"""
    return _guard(lambda: {"scope": CH.get_scope(_ws(root)), "options": CH.SCOPES,
                           "implemented": sorted(CH.IMPLEMENTED_SCOPES)})


def context_budget(root: str) -> Dict[str, Any]:
    """完整模式下每轮附上的字数与被省略的段落。"""
    return _guard(lambda: CH.context_budget(_ws(root)))


def scope_set(root: str, scope: str) -> Dict[str, Any]:
    """设置项目级的上下文范围。未实现的范围会被拒绝。"""
    return _guard(lambda: {"scope": CH.set_scope(_ws(root), scope)})


def chat_list(root: str) -> Dict[str, Any]:
    """本项目的历史对话（最近更新在前）与当前对话 id。"""
    return _guard(lambda: CH.list_conversations(_ws(root)))


def chat_switch(root: str, cid: str) -> Dict[str, Any]:
    """切换到本项目的另一段对话。"""
    return _guard(lambda: CH.switch_chat(_ws(root), cid))


def list_proposals(root: str, status: Optional[str] = "pending") -> Dict[str, Any]:
    return _guard(lambda: PR.list_items(_ws(root), status))


def accept_proposal(root: str, pid: str, edited_value: Any = None) -> Dict[str, Any]:
    return _guard(lambda: PR.accept(_ws(root), pid, edited_value))


def reject_proposal(root: str, pid: str) -> Dict[str, Any]:
    return _guard(lambda: PR.reject(_ws(root), pid))


# ── B3：世界观、角色、质检 ──
from novel_writer.brainstorm import checks as K  # noqa: E402
from novel_writer.brainstorm import edit as E  # noqa: E402


def world_add(root: str, key: str, item: Any) -> Dict[str, Any]:
    return _guard(lambda: E.add_world_item(_ws(root), key, item))


def world_remove(root: str, key: str, index: int) -> Dict[str, Any]:
    return _guard(lambda: E.remove_world_item(_ws(root), key, index))


def cast_add(root: str, card: Dict[str, Any]) -> Dict[str, Any]:
    return _guard(lambda: E.add_cast(_ws(root), card))


def cast_update(root: str, cid: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    return _guard(lambda: E.update_cast(_ws(root), cid, changes))


def cast_remove(root: str, cid: str) -> Dict[str, Any]:
    return _guard(lambda: E.remove_cast(_ws(root), cid))


def run_check(root: str, use_llm: bool = False, generator=None) -> Dict[str, Any]:
    return _guard(lambda: K.run(_ws(root), use_llm=use_llm, generator=generator))


def check_history(root: str) -> Dict[str, Any]:
    return _guard(lambda: K.history(_ws(root)))


# ── B4：剧情骨架与备选方案 ──
from novel_writer.brainstorm import options as O  # noqa: E402


def plot_add_act(root: str, title: str) -> Dict[str, Any]:
    return _guard(lambda: E.add_act(_ws(root), title))


def plot_remove_act(root: str, act: int) -> Dict[str, Any]:
    return _guard(lambda: E.remove_act(_ws(root), act))


def plot_add_beat(root: str, act: int, text: str) -> Dict[str, Any]:
    return _guard(lambda: E.add_beat(_ws(root), act, text))


def plot_remove_beat(root: str, act: int, beat: int) -> Dict[str, Any]:
    return _guard(lambda: E.remove_beat(_ws(root), act, beat))


def plot_add_item(root: str, key: str, text: str) -> Dict[str, Any]:
    return _guard(lambda: E.add_plot_item(_ws(root), key, text))


def plot_remove_item(root: str, key: str, index: int) -> Dict[str, Any]:
    return _guard(lambda: E.remove_plot_item(_ws(root), key, index))


def option_create(root: str, name: str, module: str, content: Any = None, notes: str = "") -> Dict[str, Any]:
    return _guard(lambda: O.create(_ws(root), name, module, content, notes))


def option_list(root: str) -> Dict[str, Any]:
    return _guard(lambda: O.list_options(_ws(root)))


def option_diff(root: str, oid: str) -> Dict[str, Any]:
    return _guard(lambda: O.diff(_ws(root), oid))


def option_merge(root: str, oid: str) -> Dict[str, Any]:
    return _guard(lambda: O.merge(_ws(root), oid))


def option_discard(root: str, oid: str) -> Dict[str, Any]:
    return _guard(lambda: O.discard(_ws(root), oid))


def option_undo_merge(root: str, oid: str) -> Dict[str, Any]:
    return _guard(lambda: O.undo_merge(_ws(root), oid))


# ── B5：交接到小说 ──
from novel_writer.brainstorm import handoff as H  # noqa: E402


def handoff_to_novel(root: str, version: Optional[int] = None, title: Optional[str] = None,
                     novels_dir: Optional[str] = None, index_file: Optional[str] = None) -> Dict[str, Any]:
    return _guard(lambda: H.handoff(_ws(root), version=version, title=title,
                                    novels_dir=novels_dir, index_file=index_file))


def linked_novels(root: str) -> Dict[str, Any]:
    return _guard(lambda: H.linked_novels(_ws(root)))


def snapshot_version(root: str, note: str = "") -> Dict[str, Any]:
    return _guard(lambda: {"version": C.snapshot(_ws(root), note)})


# ── 对话过程日志 ──
def chat_start(root: str, text: str, mode: str = "追问", runner=None, focus=None) -> Dict[str, Any]:
    return _guard(lambda: {"turn": CH.start_turn(_ws(root), text, mode, runner=runner, focus=focus)})


def outputs(root: str) -> Dict[str, Any]:
    """构思成果总览（只读）：每个模块的标签、条数与内容行。"""
    from novel_writer.brainstorm import outline as OL
    return _guard(lambda: OL.outputs(_ws(root)))


def chat_run(root: str, turn_id: str) -> Dict[str, Any]:
    return _guard(lambda: CH.run_state(_ws(root), turn_id))


# ── 整理对话（手动） ──
from novel_writer.brainstorm import distill as DS  # noqa: E402


def distill_run(root: str, modules=None, scope: str = "pending", recent_rounds: int = 0, generator=None) -> Dict[str, Any]:
    return _guard(lambda: DS.run(_ws(root), modules=modules, scope=scope, recent_rounds=recent_rounds, generator=generator))


def distill_conflict_prompt(root: str, pid: str) -> Dict[str, Any]:
    return _guard(lambda: DS.conflict_prompt(_ws(root), pid))


def distill_minutes(root: str) -> Dict[str, Any]:
    return _guard(lambda: DS.list_minutes(_ws(root)))


def distill_read_minutes(root: str, rel: str) -> Dict[str, Any]:
    return _guard(lambda: DS.read_minutes(_ws(root), rel))


def distill_pending_range(root: str) -> Dict[str, Any]:
    return _guard(lambda: list(DS.select_range(_ws(root), "pending")))


def distill_start(root: str, modules=None, scope: str = "pending", recent_rounds: int = 0, generator=None) -> Dict[str, Any]:
    return _guard(lambda: {"run": DS.start_run(_ws(root), modules=modules, scope=scope,
                                               recent_rounds=recent_rounds, generator=generator)})


def distill_state(root: str, run_id: str) -> Dict[str, Any]:
    return _guard(lambda: DS.run_state(_ws(root), run_id))


# ── 设定文档（对话 → 世界观与设定 Markdown）──

def lore_start(root: str, scope: str = "all", recent_rounds: int = 0, generator=None) -> Dict[str, Any]:
    from novel_writer.brainstorm import lore as LO
    return _guard(lambda: {"run": LO.start_run(_ws(root), scope=scope, recent_rounds=recent_rounds, generator=generator)})


def lore_state(root: str, run_id: str) -> Dict[str, Any]:
    from novel_writer.brainstorm import lore as LO
    return _guard(lambda: LO.run_state(_ws(root), run_id))


def lore_list(root: str) -> Dict[str, Any]:
    from novel_writer.brainstorm import lore as LO
    return _guard(lambda: LO.list_docs(_ws(root)))


def lore_read(root: str, rel: str) -> Dict[str, Any]:
    from novel_writer.brainstorm import lore as LO
    return _guard(lambda: LO.read_doc(_ws(root), rel))
