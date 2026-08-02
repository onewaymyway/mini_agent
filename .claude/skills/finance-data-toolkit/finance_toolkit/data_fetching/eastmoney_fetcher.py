# -*- coding: utf-8 -*-
"""
东方财富股票数据抓取器
基于 browser-cdp 技能，从 quote.eastmoney.com 抓取真实股票数据
符合 finance-data-toolkit 统一数据契约
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


def parse_eastmoney_stock(text: str, symbol: str) -> Dict[str, Any]:
    """从东方财富页面文本提取结构化股票数据"""
    data = {
        'symbol': symbol,
        'name': '',
        'timestamp': datetime.now().isoformat(),
        'source': 'eastmoney',
    }
    
    # 策略1: 从页面标题提取股票名称(最可靠)
    title_match = re.search(r"title=[\"'](.+?)\(\d{6}\)", text)
    if title_match:
        name_match = re.search(r'([^()]+)\(\d{6}\)', title_match.group(0))
        if name_match:
            data['name'] = name_match.group(1)
    if not data['name']:
        title_match = re.search(r'([^()]+)\(\d{6}\)[^()]*—东方财富网', text)
        if title_match:
            data['name'] = title_match.group(1)
    
    # 策略2: 从'最近访问'区域提取名称
    if not data['name']:
        visit_match = re.search(r'最近访问[：:]\s*([\u4e00-\u9fa5\s]+)', text)
        if visit_match:
            data['name'] = visit_match.group(1).replace(' ', '').replace('\u3000', '')
    
    # 策略3: 从相关个股区域提取名称
    if not data['name']:
        name_match = re.search(r'([\u4e00-\u9fa5\s]+)\(\d+\.\d+\s*[+-]\d+\.\d+%\)', text)
        if name_match:
            data['name'] = name_match.group(1).replace(' ', '').replace('\u3000', '')
    
    # 名称去重
    if data['name'] and len(data['name']) > 2:
        half = len(data['name']) // 2
        if data['name'][:half] == data['name'][half:]:
            data['name'] = data['name'][:half]
    
    # 策略4: 从'公司核心数据'表格提取财务指标
    core_data_match = re.search(
        r'总市值净资产净利润市盈率\(动\)市净率毛利率净利率ROE'
        r'([\u4e00-\u9fa5\s]+)'
        r'([-\d.]+(?:万)?亿)'
        r'([-\d.]+(?:万)?亿)'
        r'([-\d.]+[亿万])'
        r'(负值|\d+(?:\.\d{1,2})?|-)'
        r'(\d+\.\d{2}|-)'
        r'([-\d.]+%)'
        r'([-\d.]+%)'
        r'([-\d.]+%)',
        text, re.DOTALL)
    
    if core_data_match:
        data['total_mv'] = core_data_match.group(2)
        data['net_assets'] = core_data_match.group(3)
        data['net_profit'] = core_data_match.group(4)
        
        pe = core_data_match.group(5)
        pb = core_data_match.group(6)
        gross_margin = core_data_match.group(7)
        net_margin = core_data_match.group(8)
        roe = core_data_match.group(9)
        
        if pe == '负值':
            data['pe_ttm'] = '负值'
        elif pe not in ('-', '--', ''):
            data['pe_ttm'] = pe
        
        if pb not in ('-', '--', ''):
            data['pb'] = pb
        
        if gross_margin not in ('-', '--', ''):
            data['gross_margin'] = gross_margin
        
        if net_margin not in ('-', '--', ''):
            data['net_margin'] = net_margin
        
        if roe not in ('-', '--', ''):
            data['roe'] = roe
    
    # 策略5: 从'相关个股'区域提取主股票价格
    if 'price' not in data and data['name']:
        name_escaped = re.escape(data['name'].replace(' ', '\\s*'))
        name_price_matches = list(re.finditer(rf'{name_escaped}\s*\((\d+\.\d+)\s*([+-]?\d+\.\d+)%\)', text))
        if name_price_matches:
            last_match = name_price_matches[-1]
            data['price'] = float(last_match.group(1))
            data['change_pct'] = float(last_match.group(2))
    
    # 策略6: 直接从文本提取价格
    if 'price' not in data:
        price_matches = list(re.finditer(r'([\u4e00-\u9fa5\s]+)\s*\((\d+\.\d+)\s*([+-]?\d+\.\d+)%\)', text))
        if price_matches:
            last_match = price_matches[-1]
            data['price'] = float(last_match.group(2))
            data['change_pct'] = float(last_match.group(3))
            if not data['name']:
                data['name'] = last_match.group(1).replace(' ', '').replace('\u3000', '')
    
    # 策略7: 分红信息
    div_match = re.search(r'分红.*?(\d+派[\d.]+元)', text)
    if div_match:
        data['dividend'] = div_match.group(1)
    
    # 策略8: 机构评级
    rating_match = re.search(r'机构评级.*?(\d+家\(\d+\))', text)
    if rating_match:
        data['institution_rating'] = rating_match.group(1)
    
    # 策略9: 资金流向
    flow_patterns = {
        'main_net_inflow': r'主力净流入[：:]\s*([-\d.]+)万',
        'main_net_ratio': r'主力净占比[：:]\s*([\d.]+)%',
        'super_large_net_inflow': r'超大单净流入[：:]\s*([-\d.]+)万',
        'super_large_ratio': r'超大单净占比[：:]\s*([\d.]+)%',
        'large_net_inflow': r'大单净流入[：:]\s*([-\d.]+)万',
        'large_ratio': r'大单净占比[：:]\s*([-\d.]+)%',
        'medium_net_inflow': r'中单净流入[：:]\s*([-\d.]+)万',
        'medium_ratio': r'中单净占比[：:]\s*([-\d.]+)%',
        'small_net_inflow': r'小单净流入[：:]\s*([-\d.]+)万',
        'small_ratio': r'小单净占比[：:]\s*([\d.]+)%',
    }
    
    for key, pattern in flow_patterns.items():
        match = re.search(pattern, text)
        if match:
            val = match.group(1)
            if val not in ('-', '--', ''):
                try:
                    data[key] = float(val)
                except ValueError:
                    pass
    
    # 策略10: 行业排名
    rank_match = re.search(r'行业排名.*?(\d+\|\d+\|\d+\|\d+)', text)
    if rank_match:
        data['industry_rank'] = rank_match.group(1)
    
    # 策略11: 四分位属性
    quartile_match = re.search(r'四分位属性.*?(较高|高|较低|低)', text)
    if quartile_match:
        data['quartile'] = quartile_match.group(1)
    
    return data


def run_browser_script(script_name: str, args: list) -> str:
    """运行 browser-cdp 脚本并返回输出"""
    # 根据脚本名称映射到新目录结构
    script_map = {
        'browser_launch.py': 'browser_ops/browser_launch.py',
        'browser_nav.py': 'browser_ops/browser_nav.py',
        'browser_console.py': 'browser_ops/browser_console.py',
        'browser_extract.py': 'browser_ops/browser_extract.py',
        'browser_input.py': 'browser_ops/browser_input.py',
        'browser_screenshot.py': 'browser_ops/browser_screenshot.py',
    }
    new_path = script_map.get(script_name, script_name)
    script_path = Path(__file__).parent.parent.parent.parent / 'browser-cdp' / new_path
    cmd = [sys.executable, str(script_path)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode != 0:
        raise RuntimeError(f"{script_name} 执行失败: {result.stderr}")
    return result.stdout


def fetch_stock_data(symbol: str, headless: bool = False) -> FinanceData:
    """抓取单只股票数据，返回标准化 FinanceData 对象"""
    # 格式化代码: 603000 -> sh603000
    if symbol.startswith(('6', '9')):
        url_symbol = f'sh{symbol}'
    else:
        url_symbol = f'sz{symbol}'
    
    url = f'https://quote.eastmoney.com/{url_symbol}.html'
    
    print("[1/4] 启动/复用浏览器实例...")
    launch_args = ['--dedicated', '--name', f'stock_{symbol}', '--start-url', url]
    if headless:
        launch_args.append('--headless')
    launch_output = run_browser_script('browser_launch.py', launch_args)
    
    # 解析端口和tab_id
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
    
    print("[3/4] 等待页面完全加载并提取内容...")
    import time
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
    except Exception:
        pass
    
    print("[3/4] 提取页面内容...")
    text = run_browser_script('browser_extract.py', ['--port', port, '--tab', tab_id, '--mode', 'text'])
    
    print("[4/4] 解析结构化数据...")
    parsed_data = parse_eastmoney_stock(text, symbol)
    
    # 合并JS获取的数据(优先级更高)
    if js_data.get('price'):
        parsed_data['price'] = float(js_data['price'])
    if js_data.get('change_pct'):
        parsed_data['change_pct'] = float(js_data['change_pct'])
    
    # 构建标准化 FinanceData 对象
    finance_data = FinanceData(
        source='eastmoney',
        data_type='quote',
        symbol=symbol,
        timestamp=datetime.now().isoformat(),
        payload=parsed_data,
        raw={'page_text': text[:5000]},  # 仅保留前5000字符用于调试
        meta={'fetch_method': 'browser-cdp', 'url': url}
    )
    
    return finance_data


def main():
    parser = argparse.ArgumentParser(description='东方财富股票数据抓取工具')
    parser.add_argument('symbol', help='股票代码,如 603000')
    parser.add_argument('--headless', action='store_true', help='无头模式运行')
    parser.add_argument('-o', '--output', help='输出JSON文件路径')
    
    args = parser.parse_args()
    
    try:
        data = fetch_stock_data(args.symbol, headless=args.headless)
        
        # 打印摘要
        payload = data.payload
        print(f"\n{'='*50}")
        print(f"抓取完成: {payload.get('name', '')}({data.symbol})")
        print(f"{'='*50}")
        print(f"当前价格: ¥{payload.get('price', 'N/A')}")
        print(f"涨跌幅: {payload.get('change_pct', 'N/A')}%")
        print(f"市盈率(TTM): {payload.get('pe_ttm', 'N/A')}")
        print(f"市净率: {payload.get('pb', 'N/A')}")
        print(f"总市值: {payload.get('total_mv', 'N/A')}")
        print(f"净利润: {payload.get('net_profit', 'N/A')}")
        print(f"ROE: {payload.get('roe', 'N/A')}")
        
        # 保存文件
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = Path(f'./temp/{args.symbol}_eastmoney_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        print(f"\n数据已保存至: {output_path}")
        
    except Exception as e:
        print(f"抓取失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()