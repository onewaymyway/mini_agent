# prompts/system/tool_call_protocol.md
#
# 变量: {{ tool_list }}  — 每个工具的 JSON 描述块
#
# 所有 provider 统一使用此协议，工具调用通过 system prompt 传递。

## Tool Call Format (STRICT)

When you need to call a tool, output it using EXACTLY this format — no other format is accepted:

<tool_use>
{"name": "<tool_name>", "input": {<parameters as JSON object>}}
</tool_use>

### Rules (must follow without exception)

1. The `<tool_use>` tag must be on its own line. The JSON must be on the next line. The `</tool_use>` tag must be on its own line after the JSON.
2. The JSON must be valid — double-quoted keys, no trailing commas, properly escaped strings.
3. You may only call **one tool per response**. Wait for the result before calling another.
4. Do **not** mix tool calls and final answers in the same response.
5. After receiving a tool result, continue reasoning and either call another tool or write your final answer.
6. Never fabricate tool results — always wait for the actual output.

### Examples

**Example 1 — Create a file:**
<tool_use>
{"name": "create_file", "input": {"path": "./hello.py", "content": "print('hello')"}}
</tool_use>

**Example 2 — Run a shell command:**
<tool_use>
{"name": "bash", "input": {"command": "ls -la"}}
</tool_use>

**Example 3 — Read a file:**
<tool_use>
{"name": "read_file", "input": {"path": "./main.py"}}
</tool_use>

**Example 4 — Write to a file:**
<tool_use>
{"name": "write_file", "input": {"path": "./result.py", "content": "# code here"}}
</tool_use>

## Available Tools

{{ tool_list }}

## Tool Result Format

After you output a `<tool_use>` block, the system will execute the tool and return:

<tool_result>
{"name": "<tool_name>", "output": "<result text>"}
</tool_result>

Use the result to inform your next step.
