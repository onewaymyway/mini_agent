"""notification/questions_store.py — cron 任务异步用户反馈的问答记录存储。

设计背景见 next_doc/cron_async_user_feedback_mechanism_plan.md。

[cron_async_feedback_hardening_plan.md D1] `cron_job_runner.py` 支持
`max_concurrent_jobs>1`，多个 cron job 线程 + API 请求线程可能并发读写
本文件对应的 `cron_questions.jsonl`。所有"读全部→改→整体覆盖写"的复合
操作（`submit_answer`/`dismiss_question`/`mark_answers_consumed`）都用
`external_input/filelock.py::ExclusiveFileLock` 包裹整个读改写窗口（跟
`pending_hits.jsonl` 的既有先例同款用法），写文件统一走
`utils/atomic_write.py::atomic_write_jsonl`（tmp+replace，避免读端看到半截
内容）。`append_question` 本身是纯追加，语义上不需要跟"整体重写"互斥，
但为了避免追加发生在另一线程"读全部"的中间导致该线程漏读到这条新记录
（读到的是追加前的快照，随后又整体覆盖写回去——虽然不会丢别的字段，但
会让那次读改写的调用方判断依据是过期数据），追加也纳入同一把锁。

跟 `reports_store.py` 刻意保持同构（同样是"小文件、低频写、整体重写"的
jsonl 存储风格），但物理上是两份完全独立的文件，语义也不同：

  - `.agent/notification/reports.jsonl`        ← 只读汇报，acknowledged 布尔已读
  - `.agent/notification/cron_questions.jsonl` ← 双向问答，答案可修改、有历史版本

`ask_user_async` 工具调用 `append_question()`/`find_pending_by_fingerprint()`
写入待回答问题；看板 API 调用 `submit_answer()` 提交/修改答案（同一入口，不
区分首次回答和修改）；用户也可以调用 `dismiss_question()` 手动忽略一条
仍未回答的问题（跟回答是两条不同路径，忽略后既不会再打扰用户，也不会
被当作"答案"注入 prompt）；`CronJobWorkspace.render_prompt()` 调用
`list_unconsumed_answers_for_job()` + `mark_answers_consumed()` 把已回答但
还没喂给过 agent 的问答对注入下一次 prompt。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Optional

from mini_agent.external_input.filelock import ExclusiveFileLock
from mini_agent.utils.atomic_write import atomic_write_jsonl

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"
STATUS_DISMISSED = "dismissed"

# [cron_async_feedback_lifecycle_and_usability_plan.md E1] dismiss 的两种来源：
# 用户在看板手动点"忽略" vs 长期无人回答被维护性 tick 自动关闭。历史记录
# 里没有这个字段的旧数据一律按 "manual" 兜底（`.get(..., DISMISS_REASON_MANUAL)`）。
DISMISS_REASON_MANUAL = "manual"
DISMISS_REASON_STALE_TIMEOUT = "stale_timeout"

# [cron_async_feedback_further_improvements_plan.md F3] agent 调用
# `ask_user_async` 时对问题紧急程度的自我判断——"blocking" 表示这个子
# 任务确实没法在没有答案的情况下继续；"normal"（默认）表示答了更好，
# 但不影响 agent 继续做其它可推进的工作。旧数据没有这个字段一律按
# "normal" 兜底（`.get("urgency") or URGENCY_NORMAL`）。
URGENCY_BLOCKING = "blocking"
URGENCY_NORMAL = "normal"
_VALID_URGENCY = {URGENCY_BLOCKING, URGENCY_NORMAL}


def normalize_urgency(urgency: Optional[str]) -> str:
    return urgency if urgency in _VALID_URGENCY else URGENCY_NORMAL



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
    atomic_write_jsonl(p, records)


def append_question(
    paths: "AgentPaths",
    job_id: str,
    question: str,
    hint: str = "",
    options: Optional[list] = None,
    urgency: Optional[str] = None,
) -> dict:
    """新建一条待回答问题，返回写入的完整记录（含新生成的 question_id）。

    调用方（`ask_user_async` 工具）应先调用 `find_pending_by_fingerprint()`
    查重，确认确实需要新建时再调用本函数——本函数本身不做去重判断，允许
    调用方在明确需要"同一问题再问一次"的场景下（用户已经关闭/忽略了上一条）
    绕过查重直接新建。

    [cron_async_feedback_further_improvements_plan.md F3] `urgency` 传
    `None`/非法值时按 `URGENCY_NORMAL` 兜底，不拒绝调用——这是 agent 自己
    的主观判断，不值得因为传错值让整个提问失败。
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
        "urgency": normalize_urgency(urgency),
    }
    try:
        p = paths.notification_cron_questions
        with ExclusiveFileLock(p):
            records = _load_all(paths)
            records.append(record)
            _write_all(paths, records)
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


