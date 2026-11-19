"""audio/synth.py — S5 配音：逐句合成、缓存、时间轴落盘、试听。

文件：
  parts/<章>/audio/<句ID>.wav     每句音频
  parts/<章>/audio/manifest.json  句ID → {key, file, duration, voice, rate, engine, text}
  parts/<章>/timeline.json        时间轴（纯计算的结果）
  preview/<哈希>.wav              试听（不进入项目状态）

缓存键 = 音色 + 语速 + 朗读文本 + 引擎链；任一变化才重配。编辑口播后只重配改动的句子。
任何一句合成失败都直接报错（不降级为静音）；已完成的句子已写入 manifest，下次续跑。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .. import cancel, joblog
from ..planning.errors import PlanError
from ..planning.runner import load_spine
from ..project_store import append_progress, now_iso, read_json, write_json_atomic
from ..providers.tts import sanitize_tts_text
from ..script.writer import load_script
from .config import rate_for
from .timeline import assign_voices, build_timeline


def project_tts_config(project_dir: Path) -> Dict[str, Any]:
    from ..app_settings import effective_group  # 延迟导入
    return effective_group(Path(project_dir), "settings")["tts"]


def update_tts_settings(project_dir: Path, raw: Dict[str, Any], now: Optional[datetime] = None):
    from ..app_settings import save_group, effective_group  # 延迟导入
    project_dir = Path(project_dir)
    cur = effective_group(project_dir, "settings")
    cur["tts"] = dict(raw or {})
    warnings = save_group(project_dir, "settings", cur, now=now)
    append_progress(project_dir, "更新配音设置", now)
    return effective_group(project_dir, "settings")["tts"], warnings


def _sentence_key(voice: str, rate: str, tts_text: str, engine_name: str) -> str:
    return hashlib.sha256(f"{voice}|{rate}|{tts_text}|{engine_name}".encode("utf-8")).hexdigest()[:20]


def _manifest_path(pdir: Path, part_id: str) -> Path:
    return pdir / "parts" / part_id / "audio" / "manifest.json"


def _load_manifest(pdir: Path, part_id: str) -> Dict[str, Any]:
    p = _manifest_path(pdir, part_id)
    return read_json(p) if p.is_file() else {}


def synthesize_part(project_dir: Path, part_id: str, tts, *, force: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    pdir = Path(project_dir)
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        raise PlanError("请先确认大纲（C0）")
    script = load_script(pdir, part_id)
    if script is None or not script.get("confirmed"):
        raise PlanError(f"{part_id} 的口播稿还没有确认（C1），不能配音")
    cfg = project_tts_config(pdir)
    voices, vwarn = assign_voices(script, cfg)
    audio_dir = pdir / "parts" / part_id / "audio"
    manifest = {} if force else _load_manifest(pdir, part_id)

    durations: Dict[str, float] = {}
    engines: Dict[str, str] = {}
    total = sum(len(b["sentences"]) for b in script["beats"])
    joblog.bound_log(f"{part_id}：共 {total} 句，开始逐句配音（已缓存的句子直接复用）")
    for b in script["beats"]:
        for i, s in enumerate(b["sentences"]):
            sid = f"{b['id']}.s{i + 1:02d}"
            voice = voices[sid]
            rate = rate_for(b.get("pace", "normal"), b["role"])
            tts_text = sanitize_tts_text(s.get("tts") or s["text"])
            key = _sentence_key(voice, rate, tts_text, tts.name)
            entry = manifest.get(sid)
            if entry and entry.get("key") == key and (audio_dir / entry["file"]).is_file():
                durations[sid] = float(entry["duration"])
                engines[sid] = entry["engine"]
                continue
            cancel.check()
            out = audio_dir / f"{sid}.wav"
            dur = float(tts.synth(tts_text, out, voice, rate))
            engine = getattr(tts, "last_engine", None) or tts.name
            joblog.bound_log(f"{sid}：已合成（{voice}，{dur:.1f} 秒，{engine}）")
            manifest[sid] = {"key": key, "file": out.name, "duration": round(dur, 3), "voice": voice,
                             "rate": rate, "engine": engine, "text": tts_text}
            write_json_atomic(_manifest_path(pdir, part_id), manifest)  # 每句落盘，停止后可续跑
            durations[sid] = dur
            engines[sid] = engine

    joblog.bound_log(f"{part_id}：配音完成，共 {len(durations)} 句", level="ok")
    timeline = build_timeline(script, durations, cfg, voices, engines)
    timeline["warnings"] = vwarn + (["本章使用了模拟配音（stub，静音），不能作为正式成片"]
                                    if "stub" in set(engines.values()) else [])
    timeline["generated_at"] = now_iso(now)
    write_json_atomic(pdir / "parts" / part_id / "timeline.json", timeline)
    append_progress(pdir, f"配音 {part_id}：{len(durations)} 句，总长 {timeline['total_sec']} 秒", now)
    return timeline


def synthesize_all(project_dir: Path, tts, *, force: bool = False, now: Optional[datetime] = None) -> list:
    spine = load_spine(Path(project_dir))
    if spine is None:
        raise PlanError("还没有大纲")
    done = []
    for p in spine["parts"]:
        cancel.check()
        synthesize_part(project_dir, p["id"], tts, force=force, now=now)
        done.append(p["id"])
    return done


def load_timeline(project_dir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = Path(project_dir) / "parts" / part_id / "timeline.json"
    return read_json(p) if p.is_file() else None


def preview(project_dir: Path, text: str, voice: str, rate: str, tts) -> Path:
    """试听一段文字。写入 preview/，不改变项目的音频与时间轴。"""
    clean = sanitize_tts_text(text)
    if not clean:
        raise PlanError("试听文字为空")
    key = _sentence_key(voice, rate, clean, tts.name)
    out = Path(project_dir) / "preview" / f"{key}.wav"
    if not out.is_file():
        cancel.check()
        tts.synth(clean, out, voice, rate)
    return out


def rebuild_timeline(project_dir: Path, part_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """只按当前时间设置重建本章时间轴（R19）：不调用配音、不改动句子音频与口播稿。

    句长取自已缓存的配音清单（audio/manifest.json）；缺少任一句的配音时拒绝执行。
    时间设置（章封面停留、章首静音、句间停顿、章尾停顿）只影响时间轴，所以这一步足够。
    """
    pdir = Path(project_dir)
    spine = load_spine(pdir)
    if spine is None or not spine.get("confirmed"):
        raise PlanError("请先确认大纲（C0）")
    script = load_script(pdir, part_id)
    if script is None or not script.get("confirmed"):
        raise PlanError(f"{part_id} 的口播稿还没有确认（C1），不能重建时间轴")
    manifest = _load_manifest(pdir, part_id)
    durations: Dict[str, float] = {}
    voices: Dict[str, str] = {}
    engines: Dict[str, str] = {}
    missing: list = []
    for b in script["beats"]:
        for i, _s in enumerate(b["sentences"]):
            sid = f"{b['id']}.s{i + 1:02d}"
            entry = manifest.get(sid)
            if not entry or not (pdir / "parts" / part_id / "audio" / entry["file"]).is_file():
                missing.append(sid)
                continue
            durations[sid] = float(entry["duration"])
            voices[sid] = entry["voice"]
            engines[sid] = entry["engine"]
    if missing:
        raise PlanError(f"{part_id} 还有 {len(missing)} 句没有配音（如 {missing[0]}），请先配音")
    cfg = project_tts_config(pdir)
    timeline = build_timeline(script, durations, cfg, voices, engines)
    timeline["warnings"] = (["本章使用了模拟配音（stub，静音），不能作为正式成片"]
                            if "stub" in set(engines.values()) else [])
    timeline["generated_at"] = now_iso(now)
    write_json_atomic(pdir / "parts" / part_id / "timeline.json", timeline)
    joblog.bound_log(f"{part_id}：时间轴已按当前时间设置重建（总长 {timeline['total_sec']} 秒，章封面 "
                     f"{timeline['lead_in_sec']} 秒），未重新配音", level="ok")
    append_progress(pdir, f"重建时间轴 {part_id}：总长 {timeline['total_sec']} 秒", now)
    return timeline
