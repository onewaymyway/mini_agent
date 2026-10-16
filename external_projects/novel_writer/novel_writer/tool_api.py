"""novel_writer/tool_api.py — 唯一承载业务逻辑的一层（M0 版本）。

约定与 world_simulator/tool_api.py 相同：入参只用基本类型，返回统一的
`{"ok": True, "data": {...}}` 或 `{"ok": False, "error": {"type", "message"}}`，
**不抛异常**。entrypoints / service_cli / Streamlit 都只是它的壳。
M1 起在这里追加运行/暂停/步骤操作等接口。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from novel_writer import config, shelf, skills_catalog
from novel_writer import settings as settings_mod
from novel_writer.workspace import Workspace, WorkspaceError, workspace_dir_name


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(kind: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"type": kind, "message": message}}


def _guard(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except WorkspaceError as exc:
        return _err("workspace_error", str(exc))
    except settings_mod.SettingsError as exc:
        return _err("validation_error", str(exc))
    except Exception as exc:  # noqa: BLE001 - 接口层不向外抛异常
        return _err("internal_error", f"{type(exc).__name__}: {exc}")


def create_novel(
    title: str,
    *,
    workspace: Optional[str] = None,
    gear: str = "G0",
    quality: str = "standard",
    total_chapters_planned: int = 0,
    novels_dir: Optional[str] = None,
    index_file: Optional[str] = None,
) -> Dict[str, Any]:
    """新建小说：创建工作目录并初始化。`workspace` 为空时用默认位置
    `data/novels/<书名>_<时间戳>/`；指定了其它位置则登记到书架索引。"""

    def run() -> Dict[str, Any]:
        base = Path(novels_dir) if novels_dir else config.NOVELS_DIR
        custom = bool(workspace)
        root = Path(workspace).expanduser() if custom else base / workspace_dir_name(title)
        ws = Workspace(root)
        result = ws.init(title, gear=gear, quality=quality, total_chapters_planned=total_chapters_planned)
        if custom:
            shelf.register(ws.root, index_file=Path(index_file) if index_file else None)
        return _ok(result)

    return _guard(run)


def list_novels(*, novels_dir: Optional[str] = None, index_file: Optional[str] = None) -> Dict[str, Any]:
    return _guard(
        lambda: _ok(
            {
                "novels": shelf.list_novels(
                    Path(novels_dir) if novels_dir else None, Path(index_file) if index_file else None
                )
            }
        )
    )


def get_novel(workspace: str) -> Dict[str, Any]:
    """项目恢复摘要 + 当前设置。"""

    def run() -> Dict[str, Any]:
        ws = Workspace(workspace)
        ws.touch_accessed()
        return _ok({"recover": ws.recover(), "settings": ws.settings()})

    return _guard(run)


def novel_health(workspace: str) -> Dict[str, Any]:
    return _guard(lambda: _ok(Workspace(workspace).health().to_dict()))


def update_settings(workspace: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """合并更新设置（挡位/质量档/预算等）；非法取值返回 validation_error。"""

    def run() -> Dict[str, Any]:
        ws = Workspace(workspace)
        current = ws.settings()
        merged = dict(current)
        for key, value in (updates or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return _ok(ws.save_settings(merged))

    return _guard(run)


def list_skills() -> Dict[str, Any]:
    return _guard(lambda: _ok({"skills": skills_catalog.list_skills()}))


def project_health() -> Dict[str, Any]:
    """项目级健康检查（不依赖 LLM/网络）：skills 完整、数据目录可写。"""

    def run() -> Dict[str, Any]:
        problems = []
        rows = skills_catalog.list_skills()
        missing = [r["name"] for r in rows if not r["present"]]
        uncatalogued = [r["name"] for r in rows if r["present"] and not r["catalogued"]]
        mismatched = [r["name"] for r in rows if not r["name_matches_dir"]]
        if missing:
            problems.append(f"缺少 skill：{missing}")
        if uncatalogued:
            problems.append(f"skills/ 下有未登记的 skill：{uncatalogued}")
        if mismatched:
            problems.append(f"SKILL.md 的 name 与目录名不一致：{mismatched}")
        try:
            config.ensure_dirs()
            probe = config.DATA_DIR / ".health_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"数据目录不可写：{exc}")
        return _ok({"ok": not problems, "problems": problems, "skills_total": len(rows), "checked_at": _dt.datetime.now().isoformat(timespec="seconds")})

    return _guard(run)
