#!/usr/bin/env python
"""
知乎专栏文章批量搜索与抓取脚本

通过百度搜索 site:zhihu.com 获取知乎专栏文章，自动解析百度重定向链接，
抓取并提取结构化内容（标题、作者、发布时间、正文）。

用法:
    python zhihu_column_search.py "自主Agent" --max-articles 20 --pages 3
    python zhihu_column_search.py "AI Agent" --max-articles 10 --headless --output-dir ./results
    python zhihu_column_search.py "大模型" --no-detail  # 仅获取搜索结果列表

示例:
    python zhihu_column_search.py "自主Agent" --max-articles 20 --pages 3
    python zhihu_column_search.py "AI Agent" --max-articles 10 --headless
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import csv
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse, quote

# 导入 baidu_search 模块复用其函数
sys.path.insert(0, str(Path(__file__).parent))
from src.searchers.baidu_search import (
    ensure_browser, resolve_baidu_redirect, random_delay,
    get_random_ua, run_cmd, PYTHON_CMD, SKILL_DIR
)
from src.utilities.detail_cleaner import clean_detail_content


# ========== 知乎专栏专用配置 ==========
ZHIHU_COLUMN_OUTPUT_DIR = SKILL_DIR / "search_results"
DEFAULT_MAX_ARTICLES = 20
DEFAULT_PAGES = 3


def search_zhihu_column_via_baidu(port: int, tab_id: str, query: str,
                                   max_pages: int = 3,
                                   wait_timeout: int = 20) -> List[Dict]:
    """通过百度搜索 site:zhihu.com 获取知乎专栏文章，自动解析重定向链接。
    
    经验总结:
    - site:zhihu.com 搜索主要返回知乎专栏文章(zhuanlan.zhihu.com)
    - 百度重定向链接必须通过导航方式解析(fetch 因CORS限制无法工作)
    - 每次解析重定向后需要导航回搜索结果页
    """
    print(f"[搜索] 正在通过百度搜索知乎专栏: {query}")
    
    all_results = []
    
    for page in range(max_pages):
        pn = page * 10
        print(f"  [第{page+1}页] pn={pn}")
        
        # 请求前随机延迟
        delay = random_delay()
        print(f"    [延迟] 请求前等待 {delay:.1f} 秒")
        
        # 构建搜索URL: site:zhihu.com 限定知乎域名
        search_url = f"https://www.baidu.com/s?wd=site:zhihu.com+{quote(query)}&pn={pn}"
        
        # 导航到搜索结果页
        nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                        "--goto", search_url, "--wait-selector", "#content_left",
                        "--timeout", str(wait_timeout)])
        if nav_result.returncode != 0:
            print(f"    [警告] 第{page+1}页导航失败: {nav_result.stderr[:200]}")
            continue
        
        time.sleep(1)
        
        # 搜索后随机延迟
        delay = random_delay(0.5, 1.5)
        print(f"    [延迟] 搜索后等待 {delay:.1f} 秒")
        
        # 使用 JavaScript 提取搜索结果
        js_code = r"""
