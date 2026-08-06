"""
Playwright 集成示例脚本

演示如何使用 PlaywrightSession 进行网页抓取和自动化操作。
"""
import sys
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from src.core.playwright_session import PlaywrightSession, PlaywrightConfig


def demo_basic_navigation():
    """基础导航示例"""
    config = PlaywrightConfig(headless=True)
    with PlaywrightSession(config) as session:
        # 打开百度
        session.goto('https://www.baidu.com')
        print(f"页面标题: {session._page.title()}")
        
        # 截图
        session.screenshot('output/baidu.png')
        
        # 提取文本
        text = session.extract_text()
        print(f"页面文本长度: {len(text)} 字符")
        
        # 提取链接
        links = session.extract_links()
        print(f"找到 {len(links)} 个链接")


def demo_search():
    """搜索示例"""
    config = PlaywrightConfig(headless=True)
    with PlaywrightSession(config) as session:
        # 打开百度
        session.goto('https://www.baidu.com')
        
        # 查找搜索框并输入
        search_box = session._page.query_selector('#kw')
        if search_box:
            search_box.fill('Python 编程')
            # 点击搜索按钮
            search_btn = session._page.query_selector('#su')
            if search_btn:
                search_btn.click()
                
                # 等待结果加载
                session.wait_for('networkidle', idle_timeout=2.0)
                
                # 提取搜索结果
                results = session._page.query_selector_all('.result')
                print(f"找到 {len(results)} 个搜索结果")
                
                # 截图
                session.screenshot('output/search_results.png')


def demo_scroll_and_extract():
    """滚动加载内容示例"""
    config = PlaywrightConfig(headless=True)
    with PlaywrightSession(config) as session:
        session.goto('https://www.zhihu.com')
        
        # 滚动页面
        for i in range(3):
            session.scroll('down', amount=800)
            session.wait_for('networkidle', idle_timeout=1.0)
        
        # 提取内容
        text = session.extract_text()
        print(f"提取文本长度: {len(text)} 字符")


def demo_js_execution():
    """执行 JavaScript 示例"""
    config = PlaywrightConfig(headless=True)
    with PlaywrightSession(config) as session:
        session.goto('https://www.baidu.com')
        
        # 执行 JS 获取页面信息
        info = session.evaluate('''
            () => {
                return {
                    title: document.title,
                    url: window.location.href,
                    elements: document.querySelectorAll('*').length,
                    images: document.querySelectorAll('img').length
                };
            }
        ''')
        print(f"页面信息: {info}")


if __name__ == '__main__':
    print("=== Playwright 集成示例 ===\n")
    
    print("1. 基础导航测试...")
    demo_basic_navigation()
    
    print("\n2. 搜索示例...")
    demo_search()
    
    print("\n3. 滚动加载示例...")
    demo_scroll_and_extract()
    
    print("\n4. JS 执行示例...")
    demo_js_execution()
    
    print("\n=== 所有示例完成 ===")
