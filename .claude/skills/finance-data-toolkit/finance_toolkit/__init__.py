# -*- coding: utf-8 -*-
"""
Finance Data Toolkit - 金融数据抓取与分析全链路工具箱
==================================================

统一入口模块，提供标准化的数据获取、分析、回测、报告生成接口。

核心架构：
- finance_toolkit.core: 核心抽象接口 (BaseScraper, FinanceData)
- finance_toolkit.scrapers: 具体数据源抓取器 (AKShare, Tushare, Eastmoney, Sina...)
- finance_toolkit.data_fetching: 数据获取高级封装
- finance_toolkit.technical_analysis: 技术指标计算与信号生成
- finance_toolkit.backtesting: 因子回测框架
- finance_toolkit.sentiment: 舆情分析
- finance_toolkit.batch_processing: 批量数据处理
- finance_toolkit.report_generation: 研报生成

快速开始：
    from finance_toolkit import create_scraper, analyze_stock, run_backtest

    # 创建抓取器
    async with create_scraper('akshare') as scraper:
        async for data in scraper.fetch(['600000.SH'], 'quote'):
            print(data.payload)

    # 一键分析股票
    result = analyze_stock('600000.SH')
    print(result.signals)

    # 运行回测
    backtest_result = run_backtest(symbols=['600000.SH', '000001.SZ'])
    print(backtest_result.sharpe_ratio)
"""

from typing import List, Dict

from .core import (
    FinanceData,
    BaseScraper,
    register_scraper,
    create_scraper,
)

# 导入子模块的公开接口
try:
    from .data_fetching import (
        fetch_realtime_quote,
        fetch_kline,
        fetch_financial,
        fetch_dividend,
        fetch_lhb,
        fetch_northbound,
        fetch_stock_basic,
        GubaPost,
        GubaComment,
        GubaUserProfile,
        EastmoneyGubaAPI,
        GubaCDPScraper,
        fetch_guba_posts,
        fetch_guba_hot_posts,
        fetch_guba_post_detail,
        fetch_guba_comments,
        fetch_guba_user_profile,
        sync_fetch_guba_posts,
        sync_fetch_guba_hot_posts,
    )
except ImportError:
    pass

try:
    from .technical_analysis import (
        calc_ma,
        calc_ema,
        calc_macd,
        calc_rsi,
        calc_boll,
        calc_kdj,
        generate_signals,
        analyze_kline_data,
        HAS_TALIB,
    )
except ImportError:
    pass

try:
    from .backtesting import (
        BacktestResult,
        FactorData,
        FactorProcessor,
        MultiFactorModel,
        FactorBacktest,
        load_kline_data,
        calc_technical_factors,
        calc_forward_returns,
        prepare_factor_data,
    )
except ImportError:
    pass

try:
    from .sentiment import (
        SentimentResult,
        LexiconSentimentAnalyzer,
        StockSentimentAggregator,
        analyze_sentiment,
        analyze_stock_sentiment,
    )
except ImportError:
    pass

try:
    from .news import (
        NewsSource,
        NewsCategory,
        FinanceNews,
        SinaNewsScraper,
        CLSNewsScraper,
        WallstreetcnScraper,
        XueqiuScraper,
        WechatScraper,
        ArxivScraper,
        RegulatorScraper,
        NewsAggregator,
        NewsStreamProcessor,
        SentimentAnalyzer,
        NewsAlert,
        AlertLevel,
        run_news_monitor,
    )
except ImportError:
    pass

try:
    from .cleaning import (
        CleanLevel,
        CleanResult,
        BaseCleaner,
        CleanPipeline,
        StructureNormalizer,
        TypeCoercer,
        TimeNormalizer,
        FieldMapper,
        SymbolNormalizer,
        QuoteValidator,
        FinancialValidator,
        NewsValidator,
        GubaValidator,
        FeatureEngineer,
        TechnicalFeatureEngineer,
        VolatilityFeatureEngineer,
        MissingValueHandler,
        TimeSeriesMissingHandler,
        Deduplicator,
        IncrementalDeduplicator,
        TimeAligner,
        QualityMetrics,
        QualityMonitor,
        DataQualityReport,
    )
except ImportError:
    pass

try:
    from .batch_processing import (
        batch_fetch_stocks,
        batch_fetch_klines,
        fetch_single_stock,
        fetch_single_kline,
    )
except ImportError:
    pass

try:
    from .report_generation import (
        generate_html_report,
        generate_markdown_report,
        generate_json_report,
        generate_comprehensive_report,
    )
except ImportError:
    pass


__version__ = '1.0.0'

