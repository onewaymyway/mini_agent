#!/usr/bin/env python
"""
arXiv 论文搜索自动化脚本

通过 CDP 控制浏览器访问 arxiv.org，搜索特定关键词的最新论文列表，
获取论文详细信息（标题、作者、摘要、日期、主题分类、PDF链接）。

用法:
    python arxiv_search.py "agent harness" --max-results 10
    python arxiv_search.py "LLM agent" --max-results 5 --no-detail
    python arxiv_search.py "reinforcement learning" --max-results 3 --output-dir ./papers

示例:
    python arxiv_search.py "agent harness" --max-results 10
    python arxiv_search.py "LLM agent" --max-results 5 --no-detail
    python arxiv_search.py "reinforcement learning" --max-results 3 --output-dir ./papers
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
from urllib.parse import quote, urljoin

# 导入 baidu_search 模块复用其函数
sys.path.insert(0, str(Path(__file__).parent))
from baidu_search import (
    ensure_browser, random_delay, get_random_ua,
    run_cmd, PYTHON_CMD, SKILL_DIR
)


ARXIV_OUTPUT_DIR = SKILL_DIR / "search_results"
ARXIV_BASE = "https://arxiv.org"


def search_arxiv_papers(port: int, tab_id: str, query: str, max_results: int = 10,
                        wait_timeout: int = 30) -> List[Dict]:
    """在 arXiv 搜索论文，返回论文列表。
    
    arXiv 搜索URL格式:
        https://arxiv.org/search/?query=xxx&searchtype=all&order=-announced_date_first
    
    搜索结果页 DOM 结构:
        li.arxiv-result — 每个论文结果容器
        p.title.is-5.mathjax — 论文标题
        a[href*=/abs/] — 论文链接 (arXiv ID)
        p.authors — 作者列表
        span.abstract-short — 短摘要
        div.tags — 分类标签 (如 cs.AI, cs.CL)
    """
    print(f"[搜索] 正在搜索 arXiv: {query}")
    
    delay = random_delay(1.0, 2.0)
    print(f"  [延迟] 请求前等待 {delay:.1f} 秒")
    
    # 构建 arXiv 搜索 URL（按最新发布日期排序）
    search_url = f"{ARXIV_BASE}/search/?query={quote(query)}&searchtype=all&order=-announced_date_first"
    print(f"  [URL] {search_url}")
    
    # 导航到搜索结果页
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port),
                         "--tab", tab_id, "--goto", search_url,
                         "--wait-selector", "body", "--timeout", str(wait_timeout)])
    if nav_result.returncode != 0:
        print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
        return []
    
    time.sleep(1.5)  # 等待页面完全渲染
    
    # 使用 JS 提取搜索结果
    js_code = r"""
