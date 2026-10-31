"""novel_writer/autopilot.py — 托管挡（G4）：自动批准与自动回答，一直运行到暂停/停止/失败/全书完成。

规则：
  * 等待批准（waiting_review）→ 直接批准；
  * 等待回答（waiting_user）→ 每个问题取第一个选项；自由文本题写入「按你的判断处理」；
    完章核查的答复照常转为修改意见，由修改循环执行；
  * 同一步骤自动回答超过 MAX_AUTO_ANSWERS 次仍不通过，就停下来等人，避免无限循环烧 LLM 调用；
  * 每次自动决定都写入运行日志，并记在步骤的 auto_decision 字段里，事后可以审计。
失败（failed）在 G4 下不在托管范围内：不会跳过，也不会无限重试（重试次数由 worker 的 MAX_AUTO_RETRIES 限制）。

不停机挡（G5）= G4 + 失败自动重置：
  * 步骤在 worker 的自动重跑（step_retry）用尽后仍失败 → 重置为 pending（带上失败原因作为重跑提示），
    按递增退避等待后继续运行，不停机；
  * 自动回答超过 MAX_AUTO_ANSWERS 也不再停下，而是清零计数、重置该步骤后继续；
  * 唯一的停止条件：全书完成、作者暂停/停止，或步骤状态是 blocked（执行器未实现，重置也没用）、
    stale 需确认（可能覆盖已确认内容）。
  * 每次重置都写入运行日志与步骤的 auto_resets / last_failure 字段，事后可审计。
"""

from __future__ import annotations

from typing import Any, Dict, List

from novel_writer import state as st
from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json

GEAR = "G4"
RELENTLESS_GEAR = "G5"
MAX_AUTO_ANSWERS = 3
# G5 每次重置失败步骤前的等待秒数（按该步骤已重置次数递增，末项封顶）。给限流/网络故障留恢复时间，
# 也避免确定性错误把 LLM 调用烧成死循环。测试中可改为 0。
RESET_BACKOFF_SEC = (30, 60, 120, 300)
FREE_TEXT_DEFAULT = "（托管模式：无特别要求，请按你的判断处理）"


def _default_answer(question: Dict[str, Any]) -> str:
    options = question.get("options") or []
    if options:
        return str(options[0])
    return FREE_TEXT_DEFAULT


def default_answers(questions: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(q.get("id") or "q"): _default_answer(q) for q in questions}


# 托管挡下，因上游变化而 stale 的步骤，哪些可以自动重跑（而不是停下来等作者确认）。
# 原则：重跑只覆盖步骤自己的派生文件或报告，不改写正文，也不改写全书设定。
#   * 章节准备类（CAT_CHAPTER_PREP）：word_quota / plot_tasks / context_pack / style_hint，都是派生数据；
#   * 幕内分析报告：act_review / act_style / act_pacing / act_theme / act_worldcheck / act_ideas，只产生报告。
# 不在此列的（章节写作与修订、归档同步、幕抽取与应用、蓝图与规划、角色与世界构建、最终审稿）仍然停下来确认。
AUTO_REFRESH_PREFIXES = ("act_review:", "act_style:", "act_pacing:", "act_theme:", "act_worldcheck:", "act_ideas:")
AUTO_REFRESH_SETTING = "autopilot_auto_refresh"


def stale_decision(step: S.StepDef, settings: Dict[str, Any]) -> str:
    """托管挡遇到 stale 步骤时的决定：'refresh'（自动重跑并记入日志）或 'confirm'（停下来等作者）。"""
    if not (settings or {}).get(AUTO_REFRESH_SETTING, True):
        return "confirm"
    if step.category == S.CAT_CHAPTER_PREP or step.id.startswith(AUTO_REFRESH_PREFIXES):
        return "refresh"
    return "confirm"


def reset_failed(ws, step: S.StepDef) -> float:
    """G5：把失败的步骤重置为 pending 以便继续运行。返回重置前应等待的秒数（由 worker 可中断地等待）。"""
    entry = st.get_step_state(ws, step.id)
    n = int(entry.get("auto_resets") or 0) + 1
    error = str(entry.get("error") or "")
    delay = RESET_BACKOFF_SEC[min(n - 1, len(RESET_BACKOFF_SEC) - 1)] if RESET_BACKOFF_SEC else 0
    st.log_line(ws, f"♻ 不停机：{step.id} 失败，{delay} 秒后自动重置并继续（第 {n} 次重置）：{error[:120]}")
    st.reset_step(ws, step.id)
    st.update_step(ws, step.id, auto_resets=n, last_failure=error[:800] or None,
                   retry_hint=error[:800] or None, auto_answers=0,
                   auto_decision=f"不停机：第 {n} 次自动重置"[:300])
    return float(delay)


def handle_waiting(ws, step: S.StepDef, status: str, relentless: bool = False) -> bool:
    """处理一个等待中的步骤。返回 True 表示已自动处理（worker 应继续循环）；False 表示需要人。

    relentless（G5）：自动回答达到上限时不停下，而是清零计数、重置该步骤后继续。"""
    from novel_writer.runner import _answers_file

    entry = st.get_step_state(ws, step.id)
    if status == st.ST_WAITING_REVIEW:
        st.update_step(ws, step.id, status=st.ST_PASSED, approved_at=st.now_iso(),
                       auto_decision="托管：自动批准")
        st.log_line(ws, f"🤖 托管：自动批准 {step.id}")
        return True

    if status != st.ST_WAITING_USER:
        return False
    count = int(entry.get("auto_answers") or 0)
    if count >= MAX_AUTO_ANSWERS and relentless:
        st.log_line(ws, f"♻ 不停机：{step.id} 已自动回答 {count} 次仍未通过，重置该步骤后继续")
        st.reset_step(ws, step.id)
        st.update_step(ws, step.id, auto_answers=0, auto_decision="不停机：自动回答达上限，已重置")
        return True
    if count >= MAX_AUTO_ANSWERS:
        st.log_line(ws, f"🤖 托管：{step.id} 已自动回答 {count} 次仍未通过，停下来等你处理")
        return False

    questions = entry.get("needs_user") or []
    answers = default_answers(questions)
    summary = "；".join(f"{q.get('question', '')[:20]} → {answers.get(str(q.get('id')), '')}" for q in questions)
    if step.id.endswith(".review"):
        from novel_writer import chapter_ops as C

        C.route_review_answers(ws, step.id, answers, questions)  # 转为修改意见，修改循环接手
    else:
        atomic_write_json(_answers_file(ws, step.id), {"answers": answers, "answered_at": st.now_iso()})
        st.reset_step(ws, step.id)
    st.update_step(ws, step.id, auto_answers=count + 1, auto_decision=f"托管：自动回答 {summary}"[:300])
    st.log_line(ws, f"🤖 托管：自动回答 {step.id}（第 {count + 1}/{MAX_AUTO_ANSWERS} 次）：{summary[:120]}")
    return True
