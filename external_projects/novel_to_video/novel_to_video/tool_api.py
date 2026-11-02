"""novel_to_video/tool_api.py — UI / CLI / daemon 共用的统一接口。

所有函数返回普通 dict（可直接 JSON 序列化），不抛出业务异常：
  {"ok": bool, "data": ..., "error": {"message": str} | None}
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import project_store as ps
from . import gender_review
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
    tts_cfg = ps.read_json(project_dir / "project.json").get("tts") or {}
    return {
        "text": make_text_llm(p.get("text")),
        "image": make_image_provider(p.get("image")),
        "video": make_video_provider(p.get("video"), ps.read_json(project_dir / "project.json").get("video") or {}),
        "tts": make_tts_provider(p.get("tts"), tts_cfg),
    }


def run_chapter(project_dir: str, no: int, *, force_script: bool = False) -> Dict[str, Any]:
    """运行单章 C-1…C-9（可重入）。返回 status 与 message，见 chapter_pipeline。"""
    import traceback
    from . import joblog as _jl
    pdir = Path(project_dir)
    try:
        with _jl.heartbeat(pdir, "chapter", f"第 {no} 章 初始化引擎（文本/图像/视频/配音）"):
            prov = _providers_for(pdir)
    except ProviderError as exc:
        msg = f"初始化引擎失败：{exc}"
        _jl.log(pdir, "chapter", f"第 {no} 章 {msg}", level="error")
        cio.set_chapter_status(pdir, no, "failed", msg)
        return _fail(msg)
    except Exception as exc:  # noqa: BLE001 - 未预期的错误也必须进入章节日志
        msg = f"初始化引擎时出现未预期的错误：{type(exc).__name__}：{exc}"
        _jl.log(pdir, "chapter", f"第 {no} 章 {msg}\n" + "\n".join(traceback.format_exc().strip().splitlines()[-12:]),
                level="error")
        cio.set_chapter_status(pdir, no, "failed", msg)
        return _fail(msg)
    try:
        res = cp.run_chapter(pdir, no, text_llm=prov["text"], image=prov["image"],
                             video=prov["video"], tts=prov["tts"], force_script=force_script)
    except Exception as exc:
        import traceback
        from . import joblog as _jl
        msg = f"章节 {no} 运行异常：{type(exc).__name__}：{exc}"
        _jl.log(pdir, "chapter", msg + "\n" + "\n".join(traceback.format_exc().strip().splitlines()[-12:]), level="error")
        return _fail(msg)
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
              visual_hint_zh: Optional[str] = None, block_texts: Optional[Dict[int, str]] = None,
              character_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    try:
        data = sr.edit(Path(project_dir), no, sid, visual_hint_en=visual_hint_en,
                       visual_hint_zh=visual_hint_zh, block_texts=block_texts, character_ids=character_ids)
    except Exception as exc:
        return _fail(str(exc))
    return _ok(data)


def replan_shots(project_dir: str, no: int) -> Dict[str, Any]:
    """重新规划本章镜头：清空本章所有由镜头派生的产物，把进度回到 C-3 之前。

    清理范围（都位于 chapters/NNN/ 下）：镜头规划 shots.yaml、镜头校验结果 shots_review.yaml、
    逐镜审核 review.json、配音 audio/、小场景视频 clips/、本章成片 video.mp4。
    保留：剧本（script 与剧本校验结果）与章节源文本。
    进度：state.json 只保留 script 阶段；章节状态置为 pending，下一次运行从 C-3 开始。
    补建角色的确认记录在全书级 global/，不随本章清空；本章的补建角色只在未确认时才会再提示。"""
    import shutil
    pdir = Path(project_dir)
    cdir = cio.chapter_dir(pdir, no)
    try:
        doc = cio.load_shots(pdir, no)
        shot_count = len(doc.get("shots") or [])
        if not (cdir / "shots.yaml").is_file() and not (cdir / "clips").exists():
            return _fail("本章还没有镜头规划或生成产物")
        removed: List[str] = []
        for sub in ("audio", "clips"):
            d = cdir / sub
            if d.exists():
                shutil.rmtree(d)
                removed.append(f"{sub}/")
        for name in ("shots.yaml", "shots_review.yaml", "review.json", "video.mp4"):
            f = cdir / name
            if f.is_file():
                f.unlink()
                removed.append(name)
        # 进度：只保留剧本阶段，其余阶段回到未运行
        state = cio.load_state(pdir, no)
        state["stages"] = {k: v for k, v in (state.get("stages") or {}).items() if k == "script"}
        cio.save_state(pdir, no, state)
        cio.set_chapter_status(pdir, no, "pending", "已重新规划，等待运行（将从 C-3 开始）")
        ps.append_progress(pdir, f"第 {no} 章已清空镜头、配音、视频与审核产物（原 {shot_count} 个镜头），等待重新规划")
    except Exception as exc:
        return _fail(f"重新规划失败：{type(exc).__name__}：{exc}")
    return _ok({"discarded": shot_count, "removed": removed})


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
    """按挡位批量运行选中章节（持有项目锁；锁被占用时拒绝）。"""
    from . import runlock as _rl
    pdir = Path(project_dir)
    try:
        _rl.acquire(pdir)
    except _rl.LockBusy as exc:
        return _fail(str(exc))
    try:
        return _run_queue_locked(project_dir, numbers, gear=gear)
    finally:
        _rl.release(pdir)


def _run_queue_locked(project_dir: str, numbers: Optional[List[int]] = None, *,
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

    from . import runlock as _rl

    hb_warned = {"done": False}

    def run_one(no: int) -> Dict[str, Any]:
        if not _rl.heartbeat(pdir) and not hb_warned["done"]:
            hb_warned["done"] = True
            from . import joblog as _jl
            _jl.log(pdir, "queue", "运行锁心跳写入失败（文件可能被其他程序占用），已忽略，运行继续", level="warn")
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


# ---------------------------------------------------------------- Phase 4：全书合成与收尾

from .steps import final_compose as _fc


def final_check(project_dir: str) -> Dict[str, Any]:
    """F-1：章节是否全部就绪，以及封面与确认状态。"""
    pdir = Path(project_dir)
    try:
        chk = _fc.check_chapters_ready(pdir)
        return _ok({**chk, "cover": _fc.cover_status(pdir),
                    "confirm": _fc.confirm_state(pdir),
                    "final_stale": _rev.final_is_stale(pdir),
                    "exists": (pdir / "exports" / "video.mp4").is_file()})
    except Exception as exc:
        return _fail(f"F-1 检查失败：{exc}")


def set_cover(project_dir: str, *, enabled: bool, title: Optional[str] = None,
              layout: Optional[str] = None, duration_sec: Optional[float] = None) -> Dict[str, Any]:
    try:
        cov = _fc.set_cover(Path(project_dir), enabled=enabled, title=title, layout=layout,
                            duration_sec=duration_sec)
    except Exception as exc:
        return _fail(str(exc))
    ps.append_progress(Path(project_dir), f"封面设置：{cov}")
    return _ok(cov)


def render_cover(project_dir: str, *, image_provider: Optional[str] = None) -> Dict[str, Any]:
    """F-2：生成背景图（缺失时）并叠字。背景图生成需要图像 provider。"""
    pdir = Path(project_dir)
    try:
        prov = make_image_provider(image_provider or _project_providers(pdir).get("image"))
    except ProviderError as exc:
        return _fail(str(exc))
    try:
        out = _fc.render_cover_assets(pdir, bg_provider=prov)
    except Exception as exc:
        return _fail(str(exc))
    return _ok(out)


def confirm_final(project_dir: str, note: str = "") -> Dict[str, Any]:
    try:
        return _ok(_fc.confirm_final(Path(project_dir), note=note))
    except Exception as exc:
        return _fail(str(exc))


def compose_book(project_dir: str, *, transition: str = "cut", use_cover: Optional[bool] = None) -> Dict[str, Any]:
    """F-3 + F-4。挡位只影响前面的检查点，本步骤在任何挡位下都需要先人工确认。"""
    pdir = Path(project_dir)
    try:
        gear = json.loads((pdir / "project.json").read_text(encoding="utf-8")).get("gear", "G0")
    except (OSError, ValueError) as exc:
        return _fail(f"读取挡位失败：{exc}")
    try:
        res = _fc.compose_book(pdir, gear=gear, transition=transition, use_cover=use_cover)
    except Exception as exc:
        return _fail(str(exc))
    return _ok(res)


# ---------------------------------------------------------------- 驾驶舱：后台运行与状态

from . import runtime as _rt
from . import cockpit as _ck


def cockpit_status(project_dir: str) -> Dict[str, Any]:
    try:
        return _ok(_ck.build_cockpit(Path(project_dir)))
    except Exception as exc:
        return _fail(f"读取驾驶舱失败：{exc}")


def start_queue_background(project_dir: str, numbers: Optional[List[int]] = None,
                           gear: Optional[str] = None) -> Dict[str, Any]:
    """在后台线程运行队列（驾驶舱使用）。锁被占用时立即返回失败，不阻塞界面。"""
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
    res = _rt.start_background(pdir, targets, g, lambda: _run_queue_locked(project_dir, targets, gear=g))
    if not res["ok"]:
        return _fail(res["error"])
    return _ok({"started": True, "chapters": targets, "gear": g})


def stop_queue(project_dir: str) -> Dict[str, Any]:
    pdir = Path(project_dir)
    if not _rt.read_state(pdir).get("state") in ("running", "stopping"):
        return _fail("当前没有正在运行的队列")
    _rt.request_stop(pdir)
    return _ok({"stop_requested": True})


# ---------------------------------------------------------------- 后台任务与实时日志

from . import jobs as _jobs
from . import joblog as _joblog


def _unwrap(res: Dict[str, Any]) -> Any:
    if not res.get("ok"):
        raise RuntimeError(res["error"]["message"])
    return res["data"]


def _start_job(project_dir: str, *, name: str, label: str, scope: str, fn) -> Dict[str, Any]:
    pdir = Path(project_dir)
    res = _jobs.start(pdir, name=name, label=label, scope=scope, fn=fn)
    if not res["ok"]:
        return _fail(res["error"])
    return _ok({"started": name, "label": label})


def start_visual_text_background(project_dir: str, *, ids: Optional[List[str]] = None,
                                 only_missing: bool = True) -> Dict[str, Any]:
    return _start_job(project_dir, name="assets_text", label="G-2 文本设定补全", scope="assets",
                      fn=lambda: _unwrap(run_visual_text(project_dir, ids=ids, only_missing=only_missing)))


def start_portraits_background(project_dir: str, *, ids: Optional[List[str]] = None,
                               regenerate: bool = False) -> Dict[str, Any]:
    label = "G-3 重新生成定妆图" if regenerate else "G-3 生成定妆图"
    return _start_job(project_dir, name="assets_portrait", label=label, scope="assets",
                      fn=lambda: _unwrap(run_portraits(project_dir, ids=ids, regenerate=regenerate)))


def start_cover_background(project_dir: str) -> Dict[str, Any]:
    return _start_job(project_dir, name="cover", label="封面生成", scope="final",
                      fn=lambda: _unwrap(render_cover(project_dir)))


def start_compose_background(project_dir: str, *, transition: str = "cut",
                             use_cover: Optional[bool] = None) -> Dict[str, Any]:
    return _start_job(project_dir, name="final_compose", label="F-3/F-4 全书合成", scope="final",
                      fn=lambda: _unwrap(compose_book(project_dir, transition=transition, use_cover=use_cover)))


def start_gender_suggest_background(project_dir: str) -> Dict[str, Any]:
    def fn() -> Dict[str, Any]:
        data = gender_review.suggest_all(Path(project_dir))
        return {"done": list(data["suggestions"].keys()), "failed": [], "skipped": []}
    return _start_job(project_dir, name="gender_suggest", label="性别线索检索", scope="gender", fn=fn)


def job_status(project_dir: str) -> Dict[str, Any]:
    return _ok(_jobs.status(Path(project_dir)))


def job_stop(project_dir: str) -> Dict[str, Any]:
    res = _jobs.request_stop(Path(project_dir))
    return _ok(res) if res["ok"] else _fail(res["error"])


def job_logs(project_dir: str, scopes: Optional[List[str]] = None, limit: int = 200) -> Dict[str, Any]:
    rows = _joblog.tail(Path(project_dir), limit=limit, scopes=scopes)
    return _ok(rows)


def llm_trace(project_dir: str, chapter: Optional[int] = None, scopes: Optional[List[str]] = None,
              steps: Optional[List[str]] = None, limit: int = 20) -> Dict[str, Any]:
    """文本模型调用的完整输入/输出（最近 limit 条，新到旧），供界面展示。"""
    from . import llm_trace as _lt
    try:
        return _ok(_lt.tail(Path(project_dir), limit=limit, chapter=chapter, scopes=scopes, steps=steps))
    except OSError as exc:
        return _fail(f"读取模型调用记录失败：{exc}")


def merge_candidates(project_dir: str, auto_id: str) -> Dict[str, Any]:
    from . import merge_character as _mc
    try:
        return _ok(_mc.merge_candidates(Path(project_dir), auto_id))
    except Exception as exc:
        return _fail(f"读取角色列表失败：{exc}")


def merge_auto_added(project_dir: str, auto_id: str, target_id: str) -> Dict[str, Any]:
    """把补建角色合并到已有角色（别名、改写镜头引用、清理已生成的下游产物）。"""
    from . import merge_character as _mc
    try:
        return _ok(_mc.merge_auto_added(Path(project_dir), auto_id, target_id))
    except (_mc.MergeError, OSError, KeyError, ValueError) as exc:
        return _fail(str(exc))


def _set_retries(project_dir: str, key: str, label: str, retries: int) -> Dict[str, Any]:
    pdir = Path(project_dir)
    try:
        n = int(retries)
        if not 0 <= n <= ps.MAX_AUTO_FIX_RETRIES:
            return _fail(f"自动修正次数应在 0–{ps.MAX_AUTO_FIX_RETRIES} 之间")
        proj = ps.read_json(pdir / "project.json")
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    proj[key] = n
    ps.write_json_atomic(pdir / "project.json", proj)
    ps.append_progress(pdir, f"{label}校验失败后的自动修正次数设为 {n}")
    return _ok({key: n})


def set_character_looks(project_dir: str, enabled: bool) -> Dict[str, Any]:
    """角色多形象开关（默认关闭）：开启后镜头规划会在角色换装/受伤等外观变化时指定形象，
    缺失的形象由 C-5 起草并走与补建角色相同的确认流程。已有镜头不受影响，需要时「重新规划本章镜头」。"""
    pdir = Path(project_dir)
    try:
        proj = ps.read_json(pdir / "project.json")
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    proj["character_looks_enabled"] = bool(enabled)
    ps.write_json_atomic(pdir / "project.json", proj)
    ps.append_progress(pdir, f"角色多形象{'开启' if enabled else '关闭'}")
    return _ok({"character_looks_enabled": bool(enabled)})


def set_script_retries(project_dir: str, retries: int) -> Dict[str, Any]:
    """剧本校验失败后自动带反馈修正的次数（默认 5；0 = 不修正、直接失败；上限 5）。"""
    return _set_retries(project_dir, "script_max_retries", "剧本", retries)


def set_video_format(project_dir: str, orientation: str, resolution: str, fps: int,
                     poll_interval_sec: float) -> Dict[str, Any]:
    """视频输出格式：orientation 为 landscape（横屏 16:9，默认）或 portrait（竖屏 9:16）；
    resolution 为 720P 或 1080P；fps 为 24/25/30；poll_interval_sec 为轮询视频状态的间隔（1–60 秒）。"""
    from .video_format import FPS_CHOICES, ORIENTATIONS, RESOLUTIONS
    if orientation not in ORIENTATIONS:
        return _fail("画面方向应为 landscape（横屏）或 portrait（竖屏）")
    if resolution not in RESOLUTIONS:
        return _fail("分辨率应为 720P 或 1080P")
    if int(fps) not in FPS_CHOICES:
        return _fail(f"帧率应为 {'/'.join(str(x) for x in FPS_CHOICES)}")
    if not (1 <= float(poll_interval_sec) <= 60):
        return _fail("轮询间隔应在 1–60 秒之间")
    pdir = Path(project_dir)
    try:
        proj = ps.read_json(pdir / "project.json")
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    video = dict(proj.get("video") or {})
    video.update(orientation=orientation, resolution=resolution, fps=int(fps), poll_interval_sec=float(poll_interval_sec))
    proj["video"] = video
    ps.write_json_atomic(pdir / "project.json", proj)
    ps.append_progress(pdir, f"视频输出格式：{orientation} {resolution} {int(fps)}fps，轮询间隔 {poll_interval_sec}s")
    return _ok({k: video[k] for k in ("orientation", "resolution", "fps", "poll_interval_sec")})


def set_video_wait(project_dir: str, max_wait_sec: int, max_retries: int, queue_wait_sec: int) -> Dict[str, Any]:
    """视频接口的等待与重试设置。max_wait_sec：等待视频完成的上限；max_retries：普通错误的重试次数（0–10）；
    queue_wait_sec：服务端排队已满（please retry later）时最多等待多久，期间不计入重试次数。"""
    if not (60 <= int(max_wait_sec) <= 6 * 3600):
        return _fail("等待视频完成的上限应在 60–21600 秒之间")
    if not (0 <= int(max_retries) <= 10):
        return _fail("重试次数应在 0–10 之间")
    if not (0 <= int(queue_wait_sec) <= 6 * 3600):
        return _fail("排队等待上限应在 0–21600 秒之间")
    pdir = Path(project_dir)
    try:
        proj = ps.read_json(pdir / "project.json")
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    video = dict(proj.get("video") or {})
    video.update(max_wait_sec=int(max_wait_sec), max_retries=int(max_retries), queue_wait_sec=int(queue_wait_sec))
    proj["video"] = video
    ps.write_json_atomic(pdir / "project.json", proj)
    ps.append_progress(pdir, f"视频等待设置：完成上限 {max_wait_sec}s，重试 {max_retries} 次，排队等待上限 {queue_wait_sec}s")
    return _ok({k: video[k] for k in ("max_wait_sec", "max_retries", "queue_wait_sec")})


def set_tts_proxy(project_dir: str, mode: Optional[str]) -> Dict[str, Any]:
    """配音（edge-tts）的网络方式：None = 系统代理（默认）；"direct" = 强制直连，不使用任何代理；
    其他字符串 = 指定代理地址，如 http://127.0.0.1:7890。"""
    pdir = Path(project_dir)
    value = (mode or "").strip() or None
    if value and value != "direct" and not (value.startswith("http://") or value.startswith("socks")):
        return _fail("代理地址应为 direct，或以 http:// / socks 开头")
    try:
        proj = ps.read_json(pdir / "project.json")
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    tts = dict(proj.get("tts") or {})
    tts["edge_tts_proxy"] = value
    proj["tts"] = tts
    ps.write_json_atomic(pdir / "project.json", proj)
    ps.append_progress(pdir, f"配音网络方式设为 {value or '系统代理'}")
    return _ok({"edge_tts_proxy": value})


def set_shots_retries(project_dir: str, retries: int) -> Dict[str, Any]:
    """镜头规划校验失败后自动带反馈修正的次数（默认 5；0 = 不修正、直接失败；上限 5）。"""
    return _set_retries(project_dir, "shots_max_retries", "镜头规划", retries)
