"""整理对话（手动）：把一段对话提取成待确认的提议，并生成 Markdown 纪要。

  * 只有作者点击「整理」才会运行；不会自动运行。
  * 可以只整理某些模块（如只提取角色）。模型输出中超出所选模块的条目会被丢弃。
  * 结果全部进入待确认的提议（带 batch 号与来源轮次），不直接写卡片。
  * 与现有卡片冲突的条目照常列出，并标记 conflict；不自动覆盖。冲突通过对话处理：
    `conflict_prompt` 生成一条带双方内容的消息，发到对话里由 AI 权衡，结论以新提议的形式再确认。
  * 进度检查点 `_studio/distill/state.json` 只在整理成功后推进，失败不会跳过对话。
  * 纪要保存在 `output/纪要/<批次>.md`。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json

STATE_FILE = "_studio/distill/state.json"
MINUTES_DIR = "output/纪要"
DISTILLABLE = ("brief", "premise", "ideas", "open_questions", "world", "cast", "plot", "options")
MODULE_LABEL = {"brief": "题材卡", "premise": "核心命题", "ideas": "灵感", "open_questions": "开放问题",
                "world": "世界观", "cast": "角色", "plot": "剧情骨架", "options": "备选方案"}
Generator = Callable[[str], str]
SYSTEM_PROMPT = ("你是小说构思的整理员。只输出一个 JSON 对象，不要调用任何工具，不要写文件，"
                 "不要使用代码块，不要在 JSON 之外输出任何文字。")


def _brief_keys() -> str:
    from novel_writer.intake import INTAKE_FIELDS
    return "、".join(f"{k}（{label}）" for k, label, _ in INTAKE_FIELDS)


VALUE_SPEC = (
    "每条候选的 value 必须严格按模块给出（一条候选只表达一件事，不要把多项合并成一条文字）：\n"
    f"- brief：op=set，value 是对象，键只能取：{_brief_keys()}，值为文字；例 {{\"protagonist\": \"……\"}}。target 留空。\n"
    "- premise：op=set，value 是一段文字。\n"
    '- ideas：op=add，value={"text": "一条灵感", "tags": ["标签"]}。\n'
    '- open_questions：op=add，value={"text": "一个问题"}；每个问题单独一条候选。\n'
    '- world：op=add，target 为列表名 rules/locations/factions/timeline/taboos，value={"text": "一条设定"}（可再加 name 等字段）；'
    "一个设定一条候选，不要把整个列表塞进一条。\n"
    '- cast：op=add，value={"name": "名字", "role": "", "want": "", "need": "", "flaw": "", "lie": "", "arc": "", "relations": ["文字"]}，'
    "各字段都是文字（relations 是文字列表）；一个角色一条候选。\n"
    '- plot：op=set，value={"acts": [{"title": "幕名", "beats": ["转折点"]}], "twists": ["文字"], "endings": ["文字"]}，只写要替换的键。\n'
    '- options：op=add，value={"name": "备选名", "module": "premise 或 plot", "content": 可选, "notes": "优缺点"}。\n'
    "value 里的字符串必须是合法 JSON 字符串（换行写成 \\n，引号转义）。"
)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _checkpoint(ws: BrainstormWorkspace) -> int:
    data = read_json(ws.root / STATE_FILE, default={}) or {}
    return int(data.get("next", 0)) if isinstance(data, dict) else 0


def _set_checkpoint(ws: BrainstormWorkspace, nxt: int) -> None:
    atomic_write_json(ws.root / STATE_FILE, {"next": nxt, "updated_at": _now()})


def select_range(ws: BrainstormWorkspace, scope: str = "pending", recent_rounds: int = 0) -> Tuple[int, int]:
    """返回 [start, end) 的对话记录下标。scope: pending（上次之后的全部）或 recent（最近若干轮）。"""
    from novel_writer.brainstorm import chat as CH

    total = len(CH.history(ws))
    if scope == "recent":
        if recent_rounds < 1:
            raise BrainstormError("最近轮数至少为 1")
        return max(0, total - recent_rounds * 2), total
    if scope != "pending":
        raise BrainstormError(f"未知的范围：{scope}")
    return min(_checkpoint(ws), total), total


def _transcript_text(records: List[Dict[str, Any]], start: int) -> str:
    lines = []
    for offset, rec in enumerate(records):
        if rec.get("error"):
            continue
        who = "作者" if rec.get("role") == "user" else "助手"
        lines.append(f"[{start + offset}] {who}：{rec.get('text', '')}")
    return "\n".join(lines)


def build_prompt(ws: BrainstormWorkspace, records: List[Dict[str, Any]], start: int, modules: List[str]) -> str:
    cards = C.all_modules(ws)
    summary = json.dumps({m: cards[m] for m in modules if m in cards and m != "options"}, ensure_ascii=False, indent=1)
    labels = "、".join(MODULE_LABEL[m] for m in modules)
    ids = ", ".join(m for m in modules)
    return (
        "你是小说构思的整理员。下面是一段构思对话。请从中提取作者已经确定或明确倾向的内容，整理成待确认的修改建议，"
        "并写一份 Markdown 纪要。\n"
        f"只提取以下模块：{labels}（模块名：{ids}）。其它模块一律不输出。\n"
        "不要提取只是被提到、未被作者认可的想法；被否定的方向写进纪要的「被否定的方向」一节，不要作为修改建议。\n"
        "与当前卡片冲突的内容照常输出，并在 reason 里说明它与现有内容的不同。不要因为冲突而省略。\n\n"
        "只输出一个 JSON 对象，不要解释或代码块，格式：\n"
        '{"candidates": [{"module": "模块名", "op": "add 或 set", "target": "set 时的目标 id（world 时为列表名）", '
        '"value": {...或文字}, "reason": "依据与说明", "source_turns": [对话编号]}], "minutes": "Markdown 纪要"}\n'
        "纪要包含四节：讨论要点、已形成的决定、被否定的方向、仍待定的问题。每条引用对话编号，如 [12]。\n\n"
        + VALUE_SPEC + "\n\n"
        f"【当前卡片（参考，用于判断冲突）】\n{summary}\n\n"
        f"【对话（编号 {start} 起）】\n{_transcript_text(records, start)}"
    )


def parse_output(reply: str) -> Dict[str, Any]:
    text = (reply or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        raise BrainstormError("整理结果没有 JSON 对象")
    try:
        data = json.loads(text[first:last + 1])
    except json.JSONDecodeError as exc:
        raise BrainstormError(f"整理结果不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise BrainstormError("整理结果格式不对")
    cands = data.get("candidates", [])
    if not isinstance(cands, list):
        raise BrainstormError("candidates 必须是列表")
    return {"candidates": cands, "minutes": str(data.get("minutes", "")).strip()}


_CAST_TEXT_FIELDS = ("role", "want", "need", "flaw", "lie", "arc")


def _as_text(v: Any) -> str:
    """把模型给的列表 / 对象 / 数字压成一段文字。"""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return "；".join(_as_text(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return "；".join(f"{k}：{_as_text(x)}" for k, x in v.items() if x not in (None, ""))
    return "" if v is None else str(v)


_NUMBERED = re.compile(r"(?<![\d.])(?:\d+[.、)）](?!\d)|[①②③④⑤⑥⑦⑧⑨⑩])\s*")


def normalize_candidate(module: str, op: str, target: Optional[str], value: Any) -> List[Tuple[str, Optional[str], Any]]:
    """把模型常见的格式偏差规整成各模块要求的结构，返回 [(op, target, value)]（可能拆成多条）。

    只做格式规整，不改变内容；无法规整的原样返回，由后面的试应用校验把它挡下来。
    """
    from novel_writer.intake import INTAKE_KEYS

    if module == "brief":
        if isinstance(value, str) and target in INTAKE_KEYS:
            return [(op, None, {target: value})]
        if isinstance(value, dict):
            return [(op, None, {k: _as_text(v) for k, v in value.items() if k in INTAKE_KEYS})]
    elif module == "premise":
        if isinstance(value, dict):
            value = value.get("text") or value.get("premise") or _as_text(value)
        return [(op, target, _as_text(value))]
    elif module in ("ideas", "open_questions") and op == "add":
        if isinstance(value, str):
            parts = [x.strip() for x in _NUMBERED.split(value) if x.strip()] if module == "open_questions" else [value.strip()]
            return [(op, target, {"text": x}) for x in (parts or [value.strip()])]
        if isinstance(value, list):
            return [(op, target, {"text": _as_text(x)}) for x in value if _as_text(x)]
    elif module == "world":
        if isinstance(value, list):
            return [(op, target, _norm_world_item(target, x)) for x in value if _as_text(x)]
        return [(op, target, _norm_world_item(target, value))]
    elif module == "cast" and isinstance(value, dict):
        card = dict(value)
        for f in _CAST_TEXT_FIELDS:
            if f in card and not isinstance(card[f], str):
                card[f] = _as_text(card[f])
        rel = card.get("relations", [])
        if not isinstance(rel, list):
            rel = [rel]
        card["relations"] = [_as_text(r) for r in rel if _as_text(r)]
        return [(op, target, card)]
    return [(op, target, value)]


def _norm_world_item(target: Optional[str], value: Any) -> Any:
    if target in ("timeline", "taboos"):
        return value if isinstance(value, str) else _as_text(value.get("text") if isinstance(value, dict) and value.get("text") else value)
    if isinstance(value, str):
        return {"text": value.strip()}
    if isinstance(value, dict):
        item = dict(value)
        if not str(item.get("text", "")).strip():
            item["text"] = _as_text({k: v for k, v in item.items() if k != "text"})
        return item
    return {"text": _as_text(value)}


def _dry_run(ws: BrainstormWorkspace, module: str, op: str, target: Optional[str], value: Any) -> None:
    """试应用：在卡片副本上执行并校验，失败抛 BrainstormError。不落盘。"""
    if module == "options":
        return
    new = P.apply_to(C.get_module(ws, module), module, op, target, value)
    C.validate_module(module, new)


def detect_conflict(ws: BrainstormWorkspace, module: str, op: str, target: Optional[str], value: Any) -> str:
    """与当前卡片比较，返回冲突说明（空串表示无冲突）。"""
    current = C.get_module(ws, module) if module != "options" else None
    if module == "premise" and op == "set" and str(current).strip() and str(value).strip() != str(current).strip():
        return f"与现有核心命题不同：现有「{str(current).strip()[:80]}」"
    if module == "brief" and op == "set" and isinstance(value, dict):
        diffs = [k for k, v in value.items() if str(current.get(k, "")).strip() and str(v).strip() != str(current.get(k, "")).strip()]
        if diffs:
            return "与现有题材字段不同：" + "、".join(diffs)
    if module == "cast" and op == "set" and target in (current or {}):
        return f"修改已有角色 {target}（{(current[target] or {}).get('name', '')}）"
    if module == "plot" and op == "set" and isinstance(value, dict):
        keys = [k for k in ("acts", "twists", "endings") if k in value and value[k] != current.get(k)]
        if keys:
            return "替换现有剧情字段：" + "、".join(keys)
    if module == "ideas" and op == "set" and target:
        return f"修改已有灵感 {target}"
    return ""


def _is_duplicate(ws: BrainstormWorkspace, module: str, op: str, value: Any) -> bool:
    """只对「新增灵感 / 新增开放问题」做文字去重；其它模块的新增由冲突检测与作者判断。"""
    if op != "add" or module not in ("ideas", "open_questions"):
        return False
    text = value.get("text") if isinstance(value, dict) else value
    text = str(text or "").strip()
    if not text:
        return False
    existing = C.get_module(ws, module)
    return any(item["text"].strip() == text for item in existing)


def _emit(log: Optional[Callable[[str, str], None]], kind: str, text: str) -> None:
    if log is not None:
        log(kind, text)


def run(ws: BrainstormWorkspace, *, modules: Optional[List[str]] = None, scope: str = "pending",
        recent_rounds: int = 0, generator: Optional[Generator] = None,
        log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    """整理对话。log(kind, text) 逐步接收过程信息（范围、提示词、模型调用、每条候选的处理结果……）。"""
    from novel_writer.brainstorm import chat as CH

    chosen = list(modules) if modules else list(DISTILLABLE)
    bad = [m for m in chosen if m not in DISTILLABLE]
    if bad:
        raise BrainstormError(f"不能整理的模块：{', '.join(bad)}")
    _emit(log, "info", "模块：" + "、".join(MODULE_LABEL[m] for m in chosen))
    start, end = select_range(ws, scope, recent_rounds)
    records = CH.history(ws)[start:end]
    valid = [r for r in records if not r.get("error")]
    _emit(log, "info", f"对话范围：第 {start} 至 {max(start, end - 1)} 条记录，共 {len(records)} 条，其中有效 {len(valid)} 条")
    if not valid:
        raise BrainstormError("没有需要整理的对话")
    if generator is None:
        # 直接调用 LLMHelper（不经过 Agent）：Agent 路径下模型会去调 write_file 等工具，回复里没有 JSON
        from novel_writer.brainstorm.spark import helper_generator
        generator = lambda p: helper_generator(p, system=SYSTEM_PROMPT)  # noqa: E731
    prompt = build_prompt(ws, records, start, chosen)
    _emit(log, "info", f"提示词：{len(prompt)} 字（含当前卡片摘要与对话全文），开始调用模型")
    raw = generator(prompt)
    _emit(log, "info", f"模型返回 {len(str(raw))} 字，解析结构化结果")
    try:
        out = parse_output(raw)
    except BrainstormError:
        # 解析失败时把模型回复的开头写进过程日志，界面上才能看出模型到底返回了什么
        _emit(log, "warning", f"模型回复开头（前 300 字）：{str(raw)[:300]!r}")
        raise
    _emit(log, "info", f"解析得到 {len(out['candidates'])} 条候选，纪要 {len(out['minutes'])} 字")

    batch = "b" + uuid.uuid4().hex[:8]
    created, skipped = [], []
    for i, cand in enumerate(out["candidates"], 1):
        tag = f"候选 #{i}"
        if not isinstance(cand, dict):
            skipped.append("格式不对的条目")
            _emit(log, "warning", f"{tag}：格式不对，跳过")
            continue
        module, op = cand.get("module"), cand.get("op")
        if module not in chosen:
            skipped.append(f"模块不在所选范围内：{module}")
            _emit(log, "warning", f"{tag}：模块 {module} 不在所选范围内，跳过")
            continue
        turns = [t for t in (cand.get("source_turns") or []) if isinstance(t, int) and start <= t < end]
        target = cand.get("target") or None
        try:
            variants = normalize_candidate(module, op, target, cand.get("value"))
        except Exception as exc:  # noqa: BLE001 - 规整失败只跳过这一条
            skipped.append(f"无法规整（{module}）：{exc}")
            _emit(log, "warning", f"{tag}：无法规整（{exc}），跳过")
            continue
        if len(variants) != 1 or variants[0][2] != cand.get("value") or variants[0][1] != target:
            _emit(log, "info", f"{tag}：{MODULE_LABEL[module]} 的 value 格式已规整" + (f"，拆成 {len(variants)} 条" if len(variants) > 1 else ""))
        for n, (op_n, target_n, value) in enumerate(variants, 1):
            sub = tag if len(variants) == 1 else f"{tag}-{n}"
            if _is_duplicate(ws, module, op_n, value):
                skipped.append(f"已存在，跳过：{module}")
                _emit(log, "info", f"{sub}：{MODULE_LABEL[module]} 与已有内容相同，跳过")
                continue
            try:
                _dry_run(ws, module, op_n, target_n, value)
                conflict = detect_conflict(ws, module, op_n, target_n, value)
                created.append(P.create(ws, module, op_n, value, target=target_n, reason=cand.get("reason", ""),
                                        batch=batch, source_turns=turns, conflict=conflict))
                if conflict:
                    _emit(log, "warning", f"{sub}：{MODULE_LABEL[module]}（{op_n}）与现有内容冲突 → 待确认，标为冲突")
                else:
                    _emit(log, "success", f"{sub}：{MODULE_LABEL[module]}（{op_n}）→ 待确认，来源 {turns or '—'}")
            except BrainstormError as exc:
                skipped.append(f"无效条目（{module}）：{exc}")
                _emit(log, "warning", f"{sub}：无效（{exc}），跳过；内容仍在纪要里")

    minutes_path = _write_minutes(ws, batch, start, end, chosen, out["minutes"], created)
    rel = str(minutes_path.relative_to(ws.root)).replace("\\", "/")
    _emit(log, "info", f"纪要已写入：{rel}")
    _set_checkpoint(ws, end)
    _emit(log, "info", f"检查点推进到第 {end} 条")
    conflicts = sum(1 for p in created if p["conflict"])
    _emit(log, "success", f"完成：待确认 {len(created)} 条，其中冲突 {conflicts} 条，跳过 {len(skipped)} 条")
    return {"batch": batch, "range": [start, end], "proposals": created, "skipped": skipped, "minutes": rel}


# ── 后台运行与过程日志 ──

RUNS_DIR = "_studio/distill_runs"


def _run_files(ws: BrainstormWorkspace, run_id: str):
    base = ws.root / RUNS_DIR
    return base / f"{run_id}.json", base / f"{run_id}.jsonl"


def start_run(ws: BrainstormWorkspace, *, modules: Optional[List[str]] = None, scope: str = "pending",
              recent_rounds: int = 0, generator: Optional[Generator] = None) -> str:
    """在后台整理对话，过程日志逐条写入文件。返回运行编号。"""
    import threading

    ws.meta()
    run_id = "d" + uuid.uuid4().hex[:8]
    status_path, log_path = _run_files(ws, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    atomic_write_json(status_path, {"run": run_id, "status": "running", "started_at": _now()})
    threading.Thread(target=_run_job, args=(str(ws.root), run_id, list(modules) if modules else None, scope,
                                             recent_rounds, generator),
                     daemon=True, name=f"distill-{run_id}").start()
    return run_id


def _run_job(root: str, run_id: str, modules, scope, recent_rounds, generator) -> None:
    from novel_writer.brainstorm.runlog import Capture

    ws = BrainstormWorkspace(root)
    status_path, log_path = _run_files(ws, run_id)

    def write(kind: str, text: str) -> None:
        line = {"t": _now(), "kind": kind, "text": text}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def sink(event: Dict[str, str]) -> None:
        write(event.get("kind", "info"), event.get("text", ""))

    try:
        with Capture(sink):                     # 模型调用过程中 agent 的工具调用等也一并记录
            result = run(ws, modules=modules, scope=scope, recent_rounds=recent_rounds,
                         generator=generator, log=write)
        atomic_write_json(status_path, {"run": run_id, "status": "done", "finished_at": _now(), "result": {
            "batch": result["batch"], "range": result["range"], "minutes": result["minutes"],
            "proposals": len(result["proposals"]),
            "conflicts": sum(1 for p in result["proposals"] if p["conflict"]),
            "skipped": len(result["skipped"]),
        }})
    except Exception as exc:  # noqa: BLE001 - 失败要写进日志与状态，界面才能显示
        write("error", f"{type(exc).__name__}：{exc}")
        atomic_write_json(status_path, {"run": run_id, "status": "error", "finished_at": _now(), "error": str(exc)})


def run_state(ws: BrainstormWorkspace, run_id: str) -> Dict[str, Any]:
    status_path, log_path = _run_files(ws, run_id)
    status = read_json(status_path, default=None)
    if not isinstance(status, dict):
        raise BrainstormError(f"找不到整理任务：{run_id}")
    events = []
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {**status, "events": events}


def _write_minutes(ws, batch, start, end, modules, body, created) -> Any:
    now = _dt.datetime.now()
    path = ws.root / MINUTES_DIR / f"纪要_{now.strftime('%Y%m%d_%H%M%S')}_{batch}.md"
    head = ["# 构思纪要", "",
            f"- 生成时间：{now.isoformat(timespec='seconds')}",
            f"- 对话范围：第 {start} 至 {max(start, end - 1)} 条记录",
            f"- 整理模块：{'、'.join(MODULE_LABEL[m] for m in modules)}",
            f"- 待确认的修改：{len(created)} 条（{sum(1 for p in created if p['conflict'])} 条与现有内容冲突）",
            ""]
    atomic_write_text(path, "\n".join(head) + "\n" + (body or "（模型没有给出纪要正文）") + "\n")
    return path


def conflict_prompt(ws: BrainstormWorkspace, pid: str) -> str:
    """生成一条发到对话里的消息：把冲突的双方摆出来，请 AI 权衡并给出结论。"""
    items = [p for p in P.list_items(ws, "pending") if p["pid"] == pid]
    if not items:
        raise BrainstormError("找不到待确认的提议")
    item = items[0]
    if not item.get("conflict"):
        raise BrainstormError("这条提议没有冲突")
    cur = C.get_module(ws, item["module"]) if item["module"] != "options" else None
    if item["module"] == "cast" and item.get("target"):
        cur = C.get_module(ws, "cast").get(item["target"])
    if item["module"] == "ideas" and item.get("target"):
        cur = next((i for i in C.get_module(ws, "ideas") if i["id"] == item["target"]), None)
    return (
        f"关于整理出的一条冲突，请帮我权衡：\n"
        f"- 现有内容（{item['module']}）：{json.dumps(cur, ensure_ascii=False)}\n"
        f"- 整理出的新内容：{json.dumps(item['value'], ensure_ascii=False)}\n"
        f"- 冲突说明：{item['conflict']}\n"
        f"请说明两者的取舍，并在回复末尾用 proposals 代码块给出你建议的结果（可以是 set 修改，也可以保留原样）。"
    )


def list_minutes(ws: BrainstormWorkspace) -> List[str]:
    d = ws.root / MINUTES_DIR
    if not d.is_dir():
        return []
    return sorted((str(p.relative_to(ws.root)).replace("\\", "/") for p in d.glob("*.md")), reverse=True)


def read_minutes(ws: BrainstormWorkspace, rel: str) -> str:
    base = (ws.root / MINUTES_DIR).resolve()
    path = (ws.root / rel).resolve()
    if base not in path.parents or not path.is_file():
        raise BrainstormError("纪要不存在")
    return path.read_text(encoding="utf-8")
