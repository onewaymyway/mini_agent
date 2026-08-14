# -*- coding: utf-8 -*-
"""
DataParser 抽象层单元测试 (Step 6)
"""
import pytest
from finance_toolkit.data_parsing import (
    registry, parse_raw_data, parse_data,
    _parse_float, _parse_int, _parse_date, _now_iso,
)
# 直接导入解析器类（避免旧版缓存）
from finance_toolkit.data_parsing.quote_parser import QuoteParser
from finance_toolkit.data_parsing.base_parsers import (
    parse_tencent_quote, parse_sina_quote, parse_eastmoney_quote,
    parse_kline_data, parse_news_data, parse_sector_data,
)


class TestRegistry:
    def test_list_sources(self):
        sources = registry.list_sources()
        assert 'quote' in sources
        assert 'fund' in sources
        assert 'kline' in sources
        assert 'sector' in sources
        assert 'lhb' in sources
        assert 'forex' in sources
        assert 'crypto' in sources
        assert 'macro' in sources
        assert 'news' in sources
        assert 'etf' in sources
        assert 'bond' in sources
        assert 'guba' in sources
        assert 'commodity' in sources

    def test_list_types(self):
        types = registry.list_types()
        assert len(types) >= 20
        expected = ['quote', 'kline', 'news', 'fund_nav', 'sector_quote', 'lhb', 'northbound', 'forex_quote', 'crypto_quote', 'macro', 'gdp', 'cpi']
        for t in expected:
            assert t in types, f"Missing type: {t}"

    def test_get_by_source(self):
        parser = registry.get('quote')
        assert parser is not None
        assert parser.can_parse('quote')
        assert parser.can_parse('stock_quote')
        assert not parser.can_parse('crypto_quote')

    def test_find_parser_by_type(self):
        p = registry.find_parser('crypto_quote')
        assert p is not None
        assert p.source_name == 'crypto'
        p2 = registry.find_parser('bond_yield')
        assert p2 is not None
        assert p2.source_name == 'bond'


class TestQuoteParser:
    def test_tencent_quote(self):
        # 腾讯格式需要至少35个~分隔字段
        fields = '~'.join(['1','浦发银行','600000','10.50','10.20','10.35','10.55','10.15','10.35','10.36'] + ['0']*25)
        text = f'var hq_str_sh600000="{fields}";'
        result = parse_data('tencent_quote', text, source='quote')
        assert len(result) == 1
        assert result[0]['code'] == '600000'
        assert result[0]['price'] == 10.50
        assert result[0]['pre_close'] == 10.20

    def test_sina_quote(self):
        # 新浪格式需要至少32个逗号分隔字段
        fields = ','.join(['浦发银行','10.20','10.35','10.50','10.55','10.15','10.35','10.36','85643213','885643210'] + ['0']*22)
        text = f'var hq_str_sh600000="{fields}";'
        result = parse_data('sina_quote', text, source='quote')
        assert len(result) == 1
        assert result[0]['price'] == 10.50

    def test_eastmoney_quote(self):
        data = {'data': {'f57': '600000', 'f169': '浦发银行', 'f43': '10.50', 'f47': '10.20', 'f170': '2.5'}}
        result = parse_eastmoney_quote(data)
        assert result['code'] == '600000'
        assert result['price'] == 10.50

    def test_dict_format(self):
        data = [{'code': '600000', 'name': '浦发银行', 'price': 10.50, 'change_pct': 2.5}]
        result = parse_data('stock_quote', data)
        assert len(result) == 1
        assert result[0]['price'] == 10.50


class TestKlineParser:
    def test_basic(self):
        data = [
            {'date': '2024-01-01', 'open': 10.0, 'close': 10.5, 'high': 10.8, 'low': 9.8, 'volume': 1000000},
            {'date': '2024-01-02', 'open': 10.5, 'close': 10.2, 'high': 10.6, 'low': 10.0, 'volume': 900000},
        ]
        result = parse_kline_data(data, source='test')
        assert len(result) == 2
        assert result[0]['close'] == 10.5
        assert result[1]['volume'] == 900000

    def test_from_dict(self):
        data = {'records': [{'date': '2024-01-01', 'open': 10.0, 'close': 10.5, 'high': 10.8, 'low': 9.8, 'volume': 1000000}]}
        result = parse_data('kline', data)
        assert len(result) == 1
        assert result[0]['close'] == 10.5


class TestFundNavParser:
    def test_from_dict(self):
        data = {'code': '110011', 'name': '易方达中小盘', 'nav': 5.23, 'acc_nav': 6.10, 'nav_date': '2024-06-30'}
        result = parse_raw_data(data, 'fund', 'fund_nav')
        assert len(result) == 1
        assert result[0]['code'] == '110011'
        assert result[0]['nav'] == 5.23

    def test_js_string(self):
        js_text = '''var fS_name = "易方达";var fS_code = "110011";var Data_netWorthTrend = [{"x":"2024-06-30","y":"5.23"}];'''
        result = parse_raw_data(js_text, 'fund', 'fund_nav')
        assert len(result) >= 1
        assert result[0]['nav'] == 5.23


