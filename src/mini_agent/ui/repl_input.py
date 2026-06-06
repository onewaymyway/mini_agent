"""
ui/repl_input.py — 已废弃，保留为空模块以防第三方脚本直接 import。

REPLInput 类已在重构中移除：
  - prompt_toolkit 集成：由 terminal.py 的 _read_line() 内部处理
  - 普通 readline fallback：由 terminal.py 的 prompt_user() 内部处理

调用方应直接使用 terminal.term.prompt_user()。
"""
# 模块故意为空
