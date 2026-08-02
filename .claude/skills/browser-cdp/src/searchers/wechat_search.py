#!/usr/bin/env python
"""
微信公众号文章搜索自动化脚本

通过搜狗微信搜索 (weixin.sogou.com) 搜索公众号文章，
自动解析搜狗重定向链接，抓取文章详细内容。

功能特性：
- 文章搜索 (type=2) + 翻页抓取
- 公众号搜索 (type=1) + 主页历史文章抓取
- 多关键词批量搜索 + 合并去重
- 重定向链接自动解析
- 反爬策略：随机延迟、随机 UA、Cookie 持久化

用法:
    python wechat_search.py "自主进化Agent" --max-results 10
    python wechat_search.py "AI Agent" --max-results 5 --no-detail
    python wechat_search.py "大模型" --port 9333 --output-dir ./wechat_results
    python wechat_search.py "RAG" --max-pages 3  # 翻页抓取前3页
    python wechat_search.py "机器之心" --type account --max-articles 20  # 公众号主页抓取
    python wechat_search.py "自主进化Agent,AI Agent,Agent记忆" --multi-keywords --max-total 30  # 多关键词批量

示例:
    python wechat_search.py "自主进化Agent" --max-results 10
    python wechat_search.py "AI Agent" --max-results 5 --no-detail
    python wechat_search.py "大模型" --port 9333 --output-dir ./wechat_results
    python wechat_search.py "RAG" --max-pages 3
    python wechat_search.py "机器之心" --type account --max-articles 20
    python wechat_search.py "自主进化Agent,AI Agent,Agent记忆" --multi-keywords --max-total 30
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Set
from urllib.parse import urlparse, quote

# 导入 baidu_search 模块复用其函数
sys.path.insert(0, str(Path(__file__).parent))
from src.searchers.baidu_search import (
    ensure_browser, resolve_baidu_redirect, random_delay,
    get_random_ua, run_cmd, PYTHON_CMD, SKILL_DIR
)
from src.utilities.detail_cleaner import clean_detail_content


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

# 公众号主页历史文章选择器
ACCOUNT_HISTORY_SELECTORS = [
    "#history .weui_msg_card",
    ".weui_msg_card",
    "[href*='/s?']",
]


# ========== 核心搜索函数 ==========

def search_wechat_via_sogou(port: int, tab_id: str, query: str, max_results: int = 10,
                            wait_timeout: int = 20, page: int = 1) -> List[Dict]:
    """通过搜狗微信搜索获取公众号文章结果，自动解析重定向链接。
    
    支持翻页：page 参数指定页码（1-10）
    """
    print(f"[搜索] 正在通过搜狗微信搜索: {query} (第 {page} 页)")
    
    # 请求前随机延迟
    delay = random_delay()
    print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
    
    # 构建搜索 URL: type=2 文章搜索, query 编码, page 页码
    search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(query)}&ie=utf8&page={page}"
    
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
                'page': page,
            })
            if len(filtered) >= max_results:
                break
    
    print(f"[搜索] 第 {page} 页找到 {len(filtered)} 个有效文章结果")
    return filtered


def search_wechat_accounts(port: int, tab_id: str, query: str, max_results: int = 10,
                           wait_timeout: int = 20) -> List[Dict]:
    """搜索公众号 (type=1)，返回公众号列表。
    
    返回字段: name, account_id, description, avatar, url, qr_code
    """
    print(f"[搜索公众号] 正在搜索: {query}")
    
    delay = random_delay()
    print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
    
    search_url = f"{SOGOU_WEIXIN_BASE}?type=1&query={quote(query)}&ie=utf8"
    
    run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
            "--goto", search_url, "--wait-selector", ".news-box, .vrwrap, .account-item",
            "--timeout", str(wait_timeout)])
    time.sleep(2)
    
    delay = random_delay(1.0, 2.0)
    print(f"  [延迟] 搜索后等待 {delay:.1f} 秒")
    
    js_code = r"""
