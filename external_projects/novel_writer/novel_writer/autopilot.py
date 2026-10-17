"""novel_writer/autopilot.py — 托管挡（G4）：自动批准与自动回答，一直运行到暂停/停止/失败/全书完成。

规则：
  * 等待批准（waiting_review）→ 直接批准；
  * 等待回答（waiting_user）→ 每个问题取第一个选项；自由文本题写入「按你的判断处理」；
    完章核查的答复照常转为修改意见，由修改循环执行；
  * 同一步骤自动回答超过 MAX_AUTO_ANSWERS 次仍不通过，就停下来等人，避免无限循环烧 LLM 调用；
  * 每次自动决定都写入运行日志，并记在步骤的 auto_decision 字段里，事后可以审计。
失败（failed）不在托管范围内：不会跳过，也不会无限重试（重试次数由 worker 的 MAX_AUTO_RETRIES 限制）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from novel_writer import state as st
from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json

GEAR = "G4"
MAX_AUTO_ANSWERS = 3
FREE_TEXT_DEFAULT = "（托管模式：无特别要求，请按你的判断处理）"


def _default_answer(question: Dict[str, Any]) -> str:
    options = question.get("options") or []
    if options:
        return str(options[0])
    return FREE_TEXT_DEFAULT


def default_answers(questions: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(q.get("id") or "q"): _default_answer(q) for q in questions}


def handle_waiting(ws, step: S.StepDef, status: str) -> bool:
    """处理一个等待中的步骤。返回 True 表示已自动处理（worker 应继续循环）；False 表示需要人。"""
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
