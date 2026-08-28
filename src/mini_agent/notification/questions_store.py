"""notification/questions_store.py — cron 任务异步用户反馈的问答记录存储。

设计背景见 next_doc/cron_async_user_feedback_mechanism_plan.md。

跟 `reports_store.py` 刻意保持同构（同样是"小文件、低频写、整体重写"的
jsonl 存储风格），但物理上是两份完全独立的文件，语义也不同：

  - `.agent/notification/reports.jsonl`        ← 只读汇报，acknowledged 布尔已读
  - `.agent/notification/cron_questions.jsonl` ← 双向问答，答案可修改、有历史版本

`ask_user_async` 工具调用 `append_question()`/`find_pending_by_fingerprint()`
写入待回答问题；看板 API 调用 `submit_answer()` 提交/修改答案（同一入口，不
区分首次回答和修改）；`CronJobWorkspace.render_prompt()` 调用
`list_unconsumed_answers_for_job()` + `mark_answers_consumed()` 把已回答但
还没喂给过 agent 的问答对注入下一次 prompt。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"


def _new_question_id(job_id: str) -> str:
    return f"cq:{job_id}:{uuid.uuid4().hex[:12]}"


def _load_all(paths: "AgentPaths") -> list[dict]:
    p = paths.notification_cron_questions
    if not p.exists():
        return []
    result: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.questions_store._load_all")
        return []
    return result


def _write_all(paths: "AgentPaths", records: list[dict]) -> None:
    p = paths.notification_cron_questions
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(d, ensure_ascii=False) for d in records]
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def append_question(
    paths: "AgentPaths",
    job_id: str,
    question: str,
    hint: str = "",
    options: Optional[list] = None,
) -> dict:
    """新建一条待回答问题，返回写入的完整记录（含新生成的 question_id）。

    调用方（`ask_user_async` 工具）应先调用 `find_pending_by_fingerprint()`
    查重，确认确实需要新建时再调用本函数——本函数本身不做去重判断，允许
    调用方在明确需要"同一问题再问一次"的场景下（用户已经关闭/忽略了上一条）
    绕过查重直接新建。
    """
    record = {
        "question_id": _new_question_id(job_id),
        "job_id": job_id,
        "question": question,
        "hint": hint or "",
        "options": list(options) if options else [],
        "status": STATUS_PENDING,
        "created_at": time.time(),
        "updated_at": time.time(),
        "answer": "",
        "answer_history": [],
        "consumed": False,
    }
    try:
        p = paths.notification_cron_questions
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.questions_store.append_question")
    return record


def find_pending_by_fingerprint(paths: "AgentPaths", job_id: str, question: str) -> Optional[dict]:
    """同一 job 下是否已有相同问题文本（精确匹配，去除首尾空白后比较）且
    仍是 pending 状态的记录，有则返回该记录（供调用方复用其
    `question_id`，不新建），没有则返回 None。"""
    q = (question or "").strip()
    if not q:
        return None
    for d in _load_all(paths):
        if (
            d.get("job_id") == job_id
            and d.get("status") == STATUS_PENDING
            and (d.get("question") or "").strip() == q
        ):
            return d
    return None


def get_question(paths: "AgentPaths", question_id: str) -> Optional[dict]:
    for d in _load_all(paths):
        if d.get("question_id") == question_id:
            return d
    return None


def list_pending_questions(
    paths: "AgentPaths", job_id: Optional[str] = None,
    limit: Optional[int] = None, offset: int = 0,
) -> list[dict]:
    """待回答问题列表，按 created_at 倒序（最新的排最前），供看板"待我
    反馈"面板展示。"""
    result = [d for d in _load_all(paths) if d.get("status") == STATUS_PENDING]
    if job_id:
        result = [d for d in result if d.get("job_id") == job_id]
    result.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    if offset:
        result = result[offset:]
    if limit is not None:
        result = result[:limit]
    return result


