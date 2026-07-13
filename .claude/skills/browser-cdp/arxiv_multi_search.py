#!/usr/bin/env python
"""
arXiv 多关键词论文搜索脚本

通过 CDP 控制浏览器访问 arxiv.org，使用多个关键词搜索论文，
自动合并去重，批量获取论文详情。

用法:
    python arxiv_multi_search.py "self-evolving agent" "autonomous agent evolution" "agent self-improvement"
    python arxiv_multi_search.py --keywords keywords.txt --max-results 50
    python arxiv_multi_search.py "LLM agent" --max-detail 20 --output-dir ./papers

示例:
    # 自主进化 Agent 相关论文搜索
    python arxiv_multi_search.py "self-evolving agent" "autonomous agent evolution" \
        "agent self-improvement" "LLM agent adaptation" "evolutionary agent"
    
    # 从文件读取关键词列表
    python arxiv_multi_search.py --keywords search_keywords.txt --max-results 100
"""

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Set
from urllib.parse import quote

# 导入 baidu_search 模块复用其函数
sys.path.insert(0, str(Path(__file__).parent))
from baidu_search import (
    ensure_browser, random_delay, get_random_ua,
    run_cmd, PYTHON_CMD, SKILL_DIR
)


ARXIV_OUTPUT_DIR = SKILL_DIR / "search_results"
ARXIV_BASE = "https://arxiv.org"


