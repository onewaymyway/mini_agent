# -*- coding: utf-8 -*-
"""
数据格式校验工具使用示例
=========================
演示如何使用 format_validator.py 验证抓取结果
"""

import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from format_validator import DataFormatValidator, ValidationResult


def example_1_validate_single_file():
    """示例1: 校验单个文件"""
    print("=" * 60)
    print("示例1: 校验单个数据文件")
    print("=" * 60)

    validator = DataFormatValidator(strict=True)

    # 创建一个符合规范的数据文件
    test_data = {
        'source': 'akshare',
        'data_type': 'quote',
        'symbol': '600000.SH',
        'timestamp': '2026-08-15T10:30:00+08:00',
        'data_time': '2026-08-15',
        'payload': {
            'name': '浦发银行',
            'price': 10.50,
            'open': 10.20,
            'high': 10.80,
            'low': 10.10,
            'pre_close': 10.30
        },
        'meta': {
            'fetch_time_ms': 150.5,
            'retry_count': 0
        }
    }

    # 保存到临时文件
    temp_file = Path('temp_test_data.json')
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)

    # 校验文件
    result = validator.validate_file(temp_file)
    print(result)

    # 清理
    temp_file.unlink(missing_ok=True)
    print()


def example_2_validate_batch():
    """示例2: 批量校验目录"""
    print("=" * 60)
    print("示例2: 批量校验数据目录")
    print("=" * 60)

    validator = DataFormatValidator(strict=True)

    # 假设数据目录存在
    data_dir = Path('data/processed')

    if data_dir.exists():
        result = validator.validate_directory(data_dir, recursive=True)
        report = validator.generate_report(result)
        print(report)
    else:
        print(f"数据目录不存在: {data_dir}")
    print()


def example_3_integration_with_fetch():
    """示例3: 与抓取流程集成"""
    print("=" * 60)
    print("示例3: 抓取后自动校验")
    print("=" * 60)

    # 模拟抓取数据
    simulated_data = {
        'source': 'akshare',
        'data_type': 'kline',
        'symbol': '000001.SZ',
        'timestamp': '2026-08-15T15:00:00+08:00',
        'payload': {
            'date': '2026-08-15',
            'open': 12.50,
            'close': 12.80,
            'high': 13.00,
            'low': 12.40,
            'volume': 1500000
        }
    }

    # 校验
    validator = DataFormatValidator(strict=True)
    result = validator._validate_single_record(simulated_data, '')

    if result[0]:  # errors
        print("❌ 数据校验失败:")
        for error in result[0]:
            print(f"  - {error}")
    else:
        print("✅ 数据校验通过")
        if result[1]:  # warnings
            print("⚠️  警告:")
            for warning in result[1]:
                print(f"  - {warning}")
    print()


def example_4_cicd_integration():
    """示例4: CI/CD 集成"""
    print("=" * 60)
    print("示例4: CI/CD Pipeline 集成")
    print("=" * 60)

    print("""
# GitHub Actions 配置示例

name: Data Format Validation
on:
  push:
    paths:
      - 'data/**/*.json'
      - 'scripts/format_validator.py'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Run format validation
        run: |
          python scripts/format_validator.py data/processed --recursive
          python scripts/format_validator.py data/raw --recursive
      - name: Check exit code
        run: echo "Validation passed!"
""")
    print()


def example_5_json_output():
    """示例5: JSON 格式输出（用于程序处理）"""
    print("=" * 60)
    print("示例5: JSON 格式输出")
    print("=" * 60)

    validator = DataFormatValidator(strict=True)

    # 创建测试数据
    test_data = [
        {'source': 'test', 'data_type': 'quote', 'symbol': '600000.SH'},
        {'source': 'test', 'data_type': 'invalid_type'},  # 缺少 symbol
    ]

    for i, data in enumerate(test_data):
        result = validator._validate_single_record(data, f'[{i}]')
        print(json.dumps(result[0], ensure_ascii=False))  # errors
        print(json.dumps(result[1], ensure_ascii=False))  # warnings
    print()


def main():
    """运行所有示例"""
    print("\n" + "=" * 60)
    print("FinanceData Toolkit - 数据格式校验工具示例集")
    print("=" * 60 + "\n")

    example_1_validate_single_file()
    example_2_validate_batch()
    example_3_integration_with_fetch()
    example_4_cicd_integration()
    example_5_json_output()

    print("=" * 60)
    print("所有示例执行完毕")
    print("=" * 60)


if __name__ == '__main__':
    main()