(() => {
  const results = [];
  const containers = document.querySelectorAll('.account-item, .news-box .txt-box, [class*="account"]');
  containers.forEach((container) => {
    const nameEl = container.querySelector('.txt-box h3 a, .account-name a, h3 a');
    const name = nameEl ? (nameEl.innerText || nameEl.textContent || '').trim() : '';
    const url = nameEl ? nameEl.href : '';
    
    const descEl = container.querySelector('.txt-info, .account-desc, .sp-txt');
    const description = descEl ? (descEl.innerText || descEl.textContent || '').trim() : '';
    
    const idEl = container.querySelector('.account-id, .sp-txt2, [class*="id"]');
    const account_id = idEl ? (idEl.innerText || idEl.textContent || '').trim() : '';
    
    const avatarEl = container.querySelector('img');
    const avatar = avatarEl ? avatarEl.src : '';
    
    if (name && url) {
      results.push({name, url, description, account_id, avatar});
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
    
    filtered = []
    for r in results:
        if r.get('name') and r.get('url'):
            filtered.append({
                'name': r['name'],
                'url': r['url'],
                'description': r.get('description', ''),
                'account_id': r.get('account_id', ''),
                'avatar': r.get('avatar', ''),
            })
            if len(filtered) >= max_results:
                break
    
    print(f"[搜索公众号] 找到 {len(filtered)} 个公众号")
    return filtered
def extract_account_history_articles(port: int, tab_id: str, account_url: str,
                                      max_articles: int = 20, wait_timeout: int = 30) -> List[Dict]:
    """进入公众号主页，抓取历史文章列表。
    
    关键点：
    - 公众号主页 URL 格式：https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=xxx
    - 历史文章通常在下拉加载或点击"查看更多"
    - 文章链接格式：/s?__biz=xxx&mid=xxx&idx=xxx&sn=xxx
    """
    print(f"[公众号主页] 正在抓取历史文章: {account_url}")
    
    # 导航到公众号主页
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                         "--goto", account_url,
                         "--wait-selector", ".weui_msg_card, #history, .profile_inner",
                         "--timeout", str(wait_timeout)])
    
    if nav_result.returncode != 0:
        print(f"  [错误] 公众号主页加载失败: {nav_result.stderr[:200]}")
        return []
    
    time.sleep(3)
    
    articles = []
    seen_urls = set()
    max_scrolls = 10  # 最大下拉次数
    
    for scroll in range(max_scrolls):
        # 提取当前可见的文章卡片
        js_code = r"""
(() => {
  const results = [];
  const cards = document.querySelectorAll('.weui_msg_card, .weui_media_box, [href*="/s?"]');
  cards.forEach((card) => {
    const linkEl = card.querySelector('a[href*="/s?"]') || card.querySelector('a');
    const url = linkEl ? linkEl.href : '';
    const titleEl = card.querySelector('.weui_media_title, h4, .title');
    const title = titleEl ? (titleEl.innerText || titleEl.textContent || '').trim() : '';
    const timeEl = card.querySelector('.weui_media_time, .time, [class*="time"]');
    const pub_time = timeEl ? (timeEl.innerText || timeEl.textContent || '').trim() : '';
    const digestEl = card.querySelector('.weui_media_desc, .digest, .desc');
    const digest = digestEl ? (digestEl.innerText || digestEl.textContent || '').trim() : '';
    
    if (title && url && url.includes('mp.weixin.qq.com')) {
      results.push({title, url, pub_time, digest});
    }
  });
  return results;
})()
"""
        
        result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                        "--tab", tab_id, "--eval", js_code])
        
        if result.returncode == 0:
            try:
                stdout = result.stdout.strip()
                json_start = stdout.find('{')
                if json_start >= 0:
                    stdout = stdout[json_start:]
                output = json.loads(stdout)
                new_articles = output.get('result', [])
                
                for art in new_articles:
                    if art.get('url') and art['url'] not in seen_urls:
                        seen_urls.add(art['url'])
                        articles.append({
                            'title': art['title'],
                            'url': art['url'],
                            'pub_time': art.get('pub_time', ''),
                            'snippet': art.get('digest', ''),
                            'source': 'account_history',
                        })
                        if len(articles) >= max_articles:
                            break
            except:
                pass
        
        if len(articles) >= max_articles:
            break
        
        # 下拉加载更多
        run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                "--tab", tab_id, "--eval", "window.scrollTo(0, document.body.scrollHeight)"])
        time.sleep(2)
        
        # 检查是否有"查看更多"按钮
        js_check_more = r'document.querySelector(".loadmore, .more, [class*=\"more\"]") ? "found" : "not_found"'
        more_btn = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                           "--tab", tab_id, "--eval", js_check_more])
        if 'not_found' in more_btn.stdout:
            break
    
    print(f"[公众号主页] 共抓取 {len(articles)} 篇历史文章")
    return articles[:max_articles]


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
            f.write(f"- **公众号**: {article.get('account', article.get('name', '未知'))}\n")
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


