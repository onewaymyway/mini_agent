"""
browser-core/impl/tools_impl.py — 本 skill 对外暴露给通用引擎的唯一约定入口。

`mini_agent.skills.generative_capability.real_tools.build_default_tool_executor()`
（项目侧通用机制，阶段十四新增，见其文件头）会在构造 tool_executor 时，对
当前 `capability_call` 目标 skill 声明的 `explorer.base_tools` 逐个查找
`<该静态 skill 目录>/impl/tools_impl.py`，若存在则动态加载其中的
`TOOL_IMPLEMENTATIONS: dict[str, Callable[[dict], dict]]` 并合并进最终的
分发表。这是"skill 具体功能代码留在 skill 目录、项目代码只保留通用机制"
这条原则在 browser-core 上的落地方式：项目代码完全不知道 browser_navigate/
browser_click 等工具具体怎么实现，只知道"去这个约定路径找一份映射表"。

本文件本身不实现任何浏览器操作逻辑，只做导出，真正的实现在
`browser_core_impl.py`。
"""
from __future__ import annotations

from browser_core_impl import (
    browser_click,
    browser_close_session,
    browser_extract_content,
    browser_get_debug_snapshot,
    browser_get_page_source,
    browser_list_sessions,
    browser_navigate,
    browser_screenshot_annotated,
    browser_scroll,
    browser_type,
    browser_wait_for_selector,
)

TOOL_IMPLEMENTATIONS = {
    "browser_navigate": browser_navigate,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_scroll": browser_scroll,
    "browser_wait_for_selector": browser_wait_for_selector,
    "browser_extract_content": browser_extract_content,
    "browser_screenshot_annotated": browser_screenshot_annotated,
    # 阶段十六新增：调试原语
    "browser_get_page_source": browser_get_page_source,
    "browser_get_debug_snapshot": browser_get_debug_snapshot,
    # 阶段十八新增：调试浏览器会话的列举/关闭（见 browser_core_impl.py 对应
    # 函数的说明——排查"auto 模式复用了旧的无头会话，看不到新窗口"这类问题）
    "browser_list_sessions": browser_list_sessions,
    "browser_close_session": browser_close_session,
}
