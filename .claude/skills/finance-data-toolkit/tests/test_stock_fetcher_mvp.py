# -*- coding: utf-8 -*-
"""
A股数据抓取模块 MVP 单元测试 (v2)

测试范围:
- fetch_financial: 财务数据 (稳定)
- fetch_dividend: 分红数据 (稳定)
- fetch_lhb: 龙虎榜 (稳定)
- fetch_northbound: 北向资金 (稳定)
- StockDataFetcher: 便捷类

注意: K线接口依赖东方财富API，需要代理支持，当前环境代理不可用
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# 添加 skill 目录到路径
SKILL_DIR = Path(r"E:\codes\mini_claude_code\.claude\skills\finance-data-toolkit")
sys.path.insert(0, str(SKILL_DIR))

from finance_toolkit.data_fetching.stock_fetcher_mvp import (
    StockKline,
    FinancialReport,
    Dividend,
    LHBRecord,
    NorthboundFlow,
    StockDataFetcher,
    fetch_kline,
    fetch_financial,
    fetch_dividend,
    fetch_lhb,
    fetch_northbound,
)


class TestStockKline(unittest.TestCase):
    """测试 StockKline 数据模型"""
    
    def test_create_kline(self):
        """测试创建 Kline 对象"""
        kline = StockKline(
            symbol="000001",
            date="2026-08-08",
            open=11.00,
            close=11.50,
            high=11.60,
            low=10.90,
            volume=1000000,
            amount=11500000.0,
            amplitude=6.35,
            change_pct=4.55,
            change_amount=0.50,
            turnover_rate=0.10,
        )
        self.assertEqual(kline.symbol, "000001")
        self.assertEqual(kline.close, 11.50)
        self.assertEqual(kline.source, "akshare")
        self.assertIsNotNone(kline.timestamp)
    
    def test_kline_to_dict(self):
        """测试 Kline 转字典"""
        kline = StockKline(
            symbol="000001",
            date="2026-08-08",
            open=11.00,
            close=11.50,
            high=11.60,
            low=10.90,
            volume=1000000,
            amount=11500000.0,
            amplitude=6.35,
            change_pct=4.55,
            change_amount=0.50,
            turnover_rate=0.10,
        )
        d = kline.__dict__
        self.assertIn("symbol", d)
        self.assertIn("date", d)
        self.assertIn("close", d)


class TestFetchFinancial(unittest.TestCase):
    """测试 fetch_financial 函数"""
    
    def test_fetch_balance_sheet(self):
        """测试获取资产负债表"""
        reports = fetch_financial("000001", report_type="资产负债表")
        
        self.assertIsInstance(reports, list)
        self.assertGreater(len(reports), 0, "应返回至少一条财务记录")
        
        first = reports[0]
        self.assertEqual(first.symbol, "000001")
        self.assertEqual(first.report_type, "资产负债表")
        self.assertIsInstance(first.data, dict)
        self.assertGreater(len(first.data), 0)
    
    def test_fetch_income_statement(self):
        """测试获取利润表"""
        reports = fetch_financial("000001", report_type="利润表")
        
        self.assertIsInstance(reports, list)
        self.assertGreater(len(reports), 0)
        
        first = reports[0]
        self.assertEqual(first.report_type, "利润表")
    
    def test_fetch_cash_flow(self):
        """测试获取现金流量表"""
        reports = fetch_financial("000001", report_type="现金流量表")
        
        self.assertIsInstance(reports, list)
        self.assertGreater(len(reports), 0)


class TestFetchDividend(unittest.TestCase):
    """测试 fetch_dividend 函数"""
    
    def test_fetch_dividend_pingan(self):
        """测试获取平安银行分红数据"""
        dividends = fetch_dividend("000001")
        
        self.assertIsInstance(dividends, list)
        # 分红数据可能为空（该股票可能无分红记录）
        # 如果不为空，验证数据结构
        if len(dividends) > 0:
            first = dividends[0]
            self.assertEqual(first.symbol, "000001")
            self.assertIsInstance(first.dividend_plan, str)
    
    def test_fetch_dividend_maotai(self):
        """测试获取贵州茅台分红数据"""
        dividends = fetch_dividend("600519")
        
        self.assertIsInstance(dividends, list)
        # 茅台可能有分红记录
        if len(dividends) > 0:
            first = dividends[0]
            self.assertIsInstance(first.dividend_per_share, float)


class TestFetchLHB(unittest.TestCase):
    """测试 fetch_lhb 函数"""
    
    def test_fetch_lhb_recent(self):
        """测试获取近期龙虎榜数据"""
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        
        records = fetch_lhb(start_date=start_date, end_date=end_date)
        
        self.assertIsInstance(records, list)
        # 龙虎榜数据可能为空（周末或无数据）
        if len(records) > 0:
            first = records[0]
            self.assertIsInstance(first.symbol, str)
            self.assertIsInstance(first.name, str)


class TestFetchNorthbound(unittest.TestCase):
    """测试 fetch_northbound 函数"""
    
    def test_fetch_northbound(self):
        """测试获取北向资金数据"""
        flows = fetch_northbound()
        
        self.assertIsInstance(flows, list)
        self.assertGreater(len(flows), 0, "应返回北向资金数据")
        
        first = flows[0]
        self.assertIsInstance(first.date, str)
        self.assertIsInstance(first.total_net_inflow, float)


class TestStockDataFetcher(unittest.TestCase):
    """测试 StockDataFetcher 类"""
    
    def setUp(self):
        self.fetcher = StockDataFetcher()
    
    def test_get_financial(self):
        """测试 get_financial 方法"""
        reports = self.fetcher.get_financial("000001")
        self.assertIsInstance(reports, list)
        self.assertGreater(len(reports), 0)
    
    def test_get_dividend(self):
        """测试 get_dividend 方法"""
        dividends = self.fetcher.get_dividend("000001")
        self.assertIsInstance(dividends, list)
    
    def test_get_northbound(self):
        """测试 get_northbound 方法"""
        flows = self.fetcher.get_northbound()
        self.assertIsInstance(flows, list)
        self.assertGreater(len(flows), 0)


class TestDataIntegrity(unittest.TestCase):
    """测试数据完整性"""
    
    def test_financial_data_structure(self):
        """测试财务数据结构"""
        reports = fetch_financial("000001")
        
        if len(reports) > 0:
            first = reports[0]
            # 验证有数据
            self.assertTrue(len(first.data) > 0)


class TestKlineProxyIssue(unittest.TestCase):
    """测试K线接口的代理问题（预期失败）"""
    
    def test_kline_requires_proxy(self):
        """测试K线接口需要代理"""
        try:
            klines = fetch_kline("000001", start_date="20260701", end_date="20260809")
            # 如果成功，说明代理可用
            self.assertGreater(len(klines), 0)
        except Exception as e:
            # 预期失败：代理问题
            self.assertIn("ProxyError", str(type(e).__name__))


if __name__ == "__main__":
    # 运行测试
    unittest.main(verbosity=2)
