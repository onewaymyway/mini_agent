"""steps/chapter_script.py — C-1 剧本转换、C-2 剧本校验。

C-2 的机械校验（不依赖模型，结果可复现）：
  * 对白台词必须能在原文中找到（去空白后子串匹配）→ 防止改写/臆造对白；
  * 场次号连续、场次标题存在；
  * 旁白过长（>200 字）给出提示；
  * 说话人不在角色库中给出提示（由 C-5 补建处理，不算失败）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .. import prompts
from ..chapter_io import chapter_dir, chapter_row, norm, source_text, sha12, mark_stage
from ..project_store import read_json, write_yaml_atomic
from ..providers.text import TextLLM

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
            raise ScriptError(f"第 {lineno} 行出现在任何场次标题之前：{line[:30]}")
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


def check_script(script_text: str, source: str, known_names: List[str], target_chars: int = 0) -> Dict[str, Any]:
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
    for sc in scenes:
        for ln in sc["lines"]:
            if ln["type"] == "dialogue":
                if not ln["text"]:
                    issues.append({"level": "error", "message": f"第 {ln['lineno']} 行对白为空"})
                elif norm(ln["text"]) not in src_norm:
                    issues.append({"level": "error",
                                   "message": f"第 {ln['lineno']} 行对白未在原文中找到（疑似改写或臆造）：{ln['text'][:30]}"})
                if ln["speaker"] not in known_names:
                    issues.append({"level": "warning",
                                   "message": f"说话人“{ln['speaker']}”不在角色库中，将在 C-5 补建"})
            elif len(ln["text"]) > 200:
                issues.append({"level": "warning", "message": f"第 {ln['lineno']} 行旁白超过 200 字，建议拆分"})
    if target_chars:
        spoken = spoken_chars(script_text)
        if spoken > target_chars * OVER_RATIO:
            issues.append({"level": "error",
                           "message": f"口播总字数 {spoken} 超过目标 {target_chars} 字的 {OVER_RATIO} 倍，需要压缩"})
        elif spoken < target_chars * UNDER_RATIO:
            issues.append({"level": "warning", "message": f"口播总字数 {spoken} 明显少于目标 {target_chars} 字"})
    status = "fail" if any(i["level"] == "error" for i in issues) else "pass"
    return {"status": status, "issues": issues, "scenes": len(scenes)}


def target_chars_for(project_dir: Path) -> int:
    from ..providers.tts import CHARS_PER_SEC
    sec = int(read_json(project_dir / "project.json").get("target_chapter_duration_sec") or 90)
    return int(round(sec * CHARS_PER_SEC))


def run_script(project_dir: Path, no: int, text_llm: TextLLM, *, force: bool = False) -> Dict[str, Any]:
    """C-1 + C-2。缓存键 = 原文 + 目标字数 + 版本；未变且已通过则跳过。"""
    cdir = chapter_dir(project_dir, no)
    cdir.mkdir(parents=True, exist_ok=True)
    source = source_text(project_dir, no)
    target = target_chars_for(project_dir)
    src_hash = sha12(source, str(target), SCRIPT_VERSION)
    script_path = cdir / "script.md"
    review_path = cdir / "script_review.yaml"

    if not force and script_path.is_file() and review_path.is_file():
        review = yaml.safe_load(review_path.read_text(encoding="utf-8")) or {}
        if review.get("source_hash") == src_hash and review.get("status") == "pass":
            mark_stage(project_dir, no, "script", "done")
            return {"skipped": True, "status": "pass"}

    row = chapter_row(project_dir, no)
    raw = text_llm.ask(prompts.script_prompt(no, row["title"], source, target), system=prompts.SYSTEM_VISUAL)
    script_text = _clean_script(raw)
    script_path.write_text(script_text, encoding="utf-8")

    chars = read_json(project_dir / "global" / "characters.json")["characters"]
    known = [n for c in chars for n in c.get("names", [])]
    result = check_script(script_text, source, known, target_chars=target)
    result.update(source_hash=src_hash, script_hash=sha12(script_text), target_chars=target,
                  spoken_chars=spoken_chars(script_text))
    write_yaml_atomic(review_path, result)
    mark_stage(project_dir, no, "script", "done" if result["status"] == "pass" else "failed",
               None if result["status"] == "pass" else "剧本校验未通过")
    return {"skipped": False, "status": result["status"], "issues": result["issues"]}


def _clean_script(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-z]*|```$", "", text, flags=re.M).strip()
    if not text:
        raise ScriptError("LLM 返回的剧本为空")
    return text + "\n"
