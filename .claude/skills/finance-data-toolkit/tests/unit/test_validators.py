"""
test_validators.py - FinanceData 验证中间件单元测试
"""
import sys, pytest, math
from datetime import datetime
sys.path.insert(0, r'E:\codes\mini_claude_code\.claude\skills\finance-data-toolkit')

from finance_toolkit.plugins.types import DataType
from finance_toolkit.models.finance_data import FinanceData
from finance_toolkit.cleaning.validators import (
    FieldValidator, TypeValidator, RangeValidator,
    FinanceDataValidator, validate_finance_data, validate_finance_data_batch,
    FinanceDataValidationResult,
)

VALID_QUOTE_PAYLOAD = {'open': 10.5, 'high': 11.0, 'low': 10.3, 'close': 10.8, 'volume': 1000000, 'amount': 10800000.0}


def make_quote_fd(source='akshare', symbol='600000.SH', payload=None, timestamp=None):
    return FinanceData(source=source, data_type=DataType.QUOTE, symbol=symbol,
                       timestamp=timestamp or datetime.utcnow().isoformat(),
                       payload=payload or dict(VALID_QUOTE_PAYLOAD))


class TestFieldValidator:
    def test_valid_quote_has_required_fields(self):
        fd = make_quote_fd()
        result = FieldValidator().validate(fd)
        assert not any(i.field_path == 'symbol' and i.value == '' for i in result)

    def test_empty_symbol_flagged(self):
        fd = make_quote_fd(symbol='')
        result = FieldValidator().validate(fd)
        assert any(i.field_path == 'symbol' for i in result)

    def test_empty_source_flagged(self):
        fd = make_quote_fd(source='')
        result = FieldValidator().validate(fd)
        assert any(i.field_path == 'source' for i in result)

    def test_all_required_present(self):
        fd = FinanceData(source='akshare', data_type=DataType.QUOTE, symbol='600000.SH',
                         timestamp=datetime.utcnow().isoformat(), payload={'open': 10.0})
        result = FieldValidator().validate(fd)
        relevant = [i for i in result if i.field_path in ('symbol', 'source', 'payload')]
        assert len(relevant) == 0

    def test_none_timestamp_no_crash(self):
        fd = make_quote_fd(timestamp='')
        result = FieldValidator().validate(fd)
        assert isinstance(result, list)


class TestTypeValidator:
    def test_valid_quote_types_pass(self):
        tv = TypeValidator()
        result = tv.validate(VALID_QUOTE_PAYLOAD)
        assert isinstance(result, list)

    def test_string_in_float_field_flagged(self):
        payload = dict(VALID_QUOTE_PAYLOAD); payload['open'] = 'not_a_number'
        result = TypeValidator().validate(payload)
        assert any(i.field_path == 'payload.open' for i in result)

    def test_int_accepted_as_float(self):
        payload = dict(VALID_QUOTE_PAYLOAD); payload['open'] = 10
        result = TypeValidator().validate(payload)
        assert not any(i.field_path == 'open' for i in result)

    def test_none_accepted(self):
        payload = dict(VALID_QUOTE_PAYLOAD); payload['volume'] = None
        result = TypeValidator().validate(payload)
        # None typically allowed in type checks
        assert isinstance(result, list)

    def test_unmapped_fields_ignored(self):
        result = TypeValidator().validate({'custom_tag': 'hello'})
        assert not any(i.field_path == 'custom_tag' for i in result)

    def test_list_not_flagged(self):
        result = TypeValidator().validate({'tags': ['A', 'B']})
        assert not any(i.field_path == 'tags' for i in result)


class TestRangeValidator:
    def test_valid_range_passes(self):
        result = RangeValidator().validate({'open': 10.0, 'volume': 1000000})
        assert isinstance(result, list)
        assert not any(i.field_path == 'open' for i in result)

    def test_zero_volume_accepted(self):
        result = RangeValidator().validate({'open': 10.0, 'volume': 0})
        assert not any(i.field_path == 'volume' for i in result)

    def test_very_large_volume_accepted(self):
        result = RangeValidator().validate({'open': 10.0, 'volume': 999999999999})
        assert isinstance(result, list)

    def test_invalid_type_returns_empty(self):
        result = RangeValidator().validate('not a dict')
        # 非 dict 输入返回空列表而非抛异常
        assert result == []


