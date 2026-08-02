#!/usr/bin/env python3
"""
Browser CDP 技能端到端演示脚本

这是一个完整的示例，展示如何使用 Browser CDP 技能进行网页自动化操作。
运行前请确保已安装依赖：pip install websocket-client requests pillow
"""

import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).parent.parent / "src"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import browser_launch
from src.core import browser_nav
from src.core import browser_extract
from src.core import browser_screenshot
from src.core import browser_input
from src.core import browser_console

def main():
    print("=" * 60)
    print("Browser CDP 技能端到端演示")
    print("=" * 60)

    # 步骤 1: 启动专用浏览器实例
    print("\n[步骤 1] 启动专用浏览器实例...")
    args = browser_launch._make_args(
        name="demo-work",
        start_url="https://httpbin.org/html",
    )
    result = browser_launch.cmd_dedicated(args)
    port = result["port"]
    tab_id = result["tab_id"]
    print(f"  已启动浏览器: port={port}, tab={tab_id}")

    # 步骤 2: 导航到目标页面
    print("\n[步骤 2] 导航到目标页面...")
    browser_nav.goto(port, tab_id, "https://httpbin.org/html")
    print("  页面已加载")

    # 步骤 3: 抓取页面文本内容
    print("\n[步骤 3] 抓取页面文本内容...")
    text_content = browser_extract.extract_text(port, tab_id, max_chars=5000)
    print(f"  抓取到 {len(text_content)} 字符的文本内容")
    print(f"  预览: {text_content[:200]}...")

    # 步骤 4: 获取 HTML 内容
    print("\n[步骤 4] 获取 HTML 内容...")
    html_content = browser_extract.extract_html(port, tab_id)
    print(f"  HTML 内容长度: {len(html_content)} 字符")

    # 步骤 5: 截图并标注（用于看图操作）
    print("\n[步骤 5] 截图并标注...")
    screenshot_path = browser_screenshot.take_screenshot(port, tab_id, annotate=True)
    print(f"  截图已保存至: {screenshot_path}")

    # 步骤 6: 模拟用户交互（点击按钮）
    print("\n[步骤 6] 模拟用户交互...")
    # 这里假设页面上有可点击的元素
    try:
        browser_input.click_by_selector(port, tab_id, "button")
        print("  成功点击了页面上的第一个按钮")
    except Exception as e:
        print(f"  点击失败（可能是页面无按钮）: {e}")

    # 步骤 7: 执行 JavaScript
    print("\n[步骤 7] 执行 JavaScript...")
    js_result = browser_console.execute_js(port, tab_id, "document.title")
    print(f"  页面标题: {js_result}")

    # 步骤 8: 读取 Console 日志
    print("\n[步骤 8] 读取 Console 日志...")
    console_logs = browser_console.get_console_log(port, tab_id)
    print(f"  找到 {len(console_logs)} 条 Console 日志")
    if console_logs:
        print(f"  第一条: {console_logs[0]}")

    # 步骤 9: 关闭浏览器实例
    print("\n[步骤 9] 关闭浏览器实例...")
    browser_launch.stop_dedicated("demo-work")
    print("  浏览器已关闭")

    print("\n" + "=" * 60)
    print("演示完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()