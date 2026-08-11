# -*- coding: utf-8 -*-
"""
同花顺股票数据抓取器
从 stockpage.10jqka.com.cn 抓取股票实时行情和基本面数据
"""

import json
import re
import sys
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class FinanceData:
    """统一金融数据契约"""
    source: str
    data_type: str
    symbol: str
    timestamp: str
    payload: Dict[str, Any]
    raw: Optional[Dict] = None
    meta: Optional[Dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_10jqka_stock(text: str, symbol: str) -> Dict[str, Any]:
    """从同花顺页面提取结构化数据"""
    data = {
        'symbol': symbol,
        'name': '',
        'timestamp': datetime.now().isoformat(),
        'source': '10jqka',
    }

    # 提取股票名称
    name_match = re.search(r'<title>([^<]+?)\s*-\s*同花顺', text)
    if name_match:
        data['name'] = name_match.group(1).strip()

    # 提取价格信息 (同花顺页面格式)
    price_match = re.search(r'class=["\']price["\']\s*>\s*([\d.]+)', text)
    if price_match:
        data['price'] = float(price_match.group(1))

    # 提取涨跌幅
    change_match = re.search(r'class=["\']change["\']\s*>\s*([+-]?[\d.]+)%', text)
    if change_match:
        data['change_pct'] = float(change_match.group(1))

    # 提取成交量/额
    vol_match = re.search(r'成交量[：:]\s*([\d.]+)万', text)
    if vol_match:
        data['volume'] = vol_match.group(1)

    amt_match = re.search(r'成交额[：:]\s*([\d.]+)亿', text)
    if amt_match:
        data['amount'] = amt_match.group(1)

    # 提取PE/PB
    pe_match = re.search(r'市盈率[：:]\s*([\d.]+)', text)
    if pe_match:
        data['pe_ttm'] = pe_match.group(1)

    pb_match = re.search(r'市净率[：:]\s*([\d.]+)', text)
    if pb_match:
        data['pb'] = pb_match.group(1)

    # 提取总市值
    mv_match = re.search(r'总市值[：:]\s*([\d.]+)亿', text)
    if mv_match:
        data['total_mv'] = mv_match.group(1)

    # 提取ROE
    roe_match = re.search(r'ROE[：:]\s*([\d.]+)%', text)
    if roe_match:
        data['roe'] = roe_match.group(1)

    return data


def fetch_stock_data(symbol: str, headless: bool = False) -> FinanceData:
    """抓取同花顺股票数据"""
    # 格式化代码
    if symbol.startswith(('6', '9')):
        url_symbol = f'sh{symbol}'
    else:
        url_symbol = f'sz{symbol}'

    url = f'https://stockpage.10jqka.com.cn/{url_symbol}/'

    print(f"[10jqka] 抓取 {symbol} ({url})")

    # 使用 browser-cdp 抓取
    try:
        # 启动浏览器
        skill_dir = Path(__file__).parent.parent.parent.parent
        browser_dir = skill_dir / 'browser-cdp' / 'scripts'

        launch_cmd = [sys.executable, str(browser_dir / 'browser_launch.py'),
                      '--dedicated', '--name', f'stock_{symbol}', '--start-url', url]
        if headless:
            launch_cmd.append('--headless')

        result = subprocess.run(launch_cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"浏览器启动失败: {result.stderr}")

        # 解析端口
        port_match = re.search(r'->\s*[\d.]+:(\d+)', result.stdout)
        if not port_match:
            raise RuntimeError(f"无法解析端口: {result.stdout}")
        port = port_match.group(1)

        # 导航
        nav_cmd = [sys.executable, str(browser_dir / 'browser_nav.py'),
                   '--port', port, '--goto', url]
        subprocess.run(nav_cmd, capture_output=True, timeout=30)

        # 等待加载
        import time
        time.sleep(3)

        # 提取页面文本
        extract_cmd = [sys.executable, str(browser_dir / 'browser_extract.py'),
                       '--port', port, '--mode', 'text']
        result = subprocess.run(extract_cmd, capture_output=True, text=True, encoding='utf-8', timeout=30)
        text = result.stdout

        # 解析数据
        parsed_data = parse_10jqka_stock(text, symbol)

        finance_data = FinanceData(
            source='10jqka',
            data_type='quote',
            symbol=symbol,
            timestamp=datetime.now().isoformat(),
            payload=parsed_data,
            raw={'page_text': text[:3000]},
            meta={'fetch_method': 'browser-cdp', 'url': url}
        )

        print(f"[10jqka] 抓取成功: {parsed_data.get('name', symbol)} 价格={parsed_data.get('price', 'N/A')}")
        return finance_data

    except Exception as e:
        print(f"[10jqka] 抓取失败: {e}")
        return FinanceData(
            source='10jqka',
            data_type='quote',
            symbol=symbol,
            timestamp=datetime.now().isoformat(),
            payload={'error': str(e)},
            meta={'fetch_method': 'browser-cdp', 'url': url}
        )


def fetch_hot_stocks() -> Dict[str, FinanceData]:
    """抓取热点股票列表"""
    url = 'https://data.10jqka.com.cn/funds/hgzjl/'
    print(f"[10jqka] 抓取热点股票列表")
    # 简化实现：返回空字典，实际需要使用 browser-cdp
    return {}


def main():
    parser = argparse.ArgumentParser(description='同花顺股票数据抓取工具')
    parser.add_argument('symbol', help='股票代码')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('-o', '--output', help='输出文件')
    args = parser.parse_args()

    data = fetch_stock_data(args.symbol, headless=args.headless)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(data.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"数据已保存: {args.output}")
    else:
        print(json.dumps(data.to_dict(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()