# -*- coding: utf-8 -*-
"""
validation_rules.py 核心单规则最小单元测试集

覆盖:
- InputValidationRules: validate_url, validate_api_response, validate_request_config, validate_network_params
- TimeSeriesValidationRules: validate_kline_continuity, validate_trend_reasonableness, validate_volume_price_relationship
- BusinessLogicValidationRules: validate_a_stock_rules, validate_market_open_rules
- CrossSourceValidationRules: validate_cross_source, validate_data_confidence
- NumericValidationRules: validate_financial_ratios, validate_market_cap, validate_volume_anomalies, validate_price_patterns
- ValidationRuleRegistry: get_rules, list_categories, get_all_rule_ids
- 便捷函数: validate_input, validate_time_series, validate_business_logic, validate_numeric
"""

import sys
import os
import pytest
from datetime import datetime, timedelta
from typing import List, Dict, Any

# 确保能导入 finance_toolkit
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'finance-data-toolkit'))

from finance_toolkit.validation_rules import (
    SeverityLevel,
    ValidationIssue,
    InputValidationRules,
    TimeSeriesValidationRules,
    BusinessLogicValidationRules,
    CrossSourceValidationRules,
    NumericValidationRules,
    ValidationRuleRegistry,
    validate_input,
    validate_time_series,
    validate_business_logic,
    validate_numeric,
)


# ==================== 辅助工厂函数 ====================

def make_kline(date_str: str, open: float, close: float, high: float, low: float, volume: int = 1000000) -> Dict:
    return {
        'date': date_str,
        'open': open,
        'close': close,
        'high': high,
        'low': low,
        'volume': volume,
    }


def make_valid_klines(count: int = 10, start_price: float = 10.0) -> List[Dict]:
    """生成连续的合法K线数据"""
    klines = []
    base_date = datetime(2024, 1, 1)
    price = start_price
    for i in range(count):
        date = base_date + timedelta(days=i)
        # 跳过周末
        while date.weekday() >= 5:
            date += timedelta(days=1)
        change = (hash(date.isoformat()) % 200 - 100) / 10000.0  # ±1% 随机波动
        open_p = price
        close_p = price * (1 + change)
        high_p = max(open_p, close_p) * (1 + abs(change) / 2)
        low_p = min(open_p, close_p) * (1 - abs(change) / 2)
        klines.append(make_kline(date.strftime('%Y-%m-%d'), open_p, close_p, high_p, low_p))
        price = close_p
    return klines


class TestSeverityLevel:
    def test_severity_levels_exist(self):
        assert hasattr(SeverityLevel, 'CRITICAL')
        assert hasattr(SeverityLevel, 'ERROR')
        assert hasattr(SeverityLevel, 'WARNING')
        assert hasattr(SeverityLevel, 'INFO')

    def test_severity_values(self):
        assert SeverityLevel.CRITICAL.value == 'critical'
        assert SeverityLevel.ERROR.value == 'error'
        assert SeverityLevel.WARNING.value == 'warning'
        assert SeverityLevel.INFO.value == 'info'


class TestValidationIssue:
    def test_create_issue(self):
        issue = ValidationIssue(
            rule_id='I001',
            rule_name='URL格式校验',
            severity=SeverityLevel.ERROR,
            field='url',
            value='invalid',
            message='URL无效',
            suggestion='请使用标准URL',
        )
        assert issue.rule_id == 'I001'
        assert issue.severity == SeverityLevel.ERROR
        assert issue.to_dict()['rule_id'] == 'I001'

    def test_to_dict_returns_expected_keys(self):
        issue = ValidationIssue(
            rule_id='TS001',
            rule_name='日期格式校验',
            severity=SeverityLevel.WARNING,
            field='date',
            value='2024-01-01',
            message='test',
            suggestion='fix',
        )
        d = issue.to_dict()
        assert 'rule_id' in d
        assert 'severity' in d
        assert 'message' in d


