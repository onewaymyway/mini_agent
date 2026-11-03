"""设定文档（手动）：把构思对话整理成一份完整的小说世界观与设定 Markdown，供之后当作灵感素材直接创作。

  * 只有作者点击「生成设定文档」才会运行；不会自动运行。
  * 素材只用对话（不并入卡片）。范围：全部对话 / 最近若干轮。
  * 直接调用 LLMHelper（单轮、无工具），输出 Markdown 正文，不要求 JSON。
  * 不改卡片、不进待确认列表、不推进「整理对话」的检查点。
  * 每次生成一份新文件 `output/设定/设定_<时间>.md`，不覆盖旧的。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from novel_writer.brainstorm import distill as DS
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json

LORE_DIR = "output/设定"
RUNS_DIR = "_studio/lore_runs"
Generator = Callable[[str], str]

SYSTEM_PROMPT = ("你是小说构思的设定整理员。直接输出一份完整的 Markdown 设定文档，"
                 "不要调用任何工具，不要写文件，不要用代码块包住整份文档，不要在文档之外说明。")

SECTIONS = (
    "一、一句话概述", "二、题材与基调", "三、世界观（规则 / 地点 / 势力 / 时间线 / 禁忌）",
    "四、角色设定", "五、剧情骨架", "六、核心悬念与伏笔", "七、未定与待讨论", "八、创作灵感钩子",
)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def build_prompt(records: List[Dict[str, Any]], start: int) -> str:
    sections = "\n".join(f"## {s}" for s in SECTIONS)
    return (
        "下面是一段小说构思对话。请把其中作者已经确定或明确倾向的内容，整理成一份可以直接用来创作小说的"
        "「世界观与设定文档」，用中文 Markdown 写。\n"
        "要求：\n"
        "1. 只写作者认可或明确倾向的内容；仅被提到、未被认可的想法写进「七、未定与待讨论」；被作者否定的方向不要写进设定。\n"
        "2. 设定要具体、自洽、可直接引用：写清规则、代价、限制、人物动机与关系，不要空泛概括；对话没有说的不要编造，缺的写「待定」。\n"
        "3. 每条设定后用对话编号标注来源，如 [12]、[3][5]。\n"
        "4. 「八、创作灵感钩子」只从已确定的设定里提炼：可以展开的场景、冲突、反转、人物关系张力，每条一两句。\n"
        "5. 文档以一个一级标题开头（小说暂定名，没有就写「小说设定文档」），然后严格按下列二级标题顺序输出，"
        "某一节没有内容也保留标题并写「待定」：\n\n"
        f"{sections}\n\n"
        f"【对话（编号 {start} 起）】\n{DS._transcript_text(records, start)}"
    )


def clean_output(reply: str) -> str:
    """去掉整份文档外层可能包着的代码块围栏。"""
    text = (reply or "").strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", text, re.S)
    return (m.group(1) if m else text).strip()


def run(ws: BrainstormWorkspace, *, scope: str = "all", recent_rounds: int = 0,
        generator: Optional[Generator] = None, log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    from novel_writer.brainstorm import chat as CH

    if scope not in ("all", "recent"):
        raise BrainstormError(f"设定文档不支持的范围：{scope}")
    start, end = DS.select_range(ws, scope, recent_rounds)
    records = CH.history(ws)[start:end]
    valid = [r for r in records if not r.get("error")]
    DS._emit(log, "info", f"对话范围：第 {start} 至 {max(start, end - 1)} 条记录，共 {len(records)} 条，其中有效 {len(valid)} 条")
    if not valid:
        raise BrainstormError("没有需要整理的对话")
    if generator is None:
        from novel_writer.brainstorm.spark import helper_generator
        generator = lambda p: helper_generator(p, system=SYSTEM_PROMPT)  # noqa: E731
    prompt = build_prompt(records, start)
    DS._emit(log, "info", f"提示词：{len(prompt)} 字，开始调用模型")
    raw = generator(prompt)
    body = clean_output(str(raw))
    DS._emit(log, "info", f"模型返回 {len(str(raw))} 字")
    if len(body) < 50 or "#" not in body:
        rel_raw = DS._save_raw(ws, raw)
        DS._emit(log, "error", f"模型输出不像一份设定文档（已存档 {rel_raw}），完整内容：\n{raw}")
        raise BrainstormError("模型没有返回有效的设定文档")
    now = _dt.datetime.now()
    rel = f"{LORE_DIR}/设定_{now.strftime('%Y%m%d_%H%M%S')}.md"
    head = ["<!-- 自动生成的设定文档：对话整理而成，可直接作为创作素材 -->",
            f"<!-- 生成时间：{now.isoformat(timespec='seconds')}；对话范围：第 {start} 至 {max(start, end - 1)} 条记录 -->", ""]
    atomic_write_text(ws.root / rel, "\n".join(head) + "\n" + body + "\n")
    DS._emit(log, "success", f"设定文档已写入：{rel}（{len(body)} 字）")
    return {"path": rel, "range": [start, end], "chars": len(body)}


def list_docs(ws: BrainstormWorkspace) -> List[str]:
    d = ws.root / LORE_DIR
    if not d.is_dir():
        return []
    return sorted((str(p.relative_to(ws.root)).replace("\\", "/") for p in d.glob("*.md")), reverse=True)


def read_doc(ws: BrainstormWorkspace, rel: str) -> str:
    base = (ws.root / LORE_DIR).resolve()
    path = (ws.root / rel).resolve()
    if base not in path.parents or not path.is_file():
        raise BrainstormError("设定文档不存在")
    return path.read_text(encoding="utf-8")


# ── 后台运行与过程日志（与整理对话同一套形式）──

def _run_files(ws: BrainstormWorkspace, run_id: str):
    base = ws.root / RUNS_DIR
    return base / f"{run_id}.json", base / f"{run_id}.jsonl"


def start_run(ws: BrainstormWorkspace, *, scope: str = "all", recent_rounds: int = 0,
              generator: Optional[Generator] = None) -> str:
    ws.meta()
    run_id = "l" + uuid.uuid4().hex[:8]
    status_path, log_path = _run_files(ws, run_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    atomic_write_json(status_path, {"run": run_id, "status": "running", "started_at": _now()})
    threading.Thread(target=_run_job, args=(str(ws.root), run_id, scope, recent_rounds, generator),
                     daemon=True, name=f"lore-{run_id}").start()
    return run_id


def _run_job(root: str, run_id: str, scope: str, recent_rounds: int, generator) -> None:
    ws = BrainstormWorkspace(root)
    status_path, log_path = _run_files(ws, run_id)

    def write(kind: str, text: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": _now(), "kind": kind, "text": text}, ensure_ascii=False) + "\n")

    try:
        result = run(ws, scope=scope, recent_rounds=recent_rounds, generator=generator, log=write)
        atomic_write_json(status_path, {"run": run_id, "status": "done", "finished_at": _now(), "result": result})
    except Exception as exc:  # noqa: BLE001 - 失败要写进日志与状态，界面才能显示
        write("error", f"{type(exc).__name__}：{exc}")
        atomic_write_json(status_path, {"run": run_id, "status": "error", "finished_at": _now(), "error": str(exc)})


def run_state(ws: BrainstormWorkspace, run_id: str) -> Dict[str, Any]:
    status_path, log_path = _run_files(ws, run_id)
    status = read_json(status_path, default=None)
    if not isinstance(status, dict):
        raise BrainstormError(f"找不到设定文档任务：{run_id}")
    events = []
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {**status, "events": events}