def find_or_create_question(
    paths: "AgentPaths",
    job_id: str,
    question: str,
    hint: str = "",
    options: Optional[list] = None,
    *,
    fuzzy_threshold: Optional[float] = 0.82,
    run_id: str = "",
    urgency: Optional[str] = None,
) -> tuple[dict, bool]:
    """[cron_async_feedback_hardening_plan.md D1/D4] 查重 + 建新在同一把锁内
    原子完成，返回 `(record, is_new)`。

    D1：调用方（`ask_user_async`）原先是先 `find_pending_by_fingerprint()`
    再单独 `append_question()`，两次调用各自加锁、之间有空隙——两个线程
    可能同时判定"没有重复，需要新建"，都各自建了一条，造成同一问题被
    发了两条通知。合并成一次加锁内完成查重+建新，消除这个时间窗口。

    D4：精确指纹匹配之外新增一层模糊匹配兜底（规范化文本后用
    `difflib.SequenceMatcher` 算相似度，`fuzzy_threshold` 及以上也判定为
    重复，默认 0.82）。传 `fuzzy_threshold=None` 关闭模糊匹配、只用精确
    匹配（兼容旧行为，供不希望误合并的调用方使用）。

    [cron_async_feedback_further_improvements_plan.md F3] `urgency` 只在
    真正新建记录时生效（`normalize_urgency` 兜底非法值）；命中去重时
    返回的是已存在的记录，**不会**用这次调用的 `urgency` 覆盖已记录的
    值——语义上，去重命中意味着"这其实是同一个问题"，紧急程度应该以
    第一次提出时的判断为准，不应该被后续重复调用的参数悄悄改变。
    """
    p = paths.notification_cron_questions
    with ExclusiveFileLock(p):
        existing = _find_pending_by_fingerprint_locked(paths, job_id, question, fuzzy_threshold)
        if existing is not None:
            return existing, False
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
            # [cron_async_feedback_hardening_plan.md D6] 写入这条问题时
            # 所处的 cron run_id（非 cron 场景下为空字符串）。仅用于事后
            # 审计——跟对应 job 的 CronJobWorkspace.read_state().last_run_id
            # 比较，不一致说明这条问题可能来自一次已经不是"当前最新"的
            # run（比如被 watchdog 判定卡死放弃后，孤儿线程才迟到执行到
            # 这里）。不做写入时拦截，只做可识别标记。
            "run_id": run_id or "",
            "urgency": normalize_urgency(urgency),
        }
        records = _load_all(paths)
        records.append(record)
        _write_all(paths, records)
        return record, True


def _normalize_question_text(text: str) -> str:
    import re
    t = (text or "").strip().lower()
    t = re.sub(r"[\s,.。，、！!?？；;:：'\"“”‘’()（）\-—]+", "", t)
    return t


def _find_pending_by_fingerprint_locked(
    paths: "AgentPaths", job_id: str, question: str, fuzzy_threshold: Optional[float],
) -> Optional[dict]:
    q = (question or "").strip()
    if not q:
        return None
    candidates = [
        d for d in _load_all(paths)
        if d.get("job_id") == job_id and d.get("status") == STATUS_PENDING
    ]
    for d in candidates:
        if (d.get("question") or "").strip() == q:
            return d
    if fuzzy_threshold is not None:
        import difflib
        norm_q = _normalize_question_text(q)
        if norm_q:
            best: Optional[dict] = None
            best_ratio = 0.0
            for d in candidates:
                norm_c = _normalize_question_text(d.get("question") or "")
                if not norm_c:
                    continue
                ratio = difflib.SequenceMatcher(None, norm_q, norm_c).ratio()
                if ratio >= fuzzy_threshold and ratio > best_ratio:
                    best, best_ratio = d, ratio
            if best is not None:
                return best
    return None


