#!/usr/bin/env python
"""
知乎热榜抓取自动化脚本

通过浏览器 CDP 抓取知乎热榜内容，支持两种模式：
1. 免登录模式：抓取知乎"发现"页面（/explore），获取近期热点和潜力问题
2. 登录模式：抓取知乎热榜页面（/hot），需要浏览器已登录知乎账号

用法:
    python zhihu_hot.py --mode discover          # 免登录发现页
    python zhihu_hot.py --mode hot               # 登录态热榜（需先登录）
    python zhihu_hot.py --mode auto              # 自动检测（优先热榜，失败则降级发现页）
    python zhihu_hot.py --port 9333 --max-items 30
    python zhihu_hot.py --headless --output-dir ./zhihu_hot_results

示例:
    python zhihu_hot.py --mode discover
    python zhihu_hot.py --mode hot --max-items 50
    python zhihu_hot.py --auto --no-detail
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

# 导入 baidu_search 模块复用其函数
sys.path.insert(0, str(Path(__file__).parent))
from src.searchers.baidu_search import (
    ensure_browser, get_random_ua, run_cmd, PYTHON_CMD, SKILL_DIR
)


# ========== 知乎热榜专用配置 ==========
ZHIHU_HOT_OUTPUT_DIR = SKILL_DIR / "search_results"


def extract_hot_items_from_discover(session, max_items: int = 30) -> List[Dict]:
    """从知乎发现页面提取热点问题和潜力问题。
    
    经验总结:
    - 知乎发现页面（/explore）不需要登录即可查看
    - 包含"近期热点"、"潜力好问题"、"最新专题"、"圆桌讨论"等板块
    - 通过提取所有包含 /question/ 的链接，去重后获取热点列表
    - 页面结构可能变化，需要灵活选择器
    
    Args:
        session: CDP session 对象
        max_items: 最大提取数量
        
    Returns:
        热点列表，每项包含 title, url, meta 等信息
    """
    js_code = """
    (() => {
      const links = Array.from(document.querySelectorAll('a'))
        .filter(a => a.href.includes('/question/'));
      const results = [];
      const seen = new Set();
      
      for (const a of links) {
        if (seen.has(a.href)) continue;
        seen.add(a.href);
        
        const parent = a.closest('div');
        const meta = parent ? parent.textContent : '';
        
        results.push({
          title: a.textContent.trim().substring(0, 150),
          url: a.href,
          meta: meta.substring(0, 300)
        });
        
        if (results.length >= %d) break;
      }
      
      return JSON.stringify(results);
    })()
    """ % max_items
    
    result = session.eval_js(js_code, await_promise=True)
    return json.loads(result)


def extract_hot_items_from_hot_page(session, max_items: int = 50) -> List[Dict]:
    """从知乎热榜页面提取热点内容（需要登录态）。
    
    经验总结:
    - 知乎热榜页面（/hot）需要登录才能访问
    - 如果未登录会跳转到登录页（/signin?next=/hot）
    - 可以通过检测 URL 判断是否登录成功
    - 热榜页面结构更复杂，包含排名、热度值、回答数等元数据
    
    Args:
        session: CDP session 对象
        max_items: 最大提取数量
        
    Returns:
        热点列表，每项包含 rank, title, hot_value, url, answer_count 等信息
    """
    # 先检查当前页面状态
    state_check = """
    (() => {
      return {
        url: window.location.href,
        title: document.title,
        isHotPage: window.location.pathname === '/hot' || window.location.pathname === '/topstory/hot',
        isSigninPage: window.location.pathname === '/signin'
      };
    })()
    """
    
    state = session.eval_js(state_check, await_promise=True)
    
    if state.get('isSigninPage'):
        print("  [警告] 检测到登录页，知乎热榜需要登录才能访问")
        return []
    
    if not state.get('isHotPage'):
        print(f"  [警告] 当前页面不是热榜页面：{state.get('url')}")
        return []
    
    # 提取热榜内容
    js_code = """
    (() => {
      const items = Array.from(document.querySelectorAll('[class*=HotItem], [class*=hot-item], .HotItem-card'));
      const results = [];
      
      for (const item of items) {
        const rankEl = item.querySelector('[class*=rank], .Rank, .rank');
        const titleEl = item.querySelector('a[href*=question]');
        const hotValueEl = item.querySelector('[class*=hotValue], .hot-value, .index-hot');
        const answerEl = item.querySelector('[class*=answerCount], .answer-count');
        
        if (!titleEl) continue;
        
        results.push({
          rank: rankEl ? rankEl.textContent.trim() : '',
          title: titleEl.textContent.trim().substring(0, 150),
          url: titleEl.href,
          hot_value: hotValueEl ? hotValueEl.textContent.trim() : '',
          answer_count: answerEl ? answerEl.textContent.trim() : ''
        });
        
        if (results.length >= %d) break;
      }
      
      return JSON.stringify(results);
    })()
    """ % max_items
    
    result = session.eval_js(js_code, await_promise=True)
    return json.loads(result)


def save_zhihu_hot_results(items: List[Dict], mode: str, output_dir: Path, 
                           query: str = "知乎热榜"):
    """保存知乎热榜结果到文件。
    
    生成文件:
    - zhihu_hot_<mode>_<timestamp>.json — 完整结构化数据
    - zhihu_hot_<mode>_<timestamp>.md — 人类可读的 Markdown 报告
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    safe_mode = mode.replace(' ', '_')
    
    # 保存 JSON
    json_file = output_dir / f"zhihu_hot_{safe_mode}_{timestamp}.json"
    all_data = {
        'mode': mode,
        'query': query,
        'extract_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_items': len(items),
        'items': items
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"[保存] JSON: {json_file}")
    
    # 保存 Markdown 报告
    md_file = output_dir / f"zhihu_hot_{safe_mode}_{timestamp}.md"
    lines = []
    lines.append(f"# 知乎热榜抓取报告")
    lines.append("")
    lines.append(f"> 抓取时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 模式：{mode}")
    lines.append(f"> 总条目数：{len(items)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    if items:
        lines.append("## 热榜内容")
        lines.append("")
        
        if mode == 'hot' and items[0].get('rank'):
            # 热榜模式（有排名）
            lines.append("| # | 排名 | 问题 | 热度 | 回答数 |")
            lines.append("|---|------|------|------|--------|")
            for item in items:
                rank = item.get('rank', '')
                title = item.get('title', '')[:50]
                hot = item.get('hot_value', '')
                answers = item.get('answer_count', '')
                url = item.get('url', '')
                lines.append(f"| {rank} | {rank} | [{title}]({url}) | {hot} | {answers} |")
        else:
            # 发现页模式（无排名）
            lines.append("| # | 问题 | 元数据 |")
            lines.append("|---|------|--------|")
            for i, item in enumerate(items, 1):
                title = item.get('title', '')[:40]
                meta = item.get('meta', '')[:60]
                url = item.get('url', '')
                lines.append(f"| {i} | [{title}]({url}) | {meta} |")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 详细列表
        lines.append("## 详细内容")
        lines.append("")
        for i, item in enumerate(items, 1):
            lines.append(f"### {i}. {item.get('title', '未知')}")
            lines.append("")
            lines.append(f"- **链接**: {item.get('url', '')}")
            if item.get('rank'):
                lines.append(f"- **排名**: {item.get('rank')}")
            if item.get('hot_value'):
                lines.append(f"- **热度**: {item.get('hot_value')}")
            if item.get('answer_count'):
                lines.append(f"- **回答数**: {item.get('answer_count')}")
            if item.get('meta'):
                lines.append(f"- **元数据**: {item.get('meta')[:200]}")
            lines.append("")
    else:
        lines.append("## 未获取到内容")
        lines.append("")
        lines.append("可能原因:")
        lines.append("- 知乎热榜需要登录才能访问（热榜模式）")
        lines.append("- 页面结构发生变化，需要更新选择器")
        lines.append("- 网络连接问题")
        lines.append("")
    
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"[保存] Markdown: {md_file}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=['discover', 'hot', 'auto'], default='auto',
                       help="抓取模式：discover(发现页免登录), hot(热榜需登录), auto(自动检测) (默认：auto)")
    parser.add_argument("--max-items", type=int, default=30, 
                       help="最大提取条目数 (默认：30)")
    parser.add_argument("--output-dir", default=str(ZHIHU_HOT_OUTPUT_DIR), 
                       help="输出目录")
    parser.add_argument("--port", type=int, default=9333, 
                       help="CDP 调试端口 (默认：9333)")
    parser.add_argument("--name", default="zhihu_session", 
                       help="浏览器实例名称（默认 zhihu_session，与其他知乎脚本/登录态共用，避免误开新实例）")
    parser.add_argument("--headless", action="store_true", 
                       help="无头模式")
    parser.add_argument("--wait-timeout", type=int, default=20, 
                       help="页面等待超时秒数")
    parser.add_argument("--no-detail", action="store_true", 
                       help="仅输出列表，不保存详细报告")
    
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"知乎热榜抓取自动化")
    print(f"模式：{args.mode}")
    print(f"最大条目：{args.max_items}")
    print(f"输出目录：{args.output_dir}")
    print(f"{'='*60}")
    
    try:
        # 1. 确保浏览器运行
        user_agent = get_random_ua()
        browser_info = ensure_browser(
            port=args.port, name=args.name, headless=args.headless,
            start_url="https://www.zhihu.com", user_agent=user_agent
        )
        port = browser_info["port"]
        tab_id = browser_info["tab_id"]
        
        # 2. 连接 CDP session
        from src.core.cdp_client import connect_tab, list_tabs
        tabs = list_tabs(port=port)
        tab = None
        for t in tabs:
            if t['id'] == tab_id:
                tab = t
                break
        
        if not tab:
            print("[错误] 未找到浏览器 tab!")
            return 1
        
        session = connect_tab(tab, port=port)
        
        # 3. 根据模式选择目标页面
        mode = args.mode
        def navigate_to(url):
            """导航到指定 URL"""
            session.send('Page.navigate', {'url': url})
            time.sleep(2)
        
        if mode == 'auto':
            # 先尝试热榜，失败则降级发现页
            print("\n[自动模式] 优先尝试热榜页面...")
            navigate_to("https://www.zhihu.com/hot")
            time.sleep(3)
            
            # 检查是否成功
            check_js = """
            (() => {
              return window.location.pathname === '/signin';
            })()
            """
            is_signin = session.eval_js(check_js, await_promise=True)
            
            if is_signin:
                print("  [降级] 热榜需要登录，切换到发现页模式")
                mode = 'discover'
                navigate_to("https://www.zhihu.com/explore")
            else:
                print("  [成功] 热榜页面可访问")
                mode = 'hot'
        elif mode == 'hot':
            print("\n[热榜模式] 导航到热榜页面...")
            navigate_to("https://www.zhihu.com/hot")
        else:  # discover
            print("\n[发现页模式] 导航到发现页面...")
            navigate_to("https://www.zhihu.com/explore")
        
        # 等待页面加载
        time.sleep(3)
        
        # 4. 提取热点内容
        print(f"\n[提取] 正在提取{mode}页面内容...")
        if mode == 'hot':
            items = extract_hot_items_from_hot_page(session, args.max_items)
        else:
            items = extract_hot_items_from_discover(session, args.max_items)
        
        if not items:
            print("[警告] 未提取到热点内容")
            if mode == 'hot':
                print("  提示：知乎热榜需要登录才能访问，尝试使用 --mode discover")
            return 1
        
        print(f"[成功] 提取到 {len(items)} 条热点内容")
        
        # 5. 保存结果
        if not args.no_detail:
            save_zhihu_hot_results(
                items=items, mode=mode,
                output_dir=Path(args.output_dir)
            )
        
        # 6. 打印摘要
        print(f"\n{'='*60}")
        print(f"抓取完成！")
        print(f"  模式：{mode}")
        print(f"  提取条目：{len(items)} 条")
        if not args.no_detail:
            print(f"  输出文件：见 {args.output_dir}")
        print(f"{'='*60}")
        
        # 打印前 10 条摘要
        print(f"\n前 10 条热点:")
        for i, item in enumerate(items[:10], 1):
            title = item.get('title', '')[:60]
            if mode == 'hot' and item.get('rank'):
                print(f"  {i}. #{item.get('rank')} {title}")
            else:
                print(f"  {i}. {title}")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n[中断] 用户取消操作")
        return 130
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
