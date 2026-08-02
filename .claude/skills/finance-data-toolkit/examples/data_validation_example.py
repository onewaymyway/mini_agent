# -*- coding: utf-8 -*-
"""
数据质量校验模块使用示例

演示如何使用 finance_toolkit.validation 模块进行数据质量检查。

运行方式：
    python examples/data_validation_example.py

注意：
    本示例会自动将父目录添加到 sys.path，无需安装 finance_toolkit 包。
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime
from finance_toolkit.validation import (
    validate_kline_data,
    validate_quote_data,
    DataQualityValidator,
    check_data_quality,
)

# 自动添加父目录到 sys.path，无需安装包
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))


def create_sample_kline_data(n_days=100, include_issues=False):
    """创建示例 K 线数据"""
    dates = pd.date_range(end=datetime.now(), periods=n_days, freq='D')
    
    # 生成基础数据
    base_price = 100
    prices = [base_price]
    for _ in range(n_days - 1):
        change = np.random.normal(0, 0.02)
        prices.append(prices[-1] * (1 + change))
    
    df = pd.DataFrame({
        'date': dates,
        'open': [p * np.random.uniform(0.99, 1.01) for p in prices],
        'high': [p * np.random.uniform(1.00, 1.02) for p in prices],
        'low': [p * np.random.uniform(0.98, 1.00) for p in prices],
        'close': prices,
        'volume': [np.random.randint(1000000, 5000000) for _ in range(n_days)],
    })
    
    if include_issues:
        # 故意插入一些问题数据
        df.loc[10, 'close'] = np.nan  # 空值
        df.loc[20, 'high'] = df.loc[20, 'low'] - 1  # 最高价 < 最低价
        df.loc[30, 'close'] = df.loc[30, 'close'] * 5  # 异常涨跌幅
        df.loc[40, 'volume'] = -100  # 负值
    
    return df


def create_sample_quote_data(include_issues=False):
    """创建示例行情数据"""
    quote = {
        'close': 100.5,
        'open': 99.8,
        'high': 101.2,
        'low': 99.5,
        'pre_close': 99.0,
        'volume': 1234567,
        'amount': 123456789.0,
        'change_pct': 1.52,
    }
    
    if include_issues:
        quote['close'] = -10  # 负值
        quote['high'] = 90  # 最高价 < 最低价
    
    return quote


def demo_basic_validation():
    """基础验证示例"""
    print("=" * 60)
    print("示例 1: 基础数据验证")
    print("=" * 60)
    
    # 验证 K 线数据
    print("\n1. 验证正常 K 线数据:")
    kline_df = create_sample_kline_data()
    report = validate_kline_data(kline_df)
    print(report)
    
    # 验证有问题数据
    print("\n2. 验证有问题 K 线数据:")
    kline_df_bad = create_sample_kline_data(include_issues=True)
    report_bad = validate_kline_data(kline_df_bad)
    print(report_bad)
    
    # 验证实时行情
    print("\n3. 验证实时行情:")
    quote = create_sample_quote_data()
    quote_report = validate_quote_data(quote, symbol='600000.SH')
    print(quote_report)
    
    # 验证有问题行情
    print("\n4. 验证有问题行情:")
    quote_bad = create_sample_quote_data(include_issues=True)
    quote_bad_report = validate_quote_data(quote_bad, symbol='600000.SH')
    print(quote_bad_report)


def demo_validator_class():
    """验证器类示例"""
    print("\n" + "=" * 60)
    print("示例 2: 使用 DataQualityValidator 类")
    print("=" * 60)
    
    # 创建验证器
    validator = DataQualityValidator(
        check_continuity=True,
        check_outliers=True,
        outlier_std=2.5  # 更严格的异常值检测
    )
    
    # 验证 K 线
    print("\n1. 验证 K 线数据:")
    kline_df = create_sample_kline_data()
    report, cleaned_df = validator.validate_kline(kline_df, return_cleaned=True)
    print(report)
    
    # 批量验证
    print("\n2. 批量验证多只股票:")
    multi_df = pd.concat([
        create_sample_kline_data(n_days=50).assign(symbol='600000.SH'),
        create_sample_kline_data(n_days=50).assign(symbol='000001.SZ'),
        create_sample_kline_data(n_days=50).assign(symbol='600519.SH'),
    ], ignore_index=True)
    
    reports = validator.batch_validate_kline(multi_df, group_by='symbol')
    for symbol, rep in reports.items():
        print(f"\n{symbol}:")
        print(f"  状态：{'✓ 通过' if rep.is_valid else '✗ 失败'}")
        print(f"  问题数：{rep.total_issues}")


def demo_auto_detection():
    """自动检测示例"""
    print("\n" + "=" * 60)
    print("示例 3: 自动数据类型检测")
    print("=" * 60)
    
    # 自动检测 DataFrame
    print("\n1. 自动检测 K 线数据:")
    kline_df = create_sample_kline_data()
    report = check_data_quality(kline_df)
    print(report)
    
    # 自动检测字典
    print("\n2. 自动检测行情数据:")
    quote = create_sample_quote_data()
    report = check_data_quality(quote)
    print(report)


def demo_custom_checks():
    """自定义检查示例"""
    print("\n" + "=" * 60)
    print("示例 4: 自定义检查参数")
    print("=" * 60)
    
    # 宽松模式
    print("\n1. 宽松模式 (不检查连续性，放宽异常值阈值):")
    validator_loose = DataQualityValidator(
        check_continuity=False,
        check_outliers=True,
        outlier_std=4.0
    )
    kline_df = create_sample_kline_data(include_issues=True)
    report = validator_loose.validate_kline(kline_df)[0]
    print(report)
    
    # 严格模式
    print("\n2. 严格模式 (检查所有项目，严格异常值阈值):")
    validator_strict = DataQualityValidator(
        check_continuity=True,
        check_outliers=True,
        outlier_std=2.0
    )
    report = validator_strict.validate_kline(kline_df)[0]
    print(report)


def demo_error_handling():
    """错误处理示例"""
    print("\n" + "=" * 60)
    print("示例 5: 错误处理")
    print("=" * 60)
    
    # 缺少必需字段
    print("\n1. 缺少必需字段:")
    incomplete_df = pd.DataFrame({'date': [1, 2, 3], 'close': [100, 101, 102]})
    report = validate_kline_data(incomplete_df)
    print(report)
    
    # 不支持的数据类型
    print("\n2. 不支持的数据类型:")
    report = check_data_quality("not a dataframe")
    print(report)


def main():
    """运行所有示例"""
    print("\n" + "#" * 60)
    print("# Finance Data Toolkit - 数据质量校验模块使用示例")
    print("#" * 60)
    
    demo_basic_validation()
    demo_validator_class()
    demo_auto_detection()
    demo_custom_checks()
    demo_error_handling()
    
    print("\n" + "=" * 60)
    print("所有示例运行完成!")
    print("=" * 60)


if __name__ == '__main__':
    main()
