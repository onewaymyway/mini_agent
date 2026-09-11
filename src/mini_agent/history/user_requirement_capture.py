"""
history/user_requirement_capture.py — 真人输入后的记事本兜底捕获

方案文档：next_doc/user_requirement_notepad_capture_plan.md

背景：tools/notepad.py 的记事本常驻 system prompt、不受 history compact
影响，但更新完全依赖 agent 自己在对话中主动调用 notepad_add——一旦 agent
当轮没意识到某句话是关键要求，这条信息就只活在会话历史里，compact 之后
可能丢失/走样，导致后续工作方向偏离用户原始意图。

本模块提供一个不依赖 agent 自觉性的兜底：每次**真人**输入后，同步用 LLM
判断"是否要把这条输入的关键要求补记到记事本、补记什么"，与 agent 自己的
判断并行、互不干扰：
  - 自动捕获的条目统一打 tag="auto_requirement"；
  - 判断用的 LLM 只看得到、只能新增/更新这个 tag 下的条目，不触碰 agent
    手动记的其它条目；
  - 只允许 add/update，不允许 remove——宁可冗余，不可丢信息。

挂载点由调用方决定（cli/repl.py、api/server.py 的真人输入入口），本模块
不关心是谁调用的，只要求传入一个可用的 Agent 实例和这轮的用户原始文本。
任何异常都在本模块内部吞掉，绝不能影响调用方的主流程。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_AUTO_TAG = "auto_requirement"

_MIN_INPUT_LENGTH = 6

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "You are a silent background watcher for an AI coding/task agent. Your only "
    "job is to decide whether the user's latest message contains a requirement, "
    "constraint, goal, preference, or explicit instruction that the agent MUST "
    "NOT forget later, even after its conversation history gets summarized/"
    "compressed. This is a safety net, not the agent's main note-taking — bias "
    "strongly toward capturing anything that looks like a real requirement, and "
    "only skip small talk, pure questions with no stated requirement, or simple "
    "acknowledgements like 'continue'/'ok'/'yes'.\n\n"
    "You are only shown a curated list of notes you previously wrote yourself "
    "(tagged entries below). You may ADD a new note or UPDATE one of your own "
    "existing notes (e.g. to merge/refine), but you must NEVER delete anything — "
    "when in doubt, add a new note instead of overwriting.\n\n"
    "Respond with ONLY a JSON object, no other text:\n"
    '{"should_update": true|false, "action": "add"|"update", '
    '"entry_id": "<id or null>", "content": "<concise note text or empty>"}\n'
    'If should_update is false, set action to "add" and content to "".'
)


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    candidate = m.group(1) if m else None
    if candidate is None:
        m = _BARE_JSON_RE.search(text)
        candidate = m.group(0) if m else None
    if candidate is None:
        return None
    try:
        data = json.loads(candidate)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _build_prompt(existing_entries: list[dict], user_message: str) -> str:
    if existing_entries:
        lines = [
            f"- (id={e.get('id')}) {e.get('content', '')}" for e in existing_entries
        ]
        notes_block = "\n".join(lines)
    else:
        notes_block = "(none yet)"
    return (
        f"Your existing notes (tag={_AUTO_TAG}):\n{notes_block}\n\n"
        f"User's latest message:\n{user_message}\n\n"
        "Decide and respond with the JSON object as instructed."
    )


def _is_config_enabled(agent: Any) -> bool:
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return False
    if not getattr(cfg, "notepad_enabled", True):
        return False
    return bool(getattr(cfg, "auto_capture_user_requirement_enabled", True))


def maybe_capture_user_requirement(agent: Any, user_message: str) -> None:
    """真人输入后同步调用一次。内部完成判断 + 落盘 + 终端提示。

    任何异常都在本函数内部吞掉，绝不向上抛出，不影响调用方后续的
    agent.run_turn(...)。
    """
    try:
        if not user_message or not user_message.strip():
            return
        if len(user_message.strip()) < _MIN_INPUT_LENGTH:
            return
        if not _is_config_enabled(agent):
            return

        from mini_agent.tools.notepad import get_current_notepad

        store = get_current_notepad()
        if store is None:
            return

        existing = [e for e in store.to_list() if e.get("tag") == _AUTO_TAG]

        llm_helper = getattr(agent, "llm_helper", None)
        if llm_helper is None:
            return

        prompt = _build_prompt(existing, user_message)
        raw = llm_helper.ask(prompt, system=_SYSTEM_PROMPT, max_retries=1)

        data = _extract_json(raw)
        if not data or not data.get("should_update"):
            return

        content = str(data.get("content") or "").strip()
        if not content:
            return

        action = data.get("action") or "add"
        entry_id = data.get("entry_id")
        existing_ids = {e.get("id") for e in existing}

        if action == "update" and entry_id in existing_ids:
            entry = store.update(str(entry_id), content)
        else:
            entry = store.add(content, tag=_AUTO_TAG)

        if entry is None:
            return

        _print_capture_notice(entry, is_new=(action != "update" or entry_id not in existing_ids))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(
            _mini_agent_exc,
            where="mini_agent.history.user_requirement_capture.maybe_capture_user_requirement",
        )
        return


def _print_capture_notice(entry: Any, *, is_new: bool) -> None:
    try:
        import mini_agent.ui.renderer as R

        preview = entry.content.strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:80] + "…"
        verb = "新增" if is_new else "更新"
        R.print_info(f"📝 记事本已自动{verb}（捕获用户要求，id={entry.id}）：{preview}")
    except Exception:
        pass
