"""对话区（B2 / B6-1）：与 AI 的多轮追问。AI 的修改建议以 ```proposals 代码块附在回复末尾，
解析后登记为待确认提议，绝不直接写卡片。

B6-1 多段对话：一个构思项目可以有多段对话（历史对话列表），每段有独立的记录与框架会话。
  _studio/conversations.json           索引：active（当前对话 id）+ items（每段的元信息与文件路径）
  _studio/conversations/<id>/session.jsonl       该段的对话记录
  _studio/conversations/<id>/agent_session.json  该段的框架会话 id（用于续接）
旧版单段对话（_studio/session.jsonl）会在第一次访问时自动登记为一段，文件不动。
所有对话都只属于当前构思项目：索引与记录都在项目目录内，不会跨项目出现。

会话续接：优先用 mini_agent 的会话（同一个 session id 续接，保留完整历史）。
为稳健起见，每轮 prompt 同时附带卡片摘要与最近几轮记录；会话续接失败时仍能接上上下文。
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
from novel_writer.fsutil import atomic_write_json, read_json

# 旧版单段对话的路径（仍兼容）；新对话的路径见 _new_entry
TRANSCRIPT = "_studio/session.jsonl"
AGENT_STATE = "_studio/agent_session.json"
CONV_INDEX = "_studio/conversations.json"
CONV_DIR = "_studio/conversations"
LEGACY_ID = "c_legacy"
TITLE_MAX = 18
RECENT_TURNS = 4
MODES = {
    "追问": "围绕作者已有的想法追问，每次只问一两个最关键的问题（优先问代价、谁受影响、为什么是现在）。",
    "自由": "就作者的话自由交流：可以发散、质疑、给出例子，但不要一次塞入太多内容。",
    "收敛": "帮作者把已有的想法收束成 2–3 个备选方向（如两种结局、两种主线走向），逐项说明优缺点，"
            "给出你的推荐但不替作者决定。若作者需要，可以用 options 提议把某个方向登记为备选。",
}
Runner = Callable[[str, Optional[str]], Tuple[str, Optional[str]]]
_BLOCK = re.compile(r"```proposals\s*(.*?)```", re.S)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# ── 对话索引（多段对话） ──

def _load_index(ws: BrainstormWorkspace) -> Dict[str, Any]:
    data = read_json(ws.root / CONV_INDEX, default=None)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data
    return {"active": None, "items": []}


def _save_index(ws: BrainstormWorkspace, idx: Dict[str, Any]) -> None:
    atomic_write_json(ws.root / CONV_INDEX, idx)


def _new_entry() -> Dict[str, Any]:
    cid = "c" + uuid.uuid4().hex[:8]
    base = f"{CONV_DIR}/{cid}"
    now = _now()
    return {"id": cid, "title": "", "transcript": f"{base}/session.jsonl",
            "agent_state": f"{base}/agent_session.json", "created_at": now, "updated_at": now}


def _iso_mtime(path) -> str:
    return _dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _scan_disk(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    """索引缺失或损坏时，从磁盘重建：旧版单段对话 + 每个有记录的对话目录。"""
    found: List[Dict[str, Any]] = []
    legacy = ws.root / TRANSCRIPT
    if legacy.is_file():
        stamp = _iso_mtime(legacy)
        found.append({"id": LEGACY_ID, "title": "", "transcript": TRANSCRIPT, "agent_state": AGENT_STATE,
                      "created_at": stamp, "updated_at": stamp})
    base = ws.root / CONV_DIR
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "session.jsonl").is_file():
                stamp = _iso_mtime(d / "session.jsonl")
                found.append({"id": d.name, "title": "", "transcript": f"{CONV_DIR}/{d.name}/session.jsonl",
                              "agent_state": f"{CONV_DIR}/{d.name}/agent_session.json",
                              "created_at": stamp, "updated_at": stamp})
    return found


def _resolve_index(ws: BrainstormWorkspace, create: bool) -> Dict[str, Any]:
    """读取索引。索引缺失或损坏时从磁盘重建（旧版单段对话会被登记为一段）；create=True 时若仍无对话则新建一段。"""
    idx = _load_index(ws)
    if not idx["items"]:
        found = _scan_disk(ws)
        if found:
            idx["items"] = found
            idx["active"] = max(found, key=lambda e: e["updated_at"])["id"]
            _save_index(ws, idx)
        elif create:
            entry = _new_entry()
            idx["items"] = [entry]
            idx["active"] = entry["id"]
            _save_index(ws, idx)
    ids = [e["id"] for e in idx["items"]]
    if idx["items"] and idx.get("active") not in ids:
        latest = max(idx["items"], key=lambda e: e.get("updated_at", ""))
        idx["active"] = latest["id"]
        _save_index(ws, idx)
    return idx


def _conv_entry(ws: BrainstormWorkspace, cid: Optional[str] = None, create: bool = False) -> Optional[Dict[str, Any]]:
    idx = _resolve_index(ws, create)
    if not idx["items"]:
        return None
    target = cid or idx["active"]
    for e in idx["items"]:
        if e["id"] == target:
            return e
    raise BrainstormError(f"找不到对话：{target}")


def _touch_conv(ws: BrainstormWorkspace, cid: str) -> None:
    idx = _load_index(ws)
    for e in idx["items"]:
        if e["id"] == cid:
            e["updated_at"] = _now()
    _save_index(ws, idx)


def _read_records(path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                                   # 坏行跳过，不影响其它记录
    return out


def _title_of(ws: BrainstormWorkspace, entry: Dict[str, Any]) -> str:
    if entry.get("title"):
        return entry["title"]
    for rec in _read_records(ws.root / entry["transcript"]):
        if rec.get("role") == "user" and str(rec.get("text", "")).strip():
            text = str(rec["text"]).strip().replace("\n", " ")
            return text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")
    return "新对话"


def _public(ws: BrainstormWorkspace, entry: Dict[str, Any], active: Optional[str]) -> Dict[str, Any]:
    return {"id": entry["id"], "title": _title_of(ws, entry), "created_at": entry.get("created_at", ""),
            "updated_at": entry.get("updated_at", ""),
            "turns": len(_read_records(ws.root / entry["transcript"])),
            "current": entry["id"] == active}


def list_conversations(ws: BrainstormWorkspace) -> Dict[str, Any]:
    """本项目的全部对话（最近更新在前）与当前对话 id。只读。"""
    idx = _resolve_index(ws, create=False)
    items = sorted(idx["items"], key=lambda e: e.get("updated_at", ""), reverse=True)
    return {"current": idx.get("active"),
            "items": [_public(ws, e, idx.get("active")) for e in items]}


def current_conversation(ws: BrainstormWorkspace) -> Optional[str]:
    """当前对话 id；项目还没有任何对话时为 None。只读。"""
    return _resolve_index(ws, create=False).get("active")


def new_chat(ws: BrainstormWorkspace) -> Dict[str, Any]:
    """新开一段对话并切换过去。若当前对话还没有任何内容，直接沿用它，避免堆积空对话。"""
    idx = _resolve_index(ws, create=False)
    active = idx.get("active")
    if active and not _read_records(ws.root / _conv_entry(ws, active)["transcript"]):
        return _public(ws, _conv_entry(ws, active), active)
    entry = _new_entry()
    idx = _load_index(ws)
    idx["items"].append(entry)
    idx["active"] = entry["id"]
    _save_index(ws, idx)
    return _public(ws, entry, entry["id"])


def switch_chat(ws: BrainstormWorkspace, cid: str) -> Dict[str, Any]:
    """切换到本项目的另一段对话。记录与框架会话都保留。"""
    idx = _resolve_index(ws, create=False)
    entry = _conv_entry(ws, cid)
    if entry is None:
        raise BrainstormError("项目还没有对话")
    idx["active"] = entry["id"]
    _save_index(ws, idx)
    return _public(ws, entry, entry["id"])


# ── 记录 ──

def append_turn(ws: BrainstormWorkspace, role: str, text: str, conv: Optional[str] = None,
                **extra: Any) -> Dict[str, Any]:
    entry = _conv_entry(ws, conv, create=True)
    rec = {"role": role, "text": text, "at": _now(), **extra}
    path = ws.root / entry["transcript"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _touch_conv(ws, entry["id"])
    return rec


def history(ws: BrainstormWorkspace, limit: Optional[int] = None,
            conv: Optional[str] = None) -> List[Dict[str, Any]]:
    """某段对话的记录（默认当前对话）。没有对话时返回空列表。"""
    entry = _conv_entry(ws, conv)
    if entry is None:
        return []
    out = _read_records(ws.root / entry["transcript"])
    return out[-limit:] if limit else out


def _agent_state(ws: BrainstormWorkspace, conv: Optional[str] = None) -> Dict[str, Any]:
    entry = _conv_entry(ws, conv)
    if entry is None:
        return {}
    data = read_json(ws.root / entry["agent_state"], default={})
    return data if isinstance(data, dict) else {}


def _save_agent_state(ws: BrainstormWorkspace, cid: str, session_id: str) -> None:
    entry = _conv_entry(ws, cid)
    atomic_write_json(ws.root / entry["agent_state"], {"session_id": session_id, "updated_at": _now()})


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


def build_prompt(ws: BrainstormWorkspace, text: str, mode: str, conv: Optional[str] = None) -> str:
    recent = history(ws, limit=RECENT_TURNS * 2, conv=conv)
    recent_text = "\n".join(f"{'作者' if r['role'] == 'user' else '助手'}：{r['text']}" for r in recent) or "（无）"
    return (
        "你是小说构思的合作者，用中文交流，语气平等，不替作者做决定。\n"
        f"当前模式：{mode}。{MODES.get(mode, MODES['追问'])}\n\n"
        f"{context_summary(ws)}\n\n"
        f"【最近对话】\n{recent_text}\n\n"
        "如果你认为应当修改构思卡片，在回复最后附一个代码块（只在确有必要时附）：\n"
        "```proposals\n"
        '[{"module": "ideas|premise|brief|open_questions|world|cast|plot|options", "op": "add|set", '
        '"target": "（set 时需要：目标 id；world 时为列表名 rules/locations/factions/timeline/taboos）", '
        '"value": {...或文字}, "reason": "为什么建议这样改"}]\n'
        "```\n"
        "options 的格式：op 为 add，value 为 {\"name\": 备选名, \"module\": \"premise 或 plot\", \"content\": 可选的完整内容, \"notes\": 优缺点}。\n"
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

def send(ws: BrainstormWorkspace, text: str, mode: str = "追问", runner: Optional[Runner] = None, *,
         turn_id: Optional[str] = None, record_user: bool = True,
         conv: Optional[str] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise BrainstormError("请输入内容")
    if mode not in MODES:
        raise BrainstormError(f"未知模式：{mode}")
    ws.meta()
    cid = _conv_entry(ws, conv, create=True)["id"]
    extra = {"turn": turn_id} if turn_id else {}
    if record_user:
        append_turn(ws, "user", text, conv=cid, mode=mode, **extra)
    prompt = build_prompt(ws, text, mode, conv=cid)
    state = _agent_state(ws, cid)
    reply, new_sid = (runner or default_runner)(prompt, state.get("session_id"))
    if new_sid:
        _save_agent_state(ws, cid, new_sid)
    body, items, invalid = parse_reply(reply)
    made = []
    for item in items:
        try:
            made.append(P.create(ws, item["module"], item["op"], item["value"],
                                 target=item.get("target"), reason=item.get("reason", "")))
        except BrainstormError:
            invalid += 1
    append_turn(ws, "assistant", body or "（无回复）", conv=cid,
                proposals=[p["pid"] for p in made], invalid=invalid, **extra)
    ws.touch()
    return {"reply": body, "proposals": made, "invalid": invalid, "conversation": cid}


# ── 后台运行与过程日志（显示 agent 的全部输出） ──

RUNS_DIR = "_studio/chat_runs"


def _run_paths(ws: BrainstormWorkspace, turn_id: str):
    base = ws.root / RUNS_DIR
    return base / f"{turn_id}.json", base / f"{turn_id}.jsonl"


def start_turn(ws: BrainstormWorkspace, text: str, mode: str = "追问", runner: Optional[Runner] = None) -> str:
    """开始一轮对话：立即记录作者的话，在后台线程里运行 agent，过程输出逐条写入日志文件。返回 turn id。

    轮次绑定到开始时的当前对话：之后即使切换了对话，回复仍写回原来那一段。
    """
    import threading

    text = (text or "").strip()
    if not text:
        raise BrainstormError("请输入内容")
    if mode not in MODES:
        raise BrainstormError(f"未知模式：{mode}")
    ws.meta()
    cid = _conv_entry(ws, None, create=True)["id"]
    turn_id = "t" + uuid.uuid4().hex[:8]
    status_path, log_path = _run_paths(ws, turn_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(status_path, {"turn": turn_id, "status": "running", "started_at": _now(), "conversation": cid})
    log_path.write_text("", encoding="utf-8")
    append_turn(ws, "user", text, conv=cid, mode=mode, turn=turn_id)
    threading.Thread(target=_run_turn_job, args=(str(ws.root), turn_id, text, mode, runner, cid),
                     daemon=True, name=f"brainstorm-{turn_id}").start()
    return turn_id


def _run_turn_job(root: str, turn_id: str, text: str, mode: str, runner: Optional[Runner], cid: str) -> None:
    from novel_writer.brainstorm.runlog import Capture

    ws = BrainstormWorkspace(root)
    status_path, log_path = _run_paths(ws, turn_id)

    def sink(event: Dict[str, str]) -> None:
        line = {"t": _now(), **event}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    try:
        with Capture(sink):
            out = send(ws, text, mode, runner=runner, turn_id=turn_id, record_user=False, conv=cid)
        atomic_write_json(status_path, {"turn": turn_id, "status": "done", "finished_at": _now(),
                                        "proposals": len(out["proposals"]), "invalid": out["invalid"],
                                        "conversation": cid})
    except Exception as exc:  # noqa: BLE001 - 错误要记到日志与记录里，界面才能显示
        sink({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
        append_turn(ws, "assistant", f"（出错：{exc}）", conv=cid, turn=turn_id, error=True)
        atomic_write_json(status_path, {"turn": turn_id, "status": "error", "finished_at": _now(),
                                        "error": str(exc), "conversation": cid})


def run_state(ws: BrainstormWorkspace, turn_id: str) -> Dict[str, Any]:
    """一轮对话的状态与全部过程日志（按时间顺序）。"""
    status_path, log_path = _run_paths(ws, turn_id)
    status = read_json(status_path, default=None)
    if not isinstance(status, dict):
        raise BrainstormError(f"找不到对话轮次：{turn_id}")
    events = []
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {**status, "events": events}


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