# ========== 多关键词批量搜索（参考 arxiv_multi_search.py） ==========

def merge_and_deduplicate(all_results: List[Dict]) -> List[Dict]:
    """合并多关键词搜索结果，按文章 URL 去重。
    
    保留首次出现的记录（通常是最新/最相关的）
    统计每个关键词的新增文章数
    """
    seen_urls: Set[str] = set()
    unique_results = []
    keyword_stats = {}
    
    for r in all_results:
        url = r.get('url', '')
        kw = r.get('_keyword', 'unknown')
        
        if kw not in keyword_stats:
            keyword_stats[kw] = {'total': 0, 'new': 0}
        keyword_stats[kw]['total'] += 1
        
        if url and url not in seen_urls:
            seen_urls.add(url)
            # 移除内部字段
            clean_r = {k: v for k, v in r.items() if not k.startswith('_')}
            unique_results.append(clean_r)
            keyword_stats[kw]['new'] += 1
    
    print(f"\n[去重统计] 总计 {len(all_results)} 条 -> 去重后 {len(unique_results)} 条")
    for kw, stats in keyword_stats.items():
        print(f"  关键词 '{kw}': 总计 {stats['total']}, 新增 {stats['new']}")
    
    return unique_results


def multi_keyword_search(port: int, tab_id: str, keywords: List[str],
                          max_per_keyword: int = 10, max_total: int = 30,
                          wait_timeout: int = 20, no_detail: bool = False) -> List[Dict]:
    """多关键词批量搜索，自动合并去重。
    
    参考 arxiv_multi_search.py 实现：
    - 遍历每个关键词搜索
    - 实时去重（按文章 URL）
    - 达到 max_total 时提前停止
    - 统计每个关键词的新增数量
    """
    print(f"\n{'='*60}")
    print(f"  多关键词批量搜索")
    print(f"  关键词: {keywords}")
    print(f"  每词最大: {max_per_keyword}, 总计最大: {max_total}")
    print(f"{'='*60}\n")
    
    all_results = []
    
    for i, keyword in enumerate(keywords, 1):
        print(f"\n[{i}/{len(keywords)}] 正在搜索关键词: {keyword}")
        
        # 搜索当前关键词（只取第一页，或根据需要翻页）
        articles = search_wechat_via_sogou(
            port, tab_id, keyword,
            max_results=max_per_keyword,
            wait_timeout=wait_timeout,
            page=1
        )
        
        if not articles:
            print(f"  [结果] 关键词 '{keyword}' 无结果")
            continue
        
        # 标记关键词来源，用于去重统计
        for art in articles:
            art['_keyword'] = keyword
        
        all_results.extend(articles)
        
        # 实时去重检查
        unique_so_far = merge_and_deduplicate(all_results)
        print(f"  [进度] 当前累计去重后: {len(unique_so_far)} 篇")
        
        # 达到总量上限提前停止
        if len(unique_so_far) >= max_total:
            print(f"  [提前停止] 已达到最大总量 {max_total}")
            break
        
        # 关键词间随机延迟
        if i < len(keywords):
            delay = random_delay(3.0, 6.0)
            print(f"  [延迟] 关键词间等待 {delay:.1f} 秒")
    
    # 最终去重
    final_results = merge_and_deduplicate(all_results)
    
    # 如果需要抓取详情
    if not no_detail and final_results:
        print(f"\n[详情] 开始抓取 {len(final_results)} 篇文章详情...")
        for i, article in enumerate(final_results, 1):
            print(f"\n[{i}/{len(final_results)}] {article['title'][:50]}...")
            
            delay = random_delay(2.0, 4.0)
            print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            detail = extract_wechat_article(
                port, tab_id, article['url'],
                wait_timeout=wait_timeout
            )
            
            article.update(detail)
            
            # 返回搜索结果页（为下一篇做准备）
            if i < len(final_results):
                search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(keywords[0])}&ie=utf8"
                run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                        "--goto", search_url, "--wait-selector", ".news-box, .vrwrap",
                        "--timeout", str(wait_timeout)])
                time.sleep(1)
    
    return final_results[:max_total]


