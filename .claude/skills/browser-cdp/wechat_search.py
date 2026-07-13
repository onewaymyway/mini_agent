#!/usr/bin/env python
"""
微信公众号文章搜索自动化脚本

通过搜狗微信搜索 (weixin.sogou.com) 搜索公众号文章，
自动解析搜狗重定向链接，抓取文章详细内容。

用法:
    python wechat_search.py "自主进化Agent" --max-results 10
    python wechat_search.py "AI Agent" --max-results 5 --no-detail
    python wechat_search.py "大模型" --port 9333 --output-dir ./wechat_results

示例:
    python wechat_search.py "自主进化Agent" --max-results 10
    python wechat_search.py "AI Agent" --max-results 5 --no-detail
    python wechat_search.py "大模型" --port 9333 --output-dir ./wechat_results
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse, quote

# 导入 baidu_search 模块复用其函数
sys.path.insert(0, str(Path(__file__).parent))
from baidu_search import (
    ensure_browser, resolve_baidu_redirect, random_delay,
    get_random_ua, run_cmd, PYTHON_CMD, SKILL_DIR
)
from detail_cleaner import clean_detail_content


# ========== 微信公众号搜索专用配置 ==========
WECHAT_OUTPUT_DIR = SKILL_DIR / "search_results"

# 搜狗微信搜索基础 URL
SOGOU_WEIXIN_BASE = "https://weixin.sogou.com/weixin"

# 文章内容等待选择器（微信文章页面）
WECHAT_ARTICLE_SELECTORS = [
    "#js_content",           # 正文内容区
    ".rich_media_content",   # 备选正文区
    "#img-content",          # 旧版正文区
    ".weui-article",         # 另一种布局
]

# 搜索结果页文章链接选择器
SOGOU_RESULT_SELECTORS = [
    "#sogou_vr_11002601_box_0 .txt-box h3 a",  # 第1篇
    "#sogou_vr_11002601_box_1 .txt-box h3 a",  # 第2篇
    "#sogou_vr_11002601_box_2 .txt-box h3 a",  # 第3篇
    ".news-box .txt-box h3 a",                  # 通用选择器
    ".vrwrap .txt-box h3 a",                    # 另一种布局
    "a[id^='sogou_vr_11002601_title_']",        # 标题链接 ID 模式
]


def search_wechat_via_sogou(port: int, tab_id: str, query: str, max_results: int = 10,
                            wait_timeout: int = 20) -> List[Dict]:
    """通过搜狗微信搜索获取公众号文章结果，自动解析重定向链接。
    
    经验总结:
    - 搜狗微信搜索 type=2 为文章搜索，type=1 为公众号搜索
    - 搜狗结果页链接是重定向链接 (/link?url=...)，需导航解析真实 URL
    - 真实文章 URL 为 mp.weixin.qq.com/s?... 格式
    - 微信文章页面 JS 渲染较慢，需等待 #js_content 或 .rich_media_content 出现
    - 文章内容提取建议用 browser_extract --mode text 获取纯文本
    - 搜狗有反爬，需随机延迟、随机 UA、控制请求频率
    """
    print(f"[搜索] 正在通过搜狗微信搜索: {query}")
    
    # 请求前随机延迟
    delay = random_delay()
    print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
    
    # 构建搜索 URL: type=2 文章搜索, query 编码
    search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(query)}&ie=utf8"
    
    # 导航到搜索结果页
    run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
            "--goto", search_url, "--wait-selector", ".news-box, .vrwrap, #sogou_vr_11002601_box_0",
            "--timeout", str(wait_timeout)])
    time.sleep(2)  # 等待 JS 渲染完成
    
    # 搜索后随机延迟
    delay = random_delay(1.0, 2.0)
    print(f"  [延迟] 搜索后等待 {delay:.1f} 秒")
    
    # 使用 JavaScript 提取搜索结果（标题、重定向链接、摘要、公众号、时间）
    js_code = r"""
