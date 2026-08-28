"""
tools/ask_user_async.py — 异步用户反馈工具（cron 任务专用，不阻塞执行）

设计背景见 next_doc/cron_async_user_feedback_mechanism_plan.md。

与 `tools/user_input.py` 的 `ask_user`/`ask_user_confirm`/`ask_user_choice`
不是同一条路：那三个工具通过 `interaction.ask()` **同步阻塞**等待回答，
服务于"用户就在旁边、交互式对话"的场景。cron 任务由
`AutonomousLoop._tick_passive()` 在后台无人值守触发，用同步阻塞的工具会
一直卡到超时，白白占用这次触发的执行时间，也不会真正等到答案。

`ask_user_async` 反过来：调用后立刻返回，不等待任何人回答，把问题写进
`notification/questions_store.py` 并通过现有 `NotificationDispatcher` 发一条
kanban 通知；agent 应据此把相关子任务标记为搁置，转去做其它可推进的工作。
用户在看板"待我反馈"面板异步作答；下次同一个 cron job 被调度触发时，
`CronJobWorkspace.render_prompt()` 会自动把已回答的问答对通过
`{{pending_answers}}` 占位符注入 prompt，接着搁置的工作继续。

job_id 来源：`evolution/cron_context.py` 的 thread-local（由
`CronJobExecutor.run_job()` 在 cron 执行专属线程上设置）；非 cron 场景
（交互式对话里直接调用这个工具）下退化为固定分组 `"adhoc"`——问题仍会被
正常创建和通知，只是不会被任何 `render_prompt()` 自动续接消费。
"""

from __future__ import annotations

import json
import threading as _threading
from pathlib import Path
from typing import Callable, Optional

from . import tool

# ── 模块级"当前项目根目录"提供者（thread-local，与 tools/evolution.py /
#    tools/workdir_knowledge.py 的 set_project_root_provider 同款写法）──────

_project_root_local = _threading.local()


def set_project_root_provider(provider: Optional[Callable[[], Path]]) -> None:
    """由 Agent.__init__ 调用，为当前线程注册一个返回 cfg.project_root 的回调。"""
    _project_root_local.provider = provider


def _get_project_root() -> Optional[Path]:
    provider = getattr(_project_root_local, "provider", None)
    if provider is None:
        return None
    try:
        return provider()
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.tools.ask_user_async._get_project_root")
        return None


def _format_options_hint(options: Optional[list]) -> str:
    if not options:
        return ""
    return "参考选项（仅供参考，可自由文本回答）：" + "；".join(str(o) for o in options)


@tool(
    name="ask_user_async",
    description=(
        "Ask the user a question WITHOUT waiting for their answer — use this in cron/background "
        "jobs where no one is watching in real time. The call returns immediately with a "
        "pending question_id; it never blocks and never times out waiting for a response. "
        "After calling this, treat the sub-task that needed the answer as SET ASIDE and move on "
        "to other work you can still make progress on this run (or finish this run if nothing "
        "else is actionable). The question is posted to the user's dashboard; once they answer "
        "(asynchronously, could be minutes or days later), the answer will automatically be "
        "included in the prompt the next time this same cron job is triggered — you do not need "
        "to poll or wait for it yourself. Do not call this again with the same question text on "
        "the same job while a previous call is still unanswered (it will just be deduplicated). "
        "Do NOT use this for interactive conversations where a human is actively chatting with "
        "you right now — use ask_user/ask_user_confirm/ask_user_choice for that instead."
    ),
    schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user (displayed prominently on their dashboard)",
            },
            "hint": {
                "type": "string",
                "description": "Optional hint or context shown below the question",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of suggested answers, shown to the user as quick reference "
                    "only — the user can still type a free-text answer that doesn't match any "
                    "option, this is NOT a strict enum."
                ),
            },
        },
        "required": ["question"],
    },
    requires_approval=False,
)
def ask_user_async(question: str, hint: str = "", options: Optional[list] = None) -> str:
    """向用户异步提问，立刻返回 question_id，不等待回答。"""
    from mini_agent.evolution.cron_context import get_current_cron_job_id, get_current_cron_run_id
    from mini_agent.notification import questions_store
    from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage

    job_id = get_current_cron_job_id()
    run_id = get_current_cron_run_id()
    project_root = _get_project_root() or Path.cwd()

    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(project_root)

    # [cron_async_feedback_hardening_plan.md D1+D4+D6] 查重+建新合并为一次
    # 加锁操作。fuzzy_threshold 默认开启（0.82）：LLM 每次生成的问题措辞
    # 几乎不可能逐字相同，精确匹配去重在真实场景下形同虚设，导致同一语义
    # 问题反复触发都能绕过去重、看板被刷屏。规范化文本后用相似度兜底，
    # 精确匹配仍然优先尝试。run_id 一并记录，供事后识别孤儿线程迟到写入
    # （见 cron_context.set_current_cron_run_id 的说明）。
    record, is_new = questions_store.find_or_create_question(
        paths, job_id, question, hint=hint, options=options, run_id=run_id,
    )

    if is_new:
        try:
            body_lines = [question]
            if hint:
                body_lines.append(f"\n提示：{hint}")
            opt_hint = _format_options_hint(options)
            if opt_hint:
                body_lines.append(f"\n{opt_hint}")
            dispatcher = NotificationDispatcher(paths)
            dispatcher.dispatch(NotificationMessage(
                title=f"任务「{job_id}」需要你的反馈",
                body="\n".join(body_lines),
                source="cron_question",
                meta={"job_id": job_id, "question_id": record["question_id"]},
            ))
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.tools.ask_user_async.ask_user_async")

    return json.dumps({
        "status": "pending",
        "question_id": record["question_id"],
        "deduplicated": not is_new,
        "note": (
            "This question has been posted asynchronously. Do not wait for an answer — "
            "set this sub-task aside and continue with other work, or finish this run. "
            "The answer (once given) will be included automatically the next time this "
            "job runs."
        ),
    }, ensure_ascii=False)


__all__ = ["ask_user_async", "set_project_root_provider"]
