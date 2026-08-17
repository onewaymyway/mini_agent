# -*- coding: utf-8 -*-
"""
基础输入校验模块单元测试
=========================
覆盖 input_validator 的所有核心功能
"""

import pytest
from finance_toolkit.input_validator import (
    InputValidator,
    ValidationResult,
    BatchResult,
    check_stock_code,
    check_crypto_symbol,
    check_date,
    check_numeric,
    check_required_fields,
    check_price_range,
    validate,
    validate_batch,
    list_schemas,
)


# ============== 格式检查函数测试 ==============

class TestFormatCheckers:
    """测试各格式检查函数"""

    def test_check_stock_code_valid(self):
        assert check_stock_code('600519.SH') is None
        assert check_stock_code('000001.SZ') is None
        assert check_stock_code('300750.SZ') is None
        assert check_stock_code('688981.SH') is None
        assert check_stock_code('002050.BJ') is None

    def test_check_stock_code_invalid(self):
        assert check_stock_code('INVALID') is not None
        assert check_stock_code('123.SH') is not None
        assert check_stock_code('600519.XX') is not None
        assert check_stock_code('') is not None
        assert check_stock_code(None) is not None

    def test_check_crypto_symbol_valid(self):
        assert check_crypto_symbol('BTC') is None
        assert check_crypto_symbol('ETH') is None
        assert check_crypto_symbol('BTCUSDT') is None
        assert check_crypto_symbol('ETH_USD') is None

    def test_check_crypto_symbol_invalid(self):
        assert check_crypto_symbol('') is not None
        assert check_crypto_symbol(None) is not None
        assert check_crypto_symbol('123') is not None

    def test_check_date_valid(self):
        assert check_date('2024-01-15') is None
        assert check_date('2024/01/15') is None
        assert check_date('20240115') is None

    def test_check_date_invalid(self):
        assert check_date('x') is not None
        assert check_date('') is not None
        assert check_date(None) is not None

    def test_check_numeric_valid(self):
        assert check_numeric(100, 'price') is None
        assert check_numeric(0.5, 'price') is None
        assert check_numeric(-5, 'yield', allow_negative=True) is None

    def test_check_numeric_invalid(self):
        assert check_numeric(None, 'price') is not None
        assert check_numeric('abc', 'price') is not None
        assert check_numeric(-100, 'price') is not None

    def test_check_required_fields(self):
        data = {'a': 1, 'b': None, 'c': ''}
        errors = check_required_fields(data, ['a', 'b', 'c'])
        assert 'a' not in [e.split(':')[1].strip() for e in errors]
        assert len(errors) == 2

    def test_check_price_range(self):
        assert check_price_range(100, min_val=0) is None
        assert check_price_range(-1, min_val=0) is not None
        assert check_price_range(1e10, max_val=1e8, field_name='高价') is not None


# ============== InputValidator 测试 ==============

class TestInputValidator:
    """测试 InputValidator 类"""

    @pytest.fixture
    def validator(self):
        return InputValidator()

    def test_validate_stock_ok(self, validator):
        data = {
            'code': '600519.SH',
            'date': '2024-01-15',
            'open': 1680.0,
            'high': 1700.0,
            'low': 1670.0,
            'close': 1695.0,
            'volume': 50000,
            'change_pct': 0.89,
        }
        result = validator.validate(data, 'stock')
        assert result.is_valid
        assert result.schema == 'stock'

    def test_validate_stock_missing_fields(self, validator):
        data = {'code': '', 'date': ''}
        result = validator.validate(data, 'stock')
        assert not result.is_valid
        assert any('必填' in e for e in result.errors)

    def test_validate_stock_invalid_code(self, validator):
        data = {'code': 'BAD', 'date': '2024-01-15'}
        result = validator.validate(data, 'stock')
        assert not result.is_valid
        assert any('格式不正确' in e for e in result.errors)

    def test_validate_stock_negative_price(self, validator):
        data = {'code': '600519.SH', 'date': '2024-01-15', 'close': -5}
        result = validator.validate(data, 'stock')
        assert not result.is_valid

    def test_validate_none_data(self, validator):
        result = validator.validate(None, 'stock')
        assert not result.is_valid
        assert 'None' in result.errors[0]

    def test_validate_non_dict_data(self, validator):
        result = validator.validate('hello', 'stock')
        assert not result.is_valid
        assert '字典' in result.errors[0]

    def test_validate_crypto(self, validator):
        data = {'symbol': 'BTC', 'price': 45000.0, 'change_pct_24h': 2.5}
        result = validator.validate(data, 'crypto')
        assert result.is_valid

    def test_validate_futures(self, validator):
        data = {'contract': 'IF2403', 'date': '2024-01-15', 'close': 3800.0}
        result = validator.validate(data, 'futures')
        assert result.is_valid

    def test_validate_news(self, validator):
        data = {'title': '央行降息', 'source': '新华社', 'publish_time': '2024-01-15'}
        result = validator.validate(data, 'news')
        assert result.is_valid

    def test_validate_forex(self, validator):
        data = {'pair': 'EURUSD', 'date': '2024-01-15', 'mid': 1.085}
        result = validator.validate(data, 'forex')
        assert result.is_valid

    def test_infer_schema_auto(self, validator):
        # 自动推断股票代码
        data = {'code': '000001.SZ', 'date': '2024-01-15', 'close': 10.5}
        result = validator.validate(data)
        assert result.is_valid
        assert result.schema == 'stock'

    def test_infer_schema_crypto(self, validator):
        data = {'symbol': 'ETH', 'price': 2500.0, 'change_pct_24h': 1.2}
        result = validator.validate(data)
        assert result.is_valid
        assert result.schema == 'crypto'

    def test_unknown_schema(self, validator):
        result = validator.validate({'a': 1}, 'unknown_type')
        assert not result.is_valid
        assert '未知' in result.errors[0]

    def test_list_schemas(self, validator):
        schemas = validator.list_schemas()
        assert 'stock' in schemas
        assert 'crypto' in schemas
        assert len(schemas) == 10

    def test_get_schema_rules(self, validator):
        rules = validator.get_schema_rules('stock')
        assert rules is not None
        assert 'required_fields' in rules

    def test_no_max_range_configured(self, validator):
        # stock schema 未配置 close 上限，超大值不应报错
        data = {'code': '600519.SH', 'date': '2024-01-15', 'close': 9999999}
        result = validator.validate(data, 'stock')
        assert result.is_valid
        assert not result.errors

    def test_warnings_bond_yield(self, validator):
        # bond yield_rate 有 max=100 范围检查，超限应产生 warning
        data = {'code': '019823.SZ', 'date': '2024-01-15', 'yield_rate': 150.0}
        result = validator.validate(data, 'bond')
        assert result.warnings  # 超出上限应为警告


