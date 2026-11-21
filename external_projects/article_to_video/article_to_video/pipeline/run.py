"""pipeline/run.py — 可重入流水线（P7）：outline → script → voice → storyboard → render → compose → export。

可重入：每一步先检查已有产物，已完成则跳过；上游变化时下游按各自的指纹重算。
门控：遇到需要人工确认的检查点（见 gates.py）时停在那里，state=waiting；人工确认后再次运行即可继续。
预算：G2 必须设置上限；超限时 state=budget_exceeded 并保存进度，不再调用模型或生图。
停止：与其它模块一样响应停止请求（cancel），state=stopped。
状态保存在 pipeline.json，界面与命令行读取它显示进度。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import cancel, joblog
from ..audio.synth import synthesize_part
from ..compose.export import export_final
from ..compose.part import compose_part, video_dir
from ..planning.runner import confirm_spine, load_spine, plan_outline
from ..project_store import now_iso, read_json, write_json_atomic
from ..render.compose_part import render_part
from ..script.writer import confirm_script, load_script, write_part
from ..visual.storyboard import confirm_storyboard, plan_storyboard, storyboard_status
from .budget import BudgetedImageProvider, BudgetExceeded, Ledger
from .episodes import DEFAULT_EPISODE_MAX, episode_specs
from .gates import STAGES, PipelineError, check_budget_requirement, manual_checkpoints


SCOPE = "pipeline"   # 日志 scope


def _tick(ctx: Dict[str, Any]) -> None:
    ctx["ledger"].checkpoint()


def _sub(pdir: Path, stage: str, item: str):
    """阶段下的子步骤（某一章、某一期…）：写开始/结束事件，耗时久时定期写“仍在进行”。"""
    return joblog.step(pdir, SCOPE, stage, item)


def _note(pdir: Path, message: str, level: str = "info") -> None:
    joblog.log(pdir, SCOPE, message, level=level)


def _done(detail: str = "") -> Dict[str, Any]:
    return {"result": "done", "detail": detail}


def _waiting(detail: str) -> Dict[str, Any]:
    return {"result": "waiting", "detail": detail}


def _parts(pdir: Path) -> List[Dict[str, Any]]:
    spine = load_spine(pdir)
    if spine is None:
        raise PipelineError("还没有大纲")
    return spine["parts"]


def _stage_outline(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    spine = load_spine(pdir)
    if spine is None:
        engine = ctx["outline_engine"]
        if engine == "llm" and ctx["llm"] is None:
            raise PipelineError("大纲选择了模型版，但未配置文本模型")
        with _sub(pdir, "outline", f"生成大纲（{'模型' if engine == 'llm' else '离线规则'}）"):
            plan_outline(pdir, engine=engine, llm=ctx["llm"] if engine == "llm" else None)
        spine = load_spine(pdir)
    if spine.get("confirmed"):
        return _done("大纲已确认")
    if "outline" in ctx["manual"]:
        return _waiting("C0 大纲待人工确认（界面：🗺️ 大纲）")
    confirm_spine(pdir)  # 未通过 V1 校验时抛出，流水线记为失败
    return _done("大纲已自动确认（G1/G2）")


def _stage_script(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    for p in _parts(pdir):
        sc = load_script(pdir, p["id"])
        if sc is None:
            if ctx["llm"] is None:
                raise PipelineError("写稿需要文本模型（未配置）")
            with _sub(pdir, "script", p["id"]):
                write_part(pdir, p["id"], ctx["llm"])
            _tick(ctx)
        else:
            _note(pdir, f"{p['id']} 口播稿已存在，跳过")
    pending = [p["id"] for p in _parts(pdir) if not (load_script(pdir, p["id"]) or {}).get("confirmed")]
    if not pending:
        return _done("口播稿已确认")
    if "script" in ctx["manual"]:
        return _waiting("C1 口播待人工确认：" + "、".join(pending))
    for pid in pending:
        confirm_script(pdir, pid)
    return _done(f"口播稿自动确认：{len(pending)} 章")


def _stage_voice(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    if ctx["tts"] is None:
        raise PipelineError("配音需要语音引擎（未配置）")
    for p in _parts(pdir):
        if not (load_script(pdir, p["id"]) or {}).get("confirmed"):
            return _waiting("请先确认全部口播稿（C1）")
    for p in _parts(pdir):
        with _sub(pdir, "voice", p["id"]):
            synthesize_part(pdir, p["id"], ctx["tts"])
        _tick(ctx)
    return _done("配音与时间轴完成")


def _stage_storyboard(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    engine = ctx["storyboard_engine"]
    for p in _parts(pdir):
        pid = p["id"]
        st = storyboard_status(pdir, pid)
        if st["stale"] and "storyboard" in ctx["manual"]:
            return _waiting(f"{pid} 分镜已过期，需人工重新生成（C2）")
        if not st["exists"] or st["stale"]:
            with _sub(pdir, "storyboard", pid):
                plan_storyboard(pdir, pid, engine=engine, llm=ctx["llm"] if engine == "llm" else None)
            _tick(ctx)
        else:
            _note(pdir, f"{pid} 分镜已是最新，跳过")
    pending = [p["id"] for p in _parts(pdir) if not storyboard_status(pdir, p["id"])["confirmed"]]
    if not pending:
        return _done("分镜已确认")
    if "storyboard" in ctx["manual"]:
        return _waiting("C2 分镜待人工确认：" + "、".join(pending))
    for pid in pending:
        confirm_storyboard(pdir, pid)
    return _done(f"分镜自动确认：{len(pending)} 章")


def _stage_render(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    for p in _parts(pdir):
        if not storyboard_status(pdir, p["id"])["confirmed"]:
            return _waiting("请先确认全部分镜（C2）")
    for p in _parts(pdir):
        with _sub(pdir, "render", p["id"]):
            render_part(pdir, p["id"], provider=ctx["provider"])
        _tick(ctx)
    return _done("画面渲染完成")


def _compose_fresh(pdir: Path, pid: str) -> bool:
    mp4 = video_dir(pdir, pid) / "part.mp4"
    if not mp4.is_file():
        return False
    deps = [pdir / "parts" / pid / "visuals" / "manifest.json", pdir / "parts" / pid / "storyboard.json",
            pdir / "parts" / pid / "timeline.json"]
    return all(d.is_file() and d.stat().st_mtime_ns <= mp4.stat().st_mtime_ns for d in deps)


def _stage_compose(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    done = 0
    for p in _parts(pdir):
        if _compose_fresh(pdir, p["id"]):
            _note(pdir, f"{p['id']} 章视频已是最新，跳过")
            continue
        with _sub(pdir, "compose", p["id"]):
            compose_part(pdir, p["id"])
        done += 1
        _tick(ctx)
    return _done(f"章合成：新做 {done} 章")


def _stage_export(pdir: Path, ctx: Dict[str, Any]) -> Dict[str, Any]:
    parts = _parts(pdir)
    from ..app_settings import load_effective_project  # 延迟导入
    project = load_effective_project(pdir)
    specs = episode_specs(parts, float(project["settings"].get("episode_max_minutes", DEFAULT_EPISODE_MAX)))
    ctx["status"]["episodes"] = [{"index": s["index"], "total": s["total"], "parts": s["parts"]} for s in specs]
    if "export" in ctx["manual"] and not ctx["approve"]:
        return _waiting("终审待人工确认：检查成片后以 approve 导出")
    outputs = []
    if len(specs) == 1:
        with _sub(pdir, "export", "成片"):
            outputs.append(export_final(pdir, part_ids=specs[0]["parts"])["files"]["video"])
    else:
        for s in specs:
            with _sub(pdir, "export", f"第 {s['index']}/{s['total']} 期"):
                rep = export_final(pdir, part_ids=s["parts"], episode=s, label=f"_第{s['index']}期")
            outputs.append(rep["files"]["video"])
            _tick(ctx)
        if ctx["merge_all"]:
            with _sub(pdir, "export", "全集合并"):
                outputs.append(export_final(pdir, part_ids=[p["id"] for p in parts],
                                            label="_全集")["files"]["video"])
    ctx["status"]["outputs"] = outputs
    return _done("导出：" + "、".join(outputs))


_STAGE_FNS = {"outline": _stage_outline, "script": _stage_script, "voice": _stage_voice,
              "storyboard": _stage_storyboard, "render": _stage_render, "compose": _stage_compose,
              "export": _stage_export}


def _save(pdir: Path, status: Dict[str, Any], now: Optional[datetime]) -> None:
    status["updated_at"] = now_iso(now)
    write_json_atomic(pdir / "pipeline.json", status)


def run_pipeline(pdir: Path, *, until: str = "export", gear: str = "G0", max_images: Optional[int] = None,
                 max_minutes: Optional[float] = None, approve: bool = False, llm=None, tts=None,
                 image_provider=None, outline_engine: str = "llm", storyboard_engine: str = "rules",
                 merge_all: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    pdir = Path(pdir)
    if until not in STAGES:
        raise PipelineError(f"未知阶段：{until}")
    check_budget_requirement(gear, max_images, max_minutes)
    ledger = Ledger(pdir, max_images=max_images, max_minutes=max_minutes)
    provider = BudgetedImageProvider(image_provider, ledger) if image_provider is not None else None
    status: Dict[str, Any] = {"gear": gear, "until": until, "state": "running", "steps": [],
                              "waiting": None, "error": None, "episodes": [], "outputs": [],
                              "budget": None, "started_at": now_iso(now)}
    ctx = {"manual": manual_checkpoints(gear), "approve": approve, "llm": llm, "tts": tts,
           "provider": provider, "outline_engine": outline_engine, "storyboard_engine": storyboard_engine,
           "merge_all": merge_all,
           "ledger": ledger, "status": status}
    run_name = f"流水线 {gear}（运行到 {until}）"
    run_id = joblog.run_start(pdir, SCOPE, run_name)
    status["run_id"] = run_id
    run_started = status["started_at"]
    _save(pdir, status, now)   # 开始即落盘：命令行启动的运行，界面也能看到“运行中”
    end_status, end_error, end_tb = "ok", None, None
    try:
        with joblog.bind(pdir, SCOPE):
            _note(pdir, f"挡位 {gear}；人工检查点：{'、'.join(sorted(ctx['manual'])) or '无'}；"
                        f"预算：生图 {max_images or '不限'}，耗时 {max_minutes or '不限'} 分钟")
            for stage in STAGES[: STAGES.index(until) + 1]:
                cancel.check()
                _tick(ctx)
                with joblog.step(pdir, SCOPE, stage, heartbeat=False) as info:
                    res = _STAGE_FNS[stage](pdir, ctx)
                    info["detail"] = res["detail"]
                    if res["result"] == "waiting":
                        info["status"] = "waiting"
                status["steps"].append({"stage": stage, "result": res["result"], "detail": res["detail"]})
                _save(pdir, status, now)
                if res["result"] == "waiting":
                    status["state"] = "waiting"
                    status["waiting"] = {"stage": stage, "detail": res["detail"]}
                    end_status = "waiting"
                    break
            else:
                status["state"] = "done"
    except BudgetExceeded as exc:
        status["state"], status["error"] = "budget_exceeded", str(exc)
        end_status, end_error = "failed", str(exc)
    except cancel.Cancelled:
        status["state"], status["error"] = "stopped", "用户请求停止"
        end_status, end_error = "stopped", "用户请求停止"
    except Exception as exc:  # noqa: BLE001 — 记录失败原因，便于界面展示
        status["state"], status["error"] = "failed", str(exc)
        end_status, end_error, end_tb = "failed", str(exc), joblog.format_tb(exc)
    finally:
        try:
            ledger.checkpoint()
        except BudgetExceeded:
            pass
        status["budget"] = ledger.summary()
        _save(pdir, status, now)
        if end_status == "waiting":
            end_error = status["waiting"]["detail"]
        joblog.run_end(pdir, SCOPE, run_id, run_name, end_status, end_error, end_tb, run_started)
    return status


def read_status(pdir: Path) -> Optional[Dict[str, Any]]:
    p = Path(pdir) / "pipeline.json"
    return read_json(p) if p.is_file() else None


def default_providers(pdir: Path, *, offline: bool = False) -> Dict[str, Any]:
    """项目的默认引擎：文本模型（mini_agent）、语音降级链、生图（按项目设置）。offline=True 时语音静音、生图占位。"""
    from ..audio.synth import project_tts_config
    from ..project_store import load_project
    from ..providers.image import make_image_provider
    from ..providers.text import make_text_llm
    from ..providers.tts import SilenceTTS, build_chain

    if offline:
        return {"llm": None, "tts": SilenceTTS(), "image": make_image_provider("stub")}
    cfg = project_tts_config(Path(pdir))
    from ..app_settings import effective_group  # 延迟导入
    from ..providers.image import project_image_provider
    return {"llm": make_text_llm("miniagent", pdir=Path(pdir)),
            "tts": build_chain(cfg["engine_chain"], edge_proxy=cfg["edge_proxy"], use_system_proxy=cfg["use_system_proxy"]),
            "image": project_image_provider(Path(pdir), scope="流水线 ")}