(() => {
  const items = document.querySelectorAll('li.arxiv-result');
  const results = [];
  items.forEach((item, i) => {
    if (i >= 50) return;
    
    // 论文标题
    const titleEl = item.querySelector('p.title.is-5.mathjax, p.title.is-5');
    const title = titleEl ? titleEl.innerText.trim() : '';
    
    // 论文链接和 ID
    const linkEl = item.querySelector('a[href*="/abs/"]');
    const url = linkEl ? linkEl.href : '';
    const arxivId = url ? url.split('/abs/')[1] : '';
    
    // 作者
    const authorsEl = item.querySelector('p.authors');
    let authors = '';
    if (authorsEl) {
      const authorLinks = authorsEl.querySelectorAll('a');
      authors = Array.from(authorLinks).map(a => a.innerText.trim()).join(', ');
    }
    
    // 摘要（短版本）
    const abstractEl = item.querySelector('span.abstract-short');
    let abstract = abstractEl ? abstractEl.innerText.trim() : '';
    // 去除末尾的 "▽ More"
    abstract = abstract.replace(/▽ More$/, '').replace(/…$/, '').trim();
    
    // 分类标签
    const tagsEl = item.querySelector('div.tags');
    const tags = tagsEl ? tagsEl.innerText.trim().replace(/\s+/g, ' ') : '';
    
    // 提交日期
    const dateEl = item.querySelector('p.is-size-7 a, .is-size-7');
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && url) {
      results.push({title, url, arxivId, authors, abstract, tags, date});
    }
  });
  return JSON.stringify(results);
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
        raw_results = json.loads(output.get('result', '[]'))
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[警告] 无法解析JS结果: {e}")
        return []
    
    # 限制结果数量
    filtered = raw_results[:max_results]
    
    print(f"[搜索] 找到 {len(raw_results)} 个结果，取前 {len(filtered)} 个")
    for i, r in enumerate(filtered):
        print(f"  [{i+1}] {r.get('arxivId', '')} — {r['title'][:60]}")
    
    return filtered

def fetch_arxiv_paper_detail(port: int, tab_id: str, url: str,
                             wait_timeout: int = 20) -> Dict:
    """获取 arXiv 论文详情页的完整信息。
    
    论文详情页 DOM 结构:
        h1.title.mathjax — 论文标题
        .authors a — 作者列表（每个作者一个 <a>）
        .abstract.mathjax — 完整摘要
        .dateline — 提交日期
        .subjects — 主题分类
        a[href*=/pdf/] — PDF 下载链接
    
    经验总结:
    - arXiv 详情页不需要登录，可直接访问
    - 摘要使用 .abstract.mathjax 选择器，需去掉 "Abstract:" 前缀
    - 作者列表通过 .authors a 获取，每个 <a> 是一个作者
    - PDF 链接格式: https://arxiv.org/pdf/<arxivId>
    """
    print(f"  [详情] 正在获取: {url}")
    
    # 请求前随机延迟
    delay = random_delay(1.0, 2.0)
    
    # 导航到论文详情页
    nav_result = run_cmd([PYTHON_CMD, "browser_nav.py", "--port", str(port),
                         "--tab", tab_id, "--goto", url,
                         "--wait-selector", "body", "--timeout", str(wait_timeout)])
    if nav_result.returncode != 0:
        return {'url': url, 'success': False, 'error': '导航失败'}
    
    time.sleep(1.5)  # 等待页面渲染
    
    # 使用 JS 提取论文详情
    js_code = r"""
(() => {
  const result = {};
  
  // 标题
  const titleEl = document.querySelector('h1.title.mathjax');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 作者列表
  const authorEls = document.querySelectorAll('.authors a');
  result.authors = Array.from(authorEls).map(a => a.innerText.trim()).filter(n => n);
  
  // 完整摘要
  const abstractEl = document.querySelector('.abstract.mathjax');
  let abstract = abstractEl ? abstractEl.innerText.trim() : '';
  // 去掉 "Abstract:" 前缀
  if (abstract.startsWith('Abstract:')) {
    abstract = abstract.substring('Abstract:'.length).trim();
  } else if (abstract.startsWith('Abstract')) {
    abstract = abstract.substring('Abstract'.length).trim();
  }
  result.abstract = abstract;
  
  // 提交日期
  const dateEl = document.querySelector('.dateline');
  result.date = dateEl ? dateEl.innerText.trim() : '';
  
  // 主题分类
  const subjectsEl = document.querySelector('.subjects');
  result.subjects = subjectsEl ? subjectsEl.innerText.trim().replace(/\s+/g, ' ') : '';
  
  // 评论信息（如有）
  const commentsEl = document.querySelector('.tablecell.comments');
  result.comments = commentsEl ? commentsEl.innerText.trim() : '';
  
  // PDF 链接
  const pdfLink = document.querySelector('a[href*="/pdf/"]');
  result.pdfUrl = pdfLink ? pdfLink.href : '';
  
  // arXiv ID
  const canonicalEl = document.querySelector('link[rel="canonical"]');
  result.arxivId = canonicalEl ? canonicalEl.href.split('/abs/')[1] : '';
  
  return JSON.stringify(result);
})()
"""
    
    result = run_cmd([PYTHON_CMD, "browser_console.py", "--port", str(port),
                     "--tab", tab_id, "--eval", js_code])
    
    if result.returncode != 0:
        return {'url': url, 'success': False, 'error': f'JS提取失败: {result.stderr[:100]}'}
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start < 0:
            return {'url': url, 'success': False, 'error': '输出解析失败'}
        output = json.loads(stdout[json_start:])
        detail = json.loads(output.get('result', '{}'))
        
        detail['url'] = url
        detail['success'] = bool(detail.get('title', ''))
        
        if detail['success']:
            print(f"  [详情] 标题: {detail['title'][:60]}")
            print(f"  [详情] 作者: {', '.join(detail.get('authors', [])[:3])}...")
            print(f"  [详情] 摘要长度: {len(detail.get('abstract', ''))} 字符")
        
        return detail
    except Exception as e:
        return {'url': url, 'success': False, 'error': str(e)}


def save_arxiv_results(results: List[Dict], details: List[Dict],
                        output_dir: Path, query: str):
    """保存 arXiv 搜索结果到文件。
    
    生成文件:
    - arxiv_search_<query>.json — 完整结构化数据
    - arxiv_search_<query>.md — 人类可读的 Markdown 报告
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_query = query.replace(' ', '_').replace('/', '_')
    
    # 保存 JSON
    json_file = output_dir / f"arxiv_search_{safe_query}.json"
    all_data = {
        'query': query,
        'search_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_results': len(results),
        'fetched_details': len(details),
        'search_index': results,
        'paper_details': details
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"[保存] JSON: {json_file}")
    
    # 保存 Markdown 报告
    md_file = output_dir / f"arxiv_search_{safe_query}.md"
    lines = []
    lines.append(f"# arXiv 论文搜索结果: {query}")
    lines.append("")
    lines.append(f"> 搜索时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: arxiv.org (按最新发布日期排序)")
    lines.append(f"> 搜索结果: {len(results)} 篇 (获取详情: {len(details)} 篇)")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 论文详情
    if details:
        lines.append("## 论文详情")
        lines.append("")
        for i, d in enumerate(details):
            title = d.get('title', '未知')
            arxiv_id = d.get('arxivId', '')
            url = d.get('url', '')
            authors = d.get('authors', [])
            abstract = d.get('abstract', '')
            date = d.get('date', '')
            subjects = d.get('subjects', '')
            pdf_url = d.get('pdfUrl', '')
            comments = d.get('comments', '')
            
            lines.append(f"### {i+1}. {title}")
            lines.append("")
            if arxiv_id:
                lines.append(f"- **arXiv ID**: `{arxiv_id}`")
            if url:
                lines.append(f"- **链接**: {url}")
            if pdf_url:
                lines.append(f"- **PDF**: {pdf_url}")
            if authors:
                lines.append(f"- **作者**: {', '.join(authors)}")
            if date:
                lines.append(f"- **日期**: {date}")
            if subjects:
                lines.append(f"- **主题**: {subjects}")
            if comments:
                lines.append(f"- **评论**: {comments}")
            lines.append("")
            if abstract:
                lines.append("**摘要**:")
                lines.append("")
                lines.append(abstract)
                lines.append("")
            lines.append("---")
            lines.append("")
    
    # 附录: 搜索结果索引
    lines.append("## 附录: 搜索结果索引")
    lines.append("")
    lines.append("| # | arXiv ID | 标题 | 作者 | 标签 |")
    lines.append("|---|----------|------|------|------|")
    for i, r in enumerate(results):
        aid = r.get('arxivId', '')
        title = r.get('title', '')[:40]
        authors = r.get('authors', '')[:30]
        tags = r.get('tags', '')[:20]
        lines.append(f"| {i+1} | {aid} | {title} | {authors} | {tags} |")
    
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
    parser.add_argument("--max-detail", type=int, default=5, help="最多获取详情的论文数 (默认: 5)")
    parser.add_argument("--output-dir", default=str(ARXIV_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="CDP调试端口 (默认: 9333)")
    parser.add_argument("--name", default="arxiv_search", help="浏览器实例名称")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="页面等待超时秒数")
    parser.add_argument("--no-detail", action="store_true", help="不获取论文详情，仅搜索结果列表")
    
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"arXiv 论文搜索自动化")
    print(f"关键词: {args.query}")
    print(f"最大结果: {args.max_results}")
    print(f"输出目录: {args.output_dir}")
    print(f"{'='*60}")
    
    try:
        # 1. 确保浏览器运行
        user_agent = get_random_ua()
        browser_info = ensure_browser(
            port=args.port, name=args.name, headless=args.headless,
            start_url="https://arxiv.org", user_agent=user_agent
        )
        port = browser_info["port"]
        tab_id = browser_info["tab_id"]
        
        # 2. 搜索论文
        results = search_arxiv_papers(
            port=port, tab_id=tab_id, query=args.query,
            max_results=args.max_results, wait_timeout=args.wait_timeout
        )
        
        if not results:
            print("[警告] 未找到搜索结果")
            return 1
        
        # 3. 获取论文详情
        details = []
        if not args.no_detail:
            papers_to_fetch = results[:args.max_detail]
            print(f"\n[详情] 正在获取 {len(papers_to_fetch)} 篇论文的详细信息...")
            for i, paper in enumerate(papers_to_fetch):
                print(f"\n[{i+1}/{len(papers_to_fetch)}] {paper.get('arxivId', '')} — {paper['title'][:50]}...")
                detail = fetch_arxiv_paper_detail(
                    port=port, tab_id=tab_id, url=paper['url'],
                    wait_timeout=args.wait_timeout
                )
                details.append(detail)
                time.sleep(1.5)  # 论文之间间隔
        
        # 4. 保存结果
        save_arxiv_results(
            results=results, details=details,
            output_dir=Path(args.output_dir), query=args.query
        )
        
        # 5. 打印摘要
        print(f"\n{'='*60}")
        print(f"搜索完成！")
        print(f"  搜索结果: {len(results)} 篇")
        print(f"  获取详情: {len(details)} 篇")
        print(f"{'='*60}")
        for i, r in enumerate(results[:10], 1):
            status = "✓" if i <= len(details) else "○"
            print(f"  {i}. {status} {r.get('arxivId', '')} — {r['title'][:60]}")
        
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
