# -*- coding: utf-8 -*-
"""
股票数据抓取集成测试
覆盖API对接、数据解析、异常处理
"""

import pytest
from unittest.mock import patch
from datetime import datetime, timedelta
import pandas as pd
import sys
import os

# 添加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from finance_toolkit.data_fetching.stock_fetcher import (
    StockQuote, StockKline, FinancialReport, Dividend,
    LHBRecord, NorthboundFlow, StockBasic,
    fetch_realtime_quote, fetch_kline, fetch_financial,
    fetch_dividend, fetch_lhb, fetch_northbound, fetch_stock_basic,
    fetch_all_stock_data,
)
from finance_toolkit.interface import to_standard_symbol, to_sina_symbol, to_akshare_symbol
from finance_toolkit.exceptions import SourceUnavailableError


# ============== 模拟数据（DataFrame格式）=============

MOCK_QUOTE_DF = pd.DataFrame([
    {
        "代码": "600000", "名称": "浦发银行",
        "最新价": 10.70, "涨跌幅": 2.10, "今开": 10.50,
        "最高": 10.80, "最低": 10.40, "昨收": 10.48,
        "成交量": 1000000, "成交额": 10500000.0,
        "换手率": 0.5, "市盈率-动态": 5.5, "市净率": 0.6,
        "总市值": 105000000000, "流通市值": 95000000000,
    },
    {
        "代码": "000001", "名称": "平安银行",
        "最新价": 15.20, "涨跌幅": 1.33, "今开": 15.00,
        "最高": 15.30, "最低": 14.95, "昨收": 15.00,
        "成交量": 2000000, "成交额": 30200000.0,
        "换手率": 0.8, "市盈率-动态": 6.2, "市净率": 0.7,
        "总市值": 200000000000, "流通市值": 180000000000,
    },
])

MOCK_KLINE_DF = pd.DataFrame([
    {
        "日期": "2026-08-12", "开盘": 10.50, "收盘": 10.70,
        "最高": 10.80, "最低": 10.40, "成交量": 1000000,
        "成交额": 10500000.0, "振幅": 3.83, "涨跌幅": 2.10,
        "涨跌额": 0.22, "换手率": 0.5,
    },
    {
        "日期": "2026-08-11", "开盘": 10.40, "收盘": 10.48,
        "最高": 10.55, "最低": 10.35, "成交量": 800000,
        "成交额": 8384000.0, "振幅": 2.87, "涨跌幅": 0.96,
        "涨跌额": 0.08, "换手率": 0.38,
    },
])

MOCK_FINANCIAL_DF = pd.DataFrame([
    {"报告期": "2026-06-30", "总资产": 5000000000000, "总负债": 4600000000000, "净资产": 400000000000, "营业收入": 300000000000, "净利润": 80000000000},
    {"报告期": "2026-03-31", "总资产": 4900000000000, "总负债": 4500000000000, "净资产": 400000000000, "营业收入": 75000000000, "净利润": 20000000000},
])

MOCK_DIVIDEND_DF = pd.DataFrame([
    {"报告期": "2025年报", "每股分红": 0.325, "分红方案": "10派3.25元", "除权除息日": "2026-06-15", "股权登记日": "2026-06-14"},
])

MOCK_LHB_DF = pd.DataFrame([
    {"龙虎榜日期": "2026-08-12", "代码": "600000", "名称": "浦发银行", "解释说明": "日振幅达到15%的前5只证券", "买入金额": 50000000, "卖出金额": 30000000, "净买入": 20000000, "买入营业部": "机构专用", "卖出营业部": "机构专用"},
])

MOCK_NORTHBOUND_DF = pd.DataFrame([
    {"日期": "2026-08-12", "沪股通净流入": 15.5, "深股通净流入": 12.3, "北向资金净流入": 27.8},
    {"日期": "2026-08-11", "沪股通净流入": -5.2, "深股通净流入": 3.1, "北向资金净流入": -2.1},
])

MOCK_BASIC_DF = pd.DataFrame([
    {"代码": "600000", "名称": "浦发银行", "行业": "银行", "上市日期": "1999-11-10"},
    {"代码": "000001", "名称": "平安银行", "行业": "银行", "上市日期": "1991-04-03"},
])