def search_arxiv_papers(port: int, tab_id: str, query: str, max_results: int = 50,
                        wait_timeout: int = 30) -> List[Dict]:
    """在 arXiv 搜索论文，返回论文列表。
    
    arXiv 搜索 URL 格式:
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
    
    // 作者（返回数组格式，便于后续处理）
    const authorsEl = item.querySelector('p.authors');
    let authors = [];
    if (authorsEl) {
      const authorLinks = authorsEl.querySelectorAll('a');
      authors = Array.from(authorLinks).map(a => a.innerText.trim()).filter(n => n);
    }
    
    // 摘要（短版本）
    const abstractEl = item.querySelector('span.abstract-short');
    let abstract = abstractEl ? abstractEl.innerText.trim() : '';
    // 去除末尾的 "▽ More" 和省略号，也处理开头的省略号
    abstract = abstract.replace(/▽ More$/, '').replace(/…$/, '').replace(/^…/, '').trim();
    
    // 分类标签
    const tagsEl = item.querySelector('div.tags');
    const tags = tagsEl ? tagsEl.innerText.trim().replace(/\s+/g, ' ') : '';
    
    // 提交日期（清理无用文本）
    const dateEl = item.querySelector('p.is-size-7 a, .is-size-7');
    let date = dateEl ? dateEl.innerText.trim() : '';
    // 去除 "▽ More"、"doi" 等无用后缀
    date = date.replace(/▽ More$/, '').replace(/^doi$/, '').trim();
    
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
        print(f"[警告] JS 提取失败: {result.stderr[:200]}")
        return []
    
    try:
        stdout = result.stdout.strip()
        json_start = stdout.find('{')
        if json_start < 0:
            return []
        output = json.loads(stdout[json_start:])
        raw_results = json.loads(output.get('result', '[]'))
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[警告] 无法解析 JS 结果: {e}")
        return []
    
    # 限制结果数量
    filtered = raw_results[:max_results]
    
    print(f"[搜索] 找到 {len(raw_results)} 个结果，取前 {len(filtered)} 个")
    for i, r in enumerate(filtered):
        arxiv_id = r.get('arxivId', '')
        title = r['title'][:60]
        # 作者处理：可能是字符串或数组
        authors = r.get('authors', [])
        if isinstance(authors, list):
            author_str = ', '.join(authors[:3]) + ('...' if len(authors) > 3 else '')
        else:
            author_str = str(authors)[:30]
        print(f"  [{i+1}] {arxiv_id} — {title} ({author_str})")
    
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
        return {'url': url, 'success': False, 'error': f'JS 提取失败: {result.stderr[:100]}'}
    
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


def merge_and_deduplicate(all_results: List[List[Dict]]) -> List[Dict]:
    """合并多个搜索结果并去重。
    
    去重策略: 基于 arXiv ID 去重，保留第一个出现的记录。
    """
    seen_ids: Set[str] = set()
    merged: List[Dict] = []
    
    for results in all_results:
        for paper in results:
            arxiv_id = paper.get('arxivId', '')
            if arxiv_id and arxiv_id not in seen_ids:
                seen_ids.add(arxiv_id)
                merged.append(paper)
    
    return merged


def save_arxiv_results(results: List[Dict], details: List[Dict],
                        output_dir: Path, query: str, keywords: List[str]):
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
        'keywords': keywords,
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
    lines.append(f"> 搜索关键词: {', '.join(keywords)}")
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
    
    # 附录：搜索结果索引
    lines.append("## 附录：搜索结果索引")
    lines.append("")
    lines.append("| # | arXiv ID | 标题 | 作者 | 标签 |")
    lines.append("|---|----------|------|------|------|")
    for i, r in enumerate(results):
        aid = r.get('arxivId', '')
        title = r.get('title', '')[:40]
        # 作者处理：可能是字符串或数组
        authors = r.get('authors', [])
        if isinstance(authors, list):
            author_str = ', '.join(authors[:2])[:30]
        else:
            author_str = str(authors)[:30]
        tags = r.get('tags', '')[:20]
        lines.append(f"| {i+1} | {aid} | {title} | {author_str} | {tags} |")
    
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"[保存] Markdown: {md_file}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("query", nargs='+', help="搜索关键词（可多个）")
    parser.add_argument("--keywords-file", default=None, help="从文件读取关键词列表（每行一个）")
    parser.add_argument("--max-results", type=int, default=15, help="每个关键词的最大搜索结果数量 (默认: 15)")
    parser.add_argument("--max-detail", type=int, default=35, help="最多获取详情的论文数 (默认: 35)")
    parser.add_argument("--output-dir", default=str(ARXIV_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="CDP 调试端口 (默认: 9333)")
    parser.add_argument("--name", default="arxiv_multi", help="浏览器实例名称")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="页面等待超时秒数")
    parser.add_argument("--no-detail", action="store_true", help="不获取论文详情，仅搜索结果列表")
    
    args = parser.parse_args()
    
    # 处理关键词
    keywords = args.query
    if args.keywords_file:
        kw_path = Path(args.keywords_file)
        if kw_path.exists():
            with open(kw_path, 'r', encoding='utf-8') as f:
                keywords = [line.strip() for line in f if line.strip()]
            print(f"[加载] 从 {kw_path} 加载 {len(keywords)} 个关键词")
    
    if not keywords:
        print("[错误] 没有提供搜索关键词")
        return 1
    
    print(f"{'='*60}")
    print(f"arXiv 多关键词论文搜索")
    print(f"关键词数量: {len(keywords)}")
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
        
        # 2. 多关键词搜索
        all_results: List[List[Dict]] = []
        for i, keyword in enumerate(keywords, 1):
            print(f"\n[{i}/{len(keywords)}] 搜索: {keyword}")
            results = search_arxiv_papers(
                port=port, tab_id=tab_id, query=keyword,
                max_results=args.max_results, wait_timeout=args.wait_timeout
            )
            all_results.append(results)
            
            # 合并去重后的总数
            merged = merge_and_deduplicate(all_results)
            print(f"  当前累计: {len(merged)} 篇 (去重后)")
            
            # 如果已达到目标数量，提前停止
            if len(merged) >= 40:
                print("  已达到 40 篇，停止搜索")
                break
            
            # 关键词之间间隔
            time.sleep(2.0)
        
        # 3. 合并去重
        all_papers = merge_and_deduplicate(all_results)
        print(f"\n[搜索完成] 共获取 {len(all_papers)} 篇去重论文")
        
        # 保存中间结果
        temp_file = SKILL_DIR / "temp_data" / "arxiv_multi_search_temp.json"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(all_papers, f, ensure_ascii=False, indent=2)
        print(f"中间结果已保存: {temp_file}")
        
        # 4. 获取论文详情
        details = []
        if not args.no_detail:
            papers_to_fetch = all_papers[:args.max_detail]
            print(f"\n[详情] 正在获取 {len(papers_to_fetch)} 篇论文的详细信息...")
            
            for i, paper in enumerate(papers_to_fetch, 1):
                print(f"\n[{i}/{len(papers_to_fetch)}] {paper.get('arxivId', '')} — {paper['title'][:50]}...")
                detail = fetch_arxiv_paper_detail(
                    port=port, tab_id=tab_id, url=paper['url'],
                    wait_timeout=args.wait_timeout
                )
                details.append(detail)
                time.sleep(1.5)  # 论文之间间隔
            
            success_count = sum(1 for d in details if d.get('success'))
            print(f"\n[详情完成] 成功: {success_count}/{len(details)}")
        
        # 5. 保存结果
        save_arxiv_results(
            results=all_papers, details=details,
            output_dir=Path(args.output_dir),
            query=' '.join(keywords), keywords=keywords
        )
        
        # 6. 打印摘要
        print(f"\n{'='*60}")
        print(f"完成！共 {len(all_papers)} 篇论文，{len(details)} 篇有详情")
        print(f"{'='*60}")
        
        # 显示前 10 篇
        for i, r in enumerate(all_papers[:10], 1):
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