def search_with_pagination(port: int, tab_id: str, query: str,
                            max_results: int = 10, max_pages: int = 3,
                            wait_timeout: int = 20, no_detail: bool = False) -> List[Dict]:
    """翻页搜索：抓取前 max_pages 页的结果。
    
    每页通常 10 条结果，翻页通过 page 参数。
    """
    print(f"\n{'='*60}")
    print(f"  翻页搜索: {query}")
    print(f"  最大页数: {max_pages}, 目标总数: {max_results}")
    print(f"{'='*60}\n")
    
    all_articles = []
    
    for page in range(1, max_pages + 1):
        print(f"\n[第 {page} 页] 正在抓取...")
        
        articles = search_wechat_via_sogou(
            port, tab_id, query,
            max_results=max_results,
            wait_timeout=wait_timeout,
            page=page
        )
        
        if not articles:
            print(f"  [结果] 第 {page} 页无结果，停止翻页")
            break
        
        all_articles.extend(articles)
        
        # 去重
        seen = set()
        unique = []
        for art in all_articles:
            url = art.get('url', '')
            if url and url not in seen:
                seen.add(url)
                unique.append(art)
        all_articles = unique
        
        print(f"  [进度] 当前累计去重后: {len(all_articles)} 篇")
        
        if len(all_articles) >= max_results:
            break
        
        # 页间延迟
        if page < max_pages:
            delay = random_delay(2.0, 4.0)
            print(f"  [延迟] 页间等待 {delay:.1f} 秒")
    
    # 如果需要抓取详情
    if not no_detail and all_articles:
        print(f"\n[详情] 开始抓取 {len(all_articles)} 篇文章详情...")
        for i, article in enumerate(all_articles, 1):
            print(f"\n[{i}/{len(all_articles)}] {article['title'][:50]}...")
            
            delay = random_delay(2.0, 4.0)
            print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            detail = extract_wechat_article(
                port, tab_id, article['url'],
                wait_timeout=wait_timeout
            )
            
            article.update(detail)
            
            if i < len(all_articles):
                search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(query)}&ie=utf8&page={page}"
                run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                        "--goto", search_url, "--wait-selector", ".news-box, .vrwrap",
                        "--timeout", str(wait_timeout)])
                time.sleep(1)
    
    return all_articles[:max_results]