(() => {
  const results = [];
  const containers = document.querySelectorAll('#content_left .result, #content_left .c-container');
  containers.forEach((container) => {
    const titleEl = container.querySelector('h3 a');
    const title = titleEl ? (titleEl.innerText || titleEl.textContent || '').trim() : '';
    const url = titleEl ? titleEl.href : '';
    const snippetEl = container.querySelector('.c-abstract, [class*=abstract]');
    const snippet = snippetEl ? (snippetEl.innerText || snippetEl.textContent || '').trim() : '';
    if (title && url && url.startsWith('http')) {
      results.push({title, url, snippet});
    }
  });
  return results;
})()
"""
        
        result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                        "--tab", tab_id, "--eval", js_code])
        
        if result.returncode != 0:
            print(f"    [警告] JS提取失败: {result.stderr[:200]}")
            continue
        
        try:
            stdout = result.stdout.strip()
            json_start = stdout.find('{')
            if json_start < 0:
                continue
            output = json.loads(stdout[json_start:])
            raw_results = output.get('result', [])
        except json.JSONDecodeError:
            print(f"    [警告] 无法解析JS结果")
            continue
        
        # 解析百度重定向链接并分类
        page_results = []
        for r in raw_results:
            if not (r.get('title') and r.get('url')):
                continue
            url = r['url']
            
            # 检测百度重定向链接
            if 'baidu.com/link?' in url:
                print(f"    [重定向] 检测到百度重定向链接，正在解析...")
                url = resolve_baidu_redirect(port, tab_id, url, wait_timeout=10, max_retries=1)
            
            # 按URL分类：只保留知乎专栏文章
            result_type = classify_zhihu_column_url(url)
            
            if result_type == 'column':
                page_results.append({
                    'title': r['title'],
                    'url': url,
                    'snippet': r.get('snippet', ''),
                    'type': result_type
                })
                print(f"    [专栏] {r['title'][:50]}")
            
            if len(page_results) >= 10:  # 每页最多10条专栏
                break
        
        all_results.extend(page_results)
        print(f"    [第{page+1}页] 找到 {len(page_results)} 篇专栏文章")
        
        # 翻页间隔
        if page < max_pages - 1:
            delay = random_delay(2, 4)
            print(f"    [翻页] 等待 {delay:.1f} 秒...")
    
    print(f"[搜索] 共找到 {len(all_results)} 篇知乎专栏文章")
    return all_results


def classify_zhihu_column_url(url: str) -> str:
    """根据URL分类知乎页面类型，只保留专栏文章。
    
    知乎URL模式:
    - 专栏文章: zhuanlan.zhihu.com/p/xxx
    - 问答页面: zhihu.com/question/xxx 或 zhihu.com/question/xxx/answer/xxx
    - 其他: zhihu.com/column, zhihu.com/topic 等
    """
    if 'zhuanlan.zhihu.com' in url and '/p/' in url:
        return 'column'
    elif 'zhihu.com/question' in url or 'zhihu.com/answer' in url:
        return 'question'
    elif 'zhihu.com' in url:
        return 'other_zhihu'
    else:
        return 'non_zhihu'


def fetch_zhihu_column(port: int, tab_id: str, url: str,
                        wait_timeout: int = 20, max_chars: int = 8000) -> Dict:
    """抓取知乎专栏文章内容。
    
    知乎专栏页面结构:
    - 标题: .Post-Title 或 h1
    - 作者: .AuthorInfo-name
    - 发布时间: .ContentItem-time 或 meta[property=article:published_time]
    - 正文: .Post-RichTextContainer 或 .RichText
    
    经验总结:
    - 专栏文章通常不需要登录即可查看完整内容
    - .Post-RichTextContainer 是正文主容器
    - 需要清理推荐文章、底部标签等无关元素
    - 限制最大字符数避免过长内容
    - 关键：内容是动态加载的，需要等待正文文本非空
    """
    print(f"  [专栏] 正在抓取: {url[:80]}...")
    
    # 导航到专栏页面
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port),
                         "--tab", tab_id, "--goto", url,
                         "--wait-selector", "body", "--timeout", str(wait_timeout)])
    if nav_result.returncode != 0:
        return {'url': url, 'title': '', 'author': '', 'content': '',
                'publish_time': '', 'success': False, 'error': '导航失败'}
    
    # 等待页面渲染，并等待正文内容加载完成
    print(f"  [等待] 等待正文内容加载...")
    wait_content_js = r"""
(() => {
  const contentEl = document.querySelector('.Post-RichTextContainer, .Post-RichText, .RichText, .article-content');
  if (!contentEl) return {ready: false, reason: 'no_element'};
  const text = contentEl.innerText.trim();
  if (text.length > 100) return {ready: true, length: text.length};
  return {ready: false, reason: 'content_too_short', length: text.length};
})()
"""
    
    max_wait = wait_timeout
    start_time = time.time()
    while time.time() - start_time < max_wait:
        result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                         "--tab", tab_id, "--eval", wait_content_js])
        if result.returncode == 0:
            try:
                stdout = result.stdout.strip()
                json_start = stdout.find('{')
                if json_start >= 0:
                    output = json.loads(stdout[json_start:])
                    check_result = output.get('result', {})
                    if check_result.get('ready'):
                        print(f"  [就绪] 正文已加载，长度: {check_result.get('length')} 字符")
                        break
                    else:
                        print(f"  [等待中] {check_result.get('reason')}, 当前长度: {check_result.get('length', 0)}")
            except:
                pass
        time.sleep(1.5)
    else:
        print(f"  [警告] 等待正文加载超时，尝试继续提取...")
    
    # 使用 JS 提取专栏内容 - 分步执行避免转义问题
    # 先获取标题、作者、发布时间
    js_get_meta = r"""