(() => {
  const results = [];
  // 搜狗微信搜索结果容器
  const containers = document.querySelectorAll('.news-box, .vrwrap, [id^="sogou_vr_11002601_box_"]');
  containers.forEach((container) => {
    // 标题链接
    const titleEl = container.querySelector('h3 a, .txt-box h3 a, a[id^="sogou_vr_11002601_title_"]');
    const title = titleEl ? (titleEl.innerText || titleEl.textContent || '').trim() : '';
    const redirectUrl = titleEl ? titleEl.href : '';
    
    // 摘要
    const snippetEl = container.querySelector('.txt-info, .txt-box p, [class*="txt"]');
    const snippet = snippetEl ? (snippetEl.innerText || snippetEl.textContent || '').trim() : '';
    
    // 公众号名称
    const accountEl = container.querySelector('.account, .txt-box .s-p, [class*="account"]');
    const account = accountEl ? (accountEl.innerText || accountEl.textContent || '').trim() : '';
    
    // 发布时间
    const timeEl = container.querySelector('.s2, .txt-box .s2, [class*="time"]');
    const pubTime = timeEl ? (timeEl.innerText || timeEl.textContent || '').trim() : '';
    
    if (title && redirectUrl && redirectUrl.startsWith('http')) {
      results.push({title, redirectUrl, snippet, account, pubTime});
    }
  });
  return results;
})()
"""
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                    "--tab", tab_id, "--eval", js_code])
    
    if result.returncode != 0:
        print(f"[警告] JS提取失败: {result.stderr[:200]}")
        return []
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start >= 0:
            stdout = stdout[json_start:]
        output = json.loads(stdout)
        results = output.get('result', [])
    except json.JSONDecodeError:
        print(f"[警告] 无法解析JS结果: {result.stdout[:200]}")
        return []
    
    # 解析重定向链接，获取真实文章 URL
    filtered = []
    for r in results:
        if r.get('title') and r.get('redirectUrl'):
            redirect_url = r['redirectUrl']
            # 搜狗重定向链接特征: /link?url=... 或 weixin.sogou.com/link?url=...
            if '/link?url=' in redirect_url or 'weixin.sogou.com/link' in redirect_url:
                print(f"  [重定向] 正在解析: {r['title'][:30]}...")
                real_url = resolve_sogou_redirect(port, tab_id, redirect_url, wait_timeout)
                if real_url:
                    r['url'] = real_url
                else:
                    r['url'] = redirect_url  # 解析失败保留原链接
            else:
                r['url'] = redirect_url
            
            filtered.append({
                'title': r['title'],
                'url': r.get('url', redirect_url),
                'redirect_url': redirect_url,
                'snippet': r.get('snippet', ''),
                'account': r.get('account', ''),
                'pub_time': r.get('pubTime', ''),
            })
            if len(filtered) >= max_results:
                break
    
    print(f"[搜索] 找到 {len(filtered)} 个有效文章结果")
    return filtered


def resolve_sogou_redirect(port: int, tab_id: str, redirect_url: str, wait_timeout: int = 15) -> Optional[str]:
    """解析搜狗微信搜索的重定向链接，获取真实微信文章 URL。
    
    关键点:
    - 搜狗重定向链接必须通过浏览器导航解析（fetch 因 CORS 无法工作）
    - 导航后等待 URL 变化为 mp.weixin.qq.com
    - 解析完成后需导航回搜索结果页继续处理下一条
    """
    try:
        # 记录当前搜索结果页 URL，以便返回
        current_url_result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                                      "--tab", tab_id, "--eval", "window.location.href"])
        search_page_url = ""
        if current_url_result.returncode == 0:
            try:
                search_page_url = json.loads(current_url_result.stdout.strip()).get('result', '')
            except:
                pass
        
        # 导航到重定向链接
        run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                "--goto", redirect_url, "--wait-until", "domcontentloaded",
                "--timeout", str(wait_timeout)])
        time.sleep(2)
        
        # 获取真实 URL
        url_result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                              "--tab", tab_id, "--eval", "window.location.href"])
        real_url = ""
        if url_result.returncode == 0:
            try:
                real_url = json.loads(url_result.stdout.strip()).get('result', '')
            except:
                pass
        
        # 验证是否为微信文章页面
        if real_url and 'mp.weixin.qq.com' in real_url:
            print(f"    [解析成功] 真实 URL: {real_url[:80]}...")
            # 导航回搜索结果页
            if search_page_url and 'weixin.sogou.com' in search_page_url:
                run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                        "--goto", search_page_url, "--wait-selector", ".news-box, .vrwrap",
                        "--timeout", str(wait_timeout)])
                time.sleep(1)
            return real_url
        else:
            print(f"    [解析失败] 未跳转到微信文章页: {real_url[:80]}")
            # 尝试返回搜索页
            if search_page_url and 'weixin.sogou.com' in search_page_url:
                run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                        "--goto", search_page_url, "--wait-selector", ".news-box, .vrwrap",
                        "--timeout", str(wait_timeout)])
                time.sleep(1)
            return None
    except Exception as e:
        print(f"    [异常] 解析重定向失败: {e}")
        return None


def extract_wechat_article(port: int, tab_id: str, article_url: str, wait_timeout: int = 30) -> Dict:
    """抓取微信公众号文章详细内容。
    
    关键点:
    - 微信文章页面 JS 渲染，需等待正文选择器出现
    - 使用 browser_extract --mode text 获取纯文本正文
    - 文章可能包含图片、视频、小程序卡片等，纯文本模式可过滤
    - 部分文章有反爬（需登录、验证码），遇到则记录错误继续
    """
    print(f"  [抓取] 正在获取文章内容...")
    
    # 导航到文章页
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                         "--goto", article_url,
                         "--wait-selector", ",".join(WECHAT_ARTICLE_SELECTORS),
                         "--timeout", str(wait_timeout)])
    
    if nav_result.returncode != 0:
        print(f"    [错误] 文章页面加载失败: {nav_result.stderr[:200]}")
        return {
            'url': article_url,
            'title': '',
            'content': '',
            'author': '',
            'publish_time': '',
            'error': '页面加载失败'
        }
    
    # 等待内容完全渲染
    time.sleep(3)
    
    # 提取纯文本内容
    extract_result = run_cmd([PYTHON_CMD, "browser_extract.py", "--port", str(port),
                             "--tab", tab_id, "--mode", "text", "--max-chars", "50000"])
    
    content = ""
    if extract_result.returncode == 0:
        content = extract_result.stdout.strip()
    else:
        print(f"    [警告] 内容提取失败: {extract_result.stderr[:200]}")
    
    # 尝试提取标题、作者、发布时间等元数据
    meta_js = r"""