class TestInputValidationRules:
    """测试输入校验规则 I001-I020"""

    def test_validate_url_valid(self):
        issues = InputValidationRules.validate_url('https://eastmoney.com/sh600000.html')
        assert len(issues) == 0

    def test_validate_url_none(self):
        issues = InputValidationRules.validate_url(None)
        assert len(issues) == 1
        assert issues[0].rule_id == 'I001'
        assert issues[0].severity == SeverityLevel.CRITICAL

    def test_validate_url_empty_string(self):
        issues = InputValidationRules.validate_url('')
        assert len(issues) == 1
        assert issues[0].rule_id == 'I001'

    def test_validate_url_invalid_format(self):
        issues = InputValidationRules.validate_url('not-a-url')
        assert len(issues) >= 1
        assert any(i.rule_id == 'I001' for i in issues)

    def test_validate_url_unlisted_domain(self):
        issues = InputValidationRules.validate_url('https://example.com/data')
        assert len(issues) >= 1
        assert any(i.rule_id == 'I002' for i in issues)

    def test_validate_url_waf_pattern(self):
        # URL包含常见WAF特征
        issues = InputValidationRules.validate_url('https://site.com/waf/challenge?token=abc')
        assert len(issues) >= 1
        assert any(i.rule_id == 'I003' for i in issues)

    def test_validate_api_response_none(self):
        issues = InputValidationRules.validate_api_response(None)
        assert len(issues) >= 1
        assert any(i.rule_id == 'I004' for i in issues)

    def test_validate_api_response_valid_dict(self):
        response = {'data': {'price': 10.5}, 'status': 'success'}
        issues = InputValidationRules.validate_api_response(response)
        # 正常响应不应有CRITICAL问题
        critical_issues = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical_issues) == 0

    def test_validate_api_response_with_error_code(self):
        response = {'errno': -1, 'message': 'server error'}
        issues = InputValidationRules.validate_api_response(response)
        assert any(i.rule_id == 'I006' for i in issues)

    def test_validate_api_response_non_dict_type(self):
        issues = InputValidationRules.validate_api_response([1, 2, 3], expected_type='dict')
        assert any(i.rule_id == 'I005' for i in issues)

    def test_validate_request_config_valid(self):
        config = {'method': 'GET', 'timeout': 10, 'retries': 3}
        issues = InputValidationRules.validate_request_config(config)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_validate_request_config_invalid_method(self):
        config = {'method': 'HACK'}
        issues = InputValidationRules.validate_request_config(config)
        assert any(i.rule_id == 'I008' for i in issues)

    def test_validate_request_config_timeout_out_of_range(self):
        config = {'timeout': 0}  # 低于下限
        issues = InputValidationRules.validate_request_config(config)
        assert any(i.rule_id == 'I009' for i in issues)

    def test_validate_request_config_concurrency_out_of_range(self):
        config = {'concurrency': 100}  # 超过上限
        issues = InputValidationRules.validate_request_config(config)
        assert any(i.rule_id == 'I010' for i in issues)

    def test_validate_request_config_retries_out_of_range(self):
        config = {'retries': 20}  # 超过上限
        issues = InputValidationRules.validate_request_config(config)
        assert any(i.rule_id == 'I011' for i in issues)

    def test_validate_network_params_page_below_one(self):
        params = {'page': 0}
        issues = InputValidationRules.validate_network_params(params)
        assert any(i.rule_id == 'I016' for i in issues)

    def test_validate_network_params_date_range_invalid(self):
        params = {'start_date': '2024-12-01', 'end_date': '2024-01-01'}
        issues = InputValidationRules.validate_network_params(params)
        assert any(i.rule_id == 'I018' for i in issues)

    def test_validate_network_params_date_range_too_long(self):
        params = {'start_date': '2010-01-01', 'end_date': '2024-12-31'}
        issues = InputValidationRules.validate_network_params(params)
        assert any(i.rule_id == 'I019' for i in issues)

    def test_validate_network_params_type_mismatch(self):
        params = {'code': 123456}  # 期望str但给了int
        issues = InputValidationRules.validate_network_params(params)
        assert any(i.rule_id == 'I020' for i in issues)


