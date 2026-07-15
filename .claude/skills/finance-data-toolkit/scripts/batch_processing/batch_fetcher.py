# -*- coding: utf-8 -*-
"""
批量数据获取器
统一了 batch_fetch_stocks.py 和 batch_fetch_kline.py 的功能
符合 finance-data-toolkit 架构
"""

import json
import sys
import argparse
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed


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


def run_browser_script(script_name: str, args: list) -> str:
    """运行 browser-cdp 脚本并返回输出"""
    script_path = Path(__file__).parent.parent.parent.parent / 'browser-cdp' / script_name
    cmd = [sys.executable, str(script_path)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"{script_name} 执行失败: {result.stderr}")
    return result.stdout


def fetch_single_stock(symbol: str, headless: bool = True, output_dir: Path = None) -> Dict[str, Any]:
    """抓取单只股票数据 (东方财富)"""
    # 格式化代码: 603000 -> sh603000
    if symbol.startswith(('6', '9')):
        url_symbol = f'sh{symbol}'
    else:
        url_symbol = f'sz{symbol}'
    
    url = f'https://quote.eastmoney.com/{url_symbol}.html'
    
    print(f"[1/4] 启动/复用浏览器实例...")
    launch_args = ['--dedicated', '--name', f'stock_{symbol}', '--start-url', url]
    if headless:
        launch_args.append('--headless')
    launch_output = run_browser_script('browser_launch.py', launch_args)
    
    # 解析端口和tab_id
    import re
    port_match = re.search(r'->\s*\d+\.\d+\.\d+\.\d+:(\d+)', launch_output)
    if not port_match:
        port_match = re.search(r'->\s*127\.0\.0\.1:(\d+)', launch_output)
    if not port_match:
        port_match = re.search(r'->\s*[\d.]+:(\d+)', launch_output)
    
    tab_match = re.search(r'首个 tab id=(\S+)', launch_output)
    if not tab_match:
        tab_match = re.search(r'tab id=(\S+)', launch_output)
    if not tab_match:
        tab_match = re.search(r'id=([A-F0-9]{32})', launch_output)
    
    if not port_match:
        raise RuntimeError(f"无法解析浏览器端口: {launch_output}")
    
    port = port_match.group(1)
    
    if not tab_match:
        print(f"[*] 未检测到tab_id,尝试获取端口 {port} 的tab列表...")
        list_output = run_browser_script('browser_launch.py', ['--list', '--port', str(port)])
        try:
            tabs = json.loads(list_output)
            if tabs and len(tabs) > 0:
                tab_id = tabs[0]['id']
                print(f"[*] 使用第一个tab: {tab_id}")
            else:
                raise RuntimeError(f"tab列表为空: {list_output}")
        except json.JSONDecodeError:
            tab_match = re.search(r'id=([A-F0-9]{32})', list_output)
            if not tab_match:
                raise RuntimeError(f"无法获取tab_id: {list_output}")
            tab_id = tab_match.group(1)
    else:
        tab_id = tab_match.group(1)
    
    print(f"[*] 使用tab_id: {tab_id}")
    
    print(f"[2/4] 导航到 {url}...")
    run_browser_script('browser_nav.py', ['--port', port, '--tab', tab_id, '--goto', url])
    
    print(f"[3/4] 等待页面完全加载并提取内容...")
    time.sleep(3)
    
    # 执行JavaScript获取实时行情数据
    js_code = r"""
    (function() {
        var data = {};
        
        var priceEl = document.querySelector('.quote_quotenums') || document.querySelector('.quote3l_l');
        if (priceEl) {
            var text = priceEl.innerText.trim();
            var lines = text.split('\n');
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line && /^\d+\.\d+$/.test(line)) {
                    data.price = line;
                    if (i + 1 < lines.length) {
                        var nextLine = lines[i+1].trim();
                        if (nextLine && /^[+-]?\d+\.\d+%$/.test(nextLine)) {
                            data.change_pct = nextLine.replace('%', '');
                        }
                    }
                    break;
                }
            }
        }
        
        var stockCode = document.querySelector('[data-code], [data-stock-code]');
        if (stockCode) data.stockCode = stockCode.getAttribute('data-code') || stockCode.getAttribute('data-stock-code');
        
        return JSON.stringify(data);
    })();
    """
    
    js_result = run_browser_script('browser_console.py', ['--port', port, '--tab', tab_id, '--eval', js_code])
    
    js_data = {}
    try:
        js_data = json.loads(js_result.strip())
    except:
        pass
    
    print(f"[3/4] 提取页面内容...")
    text = run_browser_script('browser_extract.py', ['--port', port, '--tab', tab_id, '--mode', 'text'])
    
    print(f"[4/4] 解析结构化数据...")
    # 这里简化解析，实际应调用 eastmoney_fetcher.parse_eastmoney_stock
    data = {'symbol': symbol, 'source': 'eastmoney', 'timestamp': datetime.now().isoformat()}
    
    # 简单的正则提取
    price_match = re.search(r'(\d+\.\d+)\s+([+-]\d+\.\d+)\(([+-]\d+\.\d+)%\)', text)
    if price_match:
        data['price'] = float(price_match.group(1))
        data['change_amt'] = float(price_match.group(2))
        data['change_pct'] = float(price_match.group(3))
    
    if js_data.get('price'):
        data['price'] = float(js_data['price'])
    if js_data.get('change_pct'):
        data['change_pct'] = float(js_data['change_pct'])
    
    data['_raw_text'] = text[:5000]
    
    # 保存文件
    if output_dir:
        output_path = output_dir / f'{symbol}_eastmoney_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"数据已保存至: {output_path}")
    
    return {'symbol': symbol, 'success': True, 'data': data}


