"""
browser-site-scraper / members / free-proxy-list / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎的探索子agent自动蒸馏生成
（source: explored, distill_source_kind: script_source）。
探索子agent在探索成功后自行判断这次解法具备可参数化复用的形状，直接提交了
以下源码；蒸馏器只做了"沙箱自测 + intent_schema 校验 + 原子落盘"，未对动作
序列做任何猜测或改写。
"""

#!/usr/bin/env python3
"""抓取 free-proxy-list.net 的代理列表"""
import re
from bs4 import BeautifulSoup

def parse_text_excerpt(text_excerpt):
    """从 tab-separated 文本中提取代理数据"""
    results = []
    lines = text_excerpt.strip().split('\n')
    for line in lines[1:]:  # 跳过表头
        parts = line.split('\t')
        if len(parts) >= 9:
            results.append({
                'ip': parts[0].strip(),
                'port': parts[1].strip(),
                'code': parts[2].strip(),
                'country': parts[3].strip(),
                'anonymity': parts[4].strip(),
                'google': parts[5].strip(),
                'https': parts[6].strip(),
                'last_checked': parts[7].strip(),
            })
    return results

def run(input: dict) -> dict:
    try:
        url = input.get('target', {}).get('url', '')
        if not url:
            return {'status': 'fail', 'error': '缺少 target.url'}
        if 'free-proxy-list.net' not in url:
            return {'status': 'fail', 'error': '仅支持 free-proxy-list.net 站点'}
        from tool_runtime import get_tool_executor
        executor = get_tool_executor()
        nav_result = executor('browser_navigate', {'url': url})
        if not nav_result.get('ok'):
            return {'status': 'fail', 'error': f'导航失败: {nav_result}'}
        executor('browser_wait_for_selector', {'selector': 'table'})
        extract_result = executor('browser_extract_content', {'selector': 'table'})
        if not extract_result.get('ok'):
            return {'status': 'fail', 'error': '提取失败'}
        data = extract_result.get('data', {})
        text = data.get('text_excerpt', '')
        if not text:
            return {'status': 'fail', 'error': '未找到代理数据'}
        results = parse_text_excerpt(text)
        return {'status': 'success', 'data': {'results': results}}
    except Exception as e:
        return {'status': 'fail', 'error': str(e)}