# ============== 符号转换测试 ==============

class TestSymbolConversion:
    """符号格式转换测试"""

    def test_to_standard_symbol_sh(self):
        """上海股票代码转换"""
        assert to_standard_symbol('600000') == '600000.SH'
        assert to_standard_symbol('688001') == '688001.SH'
        assert to_standard_symbol('900001') == '900001.SH'

    def test_to_standard_symbol_sz(self):
        """深圳股票代码转换"""
        assert to_standard_symbol('000001') == '000001.SZ'
        assert to_standard_symbol('300001') == '300001.SZ'
        assert to_standard_symbol('002001') == '002001.SZ'

    def test_to_standard_symbol_already_formatted(self):
        """已格式化代码不变"""
        assert to_standard_symbol('600000.SH') == '600000.SH'
        assert to_standard_symbol('000001.SZ') == '000001.SZ'

    def test_to_standard_symbol_with_space(self):
        """带空格的代码"""
        assert to_standard_symbol(' 600000 ') == '600000.SH'

    def test_to_sina_symbol(self):
        """转换为新浪格式"""
        assert to_sina_symbol('600000.SH') == 'sh600000'
        assert to_sina_symbol('000001.SZ') == 'sz000001'

    def test_to_akshare_symbol(self):
        """转换为akshare格式"""
        assert to_akshare_symbol('600000.SH') == '600000'
        assert to_akshare_symbol('000001.SZ') == '000001'


# ============== 数据模型测试 ==============

class TestStockQuoteModel:
    """StockQuote 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        q = StockQuote(
            symbol='600000', name='浦发银行', price=10.70, change_pct=2.10,
            open=10.50, high=10.80, low=10.40, prev_close=10.48,
            volume=1000000, amount=10500000.0, turnover_rate=0.5,
            pe_ratio=5.5, pb_ratio=0.6, total_market_cap=105000000000, float_market_cap=95000000000,
        )
        assert q.symbol == '600000'
        assert q.price == 10.70
        assert q.pe_ratio == 5.5

    def test_fields_not_none(self):
        """所有字段非空"""
        q = StockQuote(symbol='600000', name='浦发银行', price=10.70, change_pct=2.10, open=10.50, high=10.80, low=10.40, prev_close=10.48, volume=1000000, amount=10500000.0, turnover_rate=0.5, pe_ratio=5.5, pb_ratio=0.6, total_market_cap=105000000000, float_market_cap=95000000000)
        assert q.symbol != ''
        assert q.name != ''
        assert q.price > 0
        assert isinstance(q.volume, int)
        assert isinstance(q.amount, float)

    def test_default_values(self):
        """默认值"""
        q = StockQuote(symbol='600000', name='浦发银行', price=10.70, change_pct=2.10, open=10.50, high=10.80, low=10.40, prev_close=10.48, volume=0, amount=0.0, turnover_rate=0.0, pe_ratio=0.0, pb_ratio=0.0, total_market_cap=0.0, float_market_cap=0.0)
        assert q.price == 10.70
        assert q.pe_ratio == 0.0
        assert q.source == 'akshare'


class TestStockKlineModel:
    """StockKline 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        k = StockKline(symbol='600000', date='2026-08-12', open=10.50, close=10.70, high=10.80, low=10.40, volume=1000000, amount=10500000.0, amplitude=3.83, change_pct=2.10, change_amount=0.22, turnover_rate=0.5)
        assert k.date == '2026-08-12'
        assert k.close == 10.70
        assert k.amplitude == 3.83

    def test_timestamp_present(self):
        """时间戳存在"""
        k = StockKline(symbol='600000', date='2026-08-12', open=10.50, close=10.70, high=10.80, low=10.40, volume=1000000, amount=10500000.0, amplitude=3.83, change_pct=2.10, change_amount=0.22, turnover_rate=0.5)
        assert k.timestamp != ''
        assert 'T' in k.timestamp