(() => {
  const meta = {};
  // 标题
  const titleEl = document.querySelector('#activity-name, h1.rich_media_title, .rich_media_title');
  meta.title = titleEl ? (titleEl.innerText || titleEl.textContent || '').trim() : '';
  
  // 作者/公众号
  const authorEl = document.querySelector('#js_name, .rich_media_meta_list .profile_nickname, .profile_meta a');
  meta.author = authorEl ? (authorEl.innerText || authorEl.textContent || '').trim() : '';
  
  // 发布时间
  const timeEl = document.querySelector('#publish_time, .rich_media_meta_list .publish_time, .rich_media_meta .publish_time');
  meta.publish_time = timeEl ? (timeEl.innerText || timeEl.textContent || '').trim() : '';
  
  // 文章封面图
  const coverEl = document.querySelector('.rich_media_thumb img, #js_cover_image');
  meta.cover = coverEl ? coverEl.src : '';
  
  return meta;
})()
"""
    
    meta_result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                          "--tab", tab_id, "--eval", meta_js])
    
    meta = {}
    if meta_result.returncode == 0:
        try:
            stdout = meta_result.stdout.strip()
            json_start = stdout.find('{')
            if json_start >= 0:
                stdout = stdout[json_start:]
            meta = json.loads(stdout).get('result', {})
        except:
            pass
    
    # 清理内容
    cleaned_content = clean_detail_content(content) if content else ""
    
    return {
        'url': article_url,
        'title': meta.get('title', ''),
        'content': cleaned_content,
        'raw_content': content,
        'author': meta.get('author', ''),
        'publish_time': meta.get('publish_time', ''),
        'cover': meta.get('cover', ''),
        'word_count': len(cleaned_content),
    }


def save_results(results: List[Dict], query: str, output_dir: Path) -> Dict[str, str]:
    """保存结果为 JSON 和 Markdown 格式"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_query = "".join(c for c in query if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_query = safe_query.replace(' ', '_')[:50]
    
    base_name = f"wechat_{safe_query}_{timestamp}"
    json_path = output_dir / f"{base_name}.json"
    md_path = output_dir / f"{base_name}.md"
    
    # 保存 JSON
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'query': query,
            'search_engine': 'sogou_weixin',
            'crawl_time': time.strftime("%Y-%m-%d %H:%M:%S"),
            'total': len(results),
            'articles': results
        }, f, ensure_ascii=False, indent=2)
    
    # 保存 Markdown
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# 搜狗微信搜索: {query}\n\n")
        f.write(f"**搜索时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**结果数量**: {len(results)}\n\n")
        f.write("---\n\n")
        
        for i, article in enumerate(results, 1):
            f.write(f"## {i}. {article.get('title', '无标题')}\n\n")
            f.write(f"- **公众号**: {article.get('account', '未知')}\n")
            f.write(f"- **发布时间**: {article.get('pub_time', article.get('publish_time', '未知'))}\n")
            f.write(f"- **文章链接**: {article.get('url', '无')}\n")
            f.write(f"- **重定向链接**: {article.get('redirect_url', '无')}\n")
            f.write(f"- **字数**: {article.get('word_count', 0)}\n\n")
            
            if article.get('snippet'):
                f.write(f"### 摘要\n{article['snippet']}\n\n")
            
            if article.get('content'):
                f.write(f"### 正文内容\n{article['content'][:3000]}")
                if len(article['content']) > 3000:
                    f.write(f"... (共 {len(article['content'])} 字)")
                f.write(f"\n\n")
            
            f.write("---\n\n")
    
    print(f"[保存] JSON: {json_path}")
    print(f"[保存] Markdown: {md_path}")
    
    return {'json': str(json_path), 'markdown': str(md_path)}


