"""novel_to_video/tool_api.py — UI / CLI / daemon 共用的统一接口。

所有函数返回普通 dict（可直接 JSON 序列化），不抛出业务异常：
  {"ok": bool, "data": ..., "error": {"message": str} | None}
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import project_store as ps
from .call_log import count_calls
from .source_import import import_source
from .steps import global_visual as gv
from .providers.factory import ProviderError, make_image_provider, make_text_llm


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _fail(message: str, data: Any = None) -> Dict[str, Any]:
    return {"ok": False, "data": data, "error": {"message": message}}


def preview_source(source_dir: str, title: Optional[str] = None) -> Dict[str, Any]:
    """只读检查导出目录，不创建任何文件。"""
    try:
        result = import_source(source_dir, title=title)
    except Exception as exc:  # 导入器本身的意外错误也要以结构化形式返回
        return _fail(f"导入检查异常：{exc}")
    return _ok(result.to_dict())


def create_project_from_source(
    projects_root: str,
    source_dir: str,
    *,
    title: Optional[str] = None,
    chapter_spec: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """导入并创建项目。dry_run=True 时只返回将要选中的章节，不写盘。"""
    try:
        result = import_source(source_dir, title=title)
    except Exception as exc:
        return _fail(f"导入异常：{exc}")
    if not result.ok:
        return _fail("导入存在阻断性错误，未创建项目", data=result.to_dict())
    try:
        selected = ps.parse_chapter_spec(chapter_spec, [c["no"] for c in result.chapters])
    except ValueError as exc:
        return _fail(str(exc))
    if dry_run:
        return _ok({"dry_run": True, "selected_chapters": selected, "report": result.to_dict()})
    try:
        proj = ps.create_project(Path(projects_root), result, title=title,
                                 chapter_spec=chapter_spec, options=options)
    except Exception as exc:
        return _fail(f"创建项目失败：{exc}")
    return _ok({"project_dir": str(proj), "selected_chapters": selected,
                "stats": result.stats, "report": result.to_dict()})


def list_projects(projects_root: str) -> Dict[str, Any]:
    rows = []
    for d in ps.list_project_dirs(Path(projects_root)):
        try:
            info = ps.load_project(d)
        except Exception as exc:
            rows.append({"dir": str(d), "error": str(exc)})
            continue
        proj = info["project"]
        rows.append({
            "dir": str(d),
            "title": proj.get("title"),
            "created_at": proj.get("created_at"),
            "chapters": len(info["chapters"]),
            "selected": sum(1 for c in info["chapters"] if c.get("selected")),
            "characters": len(info["characters"]),
            "locations": len(info["locations"]),
        })
    return _ok(rows)


def check_source_drift(project_dir: str) -> Dict[str, Any]:
    """对比项目记录的导出指纹与源目录当前指纹，判断导出是否已被改动。"""
    try:
        info = ps.load_project(Path(project_dir))
    except Exception as exc:
        return _fail(str(exc))
    stored = info["project"].get("source_fingerprint")
    source = info["project"].get("source_dir")
    current = import_source(source).fingerprint if source else ""
    return _ok({"stored": stored, "current": current, "changed": stored != current})


def project_health() -> Dict[str, Any]:
    """健康检查：依赖可用、项目目录可写。不依赖网络与 LLM。"""
    problems = []
    try:
        import yaml  # noqa: F401
    except ImportError:
        problems.append("缺少 pyyaml，请 pip install -r requirements.txt")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.json"
            ps.write_json_atomic(probe, {"ok": True})
            if ps.read_json(probe) != {"ok": True}:
                problems.append("原子写入回读不一致")
    except OSError as exc:
        problems.append(f"临时目录不可写：{exc}")
    return _ok({"ok": not problems, "problems": problems})


# ---------------------------------------------------------------- Phase 1：全书级素材

def _project_providers(project_dir: Path) -> Dict[str, str]:
    proj = ps.read_json(project_dir / "project.json")
    return proj.get("providers") or {"image": "agnes", "text": "miniagent"}


def run_visual_text(project_dir: str, *, ids: Optional[List[str]] = None,
                    only_missing: bool = True, text_provider: Optional[str] = None) -> Dict[str, Any]:
    """G-2：补全视觉锚点、声音设定、性别（仅缺失时）。"""
    pdir = Path(project_dir)
    try:
        llm = make_text_llm(text_provider or _project_providers(pdir).get("text"))
    except ProviderError as exc:
        return _fail(str(exc))
    try:
        summary = gv.visual_text_pass(pdir, llm, only_missing=only_missing, ids=ids)
    except Exception as exc:
        return _fail(f"G-2 执行异常：{exc}")
    return _ok(summary)


def run_portraits(project_dir: str, *, ids: Optional[List[str]] = None, regenerate: bool = False,
                  image_provider: Optional[str] = None) -> Dict[str, Any]:
    """G-3：生成定妆图。regenerate=True 时对 ids 强制生成新版本。"""
    pdir = Path(project_dir)
    try:
        image = make_image_provider(image_provider or _project_providers(pdir).get("image"))
    except ProviderError as exc:
        return _fail(str(exc))
    try:
        summary = gv.portrait_pass(pdir, image, ids=ids, regenerate=regenerate)
    except Exception as exc:
        return _fail(f"G-3 执行异常：{exc}")
    return _ok(summary)


def get_visual_status(project_dir: str) -> Dict[str, Any]:
    try:
        status = gv.visual_status(Path(project_dir))
    except Exception as exc:
        return _fail(f"读取素材状态失败：{exc}")
    status["api_calls"] = {"text": count_calls(Path(project_dir), "text"),
                           "image": count_calls(Path(project_dir), "image")}
    return _ok(status)


def review_asset(project_dir: str, kind: str, eid: str, action: str, what: Optional[str] = None) -> Dict[str, Any]:
    """action: approve（what=text|portrait）/ reject_portrait（驳回定妆图，等待重生成）。"""
    pdir = Path(project_dir)
    try:
        if action == "approve":
            item = gv.approve(pdir, kind, eid, what or "")
        elif action == "reject_portrait":
            item = gv.reject_portrait(pdir, kind, eid)
        else:
            return _fail(f"未知审核动作：{action}")
    except Exception as exc:
        return _fail(str(exc))
    return _ok(item)


def edit_asset(project_dir: str, kind: str, eid: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    try:
        item = gv.update_entity(Path(project_dir), kind, eid, fields)
    except Exception as exc:
        return _fail(str(exc))
    return _ok(item)
