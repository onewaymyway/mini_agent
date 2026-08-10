# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 统一数据抓取接口

提供统一的数据抓取接口，整合多源数据，自动降级和重试。

使用示例：
    from finance_toolkit.unified_fetcher import UnifiedFetcher
    
    fetcher = UnifiedFetcher()
    
    # 获取实时行情
    data = fetcher.fetch_realtime_quote(['600000.SH', '000001.SZ'])
    
    # 获取K线数据
    kline = fetcher.fetch_kline('600000.SH', period='daily', count=100)
    
    # 获取财务数据
    finance = fetcher.fetch_financial_data('600000.SH')
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .exceptions import (
    SourceUnavailableError,
    DataNotFoundError,
    DataEmptyError,
    FallbackError,
    CircuitBreakerError,
)
from .resilience import (
    CircuitBreaker,
    FallbackManager,
    RateLimiter,
    HealthChecker,
    retry_with_backoff,
    DEFAULT_CIRCUIT_BREAKERS,
)

logger = logging.getLogger(__name__)


class DataType(Enum):
    """数据类型枚举"""
    REALTIME_QUOTE = "realtime_quote"      # 实时行情
    KLINE = "kline"                        # K线数据
    FINANCIAL = "financial"                # 财务数据
    INDEX = "index"                        # 指数数据
    NEWS = "news"                          # 新闻资讯
    SENTIMENT = "sentiment"               # 情绪数据
    SOCIAL = "social"                     # 社交数据
    MACRO = "macro"                       # 宏观经济
    FOREX = "forex"                       # 外汇数据
    CRYPTO = "crypto"                     # 加密货币
    CAPITAL_FLOW = "capital_flow"          # 资金流向
    MARGIN = "margin"                      # 融资融券
    NORTHBOUND = "northbound"              # 北向资金


@dataclass
class FetchConfig:
    """抓取配置"""
    # 数据源优先级
    source_priority: List[str] = field(default_factory=lambda: ["akshare", "eastmoney", "sina"])
    
    # 超时设置
    timeout: float = 30.0
    
    # 重试设置
    max_retries: int = 3
    retry_delay: float = 1.0
    
    # 限流设置
    rate_limit_calls: int = 10
    rate_limit_period: int = 60
    
    # 熔断设置
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 60
    
    # 健康检查
    health_check_interval: int = 300
    
    # 数据验证
    validate_data: bool = True
    min_data_points: int = 1


