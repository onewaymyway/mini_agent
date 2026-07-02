# prompts/user/tool_result_summary_request.md
#
# 发送给摘要模型，请求对某次工具调用的超长输出提炼关键信息。
# 变量：{{ tool_name }} {{ tool_input }} {{ tool_output }}

Tool called: {{ tool_name }}
Tool arguments: {{ tool_input }}

Full tool output below (between the markers). Extract the information
relevant to this tool call as instructed in the system prompt.

<tool_output>
{{ tool_output }}
</tool_output>
