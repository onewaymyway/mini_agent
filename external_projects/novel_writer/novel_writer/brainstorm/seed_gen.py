"""一句话起稿 S1：由一句话想法一次生成整体构思稿，写入草稿层新版本（不写卡片）。

  * 手动触发；直接调用 LLMHelper（单轮、无工具），与「整理对话」同一模式。
  * 模型输出 JSON → 解析（容忍代码围栏、前后文字，必要时 json_repair）→ `seed_draft.validate_draft` 校验。
  * 校验失败带着错误重试一次；仍失败则不写任何版本，模型原始输出存档并完整显示在过程日志里。
  * 后台线程运行，过程日志逐条写入 `_studio/seed_runs/<id>.jsonl`（界面每秒刷新）。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import distill as DS
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json

Generator = Callable[[str], str]
MAX_ATTEMPTS = 2
IDEA_MAX = 2000
RUNS_DIR = "_studio/seed_runs"

SYSTEM_PROMPT = ("你是小说构思助手。根据作者给出的一句话想法，一次性写出一份整体构思稿。"
                 "只输出一个 JSON 对象，不要调用任何工具，不要写文件，不要输出 JSON 之外的文字。")

SCHEMA = """{
  "premise": "核心命题（一两句话：这个故事究竟在讲什么、探讨什么）",
  "storyline": {
    "logline": "一句话主线",
    "structure": {"type": "结构类型（如：三幕、多幕长篇、群像、多线并行、非线性、连载卷式）",
                  "total_chapters": 总章数（整数，必须填写）,
                  "acts": [{"title": "幕标题", "chapters": "起止章，如 1–30", "goal": "本幕的目标与代价"}]},
    "threads": [{"name": "线索名", "role": "main 或 sub 或 character 或 mystery",
                 "opening": "起", "development": "承", "turn": "转", "ending": "合",
                 "meets": ["与哪条线索在哪里交汇（可空）"]}]
  },
  "world": {
    "rules": [{"text": "世界规则", "cost": "代价（可省略）"}],
    "locations": [{"text": "地点及其作用"}],
    "factions": [{"text": "势力及其立场"}],
    "timeline": ["关键历史事件"],
    "taboos": ["禁忌/不可触碰的设定"]
  },
  "cast": [{"name": "名字", "role": "定位", "want": "想要", "need": "真正需要", "flaw": "缺陷", "lie": "相信的谎言", "arc": "成长弧线"}],
  "plot": {"acts": [{"title": "与 storyline.structure.acts 相同的幕标题", "beats": ["该幕的关键转折点"]}], "twists": ["反转"], "endings": ["结局候选"]},
  "open_questions": ["尚未决定、需要作者拍板的问题"]
}"""

GUIDE = ("篇幅要求：这是「大致的雏形」，不是详细设定。规则 3–6 条，地点 2–5 个，势力 2–4 个，时间线 2–5 条，禁忌 1–3 条；"
         "角色 3–6 个（主角必须有 want / need / flaw）；反转 1–3 个；结局候选 2–3 个；待定问题 3–6 个（诚实列出你替作者做了假设、需要他确认的地方）。"
         "每条一两句话，具体、不要空泛口号。六个部分必须齐全。")

STRUCTURE_RULES = ("结构要求（重要）：\n"
    "1. 先决定结构：根据篇幅选择结构类型。篇幅很长（超过 50 章）时，幕数 3–12 幕，每幕 3–8 个转折点，线索 2–6 条，至少 2 条线索。\n"
    "2. 章节范围：structure.acts 的章节范围必须从第 1 章连续覆盖到 total_chapters；plot.acts 的幕标题与 structure.acts 完全相同、顺序相同。\n"
    "3. 线索：每条线索各自写一套起承转合，节奏与结局可以不同；支线与角色线要在主线的某些节点交汇（meets），不要只是主线的附属。\n"
    "4. 禁止套路：不要默认采用单线的起承转合；不要默认救赎式结局；不要让所有线索都在同一高潮收束；反派必须有自洽的动机；"
    "至少一条线索的关键转折发生在主线的低谷期，形成错位。")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# ─────────────────────────────── 提示词与解析 ───────────────────────────────

def _brief_context(ws: BrainstormWorkspace) -> str:
    brief = C.get_module(ws, "brief")
    filled = {k: v for k, v in brief.items() if str(v).strip()}
    if not filled:
        return ""
    return "【题材卡（作者已填，必须遵守）】\n" + "\n".join(f"- {k}：{v}" for k, v in filled.items())


def scale_text(target_chapters: Optional[int], structure: Optional[str]) -> str:
    """篇幅与结构偏好的提示段（作者指定了才给；没指定则要求模型自行判断并写明）。"""
    lines = []
    if target_chapters:
        lines.append(f"目标篇幅：约 {target_chapters} 章。storyline.structure.total_chapters 必须填 {target_chapters}，"
                     f"幕的章节范围须连续覆盖 1–{target_chapters}。")
    else:
        lines.append("目标篇幅未指定：请根据想法判断合适的章数（短篇 ≤ 20 章，中篇 20–50 章，长篇 > 50 章），并在 total_chapters 中填写。")
    if structure:
        lines.append(f"结构偏好：{structure}（作为 structure.type 的依据，除非与篇幅明显冲突）。")
    return "【篇幅与结构】\n" + "\n".join(lines)


def build_prompt(ws: BrainstormWorkspace, idea: str, constraints: str = "", errors: Optional[List[str]] = None, *,
                 target_chapters: Optional[int] = None, structure: Optional[str] = None) -> str:
    parts = [f"【作者的想法】\n{idea.strip()}"]
    if constraints.strip():
        parts.append(f"【作者的额外要求（必须遵守）】\n{constraints.strip()}")
    ctx = _brief_context(ws)
    if ctx:
        parts.append(ctx)
    parts.append(scale_text(target_chapters, structure))
    parts.append("【输出格式】只输出一个 JSON 对象，结构如下：\n" + SCHEMA)
    parts.append(GUIDE)
    parts.append(STRUCTURE_RULES)
    if errors:
        parts.append("【上一次输出未通过校验，请修正后重新输出完整 JSON】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def parse_output(reply: str, *, repaired: Optional[List[str]] = None) -> Dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (reply or "").strip()).strip()
    first, last = text.find("{"), text.rfind("}")
    if first < 0:
        raise BrainstormError("模型输出里没有 JSON 对象")
    err: Optional[Exception] = None
    data: Any = None
    if last > first:
        try:
            data = json.loads(text[first:last + 1])
        except json.JSONDecodeError as exc:
            err = exc
    else:
        err = ValueError("缺少结尾的 }（输出可能被截断）")
    if err is not None:
        data = DS._repair_json(text[first:])
        if not isinstance(data, dict):
            raise BrainstormError(f"模型输出不是合法 JSON：{err}")
        if repaired is not None:
            repaired.append(str(err))
    if not isinstance(data, dict):
        raise BrainstormError("模型输出的 JSON 不是对象")
    return data


def _save_raw(ws: BrainstormWorkspace, raw: Any) -> str:
    rel = f"_studio/seed_draft/raw_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    atomic_write_text(ws.root / rel, str(raw))
    return rel


def _emit(log, kind: str, text: str) -> None:
    if log is not None:
        log(kind, text)


# ─────────────────────────────── 运行 ───────────────────────────────

def _norm_target(v: Any) -> Optional[int]:
    if v in (None, "", 0):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise BrainstormError("目标章数必须是整数")
    if not 1 <= n <= 2000:
        raise BrainstormError("目标章数须在 1–2000 之间")
    return n


def _run_body(ws: BrainstormWorkspace, idea: str, constraints: str = "", *, target_chapters: Any = None,
              structure: Optional[str] = None, generator: Optional[Generator] = None,
              log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    idea, constraints = (idea or "").strip(), (constraints or "").strip()
    target = _norm_target(target_chapters)
    structure = (structure or "").strip() or None
    if not idea:
        raise BrainstormError("请先写下你的想法")
    if len(idea) > IDEA_MAX:
        raise BrainstormError(f"想法太长（{len(idea)} 字，上限 {IDEA_MAX}）；起稿只需要一个简单的想法")
    if generator is None:
        from novel_writer.brainstorm.spark import helper_generator
        generator = lambda p: helper_generator(p, system=SYSTEM_PROMPT, timeout=600)  # noqa: E731
    errors: List[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = build_prompt(ws, idea, constraints, errors, target_chapters=target, structure=structure)
        _emit(log, "info", f"第 {attempt}/{MAX_ATTEMPTS} 次：提示词 {len(prompt)} 字，开始调用模型")
        raw = generator(prompt)
        _emit(log, "info", f"模型返回 {len(str(raw))} 字，解析并校验")
        repaired: List[str] = []
        try:
            draft = parse_output(raw, repaired=repaired)
            SD.validate_draft(draft)
        except BrainstormError as exc:
            rel = _save_raw(ws, raw)
            _emit(log, "error", f"未通过：{exc}\n模型原始输出（已存档 {rel}）：\n{raw}")
            errors = [str(exc)]
            continue
        if repaired:
            rel = _save_raw(ws, raw)
            _emit(log, "warning", f"输出不是严格合法的 JSON（{repaired[0]}），已用 json_repair 修复；原始输出已存档 {rel}")
        ver = SD.save_version(ws, draft, kind="seed", reason="一句话起稿" if attempt == 1 else "一句话起稿（重试后通过）",
                              seed={"idea": idea, "constraints": constraints, "target_chapters": target, "structure": structure})
        _emit(log, "info", f"构思稿已保存为 v{ver:03d}（草稿层，尚未写入卡片）")
        return {"ver": ver, "attempts": attempt, "repaired": bool(repaired)}
    raise BrainstormError(f"起稿失败：{MAX_ATTEMPTS} 次都未通过校验（{errors[0]}）；没有写入任何版本，原始输出已存档")


# ─────────────────────────────── 后台运行与日志 ───────────────────────────────

def _run_files(ws: BrainstormWorkspace, run_id: str):
    base = ws.root / RUNS_DIR
    return base / f"{run_id}.json", base / f"{run_id}.jsonl"


def run(ws: BrainstormWorkspace, idea: str, constraints: str = "", *, target_chapters: Any = None,
        structure: Optional[str] = None, generator: Optional[Generator] = None,
        log: Optional[Callable[[str, str], None]] = None, _rid: Optional[str] = None) -> Dict[str, Any]:
    """起稿（同步）。每次调用都记入输入记录（seed_history），结束时写入结果或失败原因。"""
    from novel_writer.brainstorm import seed_history as H
    payload = {"idea": idea or "", "constraints": constraints or "", "target_chapters": target_chapters,
               "structure": structure}
    rid = _rid or H.submitted(ws, "seed", payload)
    try:
        res = _run_body(ws, idea, constraints, target_chapters=target_chapters, structure=structure,
                        generator=generator, log=log)
    except BaseException as exc:
        H.outcome(ws, rid, "failed", error=str(exc))
        raise
    H.outcome(ws, rid, "done", ver=res["ver"])
    return res


def start_run(ws: BrainstormWorkspace, idea: str, constraints: str = "", *, target_chapters: Any = None,
              structure: Optional[str] = None, generator: Optional[Generator] = None) -> str:
    """校验输入后在后台起稿，返回运行编号。有未结束的起稿/修订任务时拒绝（避免并发写版本）。
    每次提交（包括未通过校验的）都记入输入记录。"""
    from novel_writer.brainstorm import seed_history as H
    ws.meta()
    rid = H.submitted(ws, "seed", {"idea": idea or "", "constraints": constraints or "",
                                   "target_chapters": target_chapters, "structure": structure})
    try:
        if not (idea or "").strip():
            raise BrainstormError("请先写下你的想法")
        _norm_target(target_chapters)
    except BrainstormError as exc:
        H.outcome(ws, rid, "rejected", error=str(exc))
        raise
    try:
        return start_task(ws, lambda w, log: run(w, idea, constraints, target_chapters=target_chapters, structure=structure,
                                                 generator=generator, log=log, _rid=rid), prefix="s")
    except BrainstormError as exc:
        H.outcome(ws, rid, "rejected", error=str(exc))
        raise


def start_task(ws: BrainstormWorkspace, fn: Callable[[BrainstormWorkspace, Callable[[str, str], None]], Any],
               *, prefix: str = "s") -> str:
    """在后台线程执行 fn(ws, log)，状态与过程日志写入 _studio/seed_runs/。起稿与反馈修订共用（同一时间只允许一个）。"""
    import threading
    base = ws.root / RUNS_DIR
    if base.is_dir():
        for p in base.glob("*.json"):
            if (read_json(p, default={}) or {}).get("status") == "running":
                raise BrainstormError("已有起稿/修订任务在运行，请等它结束")
    run_id = prefix + uuid.uuid4().hex[:8]
    status_path, log_path = _run_files(ws, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    atomic_write_json(status_path, {"run": run_id, "status": "running", "started_at": _now()})
    threading.Thread(target=_job, args=(str(ws.root), run_id, fn), daemon=True, name=f"seed-{run_id}").start()
    return run_id


def _job(root: str, run_id: str, fn) -> None:
    from novel_writer.brainstorm.runlog import Capture
    ws = BrainstormWorkspace(root)
    status_path, log_path = _run_files(ws, run_id)

    def write(kind: str, text: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": _now(), "kind": kind, "text": text}, ensure_ascii=False) + "\n")

    try:
        with Capture(lambda e: write(e.get("kind", "info"), e.get("text", ""))):
            result = fn(ws, write)
        atomic_write_json(status_path, {"run": run_id, "status": "done", "finished_at": _now(), "result": result})
    except Exception as exc:  # noqa: BLE001 - 失败要写进日志与状态，界面才能显示
        write("error", f"{type(exc).__name__}：{exc}")
        atomic_write_json(status_path, {"run": run_id, "status": "error", "finished_at": _now(), "error": str(exc)})


def run_state(ws: BrainstormWorkspace, run_id: str) -> Dict[str, Any]:
    status_path, log_path = _run_files(ws, run_id)
    status = read_json(status_path, default=None)
    if not isinstance(status, dict):
        raise BrainstormError(f"找不到起稿任务：{run_id}")
    events = []
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {**status, "events": events}