def list_answered_questions(
    paths: "AgentPaths", job_id: Optional[str] = None,
    limit: Optional[int] = None, offset: int = 0,
) -> list[dict]:
    """已回答问题历史，按 updated_at 倒序，供看板"历史记录"面板展示
    （含完整 answer_history，不受 `consumed` 字段影响——历史面板永远展示
    全部，`consumed` 只是内部用来控制 prompt 注入去重）。"""
    result = [d for d in _load_all(paths) if d.get("status") == STATUS_ANSWERED]
    if job_id:
        result = [d for d in result if d.get("job_id") == job_id]
    result.sort(key=lambda d: d.get("updated_at") or 0, reverse=True)
    if offset:
        result = result[offset:]
    if limit is not None:
        result = result[:limit]
    return result


def submit_answer(paths: "AgentPaths", question_id: str, answer_text: str) -> Optional[dict]:
    """提交或修改一条问题的答案。新答、改答统一走这个入口（`questions_store`
    不区分"首次回答"和"修改回答"——只要调用这个函数，就把当前答案更新为
    `answer_text`，并往 `answer_history` 追加一条，不覆盖丢失旧版本）。

    答案变更后会把 `consumed` 重置为 False，让改动过的答案在下一次该
    job 触发时能再次被 `render_prompt()` 注入（即使之前已经被消费过一次）
    ——用户修改答案通常意味着"上一版答案不对/不完整，请按新的来"，理应
    让 agent 重新看到。

    返回更新后的完整记录；`question_id` 不存在时返回 None，不报错。
    """
    text = (answer_text or "").strip()
    if not text:
        return None
    records = _load_all(paths)
    updated: Optional[dict] = None
    for d in records:
        if d.get("question_id") == question_id:
            now = time.time()
            d["answer"] = text
            d.setdefault("answer_history", []).append({"text": text, "at": now})
            d["status"] = STATUS_ANSWERED
            d["updated_at"] = now
            d["consumed"] = False
            updated = d
            break
    if updated is not None:
        _write_all(paths, records)
    return updated


def list_unconsumed_answers_for_job(paths: "AgentPaths", job_id: str) -> list[dict]:
    """取出某个 job 下"已回答但还没被下一次 prompt 消费过"的问答对，供
    `CronJobWorkspace.render_prompt()` 渲染 `{{pending_answers}}` 占位符。
    按 updated_at 正序（先问的先展示），跟对话时间顺序保持一致。"""
    result = [
        d for d in _load_all(paths)
        if d.get("job_id") == job_id and d.get("status") == STATUS_ANSWERED and not d.get("consumed")
    ]
    result.sort(key=lambda d: d.get("updated_at") or 0)
    return result


def mark_answers_consumed(paths: "AgentPaths", question_ids: "set[str] | list[str]") -> int:
    """把一批问题标记为"已消费过"（`consumed=True`），`render_prompt()`
    把答案注入 prompt 后调用，避免同一个答案在后续多次触发里被反复注入。
    返回实际标记成功的条数。不存在/已经是 consumed 的条目被跳过，不报错。
    """
    ids = set(question_ids)
    if not ids:
        return 0
    records = _load_all(paths)
    matched = 0
    for d in records:
        if d.get("question_id") in ids and not d.get("consumed"):
            d["consumed"] = True
            matched += 1
    if matched:
        _write_all(paths, records)
    return matched


def list_pending_question_texts_for_job(paths: "AgentPaths", job_id: str) -> list[dict]:
    """取出某个 job 下仍是 pending 状态的问题，供 `render_prompt()` 渲染
    `{{unanswered_questions}}` 占位符，提醒 agent 不要重复提问同一个问题。
    按 created_at 正序。"""
    result = [
        d for d in _load_all(paths)
        if d.get("job_id") == job_id and d.get("status") == STATUS_PENDING
    ]
    result.sort(key=lambda d: d.get("created_at") or 0)
    return result