class UnifiedFetcher:
    """
    统一数据抓取器
    
    整合多源数据抓取，提供统一的接口和自动降级能力。
    """
    
    def __init__(self, config: Optional[FetchConfig] = None):
        self.config = config or FetchConfig()
        
        # 初始化熔断器
        self.circuit_breakers: Dict[str, CircuitBreaker] = {
            source: CircuitBreaker(
                source,
                failure_threshold=self.config.circuit_breaker_threshold,
                reset_timeout=self.config.circuit_breaker_timeout
            )
            for source in self.config.source_priority
        }
        
        # 初始化限流器
        self.rate_limiter = RateLimiter(
            max_calls=self.config.rate_limit_calls,
            period=self.config.rate_limit_period
        )
        
        # 初始化健康检查器
        self.health_checker = HealthChecker(
            sources=self._build_health_check_sources(),
            check_interval=self.config.health_check_interval
        )
        
        # 注册数据源
        self._data_sources: Dict[str, Dict[str, Callable]] = {}
        self._register_builtin_sources()
    
    def _build_health_check_sources(self) -> List[Dict[str, Any]]:
        """构建健康检查源配置"""
        sources = []
        for source in self.config.source_priority:
            sources.append({
                "name": source,
                "check_func": self._create_health_check_func(source),
                "timeout": self.config.timeout
            })
        return sources
    
    def _create_health_check_func(self, source: str) -> Callable:
        """创建健康检查函数"""
        async def check_func():
            if source in self._data_sources:
                # 尝试获取一个测试数据
                test_funcs = self._data_sources[source]
                if "realtime_quote" in test_funcs:
                    await self.rate_limiter.acquire()
                    # 使用第一个可用的函数进行测试
                    for func in test_funcs.values():
                        try:
                            result = await func(["000001.SZ"])
                            if not result:
                                raise SourceUnavailableError(source, "返回数据为空")
                            return
                        except Exception:
                            continue
            raise SourceUnavailableError(source, "健康检查失败")
        return check_func
    
    def _register_builtin_sources(self):
        """注册内置数据源"""
        # AKShare 数据源
        self._data_sources["akshare"] = {
            "realtime_quote": self._fetch_akshare_realtime_quote,
            "kline": self._fetch_akshare_kline,
            "financial": self._fetch_akshare_financial,
            "index": self._fetch_akshare_index,
        }
        
        # 东方财富数据源
        self._data_sources["eastmoney"] = {
            "realtime_quote": self._fetch_eastmoney_realtime_quote,
            "kline": self._fetch_eastmoney_kline,
            "financial": self._fetch_eastmoney_financial,
            "news": self._fetch_eastmoney_news,
        }
        
        # 新浪财经数据源
        self._data_sources["sina"] = {
            "realtime_quote": self._fetch_sina_realtime_quote,
            "kline": self._fetch_sina_kline,
        }
    
    # ==================== 实时行情 ====================
    
    async def fetch_realtime_quote(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        获取实时行情
        
        参数：
            symbols: 股票代码列表，如 ['600000.SH', '000001.SZ']
        
        返回：
            实时行情数据列表
        """
        return await self._fetch_with_fallback(
            DataType.REALTIME_QUOTE,
            self._build_fallback_sources("realtime_quote"),
            symbols
        )
    
    async def _fetch_akshare_realtime_quote(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """从 AKShare 获取实时行情"""
        try:
            import akshare as ak
            # 尝试不同的接口
            for func_name in ["stock_zh_a_spot_em", "stock_zh_a_spot"]:
                try:
                    df = getattr(ak, func_name)()
                    # 过滤需要的股票
                    result = []
                    for symbol in symbols:
                        code = symbol.split(".")[0]
                        row = df[df["代码"] == code]
                        if not row.empty:
                            result.append({
                                "symbol": symbol,
                                "name": row.iloc[0].get("名称", ""),
                                "price": float(row.iloc[0].get("最新价", 0)),
                                "change": float(row.iloc[0].get("涨跌额", 0)),
                                "change_pct": float(row.iloc[0].get("涨跌幅", 0)),
                                "volume": int(row.iloc[0].get("成交量", 0)),
                                "amount": float(row.iloc[0].get("成交额", 0)),
                                "source": "akshare"
                            })
                    return result
                except Exception:
                    continue
            raise SourceUnavailableError("akshare", "实时行情接口不可用")
        except ImportError:
            raise SourceUnavailableError("akshare", "akshare 未安装")
    
    async def _fetch_eastmoney_realtime_quote(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """从东方财富获取实时行情"""
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            result = []
            for symbol in symbols:
                code = symbol.split(".")[0]
                row = df[df["代码"] == code]
                if not row.empty:
                    result.append({
                        "symbol": symbol,
                        "name": row.iloc[0].get("名称", ""),
                        "price": float(row.iloc[0].get("最新价", 0)),
                        "change": float(row.iloc[0].get("涨跌额", 0)),
                        "change_pct": float(row.iloc[0].get("涨跌幅", 0)),
                        "volume": int(row.iloc[0].get("成交量", 0)),
                        "amount": float(row.iloc[0].get("成交额", 0)),
                        "source": "eastmoney"
                    })
            return result
        except Exception as e:
            raise SourceUnavailableError("eastmoney", str(e)[:100])
    
    async def _fetch_sina_realtime_quote(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """从新浪财经获取实时行情"""
        try:
            import requests
            codes = [s.split(".")[0] for s in symbols]
            # 新浪接口
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {"Referer": "https://finance.sina.com.cn"}
            resp = requests.get(url, headers=headers, timeout=self.config.timeout)
            resp.encoding = "gbk"
            
            result = []
            for i, symbol in enumerate(symbols):
                lines = resp.text.strip().split("\n")
                if i < len(lines) and '="' in lines[i]:
                    data = lines[i].split('="')[1].split('"')[0].split(',')
                    if len(data) >= 32:
                        result.append({
                            "symbol": symbol,
                            "name": data[0],
                            "price": float(data[3]) if data[3] else 0,
                            "change": float(data[3]) - float(data[2]) if data[2] and data[3] else 0,
                            "change_pct": float(data[32]) if data[32] else 0,
                            "volume": int(data[6]) if data[6] else 0,
                            "amount": float(data[7]) if data[7] else 0,
                            "source": "sina"
                        })
            return result
        except Exception as e:
            raise SourceUnavailableError("sina", str(e)[:100])
    
    # ==================== K线数据 ====================
    
    async def fetch_kline(self, symbol: str, period: str = "daily", count: int = 100) -> List[Dict[str, Any]]:
        """
        获取K线数据
        
        参数：
            symbol: 股票代码
            period: 周期 (daily/weekly/monthly)
            count: 数据条数
        
        返回：
            K线数据列表
        """
        return await self._fetch_with_fallback(
            DataType.KLINE,
            self._build_fallback_sources("kline"),
            symbol, period, count
        )
    
    async def _fetch_akshare_kline(self, symbol: str, period: str = "daily", count: int = 100) -> List[Dict[str, Any]]:
        """从 AKShare 获取K线数据"""
        try:
            import akshare as ak
            # 转换周期
            period_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
            ak_period = period_map.get(period, "daily")
            
            # 获取K线
            df = ak.stock_zh_a_hist(symbol=symbol.split(".")[0], period=ak_period, adjust="")
            if df is None or len(df) == 0:
                raise DataNotFoundError(DataType.KLINE.value, symbol)
            
            # 限制数量
            df = df.tail(count)
            
            result = []
            for _, row in df.iterrows():
                result.append({
                    "date": str(row.get("日期", "")),
                    "open": float(row.get("开盘", 0)),
                    "high": float(row.get("最高", 0)),
                    "low": float(row.get("最低", 0)),
                    "close": float(row.get("收盘", 0)),
                    "volume": int(row.get("成交量", 0)),
                    "amount": float(row.get("成交额", 0)),
                    "source": "akshare"
                })
            return result
        except Exception as e:
            raise SourceUnavailableError("akshare", str(e)[:100])
    
    async def _fetch_eastmoney_kline(self, symbol: str, period: str = "daily", count: int = 100) -> List[Dict[str, Any]]:
        """从东方财富获取K线数据"""
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist_em(symbol=symbol.split(".")[0], period=period, adjust="")
            if df is None or len(df) == 0:
                raise DataNotFoundError(DataType.KLINE.value, symbol)
            
            df = df.tail(count)
            result = []
            for _, row in df.iterrows():
                result.append({
                    "date": str(row.get("日期", "")),
                    "open": float(row.get("开盘", 0)),
                    "high": float(row.get("最高", 0)),
                    "low": float(row.get("最低", 0)),
                    "close": float(row.get("收盘", 0)),
                    "volume": int(row.get("成交量", 0)),
                    "amount": float(row.get("成交额", 0)),
                    "source": "eastmoney"
                })
            return result
        except Exception as e:
            raise SourceUnavailableError("eastmoney", str(e)[:100])
    
    async def _fetch_sina_kline(self, symbol: str, period: str = "daily", count: int = 100) -> List[Dict[str, Any]]:
        """从新浪财经获取K线数据"""
        try:
            import requests
            code = symbol.split(".")[0]
            market = "sh" if symbol.endswith(".SH") else "sz"
            url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            params = {"symbol": f"{market}{code}", "scale": "240", "ma": "no", "datalen": str(count)}
            resp = requests.get(url, params=params, timeout=self.config.timeout)
            
            data = resp.json()
            if not data:
                raise DataNotFoundError(DataType.KLINE.value, symbol)
            
            result = []
            for item in data[-count:]:
                result.append({
                    "date": item.get("day", ""),
                    "open": float(item.get("open", 0)),
                    "high": float(item.get("high", 0)),
                    "low": float(item.get("low", 0)),
                    "close": float(item.get("close", 0)),
                    "volume": int(item.get("volume", 0)),
                    "amount": float(item.get("amount", 0)),
                    "source": "sina"
                })
            return result
        except Exception as e:
            raise SourceUnavailableError("sina", str(e)[:100])
    
    # ==================== 财务数据 ====================
    
    async def fetch_financial_data(self, symbol: str) -> Dict[str, Any]:
        """
        获取财务数据
        
        参数：
            symbol: 股票代码
        
        返回：
            财务数据字典
        """
        return await self._fetch_with_fallback(
            DataType.FINANCIAL,
            self._build_fallback_sources("financial"),
            symbol
        )
    
    async def _fetch_akshare_financial(self, symbol: str) -> Dict[str, Any]:
        """从 AKShare 获取财务数据"""
        try:
            import akshare as ak
            code = symbol.split(".")[0]
            
            # 获取财务指标
            try:
                indicators = ak.stock_financial_analysis_indicator(symbol=code)
                if indicators is not None and len(indicators) > 0:
                    latest = indicators.iloc[-1]
                    return {
                        "symbol": symbol,
                        "source": "akshare",
                        "indicators": {
                            "roe": float(latest.get("净资产收益率(%)", 0)),
                            "roa": float(latest.get("总资产报酬率(%)", 0)),
                            "gross_margin": float(latest.get("销售毛利率(%)", 0)),
                            "net_margin": float(latest.get("销售净利率(%)", 0)),
                            "revenue_growth": float(latest.get("营业收入同比增长率(%)", 0)),
                            "profit_growth": float(latest.get("净利润同比增长率(%)", 0)),
                        }
                    }
            except Exception:
                pass
            
            raise DataNotFoundError(DataType.FINANCIAL.value, symbol)
        except Exception as e:
            raise SourceUnavailableError("akshare", str(e)[:100])
    
    async def _fetch_eastmoney_financial(self, symbol: str) -> Dict[str, Any]:
        """从东方财富获取财务数据"""
        try:
            import akshare as ak
            code = symbol.split(".")[0]
            
            # 获取财务指标
            try:
                indicators = ak.stock_financial_report_sina(stock=code, symbol="利润表")
                if indicators is not None and len(indicators) > 0:
                    return {
                        "symbol": symbol,
                        "source": "eastmoney",
                        "revenue": float(indicators.iloc[0].get("营业总收入", 0)),
                        "net_profit": float(indicators.iloc[0].get("净利润", 0)),
                    }
            except Exception:
                pass
            
            raise DataNotFoundError(DataType.FINANCIAL.value, symbol)
        except Exception as e:
            raise SourceUnavailableError("eastmoney", str(e)[:100])
    
    # ==================== 指数数据 ====================
    
    async def fetch_index_data(self, index_code: str) -> Dict[str, Any]:
        """
        获取指数数据
        
        参数：
            index_code: 指数代码，如 '000001' (上证指数)
        
        返回：
            指数数据
        """
        return await self._fetch_with_fallback(
            DataType.INDEX,
            self._build_fallback_sources("index"),
            index_code
        )
    
    async def _fetch_akshare_index(self, index_code: str) -> Dict[str, Any]:
        """从 AKShare 获取指数数据"""
        try:
            import akshare as ak
            df = ak.stock_zh_index_spot_em()
            row = df[df["代码"] == index_code]
            if row.empty:
                raise DataNotFoundError(DataType.INDEX.value, index_code)
            
            return {
                "symbol": index_code,
                "name": row.iloc[0].get("名称", ""),
                "price": float(row.iloc[0].get("最新价", 0)),
                "change": float(row.iloc[0].get("涨跌额", 0)),
                "change_pct": float(row.iloc[0].get("涨跌幅", 0)),
                "volume": int(row.iloc[0].get("成交量", 0)),
                "amount": float(row.iloc[0].get("成交额", 0)),
                "source": "akshare"
            }
        except Exception as e:
            raise SourceUnavailableError("akshare", str(e)[:100])
    
    # ==================== 新闻数据 ====================
    
    async def fetch_news(self, symbol: str, count: int = 10) -> List[Dict[str, Any]]:
        """
        获取相关新闻
        
        参数：
            symbol: 股票代码
            count: 新闻条数
        
        返回：
            新闻列表
        """
        return await self._fetch_with_fallback(
            DataType.NEWS,
            self._build_fallback_sources("news"),
            symbol, count
        )
    
    async def _fetch_eastmoney_news(self, symbol: str, count: int = 10) -> List[Dict[str, Any]]:
        """从东方财富获取新闻"""
        try:
            import akshare as ak
            code = symbol.split(".")[0]
            df = ak.stock_news_em(symbol=code)
            if df is None or len(df) == 0:
                raise DataNotFoundError(DataType.NEWS.value, symbol)
            
            result = []
            for _, row in df.head(count).iterrows():
                result.append({
                    "title": str(row.get("新闻标题", "")),
                    "content": str(row.get("新闻内容", "")),
                    "source": str(row.get("文章来源", "")),
                    "publish_time": str(row.get("发布时间", "")),
                    "url": str(row.get("新闻链接", "")),
                    "symbol": symbol
                })
            return result
        except Exception as e:
            raise SourceUnavailableError("eastmoney", str(e)[:100])
    
    # ==================== 核心方法 ====================
    
    def _build_fallback_sources(self, data_type: str) -> List[Tuple[str, Callable]]:
        """构建降级数据源列表"""
        sources = []
        for source_name in self.config.source_priority:
            if source_name in self._data_sources:
                func = self._data_sources[source_name].get(data_type)
                if func:
                    sources.append((source_name, func))
        return sources
    
    async def _fetch_with_fallback(
        self,
        data_type: DataType,
        sources: List[Tuple[str, Callable]],
        *args,
        **kwargs
    ) -> Any:
        """
        带降级的数据抓取
        
        参数：
            data_type: 数据类型
            sources: 数据源列表
            *args, **kwargs: 传递给抓取函数的参数
        
        返回：
            抓取结果
        """
        # 检查限流
        await self.rate_limiter.acquire()
        
        # 创建降级管理器
        fallback = FallbackManager(
            sources=sources,
            circuit_breakers=self.circuit_breakers
        )
        
        try:
            result = await fallback.fetch(*args, **kwargs)
            
            # 数据验证
            if self.config.validate_data and result:
                self._validate_data(data_type, result)
            
            # 记录成功结果
            if result:
                self.health_checker.record_fetch_result("akshare", True)
                self.health_checker.record_fetch_result("eastmoney", True)
                self.health_checker.record_fetch_result("sina", True)
            
            return result
            
        except FallbackError as e:
            logger.error(f"所有数据源均失败：{e.message}")
            # 记录所有源失败
            for source_name in self.config.source_priority:
                self.health_checker.record_fetch_result(source_name, False)
            raise
        except Exception as e:
            logger.error(f"数据抓取失败：{e}")
            # 记录失败
            for source_name in self.config.source_priority:
                self.health_checker.record_fetch_result(source_name, False)
            raise
    
    def _validate_data(self, data_type: DataType, data: Any) -> bool:
        """验证数据质量"""
        if data_type == DataType.REALTIME_QUOTE:
            if not isinstance(data, list) or len(data) == 0:
                raise DataEmptyError(data_type.value)
            for item in data:
                if not item.get("price", 0) > 0:
                    raise DataEmptyError(data_type.value, item.get("symbol"), "价格为空")
        
        elif data_type == DataType.KLINE:
            if not isinstance(data, list) or len(data) < self.config.min_data_points:
                raise DataEmptyError(data_type.value, details={"expected_min": self.config.min_data_points})
        
        return True
    
    # ==================== 健康监控 ====================
    
    async def start_health_monitoring(self):
        """启动健康监控"""
        await self.health_checker.start_monitoring()
        logger.info("健康监控已启动")
    
    def stop_health_monitoring(self):
        """停止健康监控"""
        self.health_checker.stop_monitoring()
        logger.info("健康监控已停止")
    
    def get_source_status(self, source_name: str) -> Dict[str, Any]:
        """获取数据源状态"""
        return {
            "health": self.health_checker.get_status(source_name),
            "circuit_breaker": {
                "state": self.circuit_breakers.get(source_name, {}).state,
                "failure_count": self.circuit_breakers.get(source_name, {}).failure_count
            } if source_name in self.circuit_breakers else None,
            "rate_limiter": {
                "available_tokens": self.rate_limiter.available_tokens
            }
        }
    
    def get_all_source_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有数据源状态"""
        return {
            source: self.get_source_status(source)
            for source in self.config.source_priority
        }
    
    def reset_circuit_breaker(self, source_name: str):
        """手动重置熔断器"""
        if source_name in self.circuit_breakers:
            self.circuit_breakers[source_name].reset()
            logger.info(f"已重置熔断器：{source_name}")
    
    def reset_all_circuit_breakers(self):
        """重置所有熔断器"""
        for cb in self.circuit_breakers.values():
            cb.reset()
        logger.info("已重置所有熔断器")


    # ==================== 资金流向 ====================

    async def fetch_capital_flow(self, symbol: str = None, data_type: str = 'stock') -> List[Dict[str, Any]]:
        """获取资金流向数据"""
        from .data_fetching.fetchers import fetch_capital_flow
        return fetch_capital_flow(symbol, data_type)

    # ==================== 融资融券 ====================

    async def fetch_margin_data(self, data_type: str = 'summary') -> List[Dict[str, Any]]:
        """获取融资融券数据"""
        from .data_fetching.fetchers import fetch_margin_data
        return fetch_margin_data(data_type)

    # ==================== 北向资金 ====================

    async def fetch_northbound_data(self, data_type: str = 'flow') -> List[Dict[str, Any]]:
        """获取北向资金数据"""
        from .data_fetching.fetchers import fetch_northbound_data
        return fetch_northbound_data(data_type)


# 便捷函数
async def fetch_realtime_quote(symbols: List[str]) -> List[Dict[str, Any]]:
    """便捷函数：获取实时行情"""
    fetcher = UnifiedFetcher()
    return await fetcher.fetch_realtime_quote(symbols)


async def fetch_kline(symbol: str, period: str = "daily", count: int = 100) -> List[Dict[str, Any]]:
    """便捷函数：获取K线数据"""
    fetcher = UnifiedFetcher()
    return await fetcher.fetch_kline(symbol, period, count)


async def fetch_financial_data(symbol: str) -> Dict[str, Any]:
    """便捷函数：获取财务数据"""
    fetcher = UnifiedFetcher()
    return await fetcher.fetch_financial_data(symbol)


async def fetch_capital_flow(symbol: str = None, data_type: str = 'stock') -> List[Dict[str, Any]]:
    """便捷函数：获取资金流向"""
    fetcher = UnifiedFetcher()
    return await fetcher.fetch_capital_flow(symbol, data_type)


async def fetch_margin_data(data_type: str = 'summary') -> List[Dict[str, Any]]:
    """便捷函数：获取融资融券"""
    fetcher = UnifiedFetcher()
    return await fetcher.fetch_margin_data(data_type)


async def fetch_northbound_data(data_type: str = 'flow') -> List[Dict[str, Any]]:
    """便捷函数：获取北向资金"""
    fetcher = UnifiedFetcher()
    return await fetcher.fetch_northbound_data(data_type)