def _count_prior_dismissed_matches(
    records: list, job_id: str, question: str, exclude_question_id: str,
    fuzzy_threshold: Optional[float] = 0.82,
) -> int:
    """[cron_async_feedback_further_improvements_plan.md F5] 在**已经加载
    到内存**的 `records`（调用方在同一把锁内 `_load_all()` 得到的快照）里，
    统计同一 `job_id` 下、语义上跟 `question` 相似、状态已经是
    `dismissed` 的历史记录条数（不含 `exclude_question_id` 自己——本函数
    在"即将把某条记录标记为 dismissed"之前调用，此时那条记录在 `records`
    里可能还是 `pending`，也可能因为调用方传入的是刚构造的新记录而根本
    不在 `records` 里，两种情况都需要显式排除，避免自我匹配把计数多算
    一次）。

    复用 D4（`find_or_create_question`）同一套"精确匹配优先，其次
    `_normalize_question_text` + `difflib.SequenceMatcher` 模糊匹配"的
    判定逻辑，语义保持一致：同一个问题换了个说法重新问，依然应该被认成
    "同一件事"。`fuzzy_threshold=None` 时只做精确匹配（预留给未来可能
    需要关闭模糊匹配的调用方，当前所有调用点都用默认值）。

    返回值只是"匹配到的历史条数"，不含"这一次"——调用方拿到这个数后自己
    `+ 1` 得到 `repeat_dismiss_count`（"这是第几次因为同一件事被忽略"）。
    """
    q = (question or "").strip()
    if not q:
        return 0
    candidates = [
        d for d in records
        if d.get("job_id") == job_id
        and d.get("status") == STATUS_DISMISSED
        and d.get("question_id") != exclude_question_id
    ]
    count = 0
    norm_q = _normalize_question_text(q) if fuzzy_threshold is not None else ""
    for d in candidates:
        c = (d.get("question") or "").strip()
        if c == q:
            count += 1
            continue
        if fuzzy_threshold is not None and norm_q:
            import difflib
            norm_c = _normalize_question_text(c)
            if norm_c and difflib.SequenceMatcher(None, norm_q, norm_c).ratio() >= fuzzy_threshold:
                count += 1
    return count


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


def count_questions(
    paths: "AgentPaths", *, status: Optional[str] = None, job_id: Optional[str] = None,
) -> int:
    """[cron_async_feedback_further_improvements_plan.md F1] 纯计数，不
    排序/不切片/不返回记录正文——供看板 tab 角标用。之前看板（E3）用
    `list_pending_questions(limit=200)` 之类的分页接口"探测"数量，既不
    精确（超过 200 条时角标固定卡在"200+"）又浪费（要把 200 条记录的
    question/hint/answer_history 等正文字段全读出来才能数有多少条）。

    `status=None` 时统计全部状态（含 pending/answered/dismissed）之和，
    通常调用方会显式传 `status` 分别统计三种。"""
    result = _load_all(paths)
    if status:
        result = [d for d in result if d.get("status") == status]
    if job_id:
        result = [d for d in result if d.get("job_id") == job_id]
    return len(result)


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
    p = paths.notification_cron_questions
    with ExclusiveFileLock(p):
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
    p = paths.notification_cron_questions
    with ExclusiveFileLock(p):
        records = _load_all(paths)
        matched = 0
        for d in records:
            if d.get("question_id") in ids and not d.get("consumed"):
                d["consumed"] = True
                matched += 1
        if matched:
            _write_all(paths, records)
    return matched