def fetch_single_kline(symbol: str, port: int, tab_id: str, scale: str = '240', datalen: int = 1023) -> Dict[str, Any]:
    """抓取单只股票K线数据 (东方财富)"""
    market = '1' if symbol.startswith('6') else '0'
    
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={market}.{symbol}&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt={scale}&fqt=1&beg=0&end=20500101&smplmt=460&lmt=1000000"
    
    run_browser_script('browser_nav.py', ['--port', str(port), '--tab', tab_id, '--goto', url])
    time.sleep(1)
    
    result = run_browser_script('browser_extract.py', ['--port', str(port), '--tab', tab_id, '--mode', 'text', '--max-chars', '200000'])
    
    # 解析JSONP
    idx = result.find('var=(')
    if idx < 0:
        return {'symbol': symbol, 'error': 'Invalid response: no var=('}
    end = result.rfind(');')
    if end < 0:
        return {'symbol': symbol, 'error': 'Invalid response: no );'}
    json_str = result[idx + 5:end]
    data = json.loads(json_str)
    
    if not data:
        return {'symbol': symbol, 'error': 'Empty data'}
    
    # 转换为标准格式
    kline = []
    for row in data:
        parts = row.split(',')
        if len(parts) >= 6:
            kline.append({
                'date': parts[0],
                'open': float(parts[1]),
                'close': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'volume': int(parts[5]),
            })
    
    return {'symbol': symbol, 'success': True, 'kline': kline}