class TestTimeSeriesValidationRules:
    """测试时间序列验证规则 TS001-TS015"""

    def test_kline_continuity_too_few(self):
        # 少于2条K线，不应报错
        issues = TimeSeriesValidationRules.validate_kline_continuity([])
        assert len(issues) == 0
        issues = TimeSeriesValidationRules.validate_kline_continuity([make_kline('2024-01-01', 10, 10.1, 10.2, 9.9)])
        assert len(issues) == 0

    def test_kline_continuity_valid(self):
        klines = make_valid_klines(10)
        issues = TimeSeriesValidationRules.validate_kline_continuity(klines)
        # 连续数据不应有ERROR级别问题
        errors = [i for i in issues if i.severity == SeverityLevel.ERROR]
        assert len(errors) == 0

    def test_kline_continuity_with_bad_date(self):
        klines = make_valid_klines(5)
        klines.append({'date': 'not-a-date', 'open': 10, 'close': 10.1, 'high': 10.2, 'low': 9.9, 'volume': 1000})
        issues = TimeSeriesValidationRules.validate_kline_continuity(klines)
        assert any(i.rule_id == 'TS001' for i in issues)

    def test_trend_reasonableness_normal(self):
        klines = make_valid_klines(20)
        issues = TimeSeriesValidationRules.validate_trend_reasonableness(klines)
        # 正常数据不应有严重问题
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_trend_reasonableness_extreme_move(self):
        # 单日涨跌幅超过15%
        klines = make_valid_klines(5, start_price=10.0)
        klines.append(make_kline('2024-01-10', 10.0, 12.0, 12.5, 9.5))  # +20%
        issues = TimeSeriesValidationRules.validate_trend_reasonableness(klines)
        assert any(i.rule_id == 'TS006' for i in issues)

    def test_volume_price_relationship_normal(self):
        klines = make_valid_klines(20)
        issues = TimeSeriesValidationRules.validate_volume_price_relationship(klines)
        # 正常数据不应有ERROR
        errors = [i for i in issues if i.severity == SeverityLevel.ERROR]
        assert len(errors) == 0

    def test_volume_price_too_few(self):
        issues = TimeSeriesValidationRules.validate_volume_price_relationship([])
        assert len(issues) == 0
        issues = TimeSeriesValidationRules.validate_volume_price_relationship([make_kline('2024-01-01', 10, 10.1, 10.2, 9.9)])
        assert len(issues) == 0


class TestBusinessLogicValidationRules:
    """测试业务逻辑验证规则 L020-L040"""

    def test_a_stock_rules_valid_main_board(self):
        data = {
            'payload': {
                'symbol': '600000',
                'open': 10.0,
                'close': 10.5,
                'pre_close': 10.0,
                'volume': 1000000,
                'amount': 10500000,
            }
        }
        issues = BusinessLogicValidationRules.validate_a_stock_rules(data)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_a_stock_rules_negative_price(self):
        data = {
            'payload': {
                'symbol': '600000',
                'open': -1.0,
                'close': 10.5,
                'pre_close': 10.0,
            }
        }
        issues = BusinessLogicValidationRules.validate_a_stock_rules(data)
        assert any(i.rule_id == 'L022' for i in issues)

    def test_a_stock_rules_negative_volume(self):
        data = {
            'payload': {
                'symbol': '600000',
                'open': 10.0,
                'close': 10.5,
                'pre_close': 10.0,
                'volume': -100,
            }
        }
        issues = BusinessLogicValidationRules.validate_a_stock_rules(data)
        assert any(i.rule_id == 'L024' for i in issues)

    def test_a_stock_rules_st_limit_exceeded(self):
        # ST股涨跌幅超过5%（change_pct=6%，超过st_board限制0.05+0.005=0.055）
        data = {
            'payload': {
                'symbol': '000001',
                'name': 'ST测试',
                'change_pct': 6.0,  # > 5% + 0.5% tolerance
                'open': 10.0,
                'close': 10.6,
                'pre_close': 10.0,
            }
        }
        issues = BusinessLogicValidationRules.validate_a_stock_rules(data)
        assert any(i.rule_id == 'L021' for i in issues)

    def test_market_open_rules_valid(self):
        data = {
            'payload': {
                'symbol': '600000',
                'open': 10.1,
                'pre_close': 10.0,
                'volume': 1000000,
                'change_pct': 1.0,
            }
        }
        issues = BusinessLogicValidationRules.validate_market_open_rules(data)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_market_open_rules_suspended(self):
        # 成交量为0且价格无变化 -> 停牌检测
        data = {
            'payload': {
                'symbol': '600000',
                'open': 10.0,
                'pre_close': 10.0,
                'volume': 0,
                'change_pct': 0.0,
            }
        }
        issues = BusinessLogicValidationRules.validate_market_open_rules(data)
        assert any(i.rule_id == 'L027' for i in issues)


