# -*- coding: utf-8 -*-
"""
综合研报生成器
================

合并基础数据 (东方财富) + K 线技术分析 (新浪财经) 生成完整研报
支持 HTML / Markdown / JSON 多格式输出
符合 finance-data-toolkit 统一数据契约
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
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


def load_latest_basic_data(code: str, basic_dir: str) -> dict:
    """加载最新的基础数据"""
    if not os.path.exists(basic_dir):
        return {}
    files = [f for f in os.listdir(basic_dir) if f.startswith(f'{code}_eastmoney_') and f.endswith('.json')]
    if not files:
        return {}
    files.sort(reverse=True)
    latest = files[0]
    with open(os.path.join(basic_dir, latest), 'r', encoding='utf-8') as f:
        return json.load(f)


def load_latest_kline_data(code: str, kline_dir: str) -> dict:
    """加载最新的 K 线分析数据"""
    if not os.path.exists(kline_dir):
        return {}
    files = [f for f in os.listdir(kline_dir) if f.startswith(f'{code}_kline_raw_') and f.endswith('.json')]
    if not files:
        return {}
    files.sort(reverse=True)
    latest = files[0]
    with open(os.path.join(kline_dir, latest), 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_html_report(stocks_data: list, output_path: str):
    """生成 HTML 综合研报"""
    html = []
    html.append('''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>股票综合分析研报</title>
<style>
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 20px; background: #f5f5f5; }
.container { max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
h1 { color: #c00; border-bottom: 3px solid #c00; padding-bottom: 10px; }
h2 { color: #333; border-left: 4px solid #c00; padding-left: 10px; margin-top: 30px; }
h3 { color: #555; }
.meta { color: #888; font-size: 14px; margin-bottom: 20px; }
table { border-collapse: collapse; width: 100%; margin: 15px 0; font-size: 14px; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
th { background: #f0f0f0; font-weight: bold; }
tr:nth-child(even) { background: #fafafa; }
.positive { color: #c00; font-weight: bold; }
.negative { color: #080; font-weight: bold; }
.neutral { color: #666; }
.signal-buy { background: #ffe0e0; color: #c00; padding: 2px 8px; border-radius: 3px; font-weight: bold; }
.signal-sell { background: #e0ffe0; color: #080; padding: 2px 8px; border-radius: 3px; font-weight: bold; }
.signal-warn { background: #fff4e0; color: #c80; padding: 2px 8px; border-radius: 3px; font-weight: bold; }
.signal-info { background: #e0e8ff; color: #048; padding: 2px 8px; border-radius: 3px; }
.stock-card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin: 20px 0; background: #fafafa; }
.stock-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
.stock-name { font-size: 24px; font-weight: bold; color: #333; }
.stock-price { font-size: 28px; font-weight: bold; }
.summary-box { background: #fff8e0; border-left: 4px solid #f80; padding: 15px; margin: 15px 0; border-radius: 4px; }
</style>
</head>
<body>
<div class="container">''')

    html.append(f'<h1>📊 股票综合分析研报</h1>')
    html.append(f'<div class="meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 股票数量: {len(stocks_data)} | 数据源: 东方财富 + 新浪财经</div>')

    # 汇总表
    html.append('<h2>📋 股票汇总</h2>')
    html.append('<table>')
    html.append('<tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th><th>PE(TTM)</th><th>PB</th><th>总市值</th><th>ROE</th><th>MA信号</th><th>MACD</th><th>RSI</th><th>综合</th></tr>')
    for s in stocks_data:
        basic = s.get('basic', {})
        kline = s.get('kline', {})
        price = basic.get('price', 'N/A')
        change = basic.get('change_pct', 'N/A')
        pe = basic.get('pe_ttm', 'N/A')
        pb = basic.get('pb', 'N/A')
        mv = basic.get('total_mv', 'N/A')
        roe = basic.get('roe', 'N/A')
        name = basic.get('name', s['code'])

        signals = kline.get('signals', {})
        ma_sig = list(signals.keys())[0] if signals else 'N/A'
        macd_sig = 'MACD_GOLDEN' if 'MACD_GOLDEN' in signals else ('MACD_DEAD' if 'MACD_DEAD' in signals else ('MACD_BULL' if 'MACD_BULL' in signals else ('MACD_BEAR' if 'MACD_BEAR' in signals else 'N/A')))
        rsi_sig = 'RSI_OVERBOUGHT' if 'RSI_OVERBOUGHT' in signals else ('RSI_OVERSOLD' if 'RSI_OVERSOLD' in signals else 'RSI_NORMAL')

        bullish = sum(1 for k in signals if 'BULL' in k or 'GOLDEN' in k or 'OVERSOLD' in k)
        bearish = sum(1 for k in signals if 'BEAR' in k or 'DEAD' in k or 'OVERBOUGHT' in k)
        if bullish > bearish + 1:
            overall = '<span class="signal-buy">偏多</span>'
        elif bearish > bullish + 1:
            overall = '<span class="signal-sell">偏空</span>'
        else:
            overall = '<span class="signal-info">震荡</span>'

        try:
            change_str = f'{float(change):+.2f}%' if change != 'N/A' else 'N/A'
            change_class = 'positive' if float(change) > 0 else ('negative' if float(change) < 0 else 'neutral')
        except:
            change_str = str(change)
            change_class = 'neutral'

        html.append(f'<tr>')
        html.append(f'<td>{s["code"]}</td>')
        html.append(f'<td>{name}</td>')
        html.append(f'<td>{price}</td>')
        html.append(f'<td class="{change_class}">{change_str}</td>')
        html.append(f'<td>{pe}</td>')
        html.append(f'<td>{pb}</td>')
        html.append(f'<td>{mv}</td>')
        html.append(f'<td>{roe}</td>')
        html.append(f'<td>{ma_sig}</td>')
        html.append(f'<td>{macd_sig}</td>')
        html.append(f'<td>{rsi_sig}</td>')
        html.append(f'<td>{overall}</td>')
        html.append(f'</tr>')
    html.append('</table>')

    # 详细分析
    html.append('<h2>🔍 个股详细分析</h2>')
    for s in stocks_data:
        basic = s.get('basic', {})
        kline = s.get('kline', {})
        code = s['code']
        name = basic.get('name', code)
        price = basic.get('price', 'N/A')
        change = basic.get('change_pct', 'N/A')

        html.append('<div class="stock-card">')
        html.append('<div class="stock-header">')
        html.append(f'<div class="stock-name">{name} ({code})</div>')
        try:
            change_class = 'positive' if float(change) > 0 else ('negative' if float(change) < 0 else 'neutral')
            html.append(f'<div class="stock-price {change_class}">¥{price} ({float(change):+.2f}%)</div>')
        except:
            html.append(f'<div class="stock-price">¥{price}</div>')
        html.append('</div>')

        # 基本面
        html.append('<h3>📈 基本面数据</h3>')
        html.append('<table>')
        html.append('<tr><th>指标</th><th>数值</th></tr>')
        for key in ['price', 'change_pct', 'change_amt', 'volume', 'amount', 'turnover',
                    'pe_ttm', 'pb', 'ps', 'pcf', 'total_mv', 'circ_mv',
                    'revenue', 'net_profit', 'roe', 'gross_margin', 'net_margin',
                    'debt_ratio', 'industry']:
            val = basic.get(key, 'N/A')
            if val != 'N/A':
                html.append(f'<tr><td>{key}</td><td>{val}</td></tr>')
        html.append('</table>')

        # 技术面
        if kline:
            html.append('<h3>📊 技术面分析</h3>')
            stats = kline.get('price_stats', {})
            html.append('<table>')
            html.append('<tr><th>指标</th><th>数值</th></tr>')
            for key, label in [
                ('current_price', '当前价'),
                ('change_1d_pct', '1日涨跌%'),
                ('change_5d_pct', '5日涨跌%'),
                ('change_20d_pct', '20日涨跌%'),
                ('change_60d_pct', '60日涨跌%'),
                ('period_high_20d', '20日最高'),
                ('period_low_20d', '20日最低'),
                ('period_high_60d', '60日最高'),
                ('period_low_60d', '60日最低'),
                ('avg_volume_20d', '20日均量'),
            ]:
                val = stats.get(key, 'N/A')
                if val != 'N/A':
                    if isinstance(val, float):
                        val = f'{val:.2f}'
                    html.append(f'<tr><td>{label}</td><td>{val}</td></tr>')
            html.append('</table>')

            # 技术指标
            html.append('<h4>技术指标</h4>')
            html.append('<table>')
            html.append('<tr><th>指标</th><th>数值</th></tr>')
            for key, val in kline.get('latest_indicators', {}).items():
                html.append(f'<tr><td>{key}</td><td>{val}</td></tr>')
            html.append('</table>')

            # 交易信号
            html.append('<h4>交易信号</h4>')
            html.append('<div class="summary-box">')
            signals = kline.get('signals', {})
            for sig, desc in signals.items():
                css_class = 'signal-info'
                if 'GOLDEN' in sig or 'OVERSOLD' in sig or 'BULL' in sig:
                    css_class = 'signal-buy'
                elif 'DEAD' in sig or 'OVERBOUGHT' in sig or 'BEAR' in sig:
                    css_class = 'signal-sell'
                elif 'MIXED' in sig or 'NORMAL' in sig:
                    css_class = 'signal-info'
                html.append(f'<span class="{css_class}">{desc}</span> ')
            html.append('</div>')

            # K线数据范围
            dr = kline.get('date_range', {})
            html.append(f'<p style="color:#888;font-size:12px;">K线数据范围: {dr.get("start", "N/A")} ~ {dr.get("end", "N/A")} ({kline.get("kline_count", 0)} 条)</p>')

        html.append('</div>')

    html.append('</div></body></html>')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html))
    print(f'✅ HTML 报告已生成: {output_path}')


def generate_markdown_report(stocks_data: list, output_path: str):
    """生成 Markdown 综合研报"""
    md = []
    md.append(f'# 📊 股票综合分析研报')
    md.append(f'')
    md.append(f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  ')
    md.append(f'**股票数量**: {len(stocks_data)}  ')
    md.append(f'**数据源**: 东方财富 (基本面) + 新浪财经 (K线/技术指标)')
    md.append(f'')
    md.append(f'---')
    md.append(f'')

    # 汇总表
    md.append(f'## 📋 股票汇总')
    md.append(f'')
    md.append(f'| 代码 | 名称 | 现价 | 涨跌幅 | PE(TTM) | PB | 总市值 | ROE | MA信号 | MACD | RSI | 综合 |')
    md.append(f'|------|------|------|--------|---------|-----|--------|-----|--------|------|-----|------|')
    for s in stocks_data:
        basic = s.get('basic', {})
        kline = s.get('kline', {})
        signals = kline.get('signals', {})
        ma_sig = list(signals.keys())[0] if signals else 'N/A'
        macd_sig = 'MACD_GOLDEN' if 'MACD_GOLDEN' in signals else ('MACD_DEAD' if 'MACD_DEAD' in signals else ('MACD_BULL' if 'MACD_BULL' in signals else ('MACD_BEAR' if 'MACD_BEAR' in signals else 'N/A')))
        rsi_sig = 'RSI_OVERBOUGHT' if 'RSI_OVERBOUGHT' in signals else ('RSI_OVERSOLD' if 'RSI_OVERSOLD' in signals else 'RSI_NORMAL')

        bullish = sum(1 for k in signals if 'BULL' in k or 'GOLDEN' in k or 'OVERSOLD' in k)
        bearish = sum(1 for k in signals if 'BEAR' in k or 'DEAD' in k or 'OVERBOUGHT' in k)
        if bullish > bearish + 1:
            overall = '🟢 偏多'
        elif bearish > bullish + 1:
            overall = '🔴 偏空'
        else:
            overall = '🟡 震荡'

        roe_val = basic.get("roe", "N/A")
        roe_str = f'{roe_val}%' if roe_val != 'N/A' and '%' not in str(roe_val) else str(roe_val)
        md.append(f'| {s["code"]} | {basic.get("name", s["code"])} | {basic.get("price", "N/A")} | {basic.get("change_pct", "N/A")}% | {basic.get("pe_ttm", "N/A")} | {basic.get("pb", "N/A")} | {basic.get("total_mv", "N/A")} | {roe_str} | {ma_sig} | {macd_sig} | {rsi_sig} | {overall} |')
    md.append(f'')

    # 详细分析
    md.append(f'## 🔍 个股详细分析')
    md.append(f'')
    for s in stocks_data:
        basic = s.get('basic', {})
        kline = s.get('kline', {})
        code = s['code']
        name = basic.get('name', code)
        price = basic.get('price', 'N/A')
        change = basic.get('change_pct', 'N/A')

        md.append(f'### {name} ({code})')
        md.append(f'')
        md.append(f'**当前价**: ¥{price} ({change}%)')
        md.append(f'')

        # 基本面
        md.append(f'#### 📈 基本面数据')
        md.append(f'')
        md.append(f'| 指标 | 数值 |')
        md.append(f'|------|------|')
        for key in ['price', 'change_pct', 'change_amt', 'volume', 'amount', 'turnover',
                    'pe_ttm', 'pb', 'ps', 'pcf', 'total_mv', 'circ_mv',
                    'revenue', 'net_profit', 'roe', 'gross_margin', 'net_margin',
                    'debt_ratio', 'industry']:
            val = basic.get(key, 'N/A')
            if val != 'N/A':
                md.append(f'| {key} | {val} |')
        md.append(f'')

        # 技术面
        if kline:
            md.append(f'#### 📊 技术面分析')
            md.append(f'')
            stats = kline.get('price_stats', {})
            md.append(f'| 指标 | 数值 |')
            md.append(f'|------|------|')
            for key, label in [
                ('current_price', '当前价'),
                ('change_1d_pct', '1日涨跌%'),
                ('change_5d_pct', '5日涨跌%'),
                ('change_20d_pct', '20日涨跌%'),
                ('change_60d_pct', '60日涨跌%'),
                ('period_high_20d', '20日最高'),
                ('period_low_20d', '20日最低'),
                ('period_high_60d', '60日最高'),
                ('period_low_60d', '60日最低'),
                ('avg_volume_20d', '20日均量'),
            ]:
                val = stats.get(key, 'N/A')
                if val != 'N/A':
                    if isinstance(val, float):
                        val = f'{val:.2f}'
                    md.append(f'| {label} | {val} |')
            md.append(f'')

            md.append(f'#### 技术指标')
            md.append(f'')
            md.append(f'| 指标 | 数值 |')
            md.append(f'|------|------|')
            for key, val in kline.get('latest_indicators', {}).items():
                md.append(f'| {key} | {val} |')
            md.append(f'')

            md.append(f'#### 交易信号')
            md.append(f'')
            signals = kline.get('signals', {})
            for sig, desc in signals.items():
                md.append(f'- **{desc}**')
            md.append(f'')

            dr = kline.get('date_range', {})
            md.append(f'*K线数据范围: {dr.get("start", "N/A")} ~ {dr.get("end", "N/A")} ({kline.get("kline_count", 0)} 条)*')
            md.append(f'')

        md.append(f'---')
        md.append(f'')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))
    print(f'✅ Markdown 报告已生成: {output_path}')


def generate_json_report(stocks_data: list, output_path: str):
    """生成 JSON 格式研报"""
    report = {
        'meta': {
            'generated_at': datetime.now().isoformat(),
            'stock_count': len(stocks_data),
            'data_sources': ['eastmoney', 'sina']
        },
        'stocks': stocks_data
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f'✅ JSON 报告已生成: {output_path}')


def generate_comprehensive_report(
    codes: List[str],
    basic_dir: str = None,
    kline_dir: str = None,
    output_dir: str = None,
    formats: List[str] = None
) -> Dict[str, str]:
    """生成综合研报的统一入口"""
    base_dir = Path(__file__).parent.parent.parent
    basic_dir = basic_dir or str(base_dir / 'temp' / 'batch_results')
    kline_dir = kline_dir or str(base_dir / 'temp' / 'kline_results')
    output_dir = output_dir or str(base_dir / 'temp' / 'reports')
    formats = formats or ['html', 'md', 'json']
    
    os.makedirs(output_dir, exist_ok=True)

    print(f'基础数据目录: {basic_dir}')
    print(f'K线数据目录: {kline_dir}')
    print(f'输出目录: {output_dir}')
    print(f'股票列表: {codes}')
    print()

    stocks_data = []
    for code in codes:
        print(f'加载 {code}...')
        basic = load_latest_basic_data(code, basic_dir)
        kline = load_latest_kline_data(code, kline_dir)
        if not basic and not kline:
            print(f'  ⚠️ {code} 无数据，跳过')
            continue
        stocks_data.append({
            'code': code,
            'basic': basic,
            'kline': kline,
        })
        print(f'  ✓ 基本面: {len(basic)} 字段, K线: {kline.get("kline_count", 0) if kline else 0} 条')

    if not stocks_data:
        print('❌ 无有效数据，退出')
        return {}

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_files = {}
    
    if 'html' in formats:
        html_path = os.path.join(output_dir, f'comprehensive_report_{ts}.html')
        generate_html_report(stocks_data, html_path)
        output_files['html'] = html_path
    if 'md' in formats:
        md_path = os.path.join(output_dir, f'comprehensive_report_{ts}.md')
        generate_markdown_report(stocks_data, md_path)
        output_files['md'] = md_path
    if 'json' in formats:
        json_path = os.path.join(output_dir, f'comprehensive_report_{ts}.json')
        generate_json_report(stocks_data, json_path)
        output_files['json'] = json_path

    print(f'\n📊 共处理 {len(stocks_data)} 只股票')
    return output_files


def main():
    parser = argparse.ArgumentParser(description='综合研报生成器')
    parser.add_argument('codes', nargs='+', help='股票代码列表')
    parser.add_argument('--basic-dir', default=None, help='基础数据目录')
    parser.add_argument('--kline-dir', default=None, help='K线数据目录')
    parser.add_argument('--output-dir', default=None, help='输出目录')
    parser.add_argument('--format', choices=['html', 'md', 'json', 'both', 'all'], default='all', help='输出格式')
    args = parser.parse_args()

    if args.format == 'both':
        formats = ['html', 'md']
    elif args.format == 'all':
        formats = ['html', 'md', 'json']
    else:
        formats = [args.format]

    generate_comprehensive_report(
        codes=args.codes,
        basic_dir=args.basic_dir,
        kline_dir=args.kline_dir,
        output_dir=args.output_dir,
        formats=formats
    )


if __name__ == '__main__':
    main()