def search_account_and_history(port: int, tab_id: str, account_query: str,
                                max_accounts: int = 3, max_articles_per_account: int = 20,
                                wait_timeout: int = 30, no_detail: bool = False) -> List[Dict]:
    """搜索公众号并抓取其历史文章。
    
    流程：
    1. 搜索公众号 (type=1)
    2. 取前 max_accounts 个公众号
    3. 进入每个公众号主页抓取历史文章
    4. 可选抓取文章详情
    """
    print(f"\n{'='*60}")
    print(f"  公众号搜索 + 历史文章抓取")
    print(f"  查询: {account_query}")
    print(f"  最大公众号数: {max_accounts}, 每个最大文章数: {max_articles_per_account}")
    print(f"{'='*60}\n")
    
    # 1. 搜索公众号
    accounts = search_wechat_accounts(port, tab_id, account_query, max_accounts, wait_timeout)
    
    if not accounts:
        print("[结果] 未找到相关公众号")
        return []
    
    all_articles = []
    
    for i, account in enumerate(accounts, 1):
        print(f"\n[{i}/{len(accounts)}] 正在抓取公众号: {account['name']}")
        print(f"  主页: {account['url']}")
        
        # 2. 抓取历史文章
        articles = extract_account_history_articles(
            port, tab_id, account['url'],
            max_articles=max_articles_per_account,
            wait_timeout=wait_timeout
        )
        
        if not articles:
            print(f"  [结果] 该公众号无历史文章或抓取失败")
            continue
        
        # 标记来源公众号
        for art in articles:
            art['account'] = account['name']
            art['account_url'] = account['url']
        
        all_articles.extend(articles)
        print(f"  [进度] 该公众号获取 {len(articles)} 篇，累计 {len(all_articles)} 篇")
        
        # 公众号间延迟
        if i < len(accounts):
            delay = random_delay(3.0, 6.0)
            print(f"  [延迟] 公众号间等待 {delay:.1f} 秒")
    
    # 去重
    seen = set()
    unique = []
    for art in all_articles:
        url = art.get('url', '')
        if url and url not in seen:
            seen.add(url)
            unique.append(art)
    
    # 如果需要抓取详情
    if not no_detail and unique:
        print(f"\n[详情] 开始抓取 {len(unique)} 篇文章详情...")
        for i, article in enumerate(unique, 1):
            print(f"\n[{i}/{len(unique)}] {article['title'][:50]}...")
            
            delay = random_delay(2.0, 4.0)
            print(f"  [延迟] 等待 {delay:.1f} 秒")
            
            detail = extract_wechat_article(
                port, tab_id, article['url'],
                wait_timeout=wait_timeout
            )
            
            article.update(detail)
            
            if i < len(unique):
                # 返回公众号主页
                run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                        "--goto", article.get('account_url', ''), "--wait-selector", ".weui_msg_card, #history",
                        "--timeout", str(wait_timeout)])
                time.sleep(1)
    
    return unique


