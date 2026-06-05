# prompts/user/compact_history.md
#
# 发送给模型，要求压缩对话历史

Please provide a concise summary of our conversation so far, structured as follows:

1. **Goal** — What the user is trying to accomplish
2. **Decisions made** — Key technical choices and the reasoning behind them
3. **Changes applied** — Files modified, created, or deleted (with a one-line description each)
4. **Current state** — Where things stand right now
5. **Pending tasks** — Any open items or next steps that were discussed

Be brief and factual. This summary will replace the full conversation history to free up context.