def main():
    parser = argparse.ArgumentParser(description='搜狗微信搜索公众号文章抓取')
    parser.add_argument('query', help='搜索关键词')
    parser.add_argument('--max-results', type=int, default=10, help='最大抓取文章数 (默认 10)')
    parser.add_argument('--no-detail', action='store_true', help='仅获取搜索结果，不抓取文章详情')
    parser.add_argument('--port', type=int, default=9333, help='CDP 端口 (默认 9333)')
    parser.add_argument('--output-dir', type=str, default=None, help='输出目录 (默认 ./search_results)')
    parser.add_argument('--headless', action='store_true', help='无头模式 (默认有头)')
    parser.add_argument('--wait-timeout', type=int, default=30, help='页面等待超时秒数 (默认 30)')
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir) if args.output_dir else WECHAT_OUTPUT_DIR
    
    print(f"{'='*60}")
    print(f"  搜狗微信搜索 - 公众号文章抓取")
    print(f"  关键词: {args.query}")
    print(f"  最大结果: {args.max_results}")
    print(f"  抓取详情: {'否' if args.no_detail else '是'}")
    print(f"  输出目录: {output_dir}")
    print(f"{'='*60}\n")
    
    # 确保浏览器就绪
    browser_info = ensure_browser(port=args.port, headless=args.headless, start_url="https://weixin.sogou.com")
    port = browser_info['port']
    tab_id = browser_info['tab_id']
    
    try:
        # 1. 搜索文章列表
        articles = search_wechat_via_sogou(
            port, tab_id, args.query,
            max_results=args.max_results,
            wait_timeout=args.wait_timeout
        )
        
        if not articles:
            print("[结果] 未找到任何文章")
            return
        
        # 2. 抓取文章详情（可选）
        if not args.no_detail:
            print(f"\n[详情] 开始抓取 {len(articles)} 篇文章详情...")
            for i, article in enumerate(articles, 1):
                print(f"\n[{i}/{len(articles)}] {article['title'][:50]}...")
                
                # 随机延迟避免触发反爬
                delay = random_delay(2.0, 4.0)
                print(f"  [延迟] 等待 {delay:.1f} 秒")
                
                detail = extract_wechat_article(
                    port, tab_id, article['url'],
                    wait_timeout=args.wait_timeout
                )
                
                # 合并详情到文章对象
                article.update(detail)
                
                # 返回搜索结果页（为下一篇做准备）
                if i < len(articles):
                    search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(args.query)}&ie=utf8"
                    run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                            "--goto", search_url, "--wait-selector", ".news-box, .vrwrap",
                            "--timeout", str(args.wait_timeout)])
                    time.sleep(1)
        
        # 3. 保存结果
        save_results(articles, args.query, output_dir)
        
        print(f"\n{'='*60}")
        print(f"  完成! 共抓取 {len(articles)} 篇文章")
        print(f"{'='*60}")
        
    except KeyboardInterrupt:
        print("\n[中断] 用户取消")
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 可选：关闭浏览器标签页
        pass


if __name__ == "__main__":
    main()