def dismiss_question(
    paths: "AgentPaths", question_id: str, *,
    reason: str = DISMISS_REASON_MANUAL, note: Optional[str] = None,
) -> Optional[dict]:
    """忽略/关闭一条**仍是 pending 状态**的问题——用户在看板点"忽略"，或
    （`reason=DISMISS_REASON_STALE_TIMEOUT` 时）被维护性 tick 判定长期无人
    回答后自动关闭。跟"回答"是两条不同的路径：忽略后 `status` 变为
    `dismissed`，不会再出现在 `list_pending_questions()`（不再打扰用户）或
    `list_pending_question_texts_for_job()`（不再出现在
    `{{unanswered_questions}}` 里提醒 agent），但也不会像回答那样注入
    `{{pending_answers}}`——agent 下次触发看不到这个问题，也看不到任何
    "答案"，就当作它从未被回答过（`ask_user_async` 的精确指纹去重只匹配
    `STATUS_PENDING`，所以同一问题文本被忽略后，agent 如果重新问一遍，
    会被当作全新问题，不会被这条已忽略的记录挡住）。

    只允许忽略 `pending` 状态的问题：已回答的问题要"改答案"应该走
    `submit_answer()`，不应该被"忽略"成一个既不是回答也不是待办的
    中间状态——对已回答的问题调用本函数返回 `None`（跟"找不到"同一个
    错误分支，调用方无需区分）。已经是 `dismissed` 的问题重复调用是
    幂等的（直接返回当前记录，不重复写文件、不覆盖已记录的
    `dismiss_reason`/`dismiss_note`）。

    `reason` 记录进 `dismiss_reason` 字段（`"manual"` | `"stale_timeout"`），
    供看板"已忽略"子面板和 `{{dismissed_questions}}` 渲染时区分展示——
    用户需要知道一个问题是自己主动关掉的，还是系统因为"太久没人回答"替
    他关掉的，这是两种完全不同的语义，不能混为一谈地展示成同一句话。

    [cron_async_feedback_further_improvements_plan.md F2] `note` 是可选的
    忽略原因说明（比如看板下拉框选的"不再需要"/"已通过其它方式解决"等，
    或用户自己填的文字），记录进 `dismiss_note` 字段。旧数据/未传参时
    该字段不存在，`.get("dismiss_note")` 兜底为 `None`。只是采集展示，
    当前没有任何下游逻辑会读取它做判断（留给未来的分析场景用）。

    返回更新后（或已是 dismissed 的）完整记录；`question_id` 不存在，
    或该问题当前是 `answered` 状态，均返回 None。
    """
    p = paths.notification_cron_questions
    with ExclusiveFileLock(p):
        records = _load_all(paths)
        target: Optional[dict] = None
        for d in records:
            if d.get("question_id") == question_id:
                target = d
                break
        if target is None:
            return None
        if target.get("status") == STATUS_DISMISSED:
            return target
        if target.get("status") != STATUS_PENDING:
            return None
        target["status"] = STATUS_DISMISSED
        target["dismiss_reason"] = reason
        if note:
            target["dismiss_note"] = note
        # [cron_async_feedback_further_improvements_plan.md F5] 在写入
        # dismissed 之前，先用同一份 records 快照统计"这个问题之前已经
        # 被语义相似地忽略过几次"，+1 就是这一次。必须在
        # `target["status"] = STATUS_DISMISSED` 之后、`_write_all` 之前
        # 计算——此时 `records`（含 `target` 本身）里其它历史 dismissed
        # 记录都还是原样，`target` 自己已经被排除在统计之外
        # （`exclude_question_id`）。
        target["repeat_dismiss_count"] = 1 + _count_prior_dismissed_matches(
            records, target.get("job_id") or "", target.get("question") or "", question_id,
        )
        target["updated_at"] = time.time()
        _write_all(paths, records)
        return target


