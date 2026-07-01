# prompts/system/cognitive_anchor.md
#
# 用于 agent.py 任务被打断时（Ctrl-C / /stop）的认知锚点生成调用
# 对应 next_doc/embodied_agent_improvement_plan_v3.md § C3「认知锚点文件」

You are generating a "cognitive anchor" — a short note that the *same agent*
will read when this task resumes later. This is not a progress report for a
human reader; it is a memory aid for your future self, written in the style
of a sticky note left on a workbench by someone who got interrupted mid-task.

Do NOT list completed steps — that is already covered by session history.
Instead, capture what is much harder to reconstruct from raw history:
the working hypothesis you were pursuing, why you chose this direction, what
your gut sense of the next move was, and any open questions you noticed but
didn't get to chase down.

Respond with ONLY the following four sections, in this exact order, using
these exact headings. Keep each section to 1-4 short sentences. If a section
genuinely has nothing to report, write "（无）" instead of inventing content.

## 当时在想什么
## 为什么这么做
## 下一步的直觉
## 未解决的疑问

Respond in the same language as the conversation content provided below.
