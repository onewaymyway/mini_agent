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
from .providers.factory import (ProviderError, make_image_provider, make_text_llm, make_tts_provider,
                                make_video_provider)
from . import chapter_io as cio
from . import chapter_pipeline as cp
from . import shot_review as sr


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


# ---------------------------------------------------------------- Phase 2：章节流水线

def _providers_for(project_dir: Path) -> Dict[str, Any]:
    p = _project_providers(project_dir)
    return {
        "text": make_text_llm(p.get("text")),
        "image": make_image_provider(p.get("image")),
        "video": make_video_provider(p.get("video")),
        "tts": make_tts_provider(p.get("tts")),
    }


def run_chapter(project_dir: str, no: int, *, force_script: bool = False) -> Dict[str, Any]:
    """运行单章 C-1…C-9（可重入）。返回 status 与 message，见 chapter_pipeline。"""
    pdir = Path(project_dir)
    try:
        prov = _providers_for(pdir)
    except ProviderError as exc:
        return _fail(str(exc))
    try:
        res = cp.run_chapter(pdir, no, text_llm=prov["text"], image=prov["image"],
                             video=prov["video"], tts=prov["tts"], force_script=force_script)
    except Exception as exc:
        return _fail(f"章节 {no} 运行异常：{exc}")
    return _ok(res)


def run_chapters(project_dir: str, numbers: Optional[List[int]] = None, *,
                 force_script: bool = False) -> Dict[str, Any]:
    """依次运行多章；某章停在 waiting_* 或 failed 时继续下一章，最后汇总。"""
    pdir = Path(project_dir)
    rows = ps.load_project(pdir)["chapters"]
    targets = [r["no"] for r in rows if r.get("selected")] if numbers is None else numbers
    results = {}
    for no in targets:
        results[no] = run_chapter(project_dir, no, force_script=force_script)
    return _ok(results)


def chapter_view(project_dir: str, no: int) -> Dict[str, Any]:
    try:
        return _ok(cp.chapter_view(Path(project_dir), no))
    except Exception as exc:
        return _fail(f"读取章节失败：{exc}")


def chapters_overview(project_dir: str) -> Dict[str, Any]:
    pdir = Path(project_dir)
    try:
        rows = ps.load_project(pdir)["chapters"]
    except Exception as exc:
        return _fail(str(exc))
    return _ok([{"no": r["no"], "title": r["title"], "selected": r.get("selected"),
                 "status": r.get("status"), "message": r.get("message")} for r in rows])


def review_shot(project_dir: str, no: int, sid: str, action: str, note: str = "") -> Dict[str, Any]:
    pdir = Path(project_dir)
    try:
        if action == "approve":
            data = sr.approve(pdir, no, sid)
        elif action == "reject":
            data = sr.reject(pdir, no, sid, note)
        else:
            return _fail(f"未知审核动作：{action}")
    except Exception as exc:
        return _fail(str(exc))
    return _ok(data)


def edit_shot(project_dir: str, no: int, sid: str, *, visual_hint_en: Optional[str] = None,
              visual_hint_zh: Optional[str] = None, block_texts: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    try:
        data = sr.edit(Path(project_dir), no, sid, visual_hint_en=visual_hint_en,
                       visual_hint_zh=visual_hint_zh, block_texts=block_texts)
    except Exception as exc:
        return _fail(str(exc))
    return _ok(data)


# ---------------------------------------------------------------- Phase 3：挡位、批量、分级回退

from . import gates as _gates
from . import batch as _batch
from . import revision as _rev


def set_gear(project_dir: str, gear: str, budget: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """设置挡位与预算。G3 必须同时给出三项预算上限，否则拒绝保存。"""
    pdir = Path(project_dir)
    try:
        g = _gates.normalize_gear(gear)
        proj = ps.read_json(pdir / "project.json")
        new_budget = {**(proj.get("budget") or {}), **(budget or {})}
        _gates.validate_budget(new_budget, g)
    except (_gates.GateError, OSError, ValueError) as exc:
        return _fail(str(exc))
    proj["gear"] = g
    proj["budget"] = new_budget
    ps.write_json_atomic(pdir / "project.json", proj)
    ps.append_progress(pdir, f"挡位设为 {g}，预算 {new_budget}")
    return _ok({"gear": g, "budget": new_budget})


def gear_policy() -> Dict[str, Any]:
    return _ok({"gears": list(_gates.GEARS), "table": _gates.policy_table(),
                "always_manual": list(_gates.ALWAYS_MANUAL)})


def run_queue(project_dir: str, numbers: Optional[List[int]] = None, *,
              gear: Optional[str] = None) -> Dict[str, Any]:
    """按挡位批量运行选中章节。某章停止不影响其他章；预算超限则整批停止。"""
    pdir = Path(project_dir)
    try:
        proj = ps.read_json(pdir / "project.json")
        g = _gates.normalize_gear(gear or proj.get("gear"))
        rows = ps.load_project(pdir)["chapters"]
    except Exception as exc:
        return _fail(f"读取项目失败：{exc}")
    targets = [r["no"] for r in rows if r.get("selected")] if numbers is None else numbers
    if not targets:
        return _fail("没有选中的章节")

    def run_one(no: int) -> Dict[str, Any]:
        r = run_chapter(project_dir, no)
        return r["data"] if r["ok"] else {"status": "failed", "message": r["error"]["message"]}

    try:
        out = _batch.run_queue(pdir, targets, gear=g, run_one=run_one)
    except _gates.GateError as exc:
        return _fail(str(exc))
    return _ok(out)


def invalidate_shot(project_dir: str, no: int, sid: str, level: str) -> Dict[str, Any]:
    try:
        data = _rev.invalidate(Path(project_dir), no, sid, level)
    except Exception as exc:
        return _fail(str(exc))
    ps.append_progress(Path(project_dir), f"第 {no} 章 {sid} 修改（层级 {level}）：清理 {data['removed']}")
    return _ok(data)


def final_status(project_dir: str) -> Dict[str, Any]:
    pdir = Path(project_dir)
    try:
        rows = ps.load_project(pdir)["chapters"]
    except Exception as exc:
        return _fail(str(exc))
    selected = [r for r in rows if r.get("selected")]
    done = [r["no"] for r in selected if r.get("status") == "done"]
    stale = [r["no"] for r in selected if r.get("status") == "stale"]
    pending = [r["no"] for r in selected if r["no"] not in done]
    return _ok({"selected": [r["no"] for r in selected], "done": done, "stale": stale,
                "pending": pending, "final_stale": _rev.final_is_stale(pdir),
                "ready": bool(selected) and not pending and not stale})


def auto_added_queue(project_dir: str) -> Dict[str, Any]:
    """补建审核页数据：所有 source=auto_added 的角色及其审核状态。"""
    pdir = Path(project_dir)
    try:
        chars = ps.read_json(pdir / "global" / "characters.json")["characters"]
        review = ps.read_json(pdir / "global" / "review.json") if (pdir / "global" / "review.json").is_file() else {"items": {}}
    except Exception as exc:
        return _fail(str(exc))
    rows = []
    for c in chars:
        if c.get("source") != "auto_added":
            continue
        item = review.get("items", {}).get(f"character:{c['id']}", {})
        rows.append({"id": c["id"], "name": (c.get("names") or [""])[0], "chapter": c.get("auto_added_chapter"),
                     "description_zh": c.get("description_zh"), "visual_anchor_en": c.get("visual_anchor_en"),
                     "voice_profile": c.get("voice_profile"), "text_status": item.get("text_status", "missing"),
                     "portrait_status": item.get("portrait_status", "missing")})
    return _ok(rows)
