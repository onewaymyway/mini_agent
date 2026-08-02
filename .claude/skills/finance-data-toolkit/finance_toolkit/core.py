# -*- coding: utf-8 -*-
"""
核心基础设施：数据契约、抓取器基类、工厂函数
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncIterator
from pathlib import Path
import importlib
import pkgutil


@dataclass
class FinanceData:
    """统一金融数据契约 - 所有模块输出标准化为此格式"""
    source: str                    # 数据源标识: akshare/tushare/eastmoney/sina/lexicon
    data_type: str                 # 数据类型: quote/kline/financial/news/guba/sentiment/report
    symbol: str                    # 标的代码: 000001.SZ / 600000.SH / BTC-USDT
    timestamp: str                 # 数据时间戳 (ISO 8601, UTC)
    payload: Dict[str, Any]        # 业务载荷 (见各模块 schema)
    raw: Optional[Dict] = None     # 原始响应 (调试用，生产可关闭)
    meta: Optional[Dict] = None    # 元信息: 请求耗时、重试次数、代理IP、版本号等

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class BaseScraper(ABC):
    """统一抓取器基类 - 所有抓取器实现此协议"""
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称"""
        pass
    
    @property
    @abstractmethod
    def supported_types(self) -> List[str]:
        """支持的数据类型列表"""
        pass
    
    @abstractmethod
    async def fetch(self, 
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[FinanceData]:
        """异步获取数据流"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()
    
    @abstractmethod
    async def close(self):
        """关闭连接/释放资源"""
        pass


# ============== 抓取器注册表 ==============

_SCRAPER_REGISTRY = {}


def register_scraper(name_or_class, scraper_class=None):
    """注册抓取器 - 支持两种用法:
    1. @register_scraper (装饰器，自动从模块名推断)
    2. register_scraper('name', ScraperClass) (显式调用)
    """
    if scraper_class is None:
        # 作为装饰器使用: @register_scraper
        cls = name_or_class
        # 从类名推断名称 (去掉 Scraper 后缀，转小写)
        name = cls.__name__.replace('Scraper', '').lower()
        _SCRAPER_REGISTRY[name] = cls
        return cls
    else:
        # 显式调用: register_scraper('name', ScraperClass)
        _SCRAPER_REGISTRY[name_or_class] = scraper_class


def create_scraper(name: str, **kwargs) -> BaseScraper:
    """创建抓取器实例
    
    支持的抓取器:
    - 'akshare': AKShare 官方数据 (需 AKSHARE_TOKEN)
    - 'tushare': Tushare Pro (需 TUSHARE_TOKEN)
    - 'eastmoney': 东方财富网页/CDP (免费，有反爬)
    - 'sina': 新浪财经 API (免费)
    - 'lexicon': 词典法舆情分析 (本地)
    """
    if name not in _SCRAPER_REGISTRY:
        # 尝试动态导入
        try:
            module = importlib.import_module(f'finance_toolkit.scrapers.{name}')
            scraper_class = getattr(module, f'{name.capitalize()}Scraper')
            _SCRAPER_REGISTRY[name] = scraper_class
        except (ImportError, AttributeError):
            available = ', '.join(_SCRAPER_REGISTRY.keys()) or 'none registered'
            raise ValueError(f"Unknown scraper: {name}. Available: {available}")
    
    return _SCRAPER_REGISTRY[name](**kwargs)


def list_scrapers() -> Dict[str, Dict]:
    """列出所有可用抓取器及其支持的数据类型"""
    result = {}
    for name, cls in _SCRAPER_REGISTRY.items():
        try:
            instance = cls()
            result[name] = {
                'source_name': instance.source_name,
                'supported_types': instance.supported_types,
            }
        except Exception:
            result[name] = {'error': 'Failed to instantiate'}
    return result


# 自动发现并注册 scrapers 目录下的抓取器
_scrapers_path = Path(__file__).parent / 'scrapers'
if _scrapers_path.exists():
    for _, module_name, _ in pkgutil.iter_modules([str(_scrapers_path)]):
        try:
            module = importlib.import_module(f'finance_toolkit.scrapers.{module_name}')
            # 查找以 Scraper 结尾的类
            for attr_name in dir(module):
                if attr_name.endswith('Scraper'):
                    cls = getattr(module, attr_name)
                    if isinstance(cls, type) and issubclass(cls, BaseScraper) and cls != BaseScraper:
                        # 从类名推断名称 (去掉 Scraper 后缀，转小写)
                        name = cls.__name__.replace('Scraper', '').lower()
                        # 只有未注册时才注册，避免重复
                        if name not in _SCRAPER_REGISTRY:
                            register_scraper(name, cls)
        except Exception:
            pass  # 忽略导入错误