class TestFinancialReportModel:
    """FinancialReport 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        r = FinancialReport(symbol='600000', report_date='2026-06-30', report_type='资产负债表', data={'总资产': 5000000000000})
        assert r.report_date == '2026-06-30'
        assert r.data['总资产'] == 5000000000000


class TestDividendModel:
    """Dividend 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        d = Dividend(symbol='600000', report_date='2025年报', dividend_per_share=0.325, dividend_plan='10派3.25元', ex_dividend_date='2026-06-15', record_date='2026-06-14')
        assert d.dividend_per_share == 0.325
        assert d.dividend_plan == '10派3.25元'
        assert d.ex_dividend_date == '2026-06-15'


class TestNorthboundFlowModel:
    """NorthboundFlow 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        n = NorthboundFlow(date='2026-08-12', sh_net_inflow=15.5, sz_net_inflow=12.3, total_net_inflow=27.8)
        assert n.date == '2026-08-12'
        assert n.total_net_inflow == 27.8


class TestLHBRecordModel:
    """LHBRecord 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        l = LHBRecord(trade_date='2026-08-12', symbol='600000', name='浦发银行', explanation='日振幅达到15%', buy_amount=50000000, sell_amount=30000000, net_buy=20000000, buy_seat='机构专用', sell_seat='机构专用')
        assert l.symbol == '600000'
        assert l.buy_amount == 50000000
        assert l.net_buy == 20000000


class TestStockBasicModel:
    """StockBasic 数据模型测试"""

    def test_create_from_params(self):
        """通过参数创建"""
        b = StockBasic(symbol='600000', name='浦发银行', market='SH', industry='银行', list_date='1999-11-10')
        assert b.symbol == '600000'
        assert b.market == 'SH'
        assert b.industry == '银行'


# ============== 实时行情测试 ==============

class TestFetchRealtimeQuote:
    """实时行情获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_single_symbol(self, mock_ak):
        """获取单个股票行情"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        result, issues = fetch_realtime_quote(symbols=['600000'])
        assert len(result) == 1
        assert result[0].symbol == '600000'
        assert result[0].price == 10.70
        assert result[0].change_pct == 2.10

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_multiple_symbols(self, mock_ak):
        """获取多个股票行情"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        result, issues = fetch_realtime_quote(symbols=['600000', '000001'])
        assert len(result) == 2
        symbols = [r.symbol for r in result]
        assert '600000' in symbols
        assert '000001' in symbols

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_all_when_no_symbols(self, mock_ak):
        """不指定symbol时获取全部"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        result, issues = fetch_realtime_quote()
        assert len(result) == 2

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_empty_result(self, mock_ak):
        """无匹配股票时返回空列表"""
        df = pd.DataFrame(columns=['代码', '名称', '最新价'])
        mock_ak.stock_zh_a_spot_em.return_value = df
        result, issues = fetch_realtime_quote(symbols=['999999'])
        assert result == []
        assert issues == []

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_api_call_params(self, mock_ak):
        """验证API调用参数"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        fetch_realtime_quote(symbols=['600000'])
        mock_ak.stock_zh_a_spot_em.assert_called_once()

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_api_error_handling(self, mock_ak):
        """API调用异常处理"""
        mock_ak.stock_zh_a_spot_em.side_effect = Exception('网络错误')
        with pytest.raises(Exception):
            fetch_realtime_quote(symbols=['600000'])

    def test_no_akshare_raises_error(self):
        """akshare未安装时报错"""
        with patch('finance_toolkit.data_fetching.stock_fetcher.HAS_AKSHARE', False):
            with pytest.raises(SourceUnavailableError):
                fetch_realtime_quote(symbols=['600000'])

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_empty_dataframe_returns_empty(self, mock_ak):
        """空DataFrame处理"""
        df = pd.DataFrame()
        mock_ak.stock_zh_a_spot_em.return_value = df
        result, issues = fetch_realtime_quote()
        assert result == []
        assert isinstance(issues, list)


# ============== K线数据测试 ==============

class TestFetchKline:
    """K线数据获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_kline_daily(self, mock_ak):
        """获取日K线"""
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF
        result = fetch_kline(symbol='600000', period='daily', start_date='20260801', end_date='20260812')
        assert len(result) == 2
        assert result[0].date == '2026-08-12'
        assert result[0].close == 10.70

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_kline_weekly(self, mock_ak):
        """获取周K线"""
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF
        result = fetch_kline(symbol='600000', period='weekly', start_date='20260801', end_date='20260812')
        assert len(result) == 2

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_default_date_range(self, mock_ak):
        """默认日期范围"""
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF
        result = fetch_kline(symbol='600000')
        mock_ak.stock_zh_a_hist.assert_called_once()
        call_args = mock_ak.stock_zh_a_hist.call_args
        assert call_args.kwargs['symbol'] == '600000'
        assert call_args.kwargs['period'] == 'daily'
        assert call_args.kwargs['adjust'] == 'qfq'

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_api_error_kline(self, mock_ak):
        """K线API异常"""
        mock_ak.stock_zh_a_hist.side_effect = Exception('连接超时')
        with pytest.raises(Exception):
            fetch_kline(symbol='600000')