def expire_stale_pending_questions(
    paths: "AgentPaths",
    *,
    stale_after_days: float = 14,
    job_id: Optional[str] = None,
    exclude_job_ids: Optional[set] = None,
) -> list[dict]:
    """[cron_async_feedback_lifecycle_and_usability_plan.md E1] 把创建超过
    `stale_after_days` 天、仍然是 `pending`（长期没人回答）的问题自动关闭
    （复用 `dismiss_question` 同一套状态转换，`dismiss_reason` 记为
    `DISMISS_REASON_STALE_TIMEOUT`），避免这类问题在"待我反馈"列表里无限
    堆积——原设计（`cron_async_user_feedback_mechanism_plan.md` §0 非目标）
    明确不做超时机制，理由是"不确定用户是不是就是需要慢慢想"；但实际运行
    后发现反面代价更大：无人问津的旧问题会一直挤占看板"待处理"列表、一直
    出现在 `{{unanswered_questions}}` 里提醒 agent"这里还欠着"，agent 本该
    能自己拿主意的边缘判断也会因为"以为用户还会来看"而一直搁置，形成越
    积越多、谁都不会去清理的心理负担。这里改成"默认给用户留够时间
    （14 天），超过之后系统替用户做主：视为放弃/不再需要这个问题，自动
    关闭并明确告知 agent 和用户"，而不是让问题永远悬着。

    只按 `created_at`（问题第一次被提出的时间）判定，不看 `updated_at`——
    `updated_at` 在 `find_or_create_question()` 命中去重时不会更新（那条
    路径根本不碰这条记录），所以 `created_at` 才是"这个问题挂了多久没人
    理"的准确度量。`stale_after_days` 可配置（由调用方——通常是
    `AutonomousLoop._tick_maintenance()`——从配置读取后传入，默认 14 天）。
    传 `job_id` 时只处理该 job 名下的问题（供 `CronJobExecutor` 需要时按
    job 粒度调用）；不传则处理全部 job（维护性 tick 的常规用法）。

    [cron_async_feedback_further_improvements_plan.md F4] `exclude_job_ids`
    （只在 `job_id` 为 `None` 的"处理全部 job"模式下有意义，跟 `job_id`
    同时传是没有意义的组合，此时以 `job_id` 单独生效为准，`exclude_job_ids`
    被忽略）——供调用方实现"按 job 覆盖阈值"：调用方先对每个设置了专属
    阈值的 job 各自调一次（传各自的 `stale_after_days` + 对应 `job_id`），
    再对"没有专属阈值的其余 job"调一次全局阈值、用 `exclude_job_ids` 排除
    掉刚才已经按专属阈值单独处理过的那些 job，避免它们被全局阈值重复
    处理一遍（重复处理本身不会产生副作用，因为第一轮已经不是 pending 的
    记录不会再被选中，但如果全局阈值比专属阈值更松，会让"专属阈值本该
    更严格更快关闭"的语义被架空，所以必须显式排除，不能依赖"重复处理无
    副作用"这个事实心存侥幸）。

    返回本次被关闭的记录列表（关闭后的完整记录，含 `job_id`/`question`），
    供调用方据此逐条发送"这个问题因长期未回答已自动关闭"的通知——自动
    关闭如果完全静默，用户会困惑"这个问题怎么凭空消失了"，这本身就是一种
    新的可用性缺陷，不能用"减少打扰"的名义制造"消失的问题去哪了"的疑惑。

    异常兜底返回空列表——这是维护性操作，不能因为它失败影响到 cron 主
    流程或问答功能本身的可用性。
    """
    try:
        cutoff = time.time() - stale_after_days * 86400
        p = paths.notification_cron_questions
        with ExclusiveFileLock(p):
            records = _load_all(paths)
            expired: list[dict] = []
            for d in records:
                if d.get("status") != STATUS_PENDING:
                    continue
                if job_id is not None:
                    if d.get("job_id") != job_id:
                        continue
                elif exclude_job_ids and d.get("job_id") in exclude_job_ids:
                    continue
                created_at = d.get("created_at") or 0
                if created_at and created_at < cutoff:
                    d["status"] = STATUS_DISMISSED
                    d["dismiss_reason"] = DISMISS_REASON_STALE_TIMEOUT
                    # [F5] 跟 dismiss_question() 同一套统计逻辑，超时自动
                    # 关闭也算一次"忽略"，同样需要计入 repeat_dismiss_count。
                    d["repeat_dismiss_count"] = 1 + _count_prior_dismissed_matches(
                        records, d.get("job_id") or "", d.get("question") or "", d.get("question_id") or "",
                    )
                    d["updated_at"] = time.time()
                    expired.append(d)
            if expired:
                _write_all(paths, records)
            return expired
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.questions_store.expire_stale_pending_questions")
        return []


def _archive_path(paths: "AgentPaths"):
    p = paths.notification_cron_questions
    return p.parent / (p.stem + ".archive.jsonl")


def archive_old_records(paths: "AgentPaths", *, retention_days: int = 90) -> int:
    """[cron_async_feedback_hardening_plan.md D5] 把超过 `retention_days`
    天的 `answered`/`dismissed` 记录从主文件挪到同目录的
    `cron_questions.archive.jsonl`（只追加，供审计查阅，不参与
    `_load_all()` 的日常读取路径）。`pending` 状态的记录永远不会被归档，
    不管多老——只要还没被回答/忽略就还是"活"的。

    返回归档的记录数。异常兜底返回 0，绝不能因为归档失败影响到问答功能
    本身的可用性（归档是维护性操作，不是关键路径）。
    """
    try:
        p = paths.notification_cron_questions
        cutoff = time.time() - retention_days * 86400
        with ExclusiveFileLock(p):
            records = _load_all(paths)
            keep: list[dict] = []
            archived: list[dict] = []
            for d in records:
                status = d.get("status")
                updated_at = d.get("updated_at") or d.get("created_at") or 0
                if status in (STATUS_ANSWERED, STATUS_DISMISSED) and updated_at < cutoff:
                    archived.append(d)
                else:
                    keep.append(d)
            if not archived:
                return 0
            archive_p = _archive_path(paths)
            archive_p.parent.mkdir(parents=True, exist_ok=True)
            with open(archive_p, "a", encoding="utf-8") as f:
                for d in archived:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
            _write_all(paths, keep)
            return len(archived)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.questions_store.archive_old_records")
        return 0


