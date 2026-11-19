"""script/restructure.py — 第二期：只重写含结构段落中有问题的口播（段落级），其它段落保持不变。

流程（计划第 5 节）：
  1. 找出需要修订的段落：含结构（流程、表格、公式）且有依赖画面的句子，或流程节点未在口播中提及；
  2. 逐段调用模型，只重写该段的句子（段落 ID、来源、节奏保持不变）；提示词中列出必须提及的节点、
     表格数字与口语表达要求；
  3. 候选稿经校验（V2/V3 与结构检查）通过才采用；三次仍不通过则保留原文并记录原因；
  4. 有任一段被改写的章：标记为未确认（需重新确认），并记录 structure_rev。

不改动已确认章的其它段落，因此后续配音只需重新合成改动的句子，分镜因口播指纹变化而过期（由系统提示）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .. import cancel, joblog
from ..planning.jsonio import parse_json_object
from ..planning.runner import load_spine
from ..project_store import now_iso, read_json, write_json_atomic
from .budget import count_chars
from .lexicon import load_lexicon
from .structure import beat_structure, beat_text, structure_issues, structure_warnings
from .validate import canonicalize_quotes, validate_script
from .writer import PROMPT_VERSION, _script_path, _update_index, _part_source, _load_script, load_blocks, normalize_script

STRUCT_REV = "s3-struct-v1"
MAX_BEAT_ATTEMPTS = 3
BEAT_SYSTEM = ("你是口播稿的修订编辑。只输出一个 JSON 对象，不要解释、前言或 Markdown 围栏。"
               "只重写指定段落的口播句子。不得添加原文没有的事实、数字或英文词；"
               "不得使用“如图所示”“见下图”等依赖画面的说法。")


def _need_revision(beat: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> List[str]:
    return structure_issues(beat, blocks)


def build_beat_prompt(part: Dict[str, Any], beat: Dict[str, Any], source: str, *, neighbors: Dict[str, str],
                      blocks: Sequence[Dict[str, Any]], issues: List[str]) -> str:
    st = beat_structure(beat, blocks)
    nodes = "、".join(st["nodes"]) or "（无）"
    numbers = "、".join(st["numbers"][:20]) or "（无）"
    old = "\n".join(f"  - {s['text']}" for s in beat["sentences"])
    lines = [
        "【段落修订任务】",
        f"PART_ID={part['id']}　BEAT_ID={beat['id']}",
        f"章标题：{part['title']}",
        f"上一段最后一句：{neighbors.get('prev') or '（无）'}",
        f"下一段第一句：{neighbors.get('next') or '（无）'}",
        "",
        "本段原文结构：" + "、".join(st["kinds"]),
        f"必须在口播中提及的流程节点（按顺序讲清先后）：{nodes}",
        f"表格中的关键数字（口播要讲清楚，不得新增）：{numbers}",
        "",
        "口语要求：不写“如图所示”“见下图”“上面的流程图”等依赖画面的说法；用“先是……，接着……，最后……”"
        "描述顺序；一句一个意思，单句不超过 30 字。",
        "",
        "本段原稿（需要修订）：",
        old,
        "",
        "原稿的问题：" + "；".join(issues),
        "",
        "请重写本段口播，输出 JSON：",
        json.dumps({"sentences": [{"text": "一句口播", "quote": False, "emphasis": ["句中要强调的词"]}]},
                   ensure_ascii=False, indent=2),
        "",
        "【本章原文】",
        source,
    ]
    return "\n".join(lines)


def _candidate_beat(obj: Any, old: Dict[str, Any], part: Dict[str, Any], *, budget_chars: int,
                    budget_minutes: float, lexicon: Dict[str, str], persona: str) -> Optional[Dict[str, Any]]:
    if not isinstance(obj, dict) or not isinstance(obj.get("sentences"), list):
        return None
    shell = {"beats": [{"role": old["role"], "source_refs": old["source_refs"], "sentences": obj["sentences"],
                        "pace": old.get("pace", "normal"), "pause_after_ms": old.get("pause_after_ms", 300)}]}
    norm = normalize_script(shell, part, persona=persona, budget_chars=budget_chars,
                            budget_minutes=budget_minutes, lexicon=lexicon)
    beat = norm["beats"][0]
    beat["id"] = old["id"]
    return beat


def revise_beat(part: Dict[str, Any], beat: Dict[str, Any], script: Dict[str, Any], *, source: str,
                section_ids: List[str], blocks: Sequence[Dict[str, Any]], llm, neighbors: Dict[str, str],
                lexicon: Dict[str, str], issues: List[str]) -> Dict[str, Any]:
    """返回 {"beat": 新段落 或 None, "reasons": [...]}。候选稿不合格时 beat 为 None。"""
    reasons: List[str] = []
    current_issues = list(issues)
    budget_minutes = float(script.get("budget_minutes") or 0)
    for attempt in range(MAX_BEAT_ATTEMPTS):
        cancel.check()
        prompt = build_beat_prompt(part, beat, source, neighbors=neighbors, blocks=blocks, issues=current_issues)
        joblog.bound_log(f"{beat['id']}：段落修订第 {attempt + 1}/{MAX_BEAT_ATTEMPTS} 次（提示词 {len(prompt)} 字）")
        t0 = time.monotonic()
        raw = llm.ask(prompt, system=BEAT_SYSTEM)
        cancel.check()
        joblog.bound_log(f"{beat['id']}：修订返回 {len(raw)} 字，用时 {time.monotonic() - t0:.1f} 秒")
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            current_issues = [f"JSON 解析失败：{exc}"]
            reasons.append(current_issues[0])
            joblog.bound_log(f"{beat['id']}：修订输出不是有效 JSON，将重试", level="warn")
            continue
        cand = _candidate_beat(obj, beat, part, budget_chars=int(script.get("budget_chars") or 0),
                               budget_minutes=budget_minutes, lexicon=lexicon, persona=script.get("persona", "host"))
        if cand is None:
            current_issues = ["缺少 sentences 数组"]
            reasons.append(current_issues[0])
            continue
        shell = dict(script, beats=[cand])
        canonicalize_quotes(shell, source)
        verdict = validate_script(shell, source, section_ids, check_pacing=False, blocks=list(blocks))
        left = structure_issues(cand, blocks)
        problems = verdict["errors"] + left
        if problems:
            current_issues = problems[:8]
            reasons.append("；".join(problems[:3]))
            joblog.bound_log(f"{beat['id']}：修订稿未通过（{len(problems)} 处），将重试：" + "；".join(problems[:3]),
                             level="warn")
            continue
        for w in structure_warnings(cand, blocks):
            joblog.bound_log(f"  ! {w}", level="warn")
        joblog.bound_log(f"{beat['id']}：段落修订通过（{len(cand['sentences'])} 句）", level="ok")
        return {"beat": cand, "reasons": reasons}
    joblog.bound_log(f"{beat['id']}：段落修订 {MAX_BEAT_ATTEMPTS} 次仍不合格，保留原稿", level="error")
    return {"beat": None, "reasons": reasons}


def refresh_part(project_dir: Path, part_id: str, llm, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """修订一章的含结构段落。没有口播稿或没有需要修订的段落时不改动任何内容。"""
    project_dir = Path(project_dir)
    spine = load_spine(project_dir)
    if spine is None:
        raise ValueError("还没有大纲")
    part = next(p for p in spine["parts"] if p["id"] == part_id)
    script = _load_script(project_dir, part_id)
    if script is None:
        return {"part": part_id, "action": "skipped", "reason": "还没有口播稿", "rewritten": [], "failed": []}
    blocks = load_blocks(project_dir)
    source = _part_source(project_dir, part["section_ids"])
    lexicon = load_lexicon(project_dir)
    beats = script["beats"]
    structured = [b["id"] for b in beats if beat_structure(b, blocks)["kinds"]]
    targets = {b["id"]: _need_revision(b, blocks) for b in beats}
    targets = {k: v for k, v in targets.items() if v}
    joblog.bound_log(f"{part_id}：含结构段落 {len(structured)} 段，需修订 {len(targets)} 段"
                     + (f"（{'、'.join(targets)}）" if targets else ""))
    if not targets:
        return {"part": part_id, "action": "unchanged", "reason": "没有需要修订的段落",
                "rewritten": [], "failed": [], "structured": len(structured)}

    new_beats = list(beats)
    rewritten: List[str] = []
    failed: List[Dict[str, str]] = []
    for i, b in enumerate(beats):
        if b["id"] not in targets:
            continue
        cancel.check()
        prev_b = beats[i - 1]["sentences"][-1]["text"] if i > 0 and beats[i - 1]["sentences"] else ""
        next_b = beats[i + 1]["sentences"][0]["text"] if i + 1 < len(beats) and beats[i + 1]["sentences"] else ""
        result = revise_beat(part, b, script, source=source, section_ids=part["section_ids"], blocks=blocks,
                             llm=llm, neighbors={"prev": prev_b, "next": next_b}, lexicon=lexicon,
                             issues=targets[b["id"]])
        if result["beat"] is None:
            failed.append({"beat": b["id"], "reason": result["reasons"][-1] if result["reasons"] else "未知"})
            continue
        new_beats[i] = result["beat"]
        rewritten.append(b["id"])

    if not rewritten:
        return {"part": part_id, "action": "failed", "reason": "没有段落修订成功", "rewritten": [],
                "failed": failed, "structured": len(structured)}

    out = dict(script, beats=new_beats, confirmed=False, confirmed_at=None, structure_rev=STRUCT_REV)
    verdict = validate_script(out, source, part["section_ids"], check_pacing=False, blocks=blocks)
    if verdict["errors"]:                       # 逐段已校验，整章复核不应出错；若出错则不写入
        raise ValueError("修订后整章校验不通过：" + "；".join(verdict["errors"][:4]))
    out["total_chars"] = verdict["total_chars"]
    out["warnings"] = verdict["warnings"]
    write_json_atomic(_script_path(project_dir, part_id), out)
    _update_index(project_dir, part_id, out, verdict)
    from ..project_store import append_progress
    append_progress(project_dir, f"修订含结构段落 {part_id}：{len(rewritten)} 段（需重新确认）", now)
    joblog.bound_log(f"{part_id}：已修订 {len(rewritten)} 段（{'、'.join(rewritten)}），本章需重新确认"
                     + (f"；未修订 {len(failed)} 段" if failed else ""), level="ok" if not failed else "warn")
    return {"part": part_id, "action": "revised", "rewritten": rewritten, "failed": failed,
            "structured": len(structured)}


def refresh_structured_beats(project_dir: Path, llm, *, part_ids: Optional[Sequence[str]] = None,
                             now: Optional[datetime] = None) -> Dict[str, Any]:
    """对全部（或指定）章执行段落级修订。单章失败只记录原因，继续下一章。"""
    project_dir = Path(project_dir)
    spine = load_spine(project_dir)
    if spine is None:
        raise ValueError("还没有大纲")
    ids = list(part_ids) if part_ids else [p["id"] for p in spine["parts"]]
    results = []
    for pid in ids:
        cancel.check()
        try:
            results.append(refresh_part(project_dir, pid, llm, now=now))
        except cancel.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — 单章异常不影响其它章
            joblog.bound_log(f"{pid}：修订异常：{exc}", level="error")
            results.append({"part": pid, "action": "failed", "reason": str(exc), "rewritten": [], "failed": []})
    revised = sum(len(r["rewritten"]) for r in results)
    joblog.bound_log(f"结构段落修订结束：改写 {revised} 段，涉及 {sum(1 for r in results if r['action'] == 'revised')} 章")
    return {"results": results, "rewritten_beats": revised}


__all__ = ["refresh_structured_beats", "refresh_part", "revise_beat", "build_beat_prompt", "STRUCT_REV"]