# ============== 财务数据测试 ==============

class TestFetchFinancial:
    """财务数据获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_balance_sheet(self, mock_ak):
        """获取资产负债表"""
        mock_ak.stock_financial_report_sina.return_value = MOCK_FINANCIAL_DF
        result = fetch_financial(symbol='600000', report_type='资产负债表')
        assert len(result) == 2
        assert result[0].report_date == '2026-06-30'
        assert result[0].data['总资产'] == 5000000000000

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_income_statement(self, mock_ak):
        """获取利润表"""
        mock_ak.stock_financial_report_sina.return_value = MOCK_FINANCIAL_DF
        result = fetch_financial(symbol='600000', report_type='利润表')
        assert len(result) == 2

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_api_error_financial(self, mock_ak):
        """财务API异常"""
        mock_ak.stock_financial_report_sina.side_effect = Exception('权限不足')
        with pytest.raises(Exception):
            fetch_financial(symbol='600000')


# ============== 分红数据测试 ==============

class TestFetchDividend:
    """分红数据获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_dividend(self, mock_ak):
        """获取分红数据"""
        mock_ak.stock_fhps_detail_em.return_value = MOCK_DIVIDEND_DF
        result = fetch_dividend(symbol='600000')
        assert len(result) == 1
        assert result[0].dividend_per_share == 0.325
        assert result[0].ex_dividend_date == '2026-06-15'

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_no_dividend(self, mock_ak):
        """无分红记录"""
        mock_ak.stock_fhps_detail_em.return_value = pd.DataFrame(columns=['报告期', '每股分红', '分红方案'])
        result = fetch_dividend(symbol='600000')
        assert result == []


# ============== 龙虎榜测试 ==============

class TestFetchLHB:
    """龙虎榜数据获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_lhb(self, mock_ak):
        """获取龙虎榜数据"""
        mock_ak.stock_lhb_detail_em.return_value = MOCK_LHB_DF
        result = fetch_lhb(start_date='20260812', end_date='20260812')
        assert len(result) == 1
        assert result[0].symbol == '600000'
        assert result[0].net_buy == 20000000

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_default_date_range_lhb(self, mock_ak):
        """龙虎榜默认日期范围"""
        mock_ak.stock_lhb_detail_em.return_value = MOCK_LHB_DF
        result = fetch_lhb()
        mock_ak.stock_lhb_detail_em.assert_called_once()
        call_args = mock_ak.stock_lhb_detail_em.call_args
        assert call_args.kwargs['end_date'] == datetime.now().strftime('%Y%m%d')


# ============== 北向资金测试 ==============

class TestFetchNorthbound:
    """北向资金数据获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_northbound(self, mock_ak):
        """获取北向资金数据"""
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value = MOCK_NORTHBOUND_DF
        result = fetch_northbound()
        assert len(result) == 2
        assert result[0].total_net_inflow == 27.8
        assert result[1].total_net_inflow == -2.1

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_empty_northbound(self, mock_ak):
        """空北向资金数据"""
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value = pd.DataFrame(columns=['日期', '沪股通净流入', '深股通净流入', '北向资金净流入'])
        result = fetch_northbound()
        assert result == []


# ============== 股票基本信息测试 ==============