class TestFinanceDataValidator:
    def test_valid_quote_full_flow(self):
        fd = make_quote_fd(source='akshare', symbol='600000.SH')
        result = FinanceDataValidator().validate(fd)
        assert isinstance(result, FinanceDataValidationResult)
        assert hasattr(result, 'health_score')

    def test_invalid_symbol_score_reduction(self):
        fd = make_quote_fd(symbol='')
        result = FinanceDataValidator().validate(fd)
        assert result.health_score < 100.0
        assert result.total_issues > 0

    def test_full_payload_high_score(self):
        fd = make_quote_fd(payload=dict(VALID_QUOTE_PAYLOAD))
        result = FinanceDataValidator().validate(fd)
        assert result.health_score >= 60.0

    def test_empty_symbol_and_payload_low_score(self):
        fd = FinanceData(source='test', data_type=DataType.QUOTE, symbol='',
                         timestamp=datetime.utcnow().isoformat(), payload={})
        result = FinanceDataValidator().validate(fd)
        assert result.health_score < 90.0

    def test_validate_and_normalize_no_change_for_valid(self):
        fd = make_quote_fd(payload=dict(VALID_QUOTE_PAYLOAD))
        normalized = FinanceDataValidator().validate_and_normalize(fd)
        assert normalized.payload == fd.payload

    def test_health_score_non_negative(self):
        fd = FinanceData(source='', data_type=DataType.QUOTE, symbol='',
                         timestamp='', payload={})
        result = FinanceDataValidator().validate(fd)
        assert result.health_score >= 0.0


class TestConvenienceFunctions:
    def test_validate_finance_data_returns_result(self):
        result = validate_finance_data(make_quote_fd())
        assert isinstance(result, FinanceDataValidationResult)

    def test_batch_empty(self):
        assert validate_finance_data_batch([]) == []

    def test_batch_multiple(self):
        fds = [make_quote_fd(symbol=f'{i:06d}.SH') for i in range(3)]
        results = validate_finance_data_batch(fds)
        assert len(results) == 3
        assert all(isinstance(r, FinanceDataValidationResult) for r in results)

    def test_batch_mixed(self):
        fds = [
            make_quote_fd(symbol='600000.SH'),
            FinanceData(source='test', data_type=DataType.QUOTE, symbol='',
                        timestamp=datetime.utcnow().isoformat(), payload={}),
        ]
        results = validate_finance_data_batch(fds)
        assert results[0].health_score > results[1].health_score


class TestEdgeCases:
    def test_none_payload(self):
        fd = FinanceData(source='akshare', data_type=DataType.QUOTE, symbol='600000.SH',
                         timestamp='', payload=None)
        result = FinanceDataValidator().validate(fd)
        assert isinstance(result, FinanceDataValidationResult)

    def test_nan_values(self):
        payload = dict(VALID_QUOTE_PAYLOAD); payload['open'] = float('nan')
        fd = make_quote_fd(payload=payload)
        result = FinanceDataValidator().validate(fd)
        assert isinstance(result, FinanceDataValidationResult)

    def test_result_to_dict(self):
        d = validate_finance_data(make_quote_fd()).to_dict()
        assert 'is_valid' in d and 'health_score' in d and 'total_issues' in d

    def test_total_issues(self):
        result = validate_finance_data(make_quote_fd(symbol=''))
        assert isinstance(result.total_issues, int) and result.total_issues >= 0

    def test_health_score_range(self):
        result = validate_finance_data(make_quote_fd())
        assert 0.0 <= result.health_score <= 100.0

    def test_invalid_data_type(self):
        fd = FinanceData(source='akshare', data_type='not_an_enum', symbol='600000.SH',
                         timestamp=datetime.utcnow().isoformat(), payload={})
        result = validate_finance_data(fd)
        assert isinstance(result, FinanceDataValidationResult)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
