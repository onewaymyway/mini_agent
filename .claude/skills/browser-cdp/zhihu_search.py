#!/usr/bin/env python
"""
知乎内容搜索自动化脚本

通过百度搜索 site:zhihu.com 获取知乎相关结果，自动解析百度重定向链接，
区分知乎问答和知乎专栏，抓取并提取结构化内容。

用法:
    python zhihu_search.py "AI Agent" --max-results 10
    python zhihu_search.py "自主Agent" --max-results 5 --no-detail
    python zhihu_search.py "大模型" --port 9333 --output-dir ./zhihu_results

示例:
    python zhihu_search.py "AI Agent" --max-results 10
    python zhihu_search.py "自主Agent" --max-results 5 --no-detail
    python zhihu_search.py "大模型" --port 9333 --output-dir ./zhihu_results
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


# ========== 知乎专用配置 ==========
ZHIHU_OUTPUT_DIR = SKILL_DIR / "search_results"


def search_zhihu_via_baidu(port: int, tab_id: str, query: str, max_results: int = 10,
                           wait_timeout: int = 20) -> List[Dict]:
    """通过百度搜索 site:zhihu.com 获取知乎相关结果，自动解析重定向链接。
    
    经验总结:
    - site:zhihu.com 搜索主要返回知乎专栏文章(zhuanlan.zhihu.com)
    - 要获取知乎问答(question页面)，需要搜索 '关键词 知乎问答' 而非 site:zhihu.com/question
    - 百度重定向链接必须通过导航方式解析(fetch 因CORS限制无法工作)
    - 每次解析重定向后需要导航回搜索结果页
    """
    print(f"[搜索] 正在通过百度搜索知乎内容: {query}")
    
    # 请求前随机延迟
    delay = random_delay()
    print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
    
    # 构建搜索URL: site:zhihu.com 限定知乎域名
    search_url = f"https://www.baidu.com/s?wd=site:zhihu.com+{quote(query)}"
    
    # 导航到搜索结果页
    run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port), "--tab", tab_id,
            "--goto", search_url, "--wait-selector", "#content_left",
            "--timeout", str(wait_timeout)])
    time.sleep(1)
    
    # 搜索后随机延迟
    delay = random_delay(0.5, 1.5)
    print(f"  [延迟] 搜索后等待 {delay:.1f} 秒")
    
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
        print(f"[警告] JS提取失败: {result.stderr[:200]}")
        return []
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start < 0:
            return []
        output = json.loads(stdout[json_start:])
        raw_results = output.get('result', [])
    except json.JSONDecodeError:
        print(f"[警告] 无法解析JS结果")
        return []
    
    # 解析百度重定向链接并分类
    filtered = []
    for r in raw_results:
        if not (r.get('title') and r.get('url')):
            continue
        url = r['url']
        
        # 检测百度重定向链接
        if 'baidu.com/link?' in url:
            print(f"  [重定向] 检测到百度重定向链接，正在解析...")
            url = resolve_baidu_redirect(port, tab_id, url, wait_timeout=10, max_retries=1)
        
        # 按URL分类
        result_type = classify_zhihu_url(url)
        
        filtered.append({
            'title': r['title'],
            'url': url,
            'snippet': r.get('snippet', ''),
            'type': result_type
        })
        print(f"  [{result_type}] {r['title'][:50]}")
        
        if len(filtered) >= max_results:
            break
    
    print(f"[搜索] 找到 {len(filtered)} 个知乎结果")
    return filtered


def classify_zhihu_url(url: str) -> str:
    """根据URL分类知乎页面类型。
    
    知乎URL模式:
    - 问答页面: zhihu.com/question/xxx 或 zhihu.com/question/xxx/answer/xxx
    - 专栏文章: zhuanlan.zhihu.com/p/xxx
    - 其他: zhihu.com/column, zhihu.com/topic 等
    """
    if 'zhihu.com/question' in url or 'zhihu.com/answer' in url:
        return 'question'
    elif 'zhuanlan.zhihu.com' in url:
        return 'column'
    elif 'zhihu.com' in url:
        return 'other_zhihu'
    else:
        return 'non_zhihu'


def fetch_zhihu_question(port: int, tab_id: str, url: str,
                         wait_timeout: int = 20, max_chars: int = 5000) -> Dict:
    """抓取知乎问答页面内容。
    
    知乎问答页面结构:
    - 问题标题: .QuestionHeader-title 或 h1
    - 回答内容: .RichContent-inner (可能有多个回答)
    - 回答者: .AuthorInfo-name
    
    经验总结:
    - 知乎问答页面可能需要登录才能看到完整内容
    - .RichContent-inner 选择器可以提取到展开后的完整回答
    - 需要限制每个回答最大字符数避免过长
    - 首个 .RichContent-inner 可能是问题补充说明而非回答
    """
    print(f"  [问答] 正在抓取: {url[:80]}...")
    
    # 导航到问答页面
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port),
                         "--tab", tab_id, "--goto", url,
                         "--wait-selector", "body", "--timeout", str(wait_timeout)])
    if nav_result.returncode != 0:
        return {'url': url, 'question': '', 'answers': [], 'success': False, 'error': '导航失败'}
    
    time.sleep(1.5)  # 等待页面渲染
    
    # 使用 JS 提取问答内容
    # 返回 JSON 字符串避免中文引号转义问题
    js_code = r"""
