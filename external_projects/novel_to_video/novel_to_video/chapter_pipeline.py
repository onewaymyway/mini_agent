"""chapter_pipeline.py — 单章流水线 C-1 … C-9，可重入。

每次运行都从头检查各阶段：已完成且输入未变的阶段直接跳过，因此可以反复调用。
停止点（返回的 status）：
  waiting_assets  补建角色等待确认文本设定/定妆图，或缺少定妆图
  waiting_review  所有小场景视频已生成，等待逐镜审核
  failed          某阶段失败，message 说明原因，修正后重新运行即可
  done            章节视频 chapter_NNN.mp4 已生成
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from . import cancel
from . import chapter_io as cio
from .media import ffmpeg as mf
from .project_store import read_json
from .providers.image import ImageProvider
from .providers.text import TextLLM
from .providers.tts import TTSProvider
from .providers.video import VideoProvider
from .shot_review import shot_states
from . import joblog
from .steps.chapter_assets import run_assets
from .steps.chapter_audio import run_audio
from .steps.chapter_clips import run_clips, run_prompts
from .steps.chapter_compose import run_compose
from .steps.chapter_script import run_script
from .steps.chapter_shots import run_shots
from .steps.global_visual import StepError
from .providers.factory import ProviderError
from .media.ffmpeg import MediaError

STAGES = ["script", "shots", "assets", "audio", "prompts", "clips", "compose"]


def _result(status: str, message: str = "", **extra: Any) -> Dict[str, Any]:
    return {"status": status, "message": message, **extra}


def run_chapter(project_dir: Path, no: int, *, text_llm: TextLLM, image: ImageProvider,
                video: VideoProvider, tts: TTSProvider, force_script: bool = False) -> Dict[str, Any]:
    from .media import ffmpeg as _mf_fmt
    _mf_fmt.use_project_format(project_dir)
    project_dir = Path(project_dir)
    row = cio.chapter_row(project_dir, no)
    if not row.get("selected"):
        return _result("failed", "该章节未被选中，请先在项目设置中选中")
    cio.chapter_dir(project_dir, no).mkdir(parents=True, exist_ok=True)
    cio.set_chapter_status(project_dir, no, "running")
    joblog.log(project_dir, "chapter", f"第 {no} 章开始运行")
    art_style = read_json(project_dir / "project.json").get("art_style")

    def stop(status: str, message: str, **extra) -> Dict[str, Any]:
        cio.set_chapter_status(project_dir, no, status, message)
        joblog.log(project_dir, "chapter", f"第 {no} 章结束：{status}　{message}",
                   level={"done": "ok", "failed": "error"}.get(status, "warn"))
        return _result(status, message, **extra)

    def halt() -> Dict[str, Any]:
        """用户立即停止：当前阶段记为待运行（不是失败），章节回到 pending，重新运行即可从断点继续。"""
        stage = _current_stage(project_dir, no)
        try:
            cio.mark_stage(project_dir, no, stage, "pending", "已手动停止")
        except Exception:  # noqa: BLE001 - 停止收尾不应因写状态失败而再抛异常
            pass
        cio.set_chapter_status(project_dir, no, "pending", "已手动停止：重新运行本章即可从断点继续")
        joblog.log(project_dir, "chapter",
                   f"第 {no} 章已立即停止（用户请求），停在：{STAGE_NAMES.get(stage, stage)}", level="warn")
        return _result("stopped", "已手动停止")

    try:
        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-1/C-2 剧本"):
            sc = run_script(project_dir, no, text_llm, force=force_script)
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-1/C-2 剧本：{sc['status']}",
                   level="ok" if sc["status"] == "pass" else "warn")
        if sc["status"] != "pass":
            msgs = [i["message"] for i in sc.get("issues", []) if i["level"] == "error"]
            return stop("failed", "剧本校验未通过：" + "；".join(msgs[:3]), issues=sc.get("issues", []))

        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-3/C-4 镜头规划"):
            _sh = run_shots(project_dir, no, text_llm)
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-3/C-4 镜头规划：{_sh.get('shots')} 个镜头"
                   + ("（沿用已有规划）" if _sh.get("skipped") else ""))

        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-5 补建角色"):
            assets = run_assets(project_dir, no, text_llm, image)
        if assets["waiting"]:
            joblog.log(project_dir, "chapter", f"第 {no} 章 C-5 等待补建角色确认：{len(assets['waiting'])} 项", level="warn")
        if assets["waiting"]:
            return stop("waiting_assets", "等待补建角色确认：" + "；".join(assets["waiting"]),
                        waiting=assets["waiting"])

        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-6 配音"):
            audio = run_audio(project_dir, no, tts)
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-6 配音完成" + (f"，{len(audio['errors'])} 个镜头口播过长" if audio["errors"] else ""),
                   level="warn" if audio["errors"] else "ok")
        if audio["errors"]:
            return stop("failed", f"镜头口播过长，需拆分：{', '.join(audio['errors'])}", errors=audio["errors"])

        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-7 提示词"):
            pr = run_prompts(project_dir, no, art_style)
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-7 提示词一致性：{'未通过 ' + str(len(pr['failed'])) + ' 个' if pr['failed'] else '通过'}",
                   level="warn" if pr["failed"] else "ok")
        if pr["failed"]:
            return stop("failed", f"提示词一致性检查未通过：{', '.join(pr['failed'])}")

        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-8 小场景视频"):
            clips = run_clips(project_dir, no, video)
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-8 小场景视频：生成 {len(clips.get('generated', []))} 个，失败 {len(clips.get('failed', []))} 个",
                   level="warn" if clips.get("failed") else "info")
        if clips.get("blocked"):
            return stop("waiting_assets", "缺少定妆图，请先到全书素材页生成并通过：" + "、".join(clips["blocked"]),
                        waiting=clips["blocked"])
        if clips.get("stopped"):
            return stop("failed", f"视频生成被停止：{clips['stopped']}")
        if clips["failed"]:
            return stop("failed", f"以下镜头视频生成失败，修正后重跑：{', '.join(clips['failed'])}",
                        failed=clips["failed"])

        states = shot_states(project_dir, no)
        if any(st != "approved" for st in states.values()):
            pending = [sid for sid, st in states.items() if st != "approved"]
            return stop("waiting_review", f"{len(pending)} 个小场景等待审核", pending=pending, states=states)

        with joblog.heartbeat(project_dir, "chapter", f"第 {no} 章 C-9 章节合成"):
            comp = run_compose(project_dir, no)
        if comp.get("waiting"):
            return stop("waiting_review", "等待审核：" + "、".join(comp["waiting"]))
        return stop("done", f"章节视频已生成（{comp['duration']} 秒）", output=comp["output"],
                    duration=comp["duration"])
    except cancel.Cancelled:  # 用户立即停止：不计为失败
        return halt()
    except (StepError, ProviderError, MediaError, RuntimeError, ValueError, KeyError) as exc:
        if cancel.is_requested(project_dir):  # 停止时各步骤常以“已停止”返回，统一按手动停止处理
            return halt()
        stage = _current_stage(project_dir, no)
        msg = f"{STAGE_NAMES.get(stage, stage)}：{exc}"
        cio.mark_stage(project_dir, no, stage, "failed", msg)
        return stop("failed", msg)
    except Exception as exc:  # noqa: BLE001 - 未预期的异常也必须带阶段与原因，不能静默失败
        if cancel.is_requested(project_dir):
            return halt()
        import traceback
        stage = _current_stage(project_dir, no)
        msg = f"{STAGE_NAMES.get(stage, stage)}：未预期的错误 {type(exc).__name__}：{exc}"
        cio.mark_stage(project_dir, no, stage, "failed", msg)
        tb = traceback.format_exc().strip().splitlines()
        joblog.log(project_dir, "chapter", f"第 {no} 章 {msg}\n" + "\n".join(tb[-12:]), level="error")
        return stop("failed", msg)


STAGE_NAMES = {
    "script": "C-1/C-2 剧本", "shots": "C-3/C-4 镜头规划", "assets": "C-5 补建角色",
    "audio": "C-6 配音", "prompts": "C-7 提示词", "clips": "C-8 小场景视频",
    "compose": "C-9 章节合成",
}


def _current_stage(project_dir: Path, no: int) -> str:
    done = cio.load_state(project_dir, no).get("stages", {})
    for st in STAGES:
        if done.get(st, {}).get("status") != "done":
            return st
    return "compose"


def _read_yaml(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or None


def _speakers(project_dir: Path, cdir: Path, shots_doc: dict) -> list:
    """剧本说话人的最终处理结果（见 chapter_script.speaker_resolution）。"""
    path = cdir / "script.md"
    if not path.is_file():
        return []
    from .steps.chapter_script import speaker_resolution
    try:
        chars = read_json(project_dir / "global" / "characters.json")["characters"]
    except (OSError, KeyError, ValueError):
        return []
    return speaker_resolution(path.read_text(encoding="utf-8"), shots_doc, chars)


def _derive_plan_warnings(shots: list) -> list:
    out, group = [], []
    for sh in shots + [None]:
        if group and (sh is None or (sh.get("scene_no"), sh.get("visual_hint_en")) !=
                      (group[0].get("scene_no"), group[0].get("visual_hint_en"))):
            if len(group) > 1:
                out.append({"raw_index": len(out) + 1, "scene_no": group[0].get("scene_no"), "parts": len(group),
                            "seconds": round(sum(float(g.get("est_seconds") or 0) for g in group), 1)})
            group = []
        if sh is not None:
            group.append(sh)
    return out


def _entity_lookup(project_dir: Path) -> Dict[str, Dict[str, Any]]:
    try:
        ents = cio.load_entities(project_dir)
    except (OSError, KeyError, ValueError):
        return {"chars": {}, "locs": {}, "props": {}}
    chars = {c["id"]: {"id": c["id"], "name": (c.get("names") or [c["id"]])[0], "names": list(c.get("names") or []),
                       "gender": c.get("gender"), "asset": c.get("asset_path")}
             for c in ents["characters"]["characters"]}
    locs = {l["id"]: {"id": l["id"], "name": l.get("name")} for l in ents["locations"]["locations"]}
    props = {pr["id"]: {"id": pr["id"], "name": pr.get("name")} for pr in ents["props"]["props"]}
    return {"chars": chars, "locs": locs, "props": props}


def chapter_view(project_dir: Path, no: int) -> Dict[str, Any]:
    """供界面展示：章节状态、各阶段、镜头列表与审核状态。"""
    project_dir = Path(project_dir)
    row = cio.chapter_row(project_dir, no)
    shots_doc = cio.load_shots(project_dir, no) or {}
    states = shot_states(project_dir, no) if shots_doc.get("shots") else {}
    cdir = cio.chapter_dir(project_dir, no)
    shots = []
    lookup = _entity_lookup(project_dir)
    for sh in shots_doc.get("shots", []):
        cast = [lookup["chars"][cid] for cid in sh.get("character_ids") or [] if cid in lookup["chars"]]
        hint = sh.get("visual_hint_zh") or ""
        shots.append({
            **{k: sh.get(k) for k in ("id", "scene_no", "location_id", "character_ids", "visual_hint_zh",
                                      "visual_hint_en", "duration_sec", "speech_sec", "error", "clip_error",
                                      "consistency", "prompt_en", "split_from", "split_part", "split_total")},
            "characters": cast,
            "props": [lookup["props"][pid] for pid in sh.get("prop_ids") or [] if pid in lookup["props"]],
            "prop_names": list(sh.get("prop_names") or []),      # 库里没有的道具名（待补建）
            "look_ids": dict(sh.get("look_ids") or {}),          # {角色id: 形象id}（阶段 4）
            "look_labels": dict(sh.get("look_labels") or {}),    # 库里没有的形象标签（待补建）
            "location_name": (lookup["locs"].get(sh.get("location_id")) or {}).get("name"),
            # 画面描述里提到了某个角色，但出镜角色里没有：视频提示词不会带上他的外观，审核时要留意
            "missing_mentions": [c["name"] for cid, c in lookup["chars"].items()
                                 if cid not in (sh.get("character_ids") or [])
                                 and any(len(n) >= 2 and n in hint for n in c["names"])],
            "blocks": [{**b, "speaker_label": (lookup["chars"].get(b.get("speaker_id")) or {}).get("name")
                        or b.get("speaker_name")} for b in sh["blocks"]],
            "state": states.get(sh["id"], "missing"),
            "clip": str((cdir / "clips" / f"{sh['id']}.mp4").relative_to(project_dir))
            if (cdir / "clips" / f"{sh['id']}.mp4").is_file() else None,
            "audio": str((cdir / "audio" / f"{sh['id']}.wav").relative_to(project_dir))
            if (cdir / "audio" / f"{sh['id']}.wav").is_file() else None,
        })
    out_path = cdir / f"chapter_{no:03d}.mp4"
    return {
        "no": no, "title": row["title"], "status": row.get("status"), "message": row.get("message"),
        "selected": row.get("selected"),
        "stages": cio.load_state(project_dir, no).get("stages", {}),
        "script": (cdir / "script.md").read_text(encoding="utf-8") if (cdir / "script.md").is_file() else None,
        "script_review": _read_yaml(cdir / "script_review.yaml"),
        "shots_review": _read_yaml(cdir / "shots_review.yaml"),
        "speakers": _speakers(project_dir, cdir, shots_doc),
        "shots": shots,
        # 新规划的镜头自带拆分记录；旧文件没有，则按“相邻镜头场次与画面描述完全相同”推断
        "plan_warnings": (shots_doc["plan_warnings"] if "plan_warnings" in shots_doc
                          else _derive_plan_warnings(shots_doc.get("shots") or [])),
        "all_characters": [{"id": cid, "name": c["name"]} for cid, c in lookup["chars"].items()],
        "new_locations": list(shots_doc.get("new_locations") or []),   # 剧本/镜头中库里没有的地点
        "new_props": list(shots_doc.get("new_props") or []),           # 镜头中库里没有的道具
        "new_looks": list(shots_doc.get("new_looks") or []),           # 镜头中需要、角色还没有的形象
        "output": str(out_path.relative_to(project_dir)) if out_path.is_file() else None,
        "counts": {s: sum(1 for v in states.values() if v == s) for s in
                   ("approved", "pending", "stale", "rejected", "missing", "failed")} if states else {},
    }
