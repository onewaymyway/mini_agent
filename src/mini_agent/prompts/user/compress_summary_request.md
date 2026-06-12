# prompts/user/compress_summary_request.md
#
# 发送给模型，要求对将被压缩的历史对话生成摘要（LLMSummaryStrategy）

Please create a concise but complete summary of the conversation above.
The summary will replace the full conversation history, so it must contain:
1. The user's overall goal
2. What has been accomplished so far (with key details like file paths, commands run, results)
3. Important decisions or findings
4. The current state / what still needs to be done

Format your response as a single paragraph of 150-250 words, in the same
language used in the conversation above.
Do NOT include meta-commentary like "Here is a summary:" — just the summary text.
