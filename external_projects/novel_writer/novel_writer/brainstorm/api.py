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
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


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