def main():
    parser = argparse.ArgumentParser(description='搜狗微信搜索公众号文章抓取')
    parser.add_argument('query', help='搜索关键词（多关键词用逗号分隔，或公众号名称）')
    parser.add_argument('--max-results', type=int, default=10, help='最大抓取文章数 (默认 10)')
    parser.add_argument('--no-detail', action='store_true', help='仅获取搜索结果，不抓取文章详情')
    parser.add_argument('--port', type=int, default=9333, help='CDP 端口 (默认 9333)')
    parser.add_argument('--output-dir', type=str, default=None, help='输出目录 (默认 ./search_results)')
    parser.add_argument('--headless', action='store_true', help='无头模式 (默认有头)')
    parser.add_argument('--wait-timeout', type=int, default=30, help='页面等待超时秒数 (默认 30)')
    
    # 新增功能参数
    parser.add_argument('--type', choices=['article', 'account'], default='article',
                        help='搜索类型: article=文章搜索(默认), account=公众号搜索+历史文章')
    parser.add_argument('--max-pages', type=int, default=1, help='翻页搜索最大页数 (默认 1，仅 article 类型有效)')
    parser.add_argument('--multi-keywords', action='store_true', 
                        help='启用多关键词批量搜索（query 用逗号分隔）')
    parser.add_argument('--max-total', type=int, default=30, help='多关键词模式下最大总文章数 (默认 30)')
    parser.add_argument('--max-per-keyword', type=int, default=10, help='多关键词模式下每个关键词最大结果数 (默认 10)')
    parser.add_argument('--max-accounts', type=int, default=3, help='公众号模式下最大公众号数 (默认 3)')
    parser.add_argument('--max-articles-per-account', type=int, default=20, help='公众号模式下每个公众号最大文章数 (默认 20)')
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir) if args.output_dir else WECHAT_OUTPUT_DIR
    
    print(f"{'='*60}")
    print(f"  搜狗微信搜索 - 公众号文章抓取")
    print(f"  关键词: {args.query}")
    print(f"  搜索类型: {args.type}")
    print(f"  最大结果: {args.max_results}")
    print(f"  抓取详情: {'否' if args.no_detail else '是'}")
    print(f"  输出目录: {output_dir}")
    if args.type == 'article' and args.max_pages > 1:
        print(f"  翻页模式: 前 {args.max_pages} 页")
    if args.multi_keywords:
        print(f"  多关键词: 是 (总计上限 {args.max_total}, 每词 {args.max_per_keyword})")
    if args.type == 'account':
        print(f"  公众号模式: 最大 {args.max_accounts} 个公众号, 每个 {args.max_articles_per_account} 篇")
    print(f"{'='*60}\n")
    
    # 确保浏览器就绪
    browser_info = ensure_browser(port=args.port, headless=args.headless, start_url="https://weixin.sogou.com")
    port = browser_info['port']
    tab_id = browser_info['tab_id']
    
    try:
        all_articles = []
        
        if args.multi_keywords:
            # 多关键词批量搜索
            keywords = [k.strip() for k in args.query.split(',') if k.strip()]
            if not keywords:
                print("[错误] 多关键词模式下 query 不能为空")
                return
            
            all_articles = multi_keyword_search(
                port, tab_id, keywords,
                max_per_keyword=args.max_per_keyword,
                max_total=args.max_total,
                wait_timeout=args.wait_timeout,
                no_detail=args.no_detail
            )
        
        elif args.type == 'account':
            # 公众号搜索 + 历史文章
            all_articles = search_account_and_history(
                port, tab_id, args.query,
                max_accounts=args.max_accounts,
                max_articles_per_account=args.max_articles_per_account,
                wait_timeout=args.wait_timeout,
                no_detail=args.no_detail
            )
        
        elif args.max_pages > 1:
            # 翻页搜索
            all_articles = search_with_pagination(
                port, tab_id, args.query,
                max_results=args.max_results,
                max_pages=args.max_pages,
                wait_timeout=args.wait_timeout,
                no_detail=args.no_detail
            )
        
        else:
            # 单页文章搜索（原有逻辑）
            articles = search_wechat_via_sogou(
                port, tab_id, args.query,
                max_results=args.max_results,
                wait_timeout=args.wait_timeout,
                page=1
            )
            
            if not articles:
                print("[结果] 未找到任何文章")
                return
            
            # 抓取文章详情（可选）
            if not args.no_detail:
                print(f"\n[详情] 开始抓取 {len(articles)} 篇文章详情...")
                for i, article in enumerate(articles, 1):
                    print(f"\n[{i}/{len(articles)}] {article['title'][:50]}...")
                    
                    delay = random_delay(2.0, 4.0)
                    print(f"  [延迟] 等待 {delay:.1f} 秒")
                    
                    detail = extract_wechat_article(
                        port, tab_id, article['url'],
                        wait_timeout=args.wait_timeout
                    )
                    
                    article.update(detail)
                    
                    if i < len(articles):
                        search_url = f"{SOGOU_WEIXIN_BASE}?type=2&query={quote(args.query)}&ie=utf8"
                        run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
                                "--goto", search_url, "--wait-selector", ".news-box, .vrwrap",
                                "--timeout", str(args.wait_timeout)])
                        time.sleep(1)
            
            all_articles = articles
        
        # 保存结果
        if all_articles:
            save_results(all_articles, args.query, output_dir)
            
            print(f"\n{'='*60}")
            print(f"  完成! 共抓取 {len(all_articles)} 篇文章")
            print(f"{'='*60}")
        else:
            print("[结果] 未获取到任何文章")
        
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