(() => {
  const qEl = document.querySelector('.QuestionHeader-title, .QuestionPage-title, h1');
  const question = qEl ? qEl.innerText.trim() : document.title;
  
  const detailEl = document.querySelector('.QuestionHeader-detail, .QuestionDetail');
  const questionDetail = detailEl ? detailEl.innerText.trim() : '';
  
  const answerEls = document.querySelectorAll('.RichContent-inner');
  const answers = [];
  answerEls.forEach((el, i) => {
    if (i >= 5) return;
    const text = el.innerText.trim().substring(0, 5000);
    if (text && text.length > 50) {
      const authorEl = el.closest('.AnswerCard, .List-item, [class*=AnswerItem]')?.querySelector('.AuthorInfo-name, .UserLink-link');
      const author = authorEl ? authorEl.innerText.trim() : '';
      answers.push({author, content: text});
    }
  });
  
  return JSON.stringify({question, questionDetail, answers, answerCount: answers.length});
})()
"""
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                     "--tab", tab_id, "--eval", js_code])
    
    if result.returncode != 0:
        return {'url': url, 'question': '', 'answers': [], 'success': False, 'error': 'JS提取失败'}
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start < 0:
            return {'url': url, 'question': '', 'answers': [], 'success': False, 'error': '输出解析失败'}
        output = json.loads(stdout[json_start:])
        inner_str = output.get('result', '{}')
        inner = json.loads(inner_str)
        
        return {
            'url': url,
            'question': inner.get('question', ''),
            'questionDetail': inner.get('questionDetail', ''),
            'answers': inner.get('answers', []),
            'answerCount': inner.get('answerCount', 0),
            'success': True
        }
    except Exception as e:
        return {'url': url, 'question': '', 'answers': [], 'success': False, 'error': str(e)}


def fetch_zhihu_column(port: int, tab_id: str, url: str,
                        wait_timeout: int = 20, max_chars: int = 5000) -> Dict:
    """抓取知乎专栏文章内容。
    
    知乎专栏页面结构:
    - 标题: .Post-Title 或 h1
    - 作者: .AuthorInfo-name
    - 正文: .Post-RichTextContainer 或 .RichText
    
    经验总结:
    - 专栏文章通常不需要登录即可查看完整内容
    - .Post-RichTextContainer 是正文主容器
    - 需要清理推荐文章、底部标签等无关元素
    - 限制最大字符数避免过长内容
    """
    print(f"  [专栏] 正在抓取: {url[:80]}...")
    
    # 导航到专栏页面
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port),
                         "--tab", tab_id, "--goto", url,
                         "--wait-selector", "body", "--timeout", str(wait_timeout)])
    if nav_result.returncode != 0:
        return {'url': url, 'title': '', 'author': '', 'content': '', 'success': False, 'error': '导航失败'}
    
    time.sleep(1.5)  # 等待页面渲染
    
    # 使用 JS 提取专栏内容
    js_code = r"""