def batch_fetch_stocks(symbols: List[str], headless: bool = True, output_dir: str = './temp/batch_results',
                       delay: float = 2.0, max_workers: int = 1) -> Dict[str, Any]:
    """批量抓取股票实时行情"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print('=' * 60)
    print(f'批量抓取开始: {len(symbols)} 只股票')
    print(f'输出目录: {output_path}')
    print(f'间隔: {delay}秒')
    print('=' * 60)
    
    results = []
    success_count = 0
    fail_count = 0
    
    for i, symbol in enumerate(symbols, 1):
        print(f'\n[{i}/{len(symbols)}] 抓取 {symbol}...')
        try:
            result = fetch_single_stock(symbol, headless=headless, output_dir=output_path)
            results.append(result)
            
            if result.get('success'):
                success_count += 1
                data = result.get('data', {})
                print(f'  ✓ 成功: {data.get("name", "")} ¥{data.get("price", "N/A")} ({data.get("change_pct", "N/A")}%)')
            else:
                fail_count += 1
                print(f'  ✗ 失败: {result.get("error", "Unknown")}')
        except Exception as e:
            fail_count += 1
            results.append({'symbol': symbol, 'success': False, 'error': str(e)})
            print(f'  ✗ 异常: {e}')
        
        if i < len(symbols):
            time.sleep(delay)
    
    # 生成汇总报告
    summary = {
        'timestamp': datetime.now().isoformat(),
        'total': len(symbols),
        'success': success_count,
        'failed': fail_count,
        'symbols': symbols,
        'results': results
    }
    summary_path = output_path / f'batch_summary_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    
    print('\n' + '=' * 60)
    print('批量抓取完成')
    print(f'成功: {success_count}, 失败: {fail_count}')
    print(f'汇总报告: {summary_path}')
    print('=' * 60)
    
    return summary


def batch_fetch_klines(symbols: List[str], port: int, tab_id: str, scale: str = '240',
                       datalen: int = 1023, delay: float = 1.0, output_dir: str = './temp/kline_results') -> Dict[str, Any]:
    """批量抓取K线数据"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print('=' * 60)
    print(f'批量K线抓取开始: {len(symbols)} 只股票')
    print(f'周期: {scale}, 数据量: {datalen}')
    print('=' * 60)
    
    results = []
    success_count = 0
    fail_count = 0
    
    for i, symbol in enumerate(symbols):
        print(f'[{i+1}/{len(symbols)}] 获取 {symbol}...')
        try:
            result = fetch_single_kline(symbol, port, tab_id, scale, datalen)
            results.append(result)
            
            if result.get('success'):
                success_count += 1
                print(f'  ✓ 成功: {len(result.get("kline", []))} 条K线')
            else:
                fail_count += 1
                print(f'  ✗ 失败: {result.get("error", "Unknown")}')
        except Exception as e:
            fail_count += 1
            results.append({'symbol': symbol, 'success': False, 'error': str(e)})
            print(f'  ✗ 异常: {e}')
        
        if i < len(symbols) - 1:
            time.sleep(delay)
    
    # 保存结果
    output_data = {
        'timestamp': datetime.now().isoformat(),
        'total': len(symbols),
        'success': success_count,
        'failed': fail_count,
        'results': results
    }
    output_file = output_path / f'kline_batch_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f'\n结果已保存到: {output_file}')
    print(f'成功: {success_count}, 失败: {fail_count}')
    
    return output_data


def main():
    parser = argparse.ArgumentParser(description='批量数据获取工具')
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # 批量抓取股票行情
    stock_parser = subparsers.add_parser('stocks', help='批量抓取股票实时行情')
    stock_parser.add_argument('symbols', nargs='*', help='股票代码列表')
    stock_parser.add_argument('--file', '-f', help='从文件读取股票代码')
    stock_parser.add_argument('--headless', action='store_true', default=True, help='无头模式')
    stock_parser.add_argument('--no-headless', action='store_false', dest='headless', help='有头模式')
    stock_parser.add_argument('--output-dir', '-o', default='./temp/batch_results', help='输出目录')
    stock_parser.add_argument('--delay', '-d', type=float, default=2.0, help='间隔秒数')
    
    # 批量抓取K线
    kline_parser = subparsers.add_parser('klines', help='批量抓取K线数据')
    kline_parser.add_argument('symbols', nargs='+', help='股票代码列表')
    kline_parser.add_argument('--port', type=int, default=9353, help='CDP端口')
    kline_parser.add_argument('--tab', default='', help='Tab ID')
    kline_parser.add_argument('--scale', default='240', help='K线周期')
    kline_parser.add_argument('--datalen', type=int, default=1023, help='数据条数')
    kline_parser.add_argument('--delay', type=float, default=1.0, help='间隔秒数')
    kline_parser.add_argument('--output-dir', '-o', default='./temp/kline_results', help='输出目录')
    
    args = parser.parse_args()
    
    if args.command == 'stocks':
        symbols = []
        if args.symbols:
            symbols.extend(args.symbols)
        if args.file:
            with open(args.file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        symbols.append(line)
        
        if not symbols:
            symbols = ['603000', '600000', '000001', '600519', '600036', '600030', '000858', '002594']
            print(f'未指定股票代码,使用默认测试集: {symbols}')
        
        symbols = list(dict.fromkeys(symbols))  # 去重
        batch_fetch_stocks(symbols, headless=args.headless, output_dir=args.output_dir, delay=args.delay)
    
    elif args.command == 'klines':
        # 如果没指定tab，尝试获取第一个可用tab
        if not args.tab:
            from browser_launch import list_tabs
            tabs = list_tabs(args.port)
            if tabs:
                args.tab = tabs[0]['id']
                print(f"使用 tab: {args.tab}")
            else:
                print("错误: 没有可用的 tab")
                sys.exit(1)
        
        batch_fetch_klines(args.symbols, args.port, args.tab, args.scale, args.datalen, args.delay, args.output_dir)
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()