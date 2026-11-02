"""steps/chapter_script.py — C-1 剧本转换、C-2 剧本校验。

C-2 的机械校验（不依赖模型，结果可复现）：
  * 对白台词必须能在原文中找到（去空白后子串匹配）→ 防止改写/臆造对白；
  * 场次号连续、场次标题存在；
  * 旁白过长（>200 字）给出提示；
  * 说话人不在角色库中给出提示（由 C-5 补建处理，不算失败）。

C-1 的模型输出是纯文本剧本。即便模型仍把它包进 JSON（如 {"script": "..."}）或代码块，
_clean_script 也会自动解包，并在日志中提示，避免因包装格式而整章失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .. import joblog
from .. import llm_trace
from .. import prompts
from ..chapter_io import chapter_dir, chapter_row, norm, source_text, sha12, mark_stage
from ..project_store import auto_fix_retries, read_json, write_yaml_atomic
from ..providers.text import TextLLM
from .location_spots import scene_places, spots_enabled

SCENE_RE = re.compile(r"^###\s*场次\s*(\d+)\s*[｜|](.*)$")
LINE_RE = re.compile(r"^([^：:\n]{1,12})[：:](.+)$")
NARRATION_TAG = "旁白"


class ScriptError(RuntimeError):
    pass


def parse_script(text: str) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        m = SCENE_RE.match(line)
        if m:
            scenes.append({"no": int(m.group(1)), "header": line, "lines": [], "lineno": lineno})
            continue
        if not scenes:
            raise ScriptError(f"第 {lineno} 行出现在任何场次标题之前：{line[:30]}"
                              "（剧本必须以“### 场次 N｜…”开头；模型输出可能带了 JSON/代码块/解释文字，"
                              "请在“模型调用记录”中查看原始输出）")
        lm = LINE_RE.match(line)
        if not lm:
            raise ScriptError(f"第 {lineno} 行格式错误（应为“旁白：”或“角色名：”）：{line[:30]}")
        speaker, body = lm.group(1).strip(), lm.group(2).strip()
        if speaker == NARRATION_TAG:
            scenes[-1]["lines"].append({"type": "narration", "speaker": None, "text": body, "lineno": lineno})
        else:
            scenes[-1]["lines"].append({"type": "dialogue", "speaker": speaker, "text": body, "lineno": lineno})
    return scenes


SCRIPT_VERSION = "v2-condense"
OVER_RATIO = 1.6   # 超过目标字数的倍数 → 失败
UNDER_RATIO = 0.3  # 低于目标字数的比例 → 提示


def spoken_chars(script_text: str) -> int:
    total = 0
    for line in script_text.splitlines():
        if line.strip() and not SCENE_RE.match(line.strip()):
            lm = LINE_RE.match(line.strip())
            if lm:
                total += len(re.sub(r"\s", "", lm.group(2)))
    return total


def check_script(script_text: str, source: str, known_names: List[str], target_chars: int = 0, *,
                 area_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """area_names 不为 None 表示地点细化已开启：场次标题只写了区域名时给警告（不判错误）。"""
    issues: List[Dict[str, str]] = []
    try:
        scenes = parse_script(script_text)
    except ScriptError as exc:
        return {"status": "fail", "issues": [{"level": "error", "message": str(exc)}], "scenes": 0}
    if not scenes:
        issues.append({"level": "error", "message": "剧本没有任何场次"})
    for i, sc in enumerate(scenes, start=1):
        if sc["no"] != i:
            issues.append({"level": "error", "message": f"场次号不连续：期望 {i}，实际 {sc['no']}"})
        if not sc["lines"]:
            issues.append({"level": "error", "message": f"场次 {sc['no']} 没有内容"})
    src_norm = norm(source)
    unknown: Dict[str, List[int]] = {}   # 库中没有的说话人 → 出现的行号（每个说话人只提示一次）
    for sc in scenes:
        for ln in sc["lines"]:
            if ln["type"] == "dialogue":
                if not ln["text"]:
                    issues.append({"level": "error", "message": f"第 {ln['lineno']} 行对白为空"})
                elif norm(ln["text"]) not in src_norm:
                    issues.append({"level": "error",
                                   "message": f"第 {ln['lineno']} 行对白未在原文中找到（疑似改写或臆造）：{ln['text'][:30]}"})
                if ln["speaker"] not in known_names:
                    unknown.setdefault(ln["speaker"], []).append(ln["lineno"])
            elif len(ln["text"]) > 200:
                issues.append({"level": "warning", "message": f"第 {ln['lineno']} 行旁白超过 200 字，建议拆分"})
    for name, linenos in unknown.items():
        issues.append({"level": "warning", "message": (
            f"说话人“{name}”不在角色库中（{len(linenos)} 处，首见第 {linenos[0]} 行）。无需处理剧本：镜头规划（C-3）会先尝试把它"
            "归入已有角色，归不进去的在 C-5 自动起草；是否需要补建以本页“补建角色”区的说明为准")})
    if area_names is not None:
        places = scene_places(script_text)
        for sc in scenes:
            info = places.get(sc["no"]) or {}
            if info.get("specific") and info["specific"] == info.get("area") and info["specific"] in area_names:
                issues.append({"level": "warning", "message": (
                    f"场次 {sc['no']} 的地点只写了区域「{info['specific']}」，没有具体场所。若这场戏发生在某个具体场所"
                    "（办公室、街道、卧室…），请把标题写成「区域·场所·时间」；只有纯粹的区域全景才保留区域名")})
    if target_chars:
        spoken = spoken_chars(script_text)
        if spoken > target_chars * OVER_RATIO:
            issues.append({"level": "error",
                           "message": f"口播总字数 {spoken} 超过目标 {target_chars} 字的 {OVER_RATIO} 倍，需要压缩"})
        elif spoken < target_chars * UNDER_RATIO:
            issues.append({"level": "warning", "message": f"口播总字数 {spoken} 明显少于目标 {target_chars} 字"})
    status = "fail" if any(i["level"] == "error" for i in issues) else "pass"
    return {"status": status, "issues": issues, "scenes": len(scenes)}


def unknown_speakers(script_text: str, known_names: List[str]) -> List[Dict[str, Any]]:
    """剧本中不在角色库里的说话人及出现次数（按首次出现顺序）。剧本无法解析时返回空列表。"""
    try:
        scenes = parse_script(script_text)
    except ScriptError:
        return []
    counts: Dict[str, int] = {}
    for sc in scenes:
        for ln in sc["lines"]:
            if ln["type"] == "dialogue" and ln["speaker"] not in known_names:
                counts[ln["speaker"]] = counts.get(ln["speaker"], 0) + 1
    return [{"name": n, "lines": c} for n, c in counts.items()]


def speaker_resolution(script_text: str, shots_doc: Optional[Dict[str, Any]],
                       characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """剧本中每个说话人最终的处理结果（按首次出现顺序）。status：
      library     名字就在角色库中；
      auto_added  已补建的角色（需要确认）；
      mapped      镜头规划把它归入了库中已有角色（target 为角色名），无需补建；
      pending     镜头规划后仍没有对应角色，C-5 会自动起草；
      unplanned   还没有镜头规划，结果未知；
      unknown     有镜头规划但无法把台词对应到镜头（例如台词被大幅改动）。
    台词与镜头的对应按文本包含关系匹配（很短的台词要求完全相等），仅用于界面说明。"""
    from ..chapter_io import norm
    try:
        scenes = parse_script(script_text)
    except ScriptError:
        return []
    by_id = {c["id"]: c for c in characters}
    by_name = {n: c for c in characters for n in c.get("names", [])}
    blocks = [b for sh in (shots_doc or {}).get("shots", []) for b in sh["blocks"] if b["type"] == "dialogue"]
    order: Dict[str, Dict[str, Any]] = {}
    for sc in scenes:
        for ln in sc["lines"]:
            if ln["type"] != "dialogue":
                continue
            r = order.setdefault(ln["speaker"], {"lines": 0, "ids": [], "pending": False})
            r["lines"] += 1
            t = norm(ln["text"])
            for b in blocks:
                bt = norm(b["text"])
                if not bt or bt not in t or (len(bt) < 6 and bt != t):
                    continue
                if b.get("speaker_id"):
                    if b["speaker_id"] not in r["ids"]:
                        r["ids"].append(b["speaker_id"])
                else:
                    r["pending"] = True
    out: List[Dict[str, Any]] = []
    for name, r in order.items():
        row = {"name": name, "lines": r["lines"], "status": "unknown", "target": None}
        if name in by_name:
            c = by_name[name]
            row.update(status="auto_added" if c.get("source") == "auto_added" else "library",
                       target=c["names"][0])
        elif not shots_doc or not shots_doc.get("shots"):
            row["status"] = "unplanned"
        elif r["ids"]:
            targets = [by_id[i] for i in r["ids"] if i in by_id]
            row.update(status="mapped", target="、".join(c["names"][0] for c in targets))
        elif r["pending"]:
            row["status"] = "pending"
        out.append(row)
    return out


def target_chars_for(project_dir: Path) -> int:
    """单章口播目标字数。target_chapter_duration_sec 为 0（或负数）表示不限制时长：返回 0，
    剧本生成不设字数预算，校验也不做字数检查，按原文实际情况生成。未设置时默认 90 秒。"""
    from ..providers.tts import CHARS_PER_SEC
    raw = read_json(project_dir / "project.json").get("target_chapter_duration_sec")
    sec = 90 if raw is None else int(raw)
    if sec <= 0:
        return 0
    return int(round(sec * CHARS_PER_SEC))


def _area_names(project_dir: Path) -> List[str]:
    """库中区域级地点的名称与别名（场次标题只写到这一级时需要提醒）。"""
    try:
        locs = read_json(project_dir / "global" / "locations.json").get("locations", [])
    except (OSError, ValueError):
        return []
    out: List[str] = []
    for l in locs:
        if (l.get("level") or "area") != "area":
            continue
        out += [str(n).strip() for n in [l.get("name")] + list(l.get("aliases") or []) if n and str(n).strip()]
    return out


def run_script(project_dir: Path, no: int, text_llm: TextLLM, *, force: bool = False) -> Dict[str, Any]:
    """C-1 + C-2。缓存键 = 原文 + 目标字数 + 版本；未变且已通过则跳过。

    校验失败时，把问题清单与上一版剧本反馈给模型自动修正，最多 script_max_retries 次（默认 2，
    写 0 则关闭、直接失败）。每次修正是一次额外的文本模型调用。"""
    cdir = chapter_dir(project_dir, no)
    cdir.mkdir(parents=True, exist_ok=True)
    source = source_text(project_dir, no)
    target = target_chars_for(project_dir)
    spots = spots_enabled(project_dir)
    area_names = _area_names(project_dir) if spots else None
    # 开关关闭时哈希与改动前相同；开关开启时纳入哈希，切换后自动重算剧本
    src_hash = sha12(source, str(target), SCRIPT_VERSION, *(["location_spots"] if spots else []))
    script_path = cdir / "script.md"
    review_path = cdir / "script_review.yaml"

    if not force and script_path.is_file() and review_path.is_file():
        review = yaml.safe_load(review_path.read_text(encoding="utf-8")) or {}
        if review.get("source_hash") == src_hash and review.get("status") == "pass":
            mark_stage(project_dir, no, "script", "done")
            return {"skipped": True, "status": "pass"}

    row = chapter_row(project_dir, no)
    chars = read_json(project_dir / "global" / "characters.json")["characters"]
    known = [n for c in chars for n in c.get("names", [])]
    retries = auto_fix_retries(project_dir, "script_max_retries")

    prompt = prompts.script_prompt(no, row["title"], source, target, spots_enabled=spots)
    attempt = 0
    while True:
        label = f"第 {no} 章 C-1 调用文本模型生成剧本" + (f"（第 {attempt} 次修正）" if attempt else f"（输入 {len(source)} 字）")
        with joblog.timed(project_dir, "chapter", label):
            raw = llm_trace.ask_traced(project_dir, text_llm, prompt, system=prompts.SYSTEM_SCRIPT,
                                       scope="chapter", label=label, step="C-1", chapter=no)
        script_text = _clean_script(raw, log=lambda m: joblog.log(project_dir, "chapter", m, level="warn"))
        script_path.write_text(script_text, encoding="utf-8")
        result = check_script(script_text, source, known, target_chars=target, area_names=area_names)
        if result["status"] == "pass" or attempt >= retries:
            break
        attempt += 1
        joblog.log(project_dir, "chapter",
                   f"第 {no} 章 C-2 校验未通过，自动把问题反馈给模型修正（第 {attempt}/{retries} 次）：\n"
                   + "\n".join(f"- {i['message']}" for i in result["issues"] if i["level"] == "error"), level="warn")
        prompt = prompts.script_fix_prompt(no, row["title"], source, target, script_text, result["issues"],
                                           spots_enabled=spots)

    result.update(source_hash=src_hash, script_hash=sha12(script_text), target_chars=target,
                  spoken_chars=spoken_chars(script_text), attempts=attempt + 1)
    write_yaml_atomic(review_path, result)
    errors = [i["message"] for i in result["issues"] if i["level"] == "error"]
    mark_stage(project_dir, no, "script", "done" if result["status"] == "pass" else "failed",
               None if result["status"] == "pass" else "剧本校验未通过：" + (errors[0] if errors else ""))
    return {"skipped": False, "status": result["status"], "issues": result["issues"]}


_JSON_TEXT_KEYS = ("script", "剧本", "text", "content")


def _unwrap_json(text: str) -> Optional[str]:
    """模型把剧本包进 JSON 时取出其中的剧本文本；不是 JSON 或取不出则返回 None。"""
    if not text.startswith(("{", "[", '"')):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list) and obj and all(isinstance(x, str) for x in obj):
        return "\n".join(obj)
    if isinstance(obj, dict):
        for k in _JSON_TEXT_KEYS:
            if isinstance(obj.get(k), str):
                return obj[k]
        strs = [v for v in obj.values() if isinstance(v, str)]
        if len(strs) == 1:
            return strs[0]
    return None


def _clean_script(raw: str, log: Optional[Callable[[str], None]] = None) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```[\w-]*|```$", "", text, flags=re.M).strip()
    inner = _unwrap_json(text)
    if inner is not None:
        if log:
            log("模型把剧本包进了 JSON，已自动解包（剧本调用应只返回纯文本）")
        text = re.sub(r"^```[\w-]*|```$", "", inner.strip(), flags=re.M).strip()
    if not text:
        raise ScriptError("LLM 返回的剧本为空")
    return text + "\n"