class TestSectorParser:
    def test_quote(self):
        data = {'sectors': [{'sector_code': 'BK0477', 'sector_name': 'CPO概念', 'change_pct': 3.5, 'top_stock': '中际旭创'}]}
        result = parse_raw_data(data, 'sector', 'sector_quote')
        assert len(result) == 1
        assert result[0]['change_pct'] == 3.5

    def test_flow(self):
        data = {'sectors': [{'sector_name': 'CPO概念', 'main_inflow': 500000000, 'change_pct': 3.5}]}
        result = parse_raw_data(data, 'sector', 'sector_flow')
        assert len(result) == 1
        assert result[0]['main_inflow'] == 500000000


class TestMacroParser:
    def test_gdp(self):
        data = {'records': [{'quarter': '2024Q1', 'gdp': 296264, 'growth_rate': 5.3, 'per_capita': 7800}]}
        result = parse_raw_data(data, 'macro', 'gdp')
        assert len(result) == 1
        assert result[0]['growth_rate'] == 5.3

    def test_cpi(self):
        data = {'records': [{'date': '2024-06', 'cpi': 100.3, 'yoy': 0.3, 'food': 101.5}]}
        result = parse_raw_data(data, 'macro', 'cpi')
        assert len(result) == 1
        assert result[0]['cpi'] == 100.3


class TestNewsParser:
    def test_basic(self):
        data = {'list': [{'title': 'A股大涨', 'url': 'http://example.com', 'source': '财经', 'ctime': '2024-06-30'}]}
        result = parse_data('news', data, source='test')
        assert len(result) == 1
        assert result[0]['title'] == 'A股大涨'


class TestCryptoParser:
    def test_basic(self):
        data = [{'symbol': 'BTC', 'price': 65000, 'change_pct': 2.5, 'market_cap': 1200000000000}]
        result = parse_data('crypto_quote', data)
        assert len(result) == 1
        assert result[0]['price'] == 65000


class TestForexParser:
    def test_quote(self):
        data = [{'code': 'USDCNY', 'price': 7.25, 'change_pct': -0.1}]
        result = parse_data('forex_quote', data)
        assert len(result) == 1
        assert result[0]['price'] == 7.25


class TestLHBParser:
    def test_basic(self):
        data = [{'code': '300123', 'name': '测试股', 'buy_amount': 50000000, 'net_amount': 30000000}]
        result = parse_data('lhb', data)
        assert len(result) == 1
        assert result[0]['net_amount'] == 30000000


class TestNorthboundParser:
    def test_basic(self):
        data = [{'date': '2024-06-30', 'ggt_net': 50000000, 'szt_net': 30000000}]
        result = parse_data('northbound', data)
        assert len(result) == 1
        assert result[0]['total_net'] == 0  # 新字段


class TestGubaParser:
    def test_basic(self):
        data = [{'post_id': '123', 'title': '大牛股', 'author': '股民A', 'view_count': 1000, 'stock_code': '600000'}]
        result = parse_data('guba', data)
        assert len(result) == 1
        assert result[0]['title'] == '大牛股'


class TestBondParser:
    def test_yield(self):
        data = [{'date': '2024-06-30', '1y': 2.5, '5y': 2.8, '10y': 3.0}]
        result = parse_data('bond_yield', data)
        assert len(result) == 1
        assert result[0]['10y'] == 3.0


class TestETFParser:
    def test_quote(self):
        data = [{'code': '510300', 'name': '沪深300ETF', 'price': 4.5, 'change_pct': 1.2}]
        result = parse_data('etf_quote', data)
        assert len(result) == 1
        assert result[0]['price'] == 4.5


class TestCommodityParser:
    def test_basic(self):
        data = [{'symbol': 'XAU', 'name': '黄金', 'price': 2350.0, 'change_pct': 0.5}]
        result = parse_data('gold_quote', data)
        assert len(result) == 1
        assert result[0]['price'] == 2350.0


class TestParseRawData:
    def test_unknown_source_returns_empty(self):
        result = parse_raw_data({}, 'unknown_src', 'quote')
        assert result == []


class TestUtils:
    def test_parse_float(self):
        assert _parse_float('10.5') == 10.5
        assert _parse_float(None) == 0.0
        assert _parse_float('abc') == 0.0

    def test_parse_int(self):
        assert _parse_int('100') == 100
        assert _parse_int(None) == 0

    def test_parse_date(self):
        assert _parse_date('2024-06-30') == '2024-06-30'
        assert _parse_date(None) == ''

    def test_now_iso(self):
        s = _now_iso()
        assert 'T' in s
        assert len(s) > 15


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
