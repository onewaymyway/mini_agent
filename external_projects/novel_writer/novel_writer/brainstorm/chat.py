"""对话区（B2）：与 AI 的多轮追问。AI 的修改建议以 ```proposals 代码块附在回复末尾，
解析后登记为待确认提议，绝不直接写卡片。

会话续接：优先用 mini_agent 的会话（同一个 session id 续接，保留完整历史）。
为稳健起见，每轮 prompt 同时附带卡片摘要与最近几轮记录；会话续接失败时仍能接上上下文。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, read_json

TRANSCRIPT = "_studio/session.jsonl"
AGENT_STATE = "_studio/agent_session.json"
RECENT_TURNS = 4
MODES = {
    "追问": "围绕作者已有的想法追问，每次只问一两个最关键的问题（优先问代价、谁受影响、为什么是现在）。",
    "自由": "就作者的话自由交流：可以发散、质疑、给出例子，但不要一次塞入太多内容。",
}
Runner = Callable[[str, Optional[str]], Tuple[str, Optional[str]]]
_BLOCK = re.compile(r"```proposals\s*(.*?)```", re.S)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# ── 记录 ──

def append_turn(ws: BrainstormWorkspace, role: str, text: str, **extra: Any) -> Dict[str, Any]:
    rec = {"role": role, "text": text, "at": _now(), **extra}
    path = ws.root / TRANSCRIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def history(ws: BrainstormWorkspace, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    path = ws.root / TRANSCRIPT
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                                   # 坏行跳过，不影响其它记录
    return out[-limit:] if limit else out


def _agent_state(ws: BrainstormWorkspace) -> Dict[str, Any]:
    data = read_json(ws.root / AGENT_STATE, default={})
    return data if isinstance(data, dict) else {}


def new_chat(ws: BrainstormWorkspace) -> None:
    """新开一段会话：丢弃框架会话 id，之后的第一轮不再续接。对话记录保留。"""
    path = ws.root / AGENT_STATE
    if path.exists():
        path.unlink()


# ── 提示词 ──

def context_summary(ws: BrainstormWorkspace) -> str:
    cards = C.all_modules(ws)
    brief = {k: v for k, v in cards["brief"].items() if v}
    ideas = cards["ideas"]
    lines = ["【当前构思卡片】"]
    lines.append("题材：" + ("；".join(f"{k}={v}" for k, v in brief.items()) if brief else "（未填写）"))
    lines.append("核心命题：" + (cards["premise"] or "（未填写）"))
    kept = [i["text"] for i in ideas if i["status"] in ("keep", "used")]
    lines.append(f"灵感（保留/已采用 {len(kept)} 条）：" + ("；".join(kept[-12:]) if kept else "（无）"))
    world = cards["world"]
    lines.append("世界观：" + ", ".join(f"{k}{len(world[k])}条" for k in C.WORLD_LISTS))
    cast = cards["cast"]
    lines.append("角色：" + ("、".join(c.get("name", cid) for cid, c in cast.items()) if cast else "（无）"))
    acts = cards["plot"]["acts"]
    lines.append("剧情幕：" + ("、".join(a.get("title", "") for a in acts) if acts else "（无）"))
    open_q = [q["text"] for q in cards["open_questions"] if not q["closed"]]
    lines.append("开放问题：" + ("；".join(open_q) if open_q else "（无）"))
    return "\n".join(lines)


def build_prompt(ws: BrainstormWorkspace, text: str, mode: str) -> str:
    recent = history(ws, limit=RECENT_TURNS * 2)
    recent_text = "\n".join(f"{'作者' if r['role'] == 'user' else '助手'}：{r['text']}" for r in recent) or "（无）"
    return (
        "你是小说构思的合作者，用中文交流，语气平等，不替作者做决定。\n"
        f"当前模式：{mode}。{MODES.get(mode, MODES['追问'])}\n\n"
        f"{context_summary(ws)}\n\n"
        f"【最近对话】\n{recent_text}\n\n"
        "如果你认为应当修改构思卡片，在回复最后附一个代码块（只在确有必要时附）：\n"
        "```proposals\n"
        '[{"module": "ideas|premise|brief|open_questions|world|cast|plot", "op": "add|set", '
        '"target": "（set 时需要：目标 id；world 时为列表名 rules/locations/factions/timeline/taboos）", '
        '"value": {...或文字}, "reason": "为什么建议这样改"}]\n'
        "```\n"
        "注意：你的建议只是提议，作者确认后才会生效。\n\n"
        f"作者说：{text}"
    )


# ── 解析 ──

def parse_reply(reply: str) -> Tuple[str, List[Dict[str, Any]], int]:
    """返回 (去掉提议块后的正文, 提议列表, 无效提议数)。"""
    text = reply or ""
    proposals: List[Dict[str, Any]] = []
    invalid = 0
    match = _BLOCK.search(text)
    if match:
        body = _BLOCK.sub("", text).strip()
        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            data = None
            invalid += 1
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("module") and item.get("op") and "value" in item:
                    proposals.append(item)
                else:
                    invalid += 1
        elif data is not None:
            invalid += 1
        text = body
    return text.strip(), proposals, invalid


# ── 发送 ──

def send(ws: BrainstormWorkspace, text: str, mode: str = "追问", runner: Optional[Runner] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise BrainstormError("请输入内容")
    if mode not in MODES:
        raise BrainstormError(f"未知模式：{mode}")
    ws.meta()
    prompt = build_prompt(ws, text, mode)
    state = _agent_state(ws)
    reply, new_sid = (runner or default_runner)(prompt, state.get("session_id"))
    if new_sid:
        atomic_write_json(ws.root / AGENT_STATE, {"session_id": new_sid, "updated_at": _now()})
    body, items, invalid = parse_reply(reply)
    append_turn(ws, "user", text, mode=mode)
    made = []
    for item in items:
        try:
            made.append(P.create(ws, item["module"], item["op"], item["value"],
                                 target=item.get("target"), reason=item.get("reason", "")))
        except BrainstormError:
            invalid += 1
    append_turn(ws, "assistant", body or "（无回复）", proposals=[p["pid"] for p in made], invalid=invalid)
    ws.touch()
    return {"reply": body, "proposals": made, "invalid": invalid}


def default_runner(prompt: str, session_id: Optional[str]) -> Tuple[str, Optional[str]]:
    """默认运行器：mini_agent 受限 Agent（无工具），尝试续接会话。"""
    from novel_writer import config
    from novel_writer.runner import _build_restricted_agent, _ensure_framework_on_path

    _ensure_framework_on_path()
    cfg = config.load_llm_cfg()
    agent = _build_restricted_agent(cfg, allowed_tools=[], max_turns=6, timeout=300)
    if session_id:
        try:
            agent.load_session(session_id)
        except Exception:  # noqa: BLE001 - 续接失败就新开，靠提示词里的摘要与最近记录接上
            pass
    reply = str(agent.run_turn(prompt))
    new_sid = None
    try:
        agent.save_session()
        sess = getattr(agent, "_session", None)
        new_sid = getattr(sess, "id", None)
    except Exception:  # noqa: BLE001
        new_sid = None
    return reply, new_sid
