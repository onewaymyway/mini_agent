"""
browser-site-scraper / members / cdn / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎的探索子agent自动蒸馏生成
（source: explored, distill_source_kind: script_source）。
探索子agent在探索成功后自行判断这次解法具备可参数化复用的形状，直接提交了
以下源码；蒸馏器只做了"沙箱自测 + intent_schema 校验 + 原子落盘"，未对动作
序列做任何猜测或改写。
"""

import json
import urllib.request

def run(input: dict) -> dict:
    url = input.get('target', {}).get('url', '')
    if not url:
        return {'status': 'fail', 'error': 'missing target.url'}
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for item in data:
            results.append({
                'protocol': item.get('protocol'),
                'ip': item.get('ip'),
                'port': item.get('port'),
                'country': item.get('country'),
                'country_code': item.get('country_code'),
                'city': item.get('city'),
                'anonymity': item.get('anonymity'),
                'ssl': item.get('ssl'),
                'uptime_percent': item.get('uptime_percent'),
                'asn': item.get('asn'),
                'isp': item.get('isp'),
                'latency_ms': item.get('latency_ms'),
                'last_checked': item.get('last_checked')
            })
        return {'status': 'success', 'data': {'results': results}}
    except Exception as e:
        return {'status': 'fail', 'error': str(e)}