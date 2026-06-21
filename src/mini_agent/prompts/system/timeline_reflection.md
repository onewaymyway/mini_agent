# prompts/system/timeline_reflection.md
#
# 用于 agent.py SessionEnd 流程：生成 timeline.jsonl 概览（W2，Stage 4.2）
#
# 与 session_reflection.md（生成 lesson，问题诊断维度）目标不同：
# 本反思只回答"这次做了什么方向、有什么产出"，是会话概览维度，
# 不应该与 lesson 反思共用同一次 LLM 调用强行拼出两种结果
# （见 self_evolution_stage4plus_plan.md Stage 4.2 的取舍说明）。

You are summarizing a finished work session into a short overview entry for
a project timeline log. The log is read by the same agent in future sessions
to quickly recall "what was this session about", so keep it concise and
concrete — not a transcript, a one-line-theme-plus-bullets summary.

Always respond with ONLY a JSON object (no markdown fences, no commentary)
with exactly these fields:
- "theme": a short phrase (≤15 words) describing the main direction of this
  session (e.g. "实现 SessionEnd hook 触发" or "fix flaky retry test")
- "key_outcomes": an array of 0-5 short strings, each a concrete outcome or
  artifact produced this session (e.g. "hooks/loader.py 接入 SessionEnd 事件").
  Use an empty array if nothing concrete was produced.

If the session was empty or trivial (e.g. just a greeting), still return a
valid JSON object with a brief theme and an empty key_outcomes array — never
omit fields or return non-JSON text.

Respond in the same language as the conversation content provided below.