(() => {
  // querySelector 不支持逗号分隔，分别尝试
  const titleSelectors = ['.Post-Title', 'h1.Post-Title', '.ArticleItem-title', 'h1'];
  let titleEl = null;
  for (const sel of titleSelectors) {
    titleEl = document.querySelector(sel);
    if (titleEl) break;
  }
  const title = titleEl ? titleEl.innerText.trim() : document.title;
  
  const authorSelectors = ['.AuthorInfo-name', '.UserLink-link', '.Post-Author .AuthorInfo-name'];
  let authorEl = null;
  for (const sel of authorSelectors) {
    authorEl = document.querySelector(sel);
    if (authorEl) break;
  }
  const author = authorEl ? authorEl.innerText.trim() : '';
  
  let publishTime = '';
  // 使用 querySelectorAll 避免 meta[property=article:published_time] 选择器中的冒号问题
  const timeEls = document.querySelectorAll('.ContentItem-time, [itemprop=datePublished]');
  for (const el of timeEls) {
    const dt = el.getAttribute('datetime') || el.getAttribute('content') || el.innerText.trim();
    if (dt) { publishTime = dt; break; }
  }
  // 单独处理 meta[property=article:published_time]
  if (!publishTime) {
    const metaEls = document.querySelectorAll('meta[property]');
    for (const el of metaEls) {
      if (el.getAttribute('property') === 'article:published_time') {
        publishTime = el.getAttribute('content') || '';
        break;
      }
    }
  }
  
  return JSON.stringify({title, author, publishTime});
})()
"""
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                     "--tab", tab_id, "--eval", js_get_meta])
    
    if result.returncode != 0:
        return {'url': url, 'title': '', 'author': '', 'content': '',
                'publish_time': '', 'success': False, 'error': 'JS提取元数据失败'}
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start >= 0:
            output = json.loads(stdout[json_start:])
            meta_str = output.get('result', '{}')
            meta = json.loads(meta_str)
            title = meta.get('title', '')
            author = meta.get('author', '')
            publish_time = meta.get('publishTime', '')
        else:
            title, author, publish_time = '', '', ''
    except Exception as e:
        title, author, publish_time = '', '', ''
    
    # 获取正文内容
    js_get_content = r"""
