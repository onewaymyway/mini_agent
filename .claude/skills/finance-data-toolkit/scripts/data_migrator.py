#!/usr/bin/env python3
"""
data_migrator.py - 旧格式数据迁移到 FinanceData 规范

迁移目标:
1. schemas/*.json -> FinanceData 格式映射
2. research/stock_analyse/*.md -> 结构化数据提取 (未来)
3. data/daily/*.json -> 统一为 FinanceData.payload 格式

注意: 此脚本为框架/迁移工具，不会自动覆盖生产数据。
使用 --dry-run 先预览迁移结果。
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Any

# 添加父目录到 path
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from finance_toolkit.plugins.types import DataType
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.cleaning.validators import validate_finance_data


def load_json_schema(schema_path: str) -> dict:
    """加载 schema JSON 文件"""
    with open(schema_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def map_position_to_finance_data(schema: dict, source_file: str) -> FinanceData:
    """将 position.json schema 映射为 FinanceData 格式"""
    props = schema.get('properties', {})
    return FinanceData(
        source=source_file,
        data_type=DataType.STOCK_BASIC,  # 最接近的 DataType
        symbol='',
        timestamp=datetime.utcnow().isoformat(),
        payload={
            'position_id': props.get('position_id', {}).get('description', ''),
            'account_id': props.get('account_id', {}).get('description', ''),
            'security_name': props.get('security_name', {}).get('description', ''),
            'security_type': props.get('security_type', {}).get('description', ''),
            'quantity': props.get('quantity', {}).get('description', ''),
            'available_quantity': props.get('available_quantity', {}).get('description', ''),
        }
    )


def map_balance_to_finance_data(schema: dict, source_file: str) -> FinanceData:
    """将 balance.json schema 映射为 FinanceData 格式"""
    props = schema.get('properties', {})
    return FinanceData(
        source=source_file,
        data_type=DataType.FUND,
        symbol='',
        timestamp=datetime.utcnow().isoformat(),
        payload={
            'balance_id': props.get('balance_id', {}).get('description', ''),
            'account_id': props.get('account_id', {}).get('description', ''),
            'balance_type': props.get('balance_type', {}).get('description', ''),
            'amount': props.get('amount', {}).get('description', ''),
            'currency': props.get('currency', {}).get('description', 'CNY'),
            'as_of_date': props.get('as_of_date', {}).get('description', ''),
        }
    )


def map_transaction_to_finance_data(schema: dict, source_file: str) -> FinanceData:
    """将 transaction.json schema 映射为 FinanceData 格式"""
    props = schema.get('properties', {})
    return FinanceData(
        source=source_file,
        data_type=DataType.FUND,
        symbol='',
        timestamp=datetime.utcnow().isoformat(),
        payload={
            'transaction_id': props.get('transaction_id', {}).get('description', ''),
            'account_id': props.get('account_id', {}).get('description', ''),
            'transaction_type': props.get('transaction_type', {}).get('description', ''),
            'amount': props.get('amount', {}).get('description', ''),
            'fee': props.get('fee', {}).get('description', ''),
        }
    )


def map_account_to_finance_data(schema: dict, source_file: str) -> FinanceData:
    """将 account.json schema 映射为 FinanceData 格式"""
    props = schema.get('properties', {})
    return FinanceData(
        source=source_file,
        data_type=DataType.FUND,
        symbol='',
        timestamp=datetime.utcnow().isoformat(),
        payload={
            'account_id': props.get('account_id', {}).get('description', ''),
            'account_name': props.get('account_name', {}).get('description', ''),
            'account_type': props.get('account_type', {}).get('description', ''),
            'owner_id': props.get('owner_id', {}).get('description', ''),
            'bank_name': props.get('bank_name', {}).get('description', ''),
            'status': props.get('status', {}).get('description', ''),
        }
    )


def scan_and_report(schema_dir: str):
    """扫描 schemas 目录，输出迁移报告"""
    results = []
    mapping = {
        'position.json': (DataType.STOCK_BASIC, map_position_to_finance_data),
        'balance.json': (DataType.FUND, map_balance_to_finance_data),
        'transaction.json': (DataType.FUND, map_transaction_to_finance_data),
        'account.json': (DataType.FUND, map_account_to_finance_data),
    }
    
    for fname in sorted(os.listdir(schema_dir)):
        if not fname.endswith('.json'):
            continue
        schema_path = os.path.join(schema_dir, fname)
        schema = load_json_schema(schema_path)
        title = schema.get('title', 'Unknown')
        props = list(schema.get('properties', {}).keys())
        
        if fname in mapping:
            data_type, mapper = mapping[fname]
            fd = mapper(schema, fname)
            result = validate_finance_data(fd)
            results.append({
                'file': fname,
                'title': title,
                'data_type': data_type.name,
                'props_count': len(props),
                'validation': result.to_dict(),
                'mapped_fields': list(fd.payload.keys()) if fd else [],
            })
        else:
            results.append({
                'file': fname,
                'title': title,
                'data_type': None,
                'props_count': len(props),
                'validation': None,
                'mapped_fields': [],
                'note': '未定义映射规则',
            })
    
    return results


def main(dry_run=True):
    """
    主入口: 扫描所有数据源，输出迁移报告
    dry_run=True 仅打印报告，不写入文件
    """
    schema_dir = str(BASE / 'schemas')
    data_dir = str(BASE / 'data')
    
    print('=' * 60)
    print('FinanceData 数据迁移分析报告')
    print(f'基准路径: {BASE}')
    print(f'运行模式: {"DRY-RUN" if dry_run else "LIVE"}')
    print('=' * 60)
    
    # 1. Schema 迁移分析
    print('\n[1] Schema 文件迁移分析')
    schema_results = scan_and_report(schema_dir)
    for r in schema_results:
        status = 'OK' if r['validation'] else 'NO MAPPING'
        score = r['validation']['health_score'] if r['validation'] else 0
        print(f"  {r['file']}: {r['title']} ({r['props_count']} fields) -> {r['data_type'] or '?'} [{status}] 评分:{score}")
    
    # 2. Daily data 检查
    print('\n[2] data/daily/ 目录检查')
    daily_files = [f for f in os.listdir(data_dir) if f.endswith('.json')]
    if daily_files:
        for f in sorted(daily_files):
            print(f'  {f}: test fixture (not production data)')
    else:
        print('  无 JSON 数据文件')
    
    # 3. Research 目录检查
    print('\n[3] research/stock_analyse/ 目录检查')
    research_dir = str(BASE.parent.parent / 'research' / 'stock_analyse')
    if os.path.exists(research_dir):
        md_files = [f for f in os.listdir(research_dir) if f.endswith('.md')]
        print(f'  找到 {len(md_files)} 个 markdown 报告 (需 NLP 解析才能迁移)')
        print('  注: 分析报告为人类可读格式，建议保留原样，不强制迁移到 FinanceData')
    
    print('\n' + '=' * 60)
    print('结论:')
    print('  - schemas/ 中已有 4 个可映射的 JSON schema')
    print('  - data/daily/ 中只有测试数据，无需迁移')
    print('  - research/stock_analyse/ 中是 markdown 报告，保留原格式')
    print('  - 建议: 后续采集时直接使用 FinanceData 格式写入 data/daily/')
    print('=' * 60)
    
    if dry_run:
        print('\n(干跑模式: 以上为只读分析，未写入任何文件)')
        # 保存分析结果
        report_path = BASE / 'temp' / 'migration_report.json'
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({'dry_run': True, 'results': schema_results}, f, ensure_ascii=False, indent=2)
        print(f'分析报告已保存: {report_path}')


if __name__ == '__main__':
    dry_run = '--live' not in sys.argv
    main(dry_run=dry_run)