# ============== 批量校验测试 ==============

class TestBatchValidation:
    """测试批量校验"""

    @pytest.fixture
    def validator(self):
        return InputValidator()

    def test_batch_all_pass(self, validator):
        data_list = [
            {'code': '600519.SH', 'date': '2024-01-15', 'close': 1695.0},
            {'code': '000001.SZ', 'date': '2024-01-15', 'close': 12.5},
        ]
        result = validator.validate_batch(data_list, 'stock')
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert abs(result.pass_rate - 1.0) < 1e-6

    def test_batch_some_fail(self, validator):
        data_list = [
            {'code': '600519.SH', 'date': '2024-01-15', 'close': 1695.0},
            {'code': 'BAD', 'date': 'x', 'close': -1},
        ]
        result = validator.validate_batch(data_list, 'stock')
        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1
        assert abs(result.pass_rate - 0.5) < 1e-6

    def test_batch_empty(self, validator):
        result = validator.validate_batch([], 'stock')
        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0

    def test_batch_result_dict(self, validator):
        data_list = [{'code': '600519.SH', 'date': '2024-01-15', 'close': 1695.0}]
        result = validator.validate_batch(data_list, 'stock')
        d = result.to_dict()
        assert d['total'] == 1
        assert d['passed'] == 1


# ============== 便捷函数测试 ==============

class TestConvenienceFunctions:
    """测试全局便捷函数"""

    def test_validate_function(self):
        result = validate({'code': '600519.SH', 'date': '2024-01-15', 'close': 1695.0}, 'stock')
        assert result.is_valid

    def test_validate_batch_function(self):
        result = validate_batch([
            {'code': '600519.SH', 'date': '2024-01-15', 'close': 1695.0},
        ], 'stock')
        assert result.passed == 1

    def test_list_schemas_function(self):
        schemas = list_schemas()
        assert 'stock' in schemas
        assert len(schemas) == 10


# ============== ValidationResult 测试 ==============

class TestValidationResult:
    """测试 ValidationResult 数据类"""

    def test_is_valid_property(self):
        r1 = ValidationResult(valid=True, errors=[])
        assert r1.is_valid
        r2 = ValidationResult(valid=False, errors=['err'])
        assert not r2.is_valid

    def test_to_dict(self):
        r = ValidationResult(valid=False, errors=['e1', 'e2'], warnings=['w1'], schema='stock')
        d = r.to_dict()
        assert d['valid'] is False
        assert len(d['errors']) == 2
        assert d['schema'] == 'stock'
        assert 'timestamp' in d

    def test_str_repr(self):
        r = ValidationResult(valid=True, errors=[], warnings=[], schema='stock')
        s = str(r)
        assert '通过' in s

        r2 = ValidationResult(valid=False, errors=['missing field'], schema='stock')
        s2 = str(r2)
        assert '失败' in s2


# ============== BatchResult 测试 ==============

class TestBatchResult:
    """测试 BatchResult 数据类"""

    def test_pass_rate_zero_total(self):
        r = BatchResult(total=0, passed=0, failed=0)
        assert r.pass_rate == 0.0

    def test_pass_rate_normal(self):
        r = BatchResult(total=10, passed=7, failed=3)
        assert abs(r.pass_rate - 0.7) < 1e-6

    def test_to_dict(self):
        r = BatchResult(total=5, passed=3, failed=2)
        d = r.to_dict()
        assert d['total'] == 5
        assert d['passed'] == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