(() => {
  // querySelector 不支持逗号分隔，分别尝试
  const contentSelectors = ['.Post-RichTextContainer', '.Post-RichText', '.RichText', '.article-content'];
  let contentEl = null;
  for (const sel of contentSelectors) {
    contentEl = document.querySelector(sel);
    if (contentEl) break;
  }
  if (!contentEl) return JSON.stringify({content: '', error: '未找到正文元素'});
  
  // 清理无关元素
  const selectorsToRemove = [
    '.RecommendArticle', '.BottomTags', '.ContentItem-actions',
    '.RichText-actions', '.Post-SubContent', '.Post-Footer',
    '.CopyrightNotice', '.VoteButton', '.CommentButton',
    '[class*=recommend]', '[class*=footer]', '[class*=action]'
  ];
  selectorsToRemove.forEach(sel => {
    contentEl.querySelectorAll(sel).forEach(el => el.remove());
  });
  
  const content = contentEl.innerText.trim().substring(0, %d);
  return JSON.stringify({content});
})()
""" % max_chars
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                     "--tab", tab_id, "--eval", js_get_content])
    
    if result.returncode != 0:
        return {'url': url, 'title': title, 'author': author, 'content': '',
                'publish_time': publish_time, 'success': False, 'error': 'JS提取正文失败'}
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start >= 0:
            output = json.loads(stdout[json_start:])
            content_str = output.get('result', '{}')
            content_data = json.loads(content_str)
            content = content_data.get('content', '')
        else:
            content = ''
    except Exception as e:
        content = ''
    
    # 使用 detail_cleaner 进一步清理
    if content:
        content = clean_detail_content(url, content)
    
    return {
        'url': url,
        'title': title,
        'author': author,
        'publish_time': publish_time,
        'content': content,
        'success': bool(content)
    }


def save_results(results: List[Dict], columns: List[Dict],
                 output_dir: Path, query: str):
    """保存知乎专栏搜索结果到文件。
    
    生成文件:
    - zhihu_column_<query>_<timestamp>.json — 完整结构化数据
    - zhihu_column_<query>_<timestamp>.md — 人类可读的 Markdown 报告
    - zhihu_column_<query>_<timestamp>_index.csv — 简易索引表
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_query = query.replace(' ', '_').replace('/', '_').replace('\\', '_')
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    
    # 1. 保存 JSON
    json_file = output_dir / f"zhihu_column_{safe_query}_{timestamp}.json"
    all_data = {
        'query': query,
        'search_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_search_results': len(results),
        'total_fetched': len(columns),
        'success_count': sum(1 for c in columns if c.get('success')),
        'search_index': results,
        'articles': columns
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"[保存] JSON: {json_file}")
    
    # 2. 保存 Markdown 报告
    md_file = output_dir / f"zhihu_column_{safe_query}_{timestamp}.md"
    lines = []
    lines.append(f"# 知乎专栏文章抓取报告: {query}")
    lines.append("")
    lines.append(f"> 搜索时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: 百度搜索 site:zhihu.com")
    lines.append(f"> 搜索结果: {len(results)} 篇, 成功抓取: {sum(1 for c in columns if c.get('success'))} 篇")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 文章列表
    if columns:
        lines.append("## 文章列表")
        lines.append("")
        for i, col in enumerate(columns, 1):
            title = col.get('title', '未知标题')
            url = col.get('url', '')
            author = col.get('author', '')
            pub_time = col.get('publish_time', '')
            content = col.get('content', '')
            success = col.get('success', False)
            
            lines.append(f"### {i}. {title}")
            lines.append("")
            lines.append(f"- **链接**: {url}")
            if author:
                lines.append(f"- **作者**: {author}")
            if pub_time:
                lines.append(f"- **发布时间**: {pub_time}")
            lines.append(f"- **内容长度**: {len(content)} 字符")
            lines.append(f"- **抓取状态**: {'✅ 成功' if success else '❌ 失败'}")
            if col.get('error'):
                lines.append(f"- **错误**: {col['error']}")
            lines.append("")
            
            if content:
                lines.append("**内容摘要**:")
                lines.append("")
                summary = content[:1000].replace('\n\n\n', '\n\n')
                lines.append(summary)
                if len(content) > 1000:
                    lines.append("...")
                lines.append("")
            lines.append("---")
            lines.append("")
    
    # 附录: 搜索结果索引
    lines.append("## 附录: 搜索结果索引")
    lines.append("")
    lines.append("| # | 标题 | URL |")
    lines.append("|---|------|-----|")
    for i, r in enumerate(results, 1):
        title = r.get('title', '')[:50]
        url = r.get('url', '')[:80]
        lines.append(f"| {i} | {title} | {url} |")
    
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"[保存] Markdown: {md_file}")
    
    # 3. 保存 CSV 索引（方便 Excel 打开）
    csv_file = output_dir / f"zhihu_column_{safe_query}_{timestamp}_index.csv"
    with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['序号', '标题', '作者', '发布时间', 'URL', '内容长度', '状态', '错误信息'])
        for i, col in enumerate(columns, 1):
            writer.writerow([
                i,
                col.get('title', ''),
                col.get('author', ''),
                col.get('publish_time', ''),
                col.get('url', ''),
                len(col.get('content', '')),
                '成功' if col.get('success') else '失败',
                col.get('error', '')
            ])
    print(f"[保存] CSV索引: {csv_file}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-articles", type=int, default=DEFAULT_MAX_ARTICLES,
                       help=f"最大抓取文章数 (默认: {DEFAULT_MAX_ARTICLES})")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES,
                       help=f"搜索页数，每页约10条 (默认: {DEFAULT_PAGES})")
    parser.add_argument("--output-dir", default=str(ZHIHU_COLUMN_OUTPUT_DIR),
                       help="输出目录")
    parser.add_argument("--port", type=int, default=9333,
                       help="CDP调试端口 (默认: 9333)")
    parser.add_argument("--name", default="zhihu_session",
                       help="浏览器实例名称（默认 zhihu_session，与其他知乎脚本/登录态共用，避免误开新实例）")
    parser.add_argument("--headless", action="store_true",
                       help="无头模式（生产环境推荐）")
    parser.add_argument("--wait-timeout", type=int, default=20,
                       help="页面等待超时秒数")
    parser.add_argument("--max-chars", type=int, default=8000,
                       help="单篇文章最大字符数")
    parser.add_argument("--no-detail", action="store_true",
                       help="不抓取详情内容，仅获取搜索结果列表")
    parser.add_argument("--delay-range", default="2,5",
                       help="请求间延迟范围(秒)，格式: min,max")
    
    args = parser.parse_args()
    
    # 解析延迟范围
    try:
        delay_min, delay_max = map(float, args.delay_range.split(','))
    except:
        delay_min, delay_max = 2.0, 5.0
    
    print(f"{'='*60}")
    print(f"知乎专栏文章批量搜索与抓取")
    print(f"关键词: {args.query}")
    print(f"最大文章数: {args.max_articles}")
    print(f"搜索页数: {args.pages}")
    print(f"输出目录: {args.output_dir}")
    print(f"无头模式: {args.headless}")
    print(f"{'='*60}")
    
    try:
        # 1. 确保浏览器运行
        user_agent = get_random_ua()
        browser_info = ensure_browser(
            port=args.port, name=args.name, headless=args.headless,
            start_url="https://www.baidu.com", user_agent=user_agent
        )
        port = browser_info["port"]
        tab_id = browser_info["tab_id"]
        
        # 2. 搜索知乎专栏文章
        results = search_zhihu_column_via_baidu(
            port=port, tab_id=tab_id, query=args.query,
            max_pages=args.pages, wait_timeout=args.wait_timeout
        )
        
        if not results:
            print("[警告] 未找到知乎专栏文章")
            return 1
        
        # 限制抓取数量
        results_to_fetch = results[:args.max_articles]
        
        # 3. 抓取详情内容
        columns_detail = []
        
        if not args.no_detail:
            print(f"\n[详情抓取] 开始抓取 {len(results_to_fetch)} 篇文章详情...")
            for i, item in enumerate(results_to_fetch, 1):
                print(f"\n[{i}/{len(results_to_fetch)}] {item['title'][:50]}...")
                detail = fetch_zhihu_column(
                    port=port, tab_id=tab_id, url=item['url'],
                    wait_timeout=args.wait_timeout, max_chars=args.max_chars
                )
                columns_detail.append(detail)
                
                # 请求间随机延迟
                if i < len(results_to_fetch):
                    delay = random_delay(delay_min, delay_max)
                    print(f"  [延迟] 等待 {delay:.1f} 秒...")
        else:
            # 仅保存搜索结果索引
            columns_detail = [
                {**r, 'success': True, 'content': '', 'author': '', 'publish_time': ''}
                for r in results_to_fetch
            ]
        
        # 4. 保存结果
        save_results(
            results=results, columns=columns_detail,
            output_dir=Path(args.output_dir), query=args.query
        )
        
        # 5. 打印摘要
        success_count = sum(1 for c in columns_detail if c.get('success'))
        print(f"\n{'='*60}")
        print(f"任务完成！")
        print(f"  搜索结果: {len(results)} 篇")
        print(f"  尝试抓取: {len(results_to_fetch)} 篇")
        print(f"  成功抓取: {success_count} 篇")
        print(f"  成功率: {success_count/len(results_to_fetch)*100:.1f}%" if results_to_fetch else "  成功率: N/A")
        print(f"  输出目录: {args.output_dir}")
        print(f"{'='*60}")
        
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
