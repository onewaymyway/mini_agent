"""
browser-site-scraper / members / blog / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎的探索子agent自动蒸馏生成
（source: explored, distill_source_kind: script_source）。
探索子agent在探索成功后自行判断这次解法具备可参数化复用的形状，直接提交了
以下源码；蒸馏器只做了"沙箱自测 + intent_schema 校验 + 原子落盘"，未对动作
序列做任何猜测或改写。
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSDN博客RAG+向量数据库文章抓取脚本

Usage:
    python scrape_csdn.py --url <article_url>

Returns structured results matching intent_schema.
"""
import re
import sys
import argparse
import json
import urllib.request
import urllib.error


def fetch_page(url: str) -> str:
    """Fetch page content with basic error handling."""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
                    try:
                        return raw.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return raw.decode('utf-8', errors='replace')
        except urllib.error.URLError as e:
            if attempt == 2:
                raise RuntimeError(f'Failed to fetch {url}: {e}')


def parse_article(html: str) -> dict:
    """Extract structured data from CSDN article HTML."""
    # Title
    title_m = re.search(r'<title>([^<]+)</title>', html)
    title = title_m.group(1).split('_')[0].strip() if title_m else ''

    # Meta description
    desc_m = re.search(r'meta property="og:description" content="([^"]+)"', html)
    summary = desc_m.group(1) if desc_m else ''

    # Article body text
    body_m = re.search(r'class="article_content[^"]*"[^>]*>(.*?)</div>\s*<script', html, re.DOTALL)
    if not body_m:
        body_m = re.search(r'class="blog-content-box[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    if not body_m:
        body_m = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)

    text = ''
    if body_m:
        text = re.sub(r'<[^>]+>', '\n', body_m.group(1))
        text = re.sub(r'\s+', ' ', text).strip()

    return {'title': title, 'summary': summary, 'text': text}


def extract_results(parsed: dict, query: str) -> list:
    """Extract structured results from parsed article content."""
    results = []

    # Article info
    results.append({
        'type': 'article_info',
        'title': parsed['title'],
        'url': '',
        'summary': parsed['summary'][:200] if parsed['summary'] else '',
    })

    # Key content paragraphs containing relevant keywords
    kw_set = {kw.lower() for kw in query.replace('，', ',').split(',') if kw.strip()}
    kw_set.update({'chromadb', 'milvus', 'faiss', 'lancedb', 'recall', 'qps',
                   '延迟', '内存', 'embedding', 'rag', '检索', '性能'})

    lines = re.split(r'[\n\r]+', parsed['text'])
    seen = set()
    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        if line in seen:
            continue
        if any(kw in line.lower() for kw in kw_set):
            results.append({'type': 'content', 'section': line[:300]})
            seen.add(line)

    return results


def run(input: dict) -> dict:
    """
    Main entry point.

    Args:
        input: dict with required fields:
            - target.url (str): URL of the CSDN article to scrape
            - text (str): Natural language description of the scraping intent
        Optional:
            - query (str): Keywords for filtering relevant content

    Returns:
        dict with 'status' and 'data' keys;
        data contains 'results' array matching intent_schema.
    """
    # Validate required fields
    target = input.get('target', {})
    url = target.get('url', '') if isinstance(target, dict) else ''
    if not url:
        return {'status': 'fail', 'data': None,
                'error': 'missing required field: target.url'}

    query = input.get('query', '')
    try:
        html = fetch_page(url)
        parsed = parse_article(html)
        results = extract_results(parsed, query)
        return {'status': 'success', 'data': {'results': results}}
    except Exception as e:
        return {'status': 'fail', 'data': None, 'error': str(e)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Scrape CSDN RAG/vector DB article')
    parser.add_argument('--url', required=True, help='CSDN article URL')
    parser.add_argument('--query', default='', help='Keywords for content filtering')
    args = parser.parse_args()

    result = run({'target': {'url': args.url}, 'query': args.query, 'text': args.query})
    print(json.dumps(result, ensure_ascii=False, indent=2))