class TestCrossSourceValidationRules:
    """测试跨源一致性验证规则 C007-C020"""

    def test_cross_source_single_source(self):
        # 只有1个源，不应进行比较
        sources = {'source_a': {'payload': {'price': 10.0}, 'status': 'success'}}
        issues = CrossSourceValidationRules.validate_cross_source(sources)
        assert len(issues) == 0

    def test_cross_source_consistent(self):
        sources = {
            'source_a': {'payload': {'price': 10.0, 'volume': 1000000}, 'status': 'success'},
            'source_b': {'payload': {'price': 10.01, 'volume': 1000100}, 'status': 'success'},
        }
        issues = CrossSourceValidationRules.validate_cross_source(sources, data_type='quote')
        # 差异在阈值内，不应有ERROR
        errors = [i for i in issues if i.severity == SeverityLevel.ERROR]
        assert len(errors) == 0

    def test_cross_source_inconsistent(self):
        sources = {
            'source_a': {'payload': {'price': 10.0, 'volume': 1000000}, 'status': 'success'},
            'source_b': {'payload': {'price': 15.0, 'volume': 1000000}, 'status': 'success'},  # 价格差异大(50% > 2%阈值)
        }
        issues = CrossSourceValidationRules.validate_cross_source(sources, data_type='quote')
        assert any(i.rule_id == 'C008' for i in issues)  # 相对差异50% > 2%阈值，触发C008

    def test_cross_source_partial_failure(self):
        sources = {
            'source_a': {'payload': {'price': 10.0}, 'status': 'success'},
            'source_b': {'payload': None, 'status': 'error'},
        }
        issues = CrossSourceValidationRules.validate_cross_source(sources)
        assert any(i.rule_id == 'C009' for i in issues)

    def test_data_confidence_all_valid(self):
        now = datetime.now().isoformat()
        sources = {
            'src_a': {'status': 'success', 'payload': {'price': 10.0, 'volume': 1000}, 'timestamp': now},
            'src_b': {'status': 'success', 'payload': {'price': 10.01, 'volume': 1001}, 'timestamp': now},
        }
        result = CrossSourceValidationRules.validate_data_confidence(sources)
        assert result['overall_confidence'] > 0
        assert 'src_a' in result['source_confidences']

    def test_data_confidence_empty(self):
        result = CrossSourceValidationRules.validate_data_confidence({})
        assert result['overall_confidence'] == 0.0
        assert result['source_confidences'] == {}


