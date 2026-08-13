# -*- coding: utf-8 -*-
"""
宏观经济数据抓取器
提供GDP、CPI、PMI、利率、汇率等宏观经济指标
"""

import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

from ..core import FinanceData

logger = logging.getLogger(__name__)


def fetch_gdp_data() -> List[FinanceData]:
    """获取GDP数据
    
    Returns:
        List[FinanceData]: GDP数据列表
    """
    results = []
    
    if HAS_AKSHARE:
        try:
            df = ak.macro_china_gdp()
            for _, row in df.iterrows():
                payload = {
                    'quarter': row.get('季度', ''),
                    'gdp': float(row.get('国内生产总值-绝对值', 0) or 0),
                    'yoy': float(row.get('国内生产总值-同比增长', 0) or 0),
                    'first_industry': float(row.get('第一产业-绝对值', 0) or 0),
                    'first_industry_yoy': float(row.get('第一产业-同比增长', 0) or 0),
                    'second_industry': float(row.get('第二产业-绝对值', 0) or 0),
                    'second_industry_yoy': float(row.get('第二产业-同比增长', 0) or 0),
                    'third_industry': float(row.get('第三产业-绝对值', 0) or 0),
                    'third_industry_yoy': float(row.get('第三产业-同比增长', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_gdp',
                    symbol='CN_GDP',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload
                ))
        except Exception as e:
            logger.error(f"GDP数据获取失败: {e}")
    
    return results


def fetch_cpi_data() -> List[FinanceData]:
    """获取CPI数据
    
    Returns:
        List[FinanceData]: CPI数据列表
    """
    results = []
    
    if HAS_AKSHARE:
        try:
            df = ak.macro_china_cpi()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('月份', ''),
                    'cpi': float(row.get('全国-当月', 0) or 0),
                    'cpi_yoy': float(row.get('全国-同比增长', 0) or 0),
                    'cpi_mom': float(row.get('全国-环比增长', 0) or 0),
                    'cpi_cum': float(row.get('全国-累计', 0) or 0),
                    'urban_cpi': float(row.get('城市-当月', 0) or 0),
                    'rural_cpi': float(row.get('农村-当月', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_cpi',
                    symbol='CN_CPI',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload
                ))
        except Exception as e:
            logger.error(f"CPI数据获取失败: {e}")
    
    return results


def fetch_pmi_data() -> List[FinanceData]:
    """获取PMI数据
    
    Returns:
        List[FinanceData]: PMI数据列表
    """
    results = []
    
    if HAS_AKSHARE:
        try:
            df = ak.macro_china_pmi()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('月份', ''),
                    'manufacturing_pmi': float(row.get('制造业PMI', 0) or 0),
                    'non_manufacturing_pmi': float(row.get('非制造业PMI', 0) or 0),
                    'service_pmi': float(row.get('服务业PMI', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_pmi',
                    symbol='CN_PMI',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload
                ))
        except Exception as e:
            logger.error(f"PMI数据获取失败: {e}")
    
    return results


def fetch_interest_rate_data() -> List[FinanceData]:
    """获取利率数据
    
    Returns:
        List[FinanceData]: 利率数据列表
    """
    results = []
    
    if HAS_AKSHARE:
        try:
            df = ak.macro_china_lpr()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('公布日期', ''),
                    'lpr_1y': float(row.get('1年', 0) or 0),
                    'lpr_5y': float(row.get('5年', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_interest_rate',
                    symbol='CN_LPR',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload
                ))
        except Exception as e:
            logger.error(f"利率数据获取失败: {e}")
    
    return results


def fetch_money_supply_data() -> List[FinanceData]:
    """获取货币供应量数据
    
    Returns:
        List[FinanceData]: 货币供应量数据列表
    """
    results = []
    
    if HAS_AKSHARE:
        try:
            df = ak.macro_china_money_supply()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('月份', ''),
                    'm0': float(row.get('M0', 0) or 0),
                    'm0_yoy': float(row.get('M0同比', 0) or 0),
                    'm1': float(row.get('M1', 0) or 0),
                    'm1_yoy': float(row.get('M1同比', 0) or 0),
                    'm2': float(row.get('M2', 0) or 0),
                    'm2_yoy': float(row.get('M2同比', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_money_supply',
                    symbol='CN_MONEY',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload
                ))
        except Exception as e:
            logger.error(f"货币供应量数据获取失败: {e}")
    
    return results


def fetch_exchange_rate_data() -> List[FinanceData]:
    """获取汇率数据
    
    Returns:
        List[FinanceData]: 汇率数据列表
    """
    results = []
    
    if HAS_AKSHARE:
        try:
            df = ak.currency_boc_safe()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('日期', ''),
                    'currency': row.get('货币名称', ''),
                    'center_price': float(row.get('人民币汇率中间价', 0) or 0),
                    'buy_price': float(row.get('现汇买入价', 0) or 0),
                    'sell_price': float(row.get('现钞买入价', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_exchange_rate',
                    symbol=payload['currency'],
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload
                ))
        except Exception as e:
            logger.error(f"汇率数据获取失败: {e}")
    
    return results


def fetch_unemployment_data() -> List[FinanceData]:
    """获取失业率数据

    Returns:
        List[FinanceData]: 失业率数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = ak.macro_china_unemployment()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('月份', ''),
                    'urban_unemployment': float(row.get('城镇调查失业率', 0) or 0),
                    'urban_sampled': float(row.get('城镇调查失业率-样本调查', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_unemployment',
                    symbol='CN_UNEMPLOYMENT',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload,
                ))
        except Exception as e:
            logger.error(f"失业率数据获取失败: {e}")

    return results


def fetch_trade_data() -> List[FinanceData]:
    """获取贸易收支数据

    Returns:
        List[FinanceData]: 贸易收支数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = ak.macro_china_trade_balance()
            for _, row in df.iterrows():
                payload = {
                    'date': row.get('月份', ''),
                    'export': float(row.get('出口', 0) or 0),
                    'import': float(row.get('进口', 0) or 0),
                    'balance': float(row.get('贸易差额', 0) or 0),
                    'export_yoy': float(row.get('出口同比', 0) or 0),
                    'import_yoy': float(row.get('进口同比', 0) or 0),
                    'balance_yoy': float(row.get('贸易差额同比', 0) or 0),
                }
                results.append(FinanceData(
                    source='akshare',
                    data_type='macro_trade',
                    symbol='CN_TRADE',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=payload,
                ))
        except Exception as e:
            logger.error(f"贸易收支数据获取失败: {e}")

    return results


def fetch_all_macro_data() -> Dict[str, List[FinanceData]]:
    """获取所有宏观经济数据

    Returns:
        Dict: 包含所有宏观经济数据类型的字典
    """
    return {
        'gdp': fetch_gdp_data(),
        'cpi': fetch_cpi_data(),
        'pmi': fetch_pmi_data(),
        'interest_rate': fetch_interest_rate_data(),
        'money_supply': fetch_money_supply_data(),
        'exchange_rate': fetch_exchange_rate_data(),
        'unemployment': fetch_unemployment_data(),
        'trade': fetch_trade_data(),
    }


class MacroFetcher:
    """宏观经济数据获取器"""
    
    def get_gdp(self) -> List[FinanceData]:
        """获取GDP数据"""
        return fetch_gdp_data()
    
    def get_cpi(self) -> List[FinanceData]:
        """获取CPI数据"""
        return fetch_cpi_data()
    
    def get_pmi(self) -> List[FinanceData]:
        """获取PMI数据"""
        return fetch_pmi_data()
    
    def get_interest_rate(self) -> List[FinanceData]:
        """获取利率数据"""
        return fetch_interest_rate_data()
    
    def get_money_supply(self) -> List[FinanceData]:
        """获取货币供应量数据"""
        return fetch_money_supply_data()
    
    def get_exchange_rate(self) -> List[FinanceData]:
        """获取汇率数据"""
        return fetch_exchange_rate_data()

    def get_unemployment(self) -> List[FinanceData]:
        """获取失业率数据"""
        return fetch_unemployment_data()

    def get_trade(self) -> List[FinanceData]:
        """获取贸易收支数据"""
        return fetch_trade_data()

    def get_all(self) -> Dict[str, List[FinanceData]]:
        """获取所有宏观数据"""
        return fetch_all_macro_data()


# 便捷实例
macro_fetcher = MacroFetcher()


if __name__ == '__main__':
    logger.info("测试宏观经济数据抓取...")
    
    logger.info("\n1. GDP数据...")
    gdp = fetch_gdp_data()
    for g in gdp[:3]:
        logger.info(f"{g.payload.get('quarter')}: GDP={g.payload.get('gdp')}, 同比={g.payload.get('yoy')}%")
    
    logger.info("\n2. CPI数据...")
    cpi = fetch_cpi_data()
    for c in cpi[:3]:
        logger.info(f"{c.payload.get('date')}: CPI={c.payload.get('cpi')}, 同比={c.payload.get('cpi_yoy')}%")
    
    logger.info("\n3. PMI数据...")
    pmi = fetch_pmi_data()
    for p in pmi[:3]:
        logger.info(f"{p.payload.get('date')}: 制造业PMI={p.payload.get('manufacturing_pmi')}")
    
    logger.info("\n4. 利率数据...")
    rate = fetch_interest_rate_data()
    for r in rate[:3]:
        logger.info(f"{r.payload.get('date')}: 1年LPR={r.payload.get('lpr_1y')}%, 5年LPR={r.payload.get('lpr_5y')}%")
    
    logger.info("\n5. 货币供应量...")
    money = fetch_money_supply_data()
    for m in money[:3]:
        logger.info(f"{m.payload.get('date')}: M2={m.payload.get('m2')}, 同比={m.payload.get('m2_yoy')}%")
    
    logger.info("\n6. 汇率数据...")
    fx = fetch_exchange_rate_data()
    for f in fx[:5]:
        logger.info(f"{f.payload.get('currency')}: 中间价={f.payload.get('center_price')}")
