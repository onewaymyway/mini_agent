# -*- coding: utf-8 -*-
"""
债券数据抓取模块

支持:
- 国债收益率曲线 (bond_china_yield)
- 可转债实时行情 (bond_zh_hs_cov_spot)
- 可转债历史数据 (bond_zh_hs_cov_daily)
- 企业债行情 (bond_zh_hs_daily)
- 债券基本信息
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

logger = logging.getLogger(__name__)


# ============== 数据模型 ==============

@dataclass
class BondYield:
    """国债收益率"""
    date: str
    yield_1y: float  # 1年期收益率
    yield_2y: float  # 2年期收益率
    yield_3y: float  # 3年期收益率
    yield_5y: float  # 5年期收益率
    yield_10y: float  # 10年期收益率
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ConvertibleBond:
    """可转债"""
    bond_code: str
    bond_name: str
    stock_code: str
    stock_name: str
    price: float  # 现价
    prev_close: float  # 昨收
    change_pct: float  # 涨跌幅
    premium: float  # 溢价率
    conversion_price: float  # 转股价
    maturity_date: str  # 到期日期
    volume: int  # 成交量
    amount: float  # 成交额
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ConvertibleBondKline:
    """可转债历史K线"""
    bond_code: str
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: int
    amount: float
    change_pct: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class CorporateBond:
    """企业债"""
    bond_code: str
    bond_name: str
    price: float
    yield_rate: float  # 到期收益率
    change: float
    change_pct: float
    volume: int
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ============== 国债收益率数据抓取 ==============

def fetch_bond_yield() -> List[BondYield]:
    """
    获取国债收益率曲线
    
    Returns:
        BondYield 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装，请执行: pip install akshare")
    
    try:
        df = ak.bond_china_yield()
        
        results = []
        for _, row in df.iterrows():
            try:
                results.append(BondYield(
                    date=str(row.get('日期', '')),
                    yield_1y=float(row.get('1年', 0) or 0),
                    yield_2y=float(row.get('2年', 0) or 0),
                    yield_3y=float(row.get('3年', 0) or 0),
                    yield_5y=float(row.get('5年', 0) or 0),
                    yield_10y=float(row.get('10年', 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"国债收益率获取失败: {e}")
        return []


# ============== 可转债数据抓取 ==============

def fetch_convertible_bond_spot() -> List[ConvertibleBond]:
    """
    获取可转债实时行情
    
    Returns:
        ConvertibleBond 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        df = ak.bond_zh_hs_cov_spot()
        
        results = []
        for _, row in df.iterrows():
            try:
                price = float(row.get('现价', 0) or 0)
                prev_close = float(row.get('昨收', 0) or 0)
                change_pct = float(row.get('涨跌幅', 0) or 0)
                premium = float(row.get('溢价率', 0) or 0)
                
                results.append(ConvertibleBond(
                    bond_code=str(row.get('代码', '')),
                    bond_name=str(row.get('名称', '')),
                    stock_code=str(row.get('股票代码', '')),
                    stock_name=str(row.get('股票名称', '')),
                    price=price,
                    prev_close=prev_close,
                    change_pct=change_pct,
                    premium=premium,
                    conversion_price=float(row.get('转股价', 0) or 0),
                    maturity_date=str(row.get('到期日', '')),
                    volume=int(row.get('成交量', 0) or 0),
                    amount=float(row.get('成交额', 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"可转债行情获取失败: {e}")
        return []


def fetch_convertible_bond_history(
    symbol: str,
    start_date: str = "20240101",
    end_date: Optional[str] = None
) -> List[ConvertibleBondKline]:
    """
    获取可转债历史K线
    
    Args:
        symbol: 可转债代码，如 "127045"
        start_date: 开始日期，格式 "YYYYMMDD"
        end_date: 结束日期，格式 "YYYYMMDD"
    
    Returns:
        ConvertibleBondKline 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        
        df = ak.bond_zh_hs_cov_daily(symbol=symbol)
        
        results = []
        for _, row in df.iterrows():
            try:
                close = float(row.get('收盘', 0) or 0)
                prev_close = float(row.get('昨收', 0) or 0)
                change_pct = 0.0
                if prev_close > 0:
                    change_pct = (close - prev_close) / prev_close * 100
                
                results.append(ConvertibleBondKline(
                    bond_code=symbol,
                    date=str(row.get('日期', '')),
                    open=float(row.get('开盘', 0) or 0),
                    close=close,
                    high=float(row.get('最高', 0) or 0),
                    low=float(row.get('最低', 0) or 0),
                    volume=int(row.get('成交量', 0) or 0),
                    amount=float(row.get('成交额', 0) or 0),
                    change_pct=change_pct,
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"可转债历史数据获取失败: {e}")
        return []


# ============== 企业债数据抓取 ==============

def fetch_corporate_bond_spot() -> List[CorporateBond]:
    """
    获取企业债实时行情
    
    Returns:
        CorporateBond 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        df = ak.bond_zh_hs_daily()
        
        results = []
        for _, row in df.iterrows():
            try:
                results.append(CorporateBond(
                    bond_code=str(row.get('代码', '')),
                    bond_name=str(row.get('名称', '')),
                    price=float(row.get('收盘价', 0) or 0),
                    yield_rate=float(row.get('到期收益率', 0) or 0),
                    change=float(row.get('涨跌', 0) or 0),
                    change_pct=float(row.get('涨跌幅', 0) or 0),
                    volume=int(row.get('成交量', 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"企业债行情获取失败: {e}")
        return []


# ============== 便捷入口 ==============

class BondDataFetcher:
    """债券数据抓取器"""
    
    def get_bond_yield(self) -> List[BondYield]:
        return fetch_bond_yield()
    
    def get_convertible_bond_spot(self) -> List[ConvertibleBond]:
        return fetch_convertible_bond_spot()
    
    def get_convertible_bond_history(
        self,
        symbol: str,
        start_date: str = "20240101",
        end_date: Optional[str] = None
    ) -> List[ConvertibleBondKline]:
        return fetch_convertible_bond_history(symbol, start_date, end_date)
    
    def get_corporate_bond_spot(self) -> List[CorporateBond]:
        return fetch_corporate_bond_spot()
    
    def get_bond_summary(self, bond_code: str) -> Dict[str, Any]:
        """获取债券综合信息"""
        return {
            'spot': self.get_convertible_bond_spot(),
            'history': self.get_convertible_bond_history(bond_code),
        }


fetcher = BondDataFetcher()


__all__ = [
    # 数据模型
    "BondYield", "ConvertibleBond", "ConvertibleBondKline", "CorporateBond",
    # 抓取函数
    "fetch_bond_yield", "fetch_convertible_bond_spot",
    "fetch_convertible_bond_history", "fetch_corporate_bond_spot",
    # 便捷类
    "BondDataFetcher", "fetcher",
]
