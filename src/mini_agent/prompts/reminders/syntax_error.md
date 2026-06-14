---
name: syntax_error
trigger_event: tool_error
condition:
  error_pattern: "SyntaxError|IndentationError|unexpected token|Parse error|JSONDecodeError"
inject_as: user
priority: 76
enabled: true
---

**[Reminder] 代码语法/解析错误处理建议：**

1. 仔细检查错误信息中的行号和列号，定位到具体位置
2. Python 缩进错误：确保统一使用空格（4个）或 Tab，不要混用
3. JSON 解析错误：使用 `python3 -m json.tool <file>` 验证 JSON 合法性
4. 检查括号、引号是否匹配闭合
5. 若是从其他文件复制的代码，注意编码问题（特殊字符、BOM 头）
6. 使用 `python3 -m py_compile <file>` 预检查 Python 语法