class TestNumericValidationRules:
    """测试数值验证增强规则 NV001-NV015"""

    def test_financial_ratios_valid(self):
        data = {
            'payload': {
                'pe_ratio': 20.0,
                'pb_ratio': 2.0,
                'roe': 0.15,
                'debt_to_asset': 0.4,
            }
        }
        issues = NumericValidationRules.validate_financial_ratios(data)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_financial_ratios_out_of_range(self):
        data = {
            'payload': {
                'pe_ratio': 1000.0,  # 超出合理范围
                'pb_ratio': 2.0,
            }
        }
        issues = NumericValidationRules.validate_financial_ratios(data)
        assert any(i.rule_id == 'NV001' for i in issues)

    def test_market_cap_valid(self):
        data = {'payload': {'market_cap': 1e10}}  # 100亿
        issues = NumericValidationRules.validate_market_cap(data)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_market_cap_too_small(self):
        data = {'payload': {'market_cap': 100}}  # 太小
        issues = NumericValidationRules.validate_market_cap(data)
        assert any(i.rule_id == 'NV006' for i in issues)

    def test_volume_anomalies_valid(self):
        klines = make_valid_klines(20)
        issues = NumericValidationRules.validate_volume_anomalies(klines)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_volume_anomalies_zero_volume_high_ratio(self):
        # 制造大量零成交量
        klines = []
        for i in range(20):
            klines.append(make_kline(f'2024-01-{i+1:02d}', 10.0, 10.0, 10.0, 9.9, volume=0))
        issues = NumericValidationRules.validate_volume_anomalies(klines)
        assert any(i.rule_id == 'NV010' for i in issues)

    def test_volume_anomalies_sudden_spike(self):
        # 需要至少10条K线且10个有效成交量才能触发NV011
        klines = make_valid_klines(10)
        klines.append(make_kline('2024-01-10', 10.0, 10.0, 10.1, 9.9, volume=100000000))  # 突增
        issues = NumericValidationRules.validate_volume_anomalies(klines)
        assert any(i.rule_id == 'NV011' for i in issues)

    def test_price_patterns_normal(self):
        klines = make_valid_klines(20)
        issues = NumericValidationRules.validate_price_patterns(klines)
        critical = [i for i in issues if i.severity == SeverityLevel.CRITICAL]
        assert len(critical) == 0

    def test_price_patterns_consecutive_same_close(self):
        # 连续4天相同收盘价
        klines = [
            make_kline('2024-01-01', 10.0, 10.0, 10.0, 10.0),
            make_kline('2024-01-02', 10.0, 10.0, 10.0, 10.0),
            make_kline('2024-01-03', 10.0, 10.0, 10.0, 10.0),
            make_kline('2024-01-04', 10.0, 10.0, 10.0, 10.0),
            make_kline('2024-01-05', 10.5, 10.5, 10.6, 10.4),
        ]
        issues = NumericValidationRules.validate_price_patterns(klines)
        assert any(i.rule_id == 'NV013' for i in issues)

    def test_price_patterns_too_few(self):
        issues = NumericValidationRules.validate_price_patterns([])
        assert len(issues) == 0
        issues = NumericValidationRules.validate_price_patterns([make_kline('2024-01-01', 10, 10.1, 10.2, 9.9)])
        assert len(issues) == 0


class TestValidationRuleRegistry:
    """测试规则注册表"""

    def test_list_categories(self):
        categories = ValidationRuleRegistry.list_categories()
        assert 'input' in categories
        assert 'time_series' in categories
        assert 'business_logic' in categories
        assert 'cross_source' in categories
        assert 'numeric' in categories

    def test_get_rules_valid(self):
        rules = ValidationRuleRegistry.get_rules('input')
        assert rules is InputValidationRules

    def test_get_rules_invalid(self):
        rules = ValidationRuleRegistry.get_rules('nonexistent')
        assert rules is None

    def test_get_all_rule_ids_not_empty(self):
        rule_ids = ValidationRuleRegistry.get_all_rule_ids()
        assert len(rule_ids) > 0


class TestConvenienceFunctions:
    """测试便捷函数"""

    def test_validate_input_url_only(self):
        issues = validate_input(url='https://quote.eastmoney.com/sh600000.html')
        assert isinstance(issues, list)

    def test_validate_input_all_none(self):
        issues = validate_input()
        assert issues == []

    def test_validate_time_series_empty(self):
        issues = validate_time_series([])
        assert issues == []

    def test_validate_business_logic_empty(self):
        issues = validate_business_logic({'payload': {}})
        assert isinstance(issues, list)

    def test_validate_numeric_both_none(self):
        issues = validate_numeric()
        assert issues == []

    def test_validate_numeric_with_data(self):
        data = {'payload': {'pe_ratio': 20.0, 'pb_ratio': 2.0}}
        issues = validate_numeric(data=data)
        assert isinstance(issues, list)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
