"""质检（B3）：只报告，不修改任何卡片。

两层：
  * structural：确定性检查（不调用模型），如角色重名、关系指向不存在的角色、规则缺代价、问题未解决等；
  * llm：可选，让模型按矛盾/含糊/说教等维度审阅卡片，输出结构化发现。

结果保存在 `_studio/checks/<id>.json`。发现项只是建议，是否修改由作者决定（之后仍走编辑或提议）。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, read_json

CHECKS_DIR = "_studio/checks"
SEVERITIES = ("warn", "info")
_REL = re.compile(r"^\s*([A-Za-z_][\w]*)\s*[:：]")
Generator = Callable[[str], str]


def structural(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    cards = C.all_modules(ws)
    out: List[Dict[str, Any]] = []

    def add(sev: str, where: str, issue: str, suggestion: str = "") -> None:
        out.append({"severity": sev, "where": where, "issue": issue, "suggestion": suggestion, "source": "structural"})

    if not cards["premise"].strip():
        add("warn", "premise", "还没有核心命题", "先用一两句话写下故事的核心，哪怕只是草稿")
    cast = cards["cast"]
    names: Dict[str, List[str]] = {}
    for cid, card in cast.items():
        names.setdefault(card.get("name", "").strip(), []).append(cid)
        if not card.get("role", "").strip():
            add("info", f"cast:{cid}", f"角色「{card.get('name', cid)}」没有定位", "写明在故事中的作用（主角、对手、导师……）")
        for rel in card.get("relations", []):
            m = _REL.match(rel)
            if m and m.group(1) not in cast:
                add("warn", f"cast:{cid}", f"关系「{rel}」指向不存在的角色 {m.group(1)}",
                    "改成已有角色的 id，或删除这条关系")
    for name, ids in names.items():
        if name and len(ids) > 1:
            add("warn", "cast", f"角色名「{name}」重复（{', '.join(ids)}）", "重名会让读者和写作流水线混淆，请改名")
    for key in ("rules",):
        for i, rule in enumerate(cards["world"][key]):
            if not str(rule.get("cost", "")).strip():
                add("info", f"world:{key}[{i}]", f"规则「{rule.get('text', '')[:20]}」没有写代价",
                    "规则的代价越具体，之后的冲突越好写")
    for i, act in enumerate(cards["plot"]["acts"]):
        if not act.get("beats"):
            add("info", f"plot:acts[{i}]", f"「{act.get('title', '')}」还没有转折点", "列出 2–3 个关键转折")
    open_n = sum(1 for q in cards["open_questions"] if not q["closed"])
    if open_n:
        add("info", "open_questions", f"还有 {open_n} 个开放问题未关闭", "逐个决定或暂时搁置")
    return out


def llm_review(ws: BrainstormWorkspace, generator: Generator) -> List[Dict[str, Any]]:
    cards = C.all_modules(ws)
    snapshot = json.dumps({k: v for k, v in cards.items() if k != "open_questions"}, ensure_ascii=False, indent=1)
    prompt = (
        "你是小说构思的质检员。下面是一份构思卡片。请只指出问题，不要改写卡片，也不要替作者做决定。\n"
        "重点检查：设定之间的矛盾（世界规则与剧情或角色行为不符）、角色动机是否站得住、"
        "说教或概念先行的风险、含糊之处。\n"
        "只输出一个 JSON 数组，不要解释或代码块。每项格式：\n"
        '{"severity": "warn|info", "where": "卡片位置，如 world:rules[0] 或 cast:c1", '
        '"issue": "问题是什么", "suggestion": "一个具体的修正方向"}\n'
        "没有问题就输出 []。最多 8 条。\n\n卡片：\n" + snapshot
    )
    reply = generator(prompt)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (reply or "").strip()).strip()
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise BrainstormError("质检模型没有返回可解析的列表")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise BrainstormError(f"质检结果不是合法 JSON：{exc}") from exc
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not str(item.get("issue", "")).strip():
            continue
        sev = item.get("severity") if item.get("severity") in SEVERITIES else "info"
        out.append({"severity": sev, "where": str(item.get("where", "")), "issue": str(item["issue"]).strip(),
                    "suggestion": str(item.get("suggestion", "")).strip(), "source": "llm"})
    return out[:8]


def run(ws: BrainstormWorkspace, *, use_llm: bool = False, generator: Optional[Generator] = None) -> Dict[str, Any]:
    findings = structural(ws)
    if use_llm:
        if generator is None:
            from novel_writer.brainstorm.spark import default_generator
            generator = default_generator
        findings += llm_review(ws, generator)
    rec = {"id": "k" + uuid.uuid4().hex[:8], "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
           "use_llm": use_llm, "findings": findings,
           "content_hash": C.content_hash(ws)}
    path = ws.root / CHECKS_DIR / f"{rec['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, rec)
    return rec


def history(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    d = ws.root / CHECKS_DIR
    if not d.is_dir():
        return []
    recs = [read_json(p, default=None) for p in d.glob("k*.json")]
    recs = [r for r in recs if isinstance(r, dict)]
    return sorted(recs, key=lambda r: r["created_at"], reverse=True)