__all__ = [
    # 核心
    'FinanceData',
    'BaseScraper',
    'register_scraper',
    'create_scraper',
    
    # 数据获取
    'fetch_realtime_quote',
    'fetch_kline',
    'fetch_financial',
    'fetch_dividend',
    'fetch_lhb',
    'fetch_northbound',
    'fetch_stock_basic',
    'GubaPost',
    'GubaComment',
    'GubaUserProfile',
    'EastmoneyGubaAPI',
    'GubaCDPScraper',
    'fetch_guba_posts',
    'fetch_guba_hot_posts',
    'fetch_guba_post_detail',
    'fetch_guba_comments',
    'fetch_guba_user_profile',
    'sync_fetch_guba_posts',
    'sync_fetch_guba_hot_posts',
    
    # 新闻抓取
    'NewsSource',
    'NewsCategory',
    'FinanceNews',
    'SinaNewsScraper',
    'CLSNewsScraper',
    'WallstreetcnScraper',
    'XueqiuScraper',
    'WechatScraper',
    'ArxivScraper',
    'RegulatorScraper',
    'NewsAggregator',
    'NewsStreamProcessor',
    'SentimentAnalyzer',
    'NewsAlert',
    'AlertLevel',
    'run_news_monitor',
    
    # 数据清洗
    'CleanLevel',
    'CleanResult',
    'BaseCleaner',
    'CleanPipeline',
    'StructureNormalizer',
    'TypeCoercer',
    'TimeNormalizer',
    'FieldMapper',
    'SymbolNormalizer',
    'QuoteValidator',
    'FinancialValidator',
    'NewsValidator',
    'GubaValidator',
    'FeatureEngineer',
    'TechnicalFeatureEngineer',
    'VolatilityFeatureEngineer',
    'MissingValueHandler',
    'TimeSeriesMissingHandler',
    'Deduplicator',
    'IncrementalDeduplicator',
    'TimeAligner',
    'QualityMetrics',
    'QualityMonitor',
    'DataQualityReport',
    
    # 技术分析
    'calc_ma',
    'calc_ema',
    'calc_macd',
    'calc_rsi',
    'calc_boll',
    'calc_kdj',
    'generate_signals',
    'analyze_kline_data',
    'HAS_TALIB',
    
    # 回测
    'BacktestResult',
    'FactorData',
    'FactorProcessor',
    'MultiFactorModel',
    'FactorBacktest',
    'load_kline_data',
    'calc_technical_factors',
    'calc_forward_returns',
    'prepare_factor_data',
    
    # 舆情
    'SentimentResult',
    'LexiconSentimentAnalyzer',
    'StockSentimentAggregator',
    'analyze_sentiment',
    'analyze_stock_sentiment',
    
    # 批量处理
    'batch_fetch_stocks',
    'batch_fetch_klines',
    'fetch_single_stock',
    'fetch_single_kline',
    
    # 报告生成
    'generate_html_report',
    'generate_markdown_report',
    'generate_json_report',
    'generate_comprehensive_report',
]


# 便捷函数
def analyze_stock(symbol: str, period: str = 'daily', datalen: int = 1023) -> Dict:
    """一键分析单只股票：获取 K 线 + 计算指标 + 生成信号"""
    from .data_fetching import fetch_kline
    from .technical_analysis import analyze_kline_data
    from datetime import datetime, timedelta
    
    # 计算日期范围
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=datalen)).strftime('%Y%m%d')
    
    kline_data = fetch_kline(symbol, period=period, start=start, end=end, source='sina')
    if not kline_data:
        return {'error': 'No data'}
    
    return analyze_kline_data(kline_data)


def run_backtest(
    symbols: List[str],
    data_dir: str = 'temp/kline_results',
    periods: int = 5,
    n_long: int = 3,
    n_short: int = 3,
    rebalance: str = 'W',
    fee: float = 0.001,
    method: str = 'equal_weight',
    long_only: bool = False
) -> Dict:
    """运行因子回测"""
    from .backtesting import (
        load_kline_data,
        calc_technical_factors,
        calc_forward_returns,
        prepare_factor_data,
        MultiFactorModel,
        FactorBacktest,
    )
    
    # 1. 加载数据
    kline_df = load_kline_data(data_dir, symbols)
    if kline_df.empty:
        return {'error': 'No kline data'}
    
    # 2. 计算因子
    factors = calc_technical_factors(kline_df)
    
    # 3. 计算前瞻收益
    forward_returns = calc_forward_returns(kline_df, periods)
    
    # 4. 准备因子数据
    factors_clean, returns_clean = prepare_factor_data(factors, forward_returns)
    
    # 5. 多因子打分
    model = MultiFactorModel(factors_clean, returns_clean)
    if method == 'ic_weighted':
        scores = model.ic_weighted_score()
    elif method == 'rank_ic':
        scores = model.rank_ic_score()
    else:
        scores = model.equal_weight_score()
    
    # 6. 回测
    backtest = FactorBacktest(scores, kline_df, fee=fee)
    if long_only:
        result = backtest.run_long_only(n_long=n_long, rebalance_freq=rebalance)
    else:
        result = backtest.run_long_short(n_long=n_long, n_short=n_short, rebalance_freq=rebalance)
    
    return result.to_dict()


# 类型提示
from typing import List, Dict
