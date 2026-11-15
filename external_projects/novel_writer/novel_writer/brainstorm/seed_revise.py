"""一句话起稿 S2：对构思稿的反馈修订（整稿 / 分部分），生成新版本。

  * 整稿反馈（sections 为空）：六个部分都允许改动。
  * 分部分反馈：只有选中的部分允许改动。范围由**程序**守卫，不依赖模型自觉：
    模型输出里范围之外的部分一律丢弃（与原稿不同时给出警告），最终稿 = 原稿 + 仅选中部分的新内容。
  * 模型可附 `_affects`：范围之外、可能需要跟着改的部分。只作提示返回，不会自动修改。
  * 没有产生任何实际改动 → 报错，不写版本。校验失败带错误重试一次；仍失败不写任何版本。
  * 总是写新版本（kind = revise），旧版本保留，可回退。
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Dict, List, Optional

from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm import seed_gen as SG
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

MAX_ATTEMPTS = SG.MAX_ATTEMPTS
FEEDBACK_MAX = 3000

SYSTEM_PROMPT = ("你是小说构思助手。根据作者的反馈修改构思稿。"
                 "只输出一个 JSON 对象，不要调用任何工具，不要写文件，不要输出 JSON 之外的文字。")


def _labels(sections: List[str]) -> str:
    return "、".join(SD.SECTION_LABELS[s] for s in sections)


def check_scope(sections: Optional[List[str]]) -> List[str]:
    """返回规范化的范围（空 = 整稿 = 全部六部分）。"""
    if not sections:
        return list(SD.SECTIONS)
    bad = [s for s in sections if s not in SD.SECTIONS]
    if bad:
        raise BrainstormError("未知部分：" + "、".join(bad))
    return [s for s in SD.SECTIONS if s in set(sections)]


def _draft_for_prompt(draft: Dict[str, Any]) -> str:
    """把角色以列表（带 id）呈现，便于模型保持 id 稳定；其余部分原样。"""
    d = copy.deepcopy(draft)
    d["cast"] = [{"id": cid, **card} for cid, card in draft["cast"].items()]
    return json.dumps(d, ensure_ascii=False, indent=2)


def build_prompt(seed: Dict[str, str], draft: Dict[str, Any], feedback: str, scope: List[str], whole: bool,
                 errors: Optional[List[str]] = None) -> str:
    parts = [f"【作者最初的想法】\n{seed.get('idea', '')}"]
    if seed.get("constraints"):
        parts.append(f"【作者的额外要求（始终遵守）】\n{seed['constraints']}")
    parts.append("【当前构思稿（JSON）】\n" + _draft_for_prompt(draft))
    parts.append(f"【作者的反馈】\n{feedback.strip()}")
    if whole:
        parts.append("【修改范围】整稿：可以修改任何部分，但只改与反馈有关的内容，其余保持原样；"
                     "输出完整的六个部分（storyline、premise、world、cast、plot、open_questions 都要有）。")
    else:
        parts.append(f"【修改范围】只允许修改：{_labels(scope)}（键名：{', '.join(scope)}）。"
                     "只输出这些部分的完整新内容，不要输出其它部分（输出了也会被丢弃）。"
                     "如果这次修改使范围之外的某些部分也需要跟着调整，请在 `_affects` 里列出它们的键名（只作提示，不会自动修改）。")
    parts.append("角色保持原有 id（cast 用列表，每个角色带 id），新增角色可不写 id。只输出 JSON 对象，不要代码围栏之外的文字。")
    if errors:
        parts.append("【上一次输出未通过校验，请修正后重新输出】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def run(ws: BrainstormWorkspace, feedback: str, sections: Optional[List[str]] = None, *,
        base_ver: Optional[int] = None, generator: Optional[SG.Generator] = None,
        log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    feedback = (feedback or "").strip()
    if not feedback:
        raise BrainstormError("请写下你的反馈")
    if len(feedback) > FEEDBACK_MAX:
        raise BrainstormError(f"反馈太长（{len(feedback)} 字，上限 {FEEDBACK_MAX}）")
    whole = not sections
    scope = check_scope(sections)
    base = SD.load(ws, base_ver)
    old, seed = base["draft"], base["seed"]
    if generator is None:
        from novel_writer.brainstorm.spark import helper_generator
        generator = lambda p: helper_generator(p, system=SYSTEM_PROMPT, timeout=600)  # noqa: E731
    SG._emit(log, "info", f"基于 v{base['ver']:03d}，范围：{'整稿' if whole else _labels(scope)}")
    errors: List[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = build_prompt(seed, old, feedback, scope, whole, errors)
        SG._emit(log, "info", f"第 {attempt}/{MAX_ATTEMPTS} 次：提示词 {len(prompt)} 字，开始调用模型")
        raw = generator(prompt)
        SG._emit(log, "info", f"模型返回 {len(str(raw))} 字，解析并校验")
        repaired: List[str] = []
        try:
            out = SG.parse_output(raw, repaired=repaired)
            merged, discarded, affects = _merge(old, out, scope, whole)
            clean = SD.validate_draft(merged)
            changed = SD.changed_sections(old, clean)
            if not changed:
                raise BrainstormError("这次修改没有产生任何实际改动（模型原样返回了原稿）")
        except BrainstormError as exc:
            rel = SG._save_raw(ws, raw)
            SG._emit(log, "error", f"未通过：{exc}\n模型原始输出（已存档 {rel}）：\n{raw}")
            errors = [str(exc)]
            continue
        if repaired:
            rel = SG._save_raw(ws, raw)
            SG._emit(log, "warning", f"输出不是严格合法的 JSON（{repaired[0]}），已用 json_repair 修复；原始输出已存档 {rel}")
        for sec in discarded:
            SG._emit(log, "warning", f"模型改动了范围之外的「{SD.SECTION_LABELS[sec]}」，已丢弃，保持原样")
        reason = f"反馈（{'整稿' if whole else _labels(scope)}）：{feedback[:60]}"
        ver = SD.save_version(ws, clean, kind="revise", reason=reason, seed=seed)
        SG._emit(log, "info", f"已保存为 v{ver:03d}，改动的部分：{_labels(changed)}（草稿层，尚未写入卡片）")
        return {"ver": ver, "base": base["ver"], "changed": changed, "discarded": discarded,
                "affects": affects, "attempts": attempt, "repaired": bool(repaired)}
    raise BrainstormError(f"修订失败：{MAX_ATTEMPTS} 次都未通过（{errors[0]}）；没有写入任何版本，原始输出已存档")


def _merge(old: Dict[str, Any], out: Dict[str, Any], scope: List[str], whole: bool):
    """最终稿 = 原稿 + 范围内的新内容。返回 (合并稿, 被丢弃的范围外部分, 受影响提示)。"""
    unknown = [k for k in out if k not in SD.SECTIONS and k != "_affects"]
    if unknown:
        raise BrainstormError("输出含未知键：" + "、".join(unknown))
    missing = [s for s in scope if s not in out]
    if missing:
        raise BrainstormError("缺少需要修改的部分：" + "、".join(SD.SECTION_LABELS[s] for s in missing))
    merged = copy.deepcopy(old)
    for sec in scope:
        merged[sec] = out[sec]
    discarded: List[str] = []
    if not whole:
        for sec in SD.SECTIONS:
            if sec in scope or sec not in out:
                continue
            try:
                same = SD.normalize_draft({**old, sec: out[sec]}).get(sec) == old[sec]
            except BrainstormError:
                same = False
            if not same:
                discarded.append(sec)
    raw_aff = out.get("_affects")
    affects = [s for s in (raw_aff if isinstance(raw_aff, list) else []) if s in SD.SECTIONS and s not in scope]
    return merged, discarded, list(dict.fromkeys(affects))


def start_run(ws: BrainstormWorkspace, feedback: str, sections: Optional[List[str]] = None, *,
              base_ver: Optional[int] = None, generator: Optional[SG.Generator] = None) -> str:
    """输入先同步校验（反馈、范围、基础版本存在），再后台运行。"""
    ws.meta()
    if not (feedback or "").strip():
        raise BrainstormError("请写下你的反馈")
    check_scope(sections)
    SD.load(ws, base_ver)
    return SG.start_task(ws, lambda w, log: run(w, feedback, sections, base_ver=base_ver, generator=generator, log=log),
                         prefix="r")


def diff(ws: BrainstormWorkspace, a: int, b: int) -> Dict[str, Any]:
    """两版之间按部分的差异：{changed: [...], diffs: {部分: unified diff}}。"""
    da, db = SD.load(ws, a)["draft"], SD.load(ws, b)["draft"]
    changed = SD.changed_sections(da, db)
    return {"a": a, "b": b, "changed": changed, "diffs": {s: SD.section_diff(da, db, s) for s in changed}}
