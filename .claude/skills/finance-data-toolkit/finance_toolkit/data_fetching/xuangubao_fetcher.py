# -*- coding: utf-8 -*-
"""
选股宝数据抓取器
从 xuangubao.com 抓取涨停板、热点板块数据
"""

import json
import re
import sys
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class StockInfo:
    """股票信息"""
    code: str
    name: str
    price: float
    change_pct: float
    limit_up: bool
    sector: str
    source: str = 'xuangubao'
    timestamp: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


def parse_xuangubao_zt(text: str) -> List[Dict[str, Any]]:
    """解析涨停板数据"""
    stocks = []

    # 匹配涨停股格式
    patterns = [
        r'<a[^>]*href="(/[^"]+)"[^>]*>([^<]+)</a>[\s\S]*?<span[^>]*>(\d+\.\d+)</span>[\s\S]*?<span[^>]*>([+-]?\d+\.\d+)%</span>',
        r'([\u4e00-\u9fa5]{2,6})\s*(\d{6})\s*([\d.]+)\s*([+-]?\d+\.\d+)%',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if len(match) >= 4:
                stocks.append({
                    'name': match[1] if len(match) > 1 else match[0],
                    'code': match[2] if len(match) > 2 else '',
                    'price': float(match[3]) if len(match) > 3 and match[3].replace('.', '').isdigit() else 0,
                    'change_pct': float(match[4]) if len(match) > 4 and match[4].replace('.', '').replace('-', '').isdigit() else 0,
                    'limit_up': True,
                })

    return stocks[:30]


def fetch_zt_stocks() -> List[Dict[str, Any]]:
    """抓取涨停板数据"""
    url = 'https://www.xuangubao.com.cn/zt'
    print(f"[选股宝] 抓取涨停板数据: {url}")

    try:
        skill_dir = Path(__file__).parent.parent.parent.parent
        browser_dir = skill_dir / 'browser-cdp' / 'scripts'

        launch_cmd = [sys.executable, str(browser_dir / 'browser_launch.py'),
                      '--dedicated', '--name', 'xuangubao_zt', '--start-url', url]

        result = subprocess.run(launch_cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"浏览器启动失败: {result.stderr}")

        port_match = re.search(r'->\s*[\d.]+:(\d+)', result.stdout)
        if not port_match:
            raise RuntimeError(f"无法解析端口: {result.stdout}")
        port = port_match.group(1)

        import time
        time.sleep(3)

        extract_cmd = [sys.executable, str(browser_dir / 'browser_extract.py'),
                       '--port', port, '--mode', 'text']
        result = subprocess.run(extract_cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        text = result.stdout

        stocks = parse_xuangubao_zt(text)
        print(f"[选股宝] 抓取成功: {len(stocks)} 只涨停股")
        return stocks

    except Exception as e:
        print(f"[选股宝] 抓取失败: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description='选股宝数据抓取工具')
    parser.add_argument('-o', '--output', help='输出文件')
    args = parser.parse_args()

    stocks = fetch_zt_stocks()

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': datetime.now().isoformat(), 'stocks': stocks},
                      f, ensure_ascii=False, indent=2)
        print(f"数据已保存: {args.output}")
    else:
        print(json.dumps({'timestamp': datetime.now().isoformat(), 'stocks': stocks},
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