def purge_questions_for_job(paths: "AgentPaths", job_id: str) -> int:
    """[cron_async_feedback_hardening_plan.md D5] cron job 被删除时调用，
    清掉该 job 名下所有问答记录（不分状态），避免永久遗留的孤儿数据。
    返回删除的记录数。异常兜底返回 0——清理失败不应该阻断 job 删除本身
    这个更重要的操作。"""
    try:
        p = paths.notification_cron_questions
        with ExclusiveFileLock(p):
            records = _load_all(paths)
            keep = [d for d in records if d.get("job_id") != job_id]
            removed = len(records) - len(keep)
            if removed:
                _write_all(paths, keep)
            return removed
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.questions_store.purge_questions_for_job")
        return 0


def list_orphaned_pending_questions(paths: "AgentPaths") -> list[dict]:
    """[cron_async_feedback_hardening_plan.md D6] 找出所有"疑似孤儿线程
    迟到写入"的 pending 问题：记录的 `run_id` 跟对应 job 当前
    `CronJobWorkspace.read_state().last_run_id` 不一致，说明这条问题写入
    时所在的那次 run，已经不是这个 job 最近一次的 run 了——可能是
    watchdog 判定卡死放弃之后，孤儿线程才迟到执行到 `ask_user_async`。

    只做识别，不做任何自动处理（不删除、不隐藏）——看板/审计工具可以
    用这个列表做置灰展示或提示，具体呈现方式留给调用方决定。`run_id`
    为空（非 cron 场景的 `"adhoc"` 分组，或旧数据没有这个字段）的记录
    不参与判定，直接跳过。异常兜底返回空列表。
    """
    try:
        from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
        result = []
        state_cache: dict[str, str] = {}
        for d in list_pending_questions(paths):
            rid = d.get("run_id") or ""
            job_id = d.get("job_id") or ""
            if not rid or not job_id or job_id == "adhoc":
                continue
            if job_id not in state_cache:
                try:
                    state_cache[job_id] = CronJobWorkspace(paths, job_id).read_state().last_run_id or ""
                except Exception:
                    state_cache[job_id] = ""
            if state_cache[job_id] and rid != state_cache[job_id]:
                result.append(d)
        return result
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.questions_store.list_orphaned_pending_questions")
        return []


def list_dismissed_questions(
    paths: "AgentPaths", job_id: Optional[str] = None,
    limit: Optional[int] = None, offset: int = 0,
) -> list[dict]:
    """已忽略问题列表，按 updated_at 倒序，供看板"已忽略"子面板展示
    （可选功能，忽略动作本身不需要用户再看它一眼，但保留可查询入口，
    避免"忽略"变成一个查无对证的黑洞操作）。"""
    result = [d for d in _load_all(paths) if d.get("status") == STATUS_DISMISSED]
    if job_id:
        result = [d for d in result if d.get("job_id") == job_id]
    result.sort(key=lambda d: d.get("updated_at") or 0, reverse=True)
    if offset:
        result = result[offset:]
    if limit is not None:
        result = result[:limit]
    return result


def list_pending_question_texts_for_job(paths: "AgentPaths", job_id: str) -> list[dict]:
    """取出某个 job 下仍是 pending 状态的问题，供 `render_prompt()` 渲染
    `{{unanswered_questions}}` 占位符，提醒 agent 不要重复提问同一个问题。

    [cron_async_feedback_further_improvements_plan.md F3] 排序改为
    "urgency=blocking 的排最前，同一组内部再按 created_at 正序"——
    `{{unanswered_questions}}` 是喂给 agent 自己看的，让它优先看到"哪些
    问题曾经判断为阻塞、还没等到答案"，跟看板"待处理"子 tab（E2，喂给
    用户看，按等待时长排）是两个不同的排序需求，不共用同一份排序逻辑。
    """
    result = [
        d for d in _load_all(paths)
        if d.get("job_id") == job_id and d.get("status") == STATUS_PENDING
    ]
    result.sort(key=lambda d: (
        0 if normalize_urgency(d.get("urgency")) == URGENCY_BLOCKING else 1,
        d.get("created_at") or 0,
    ))
    return result