class TestFetchStockBasic:
    """股票基本信息获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_basic(self, mock_ak):
        """获取股票基本信息"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_BASIC_DF
        result = fetch_stock_basic()
        assert len(result) == 2
        assert result[0].symbol == '600000'
        assert result[0].industry == '银行'

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_market_identification(self, mock_ak):
        """市场识别"""
        df = pd.DataFrame([
            {'代码': '600000', '名称': '浦发银行', '行业': '银行', '上市日期': '1999-11-10'},
            {'代码': '000001', '名称': '平安银行', '行业': '银行', '上市日期': '1991-04-03'},
        ])
        mock_ak.stock_zh_a_spot_em.return_value = df
        result = fetch_stock_basic()
        assert result[0].market == 'SH'
        assert result[1].market == 'SZ'


# ============== 批量获取测试 ==============

class TestFetchAllStockData:
    """批量获取测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_all(self, mock_ak):
        """获取多种数据类型"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF

        result, issues = fetch_all_stock_data(symbol='600000', data_types=['quote', 'kline'])
        assert 'quote' in result
        assert 'kline' in result
        assert len(result['quote']) == 1
        assert len(result['kline']) == 2

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_all_all_types(self, mock_ak):
        """获取所有数据类型"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF
        mock_ak.stock_financial_report_sina.return_value = MOCK_FINANCIAL_DF
        mock_ak.stock_fhps_detail_em.return_value = MOCK_DIVIDEND_DF
        mock_ak.stock_lhb_detail_em.return_value = MOCK_LHB_DF
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value = MOCK_NORTHBOUND_DF

        result, issues = fetch_all_stock_data(symbol='600000')
        expected_types = ['quote', 'kline', 'financial', 'dividend', 'lhb', 'northbound', 'basic']
        for t in expected_types:
            assert t in result, f"缺少数据类型: {t}"


# ============== 重试机制测试 ==============

class TestRetryMechanism:
    """重试机制测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_retry_on_failure(self, mock_ak):
        """失败后重试成功"""
        mock_ak.stock_zh_a_spot_em.side_effect = [
            Exception('第一次失败'),
            Exception('第二次失败'),
            MOCK_QUOTE_DF,
        ]
        result, issues = fetch_realtime_quote(symbols=['600000'])
        assert len(result) == 1
        assert mock_ak.stock_zh_a_spot_em.call_count == 3

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_max_retries_exceeded(self, mock_ak):
        """超过最大重试次数"""
        mock_ak.stock_zh_a_spot_em.side_effect = Exception('持续失败')
        with pytest.raises(Exception):
            fetch_realtime_quote(symbols=['600000'])
        # 首次 + 3次重试 = 4次调用
        assert mock_ak.stock_zh_a_spot_em.call_count == 4


# ============== 端到端集成测试 ==============

class TestEndToEnd:
    """端到端集成测试"""

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_full_pipeline(self, mock_ak):
        """完整数据流程"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF

        # 1. 获取实时行情
        quotes, _ = fetch_realtime_quote(symbols=['600000'])
        assert len(quotes) == 1
        assert quotes[0].price == 10.70

        # 2. 获取K线
        klines = fetch_kline(symbol='600000', period='daily', start_date='20260801', end_date='20260812')
        assert len(klines) == 2
        assert klines[0].close == 10.70

        # 3. 验证数据一致性
        assert quotes[0].symbol == klines[0].symbol

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_data_integrity(self, mock_ak):
        """数据完整性验证"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        result, _ = fetch_realtime_quote()
        assert len(result) == 2
        for q in result:
            assert hasattr(q, 'symbol')
            assert hasattr(q, 'price')
            assert hasattr(q, 'volume')
            assert q.symbol != ''
            assert q.price >= 0

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_quality_issues_returned(self, mock_ak):
        """质量issues正确返回"""
        mock_ak.stock_zh_a_spot_em.return_value = MOCK_QUOTE_DF
        result, issues = fetch_realtime_quote(symbols=['600000'])
        assert len(result) == 1
        assert isinstance(issues, list)

    @patch('finance_toolkit.data_fetching.stock_fetcher.ak')
    def test_fetch_kline_with_validation(self, mock_ak):
        """K线带验证测试"""
        mock_ak.stock_zh_a_hist.return_value = MOCK_KLINE_DF
        klines = fetch_kline(symbol='600000', period='daily', start_date='20260801', end_date='20260812', validate=True)
        assert len(klines) == 2
        assert klines[0].close == 10.70


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