(() => {
  const titleEl = document.querySelector('.Post-Title, h1.Post-Title, .ArticleItem-title, h1');
  const title = titleEl ? titleEl.innerText.trim() : document.title;
  
  const authorEl = document.querySelector('.AuthorInfo-name, .UserLink-link, .Post-Author .AuthorInfo-name');
  const author = authorEl ? authorEl.innerText.trim() : '';
  
  const contentEl = document.querySelector('.Post-RichTextContainer, .Post-RichText, .RichText, .article-content');
  if (!contentEl) return JSON.stringify({title, author, content: '', error: '未找到正文元素'});
  
  // 清理无关元素
  contentEl.querySelectorAll('.RecommendArticle, .BottomTags, .ContentItem-actions, .RichText-actions, .Post-SubContent').forEach(el => el.remove());
  
  const content = contentEl.innerText.trim().substring(0, 5000);
  return JSON.stringify({title, author, content});
})()
"""
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                     "--tab", tab_id, "--eval", js_code])
    
    if result.returncode != 0:
        return {'url': url, 'title': '', 'author': '', 'content': '', 'success': False, 'error': 'JS提取失败'}
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start < 0:
            return {'url': url, 'title': '', 'author': '', 'content': '', 'success': False, 'error': '输出解析失败'}
        output = json.loads(stdout[json_start:])
        inner_str = output.get('result', '{}')
        inner = json.loads(inner_str)
        
        return {
            'url': url,
            'title': inner.get('title', ''),
            'author': inner.get('author', ''),
            'content': inner.get('content', ''),
            'success': bool(inner.get('content', ''))
        }
    except Exception as e:
        return {'url': url, 'title': '', 'author': '', 'content': '', 'success': False, 'error': str(e)}


def save_zhihu_results(results: List[Dict], questions: List[Dict],
                       columns: List[Dict], output_dir: Path, query: str):
    """保存知乎搜索结果到文件。
    
    生成文件:
    - zhihu_search_<query>.json — 完整结构化数据
    - zhihu_search_<query>.md — 人类可读的 Markdown 报告
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_query = query.replace(' ', '_').replace('/', '_')
    
    # 保存 JSON
    json_file = output_dir / f"zhihu_search_{safe_query}.json"
    all_data = {
        'query': query,
        'search_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_results': len(results),
        'questions': questions,
        'columns': columns,
        'search_index': results
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"[保存] JSON: {json_file}")
    
    # 保存 Markdown 报告
    md_file = output_dir / f"zhihu_search_{safe_query}.md"
    lines = []
    lines.append(f"# 知乎搜索结果: {query}")
    lines.append("")
    lines.append(f"> 搜索时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: 百度搜索 site:zhihu.com")
    lines.append(f"> 搜索结果: {len(results)} 个 (知乎问答 {len(questions)} 个, 知乎专栏 {len(columns)} 个)")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 知乎问答
    if questions:
        lines.append("## 一、知乎问答")
        lines.append("")
        for qa in questions:
            lines.append(f"### 问题: {qa.get('question', '未知')}")
            lines.append("")
            lines.append(f"- **链接**: {qa.get('url', '')}")
            lines.append(f"- **回答数**: {qa.get('answerCount', 0)}")
            lines.append("")
            for i, ans in enumerate(qa.get('answers', [])):
                author = ans.get('author', '匿名')
                content = ans.get('content', '')
                lines.append(f"#### 回答 {i+1}" + (f" — {author}" if author else ""))
                lines.append("")
                lines.append(content)
                lines.append("")
        lines.append("---")
        lines.append("")
    
    # 知乎专栏
    if columns:
        lines.append("## 二、知乎专栏文章")
        lines.append("")
        for i, col in enumerate(columns):
            title = col.get('title', '未知')
            url = col.get('url', '')
            author = col.get('author', '')
            content = col.get('content', '')
            lines.append(f"### {i+1}. {title}")
            lines.append("")
            lines.append(f"- **链接**: {url}")
            if author:
                lines.append(f"- **作者**: {author}")
            lines.append(f"- **内容长度**: {len(content)} 字符")
            lines.append("")
            if content:
                lines.append("**内容摘要**:")
                lines.append("")
                summary = content[:800].replace('\n\n\n', '\n\n')
                lines.append(summary)
                if len(content) > 800:
                    lines.append("...")
                lines.append("")
            lines.append("---")
            lines.append("")
    
    # 附录: 搜索结果索引
    lines.append("## 三、附录: 搜索结果索引")
    lines.append("")
    lines.append("| # | 类型 | 标题 | URL |")
    lines.append("|---|------|------|-----|")
    type_labels = {'question': '问答', 'column': '专栏', 'other_zhihu': '其他', 'non_zhihu': '非知乎'}
    for i, r in enumerate(results):
        rtype = type_labels.get(r.get('type', ''), r.get('type', ''))
        title = r.get('title', '')[:40]
        url = r.get('url', '')[:60]
        lines.append(f"| {i+1} | {rtype} | {title} | {url} |")
    
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"[保存] Markdown: {md_file}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大搜索结果数量 (默认: 10)")
    parser.add_argument("--max-detail", type=int, default=5, help="最多抓取详情的专栏文章数 (默认: 5)")
    parser.add_argument("--output-dir", default=str(ZHIHU_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="CDP调试端口 (默认: 9333)")
    parser.add_argument("--name", default="zhihu_session", help="浏览器实例名称（默认 zhihu_session，与其他知乎脚本/登录态共用，避免误开新实例）")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--wait-timeout", type=int, default=20, help="页面等待超时秒数")
    parser.add_argument("--max-chars", type=int, default=5000, help="内容最大字符数")
    parser.add_argument("--no-detail", action="store_true", help="不抓取详情内容，仅获取搜索结果列表")
    
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"知乎内容搜索自动化")
    print(f"关键词: {args.query}")
    print(f"最大结果: {args.max_results}")
    print(f"输出目录: {args.output_dir}")
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
        
        # 2. 搜索知乎内容
        results = search_zhihu_via_baidu(
            port=port, tab_id=tab_id, query=args.query,
            max_results=args.max_results, wait_timeout=args.wait_timeout
        )
        
        if not results:
            print("[警告] 未找到知乎相关搜索结果")
            return 1
        
        # 3. 分类统计
        questions_list = [r for r in results if r['type'] == 'question']
        columns_list = [r for r in results if r['type'] == 'column']
        other_list = [r for r in results if r['type'] == 'other_zhihu']
        
        print(f"\n[分类] 知乎问答: {len(questions_list)} 个")
        print(f"[分类] 知乎专栏: {len(columns_list)} 个")
        print(f"[分类] 知乎其他: {len(other_list)} 个")
        
        # 4. 抓取详情内容
        questions_detail = []
        columns_detail = []
        
        if not args.no_detail:
            # 抓取知乎问答
            for i, q in enumerate(questions_list):
                print(f"\n[问答 {i+1}/{len(questions_list)}] {q['title'][:50]}...")
                detail = fetch_zhihu_question(
                    port=port, tab_id=tab_id, url=q['url'],
                    wait_timeout=args.wait_timeout, max_chars=args.max_chars
                )
                questions_detail.append(detail)
                time.sleep(1.0)
            
            # 抓取知乎专栏 (限制数量避免过多请求)
            columns_to_fetch = columns_list[:args.max_detail]
            for i, c in enumerate(columns_to_fetch):
                print(f"\n[专栏 {i+1}/{len(columns_to_fetch)}] {c['title'][:50]}...")
                detail = fetch_zhihu_column(
                    port=port, tab_id=tab_id, url=c['url'],
                    wait_timeout=args.wait_timeout, max_chars=args.max_chars
                )
                columns_detail.append(detail)
                time.sleep(2.0)  # 专栏之间间隔更长
        
        # 5. 保存结果
        save_zhihu_results(
            results=results, questions=questions_detail,
            columns=columns_detail, output_dir=Path(args.output_dir),
            query=args.query
        )
        
        # 6. 打印摘要
        print(f"\n{'='*60}")
        print(f"搜索完成！")
        print(f"  搜索结果: {len(results)} 个")
        print(f"  知乎问答: {len(questions_list)} 个 (抓取 {len(questions_detail)} 个)")
        print(f"  知乎专栏: {len(columns_list)} 个 (抓取 {len(columns_detail)} 个)")
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
