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

# 异常和容错模块
try:
    from .exceptions import (
        FinanceError,
        SourceError,
        SourceUnavailableError,
        SourceRateLimitedError,
        SourceAuthError,
        DataError,
        DataNotFoundError,
        DataQualityError,
        DataValidationError,
        CircuitBreakerError,
        FallbackError,
        ConfigError,
    )
    from .resilience import (
        CircuitBreaker,
        FallbackManager,
        retry_with_backoff,
        RetryStrategySelector,
        get_retry_engine,
        RetryConfig,
    )
    from .retry_engine import (
        RetryEngine,
        ExponentialBackoffRetry,
        FixedIntervalRetry,
        AdaptiveBackoffRetry,
        retry_on_error,
    )
    from .retry_config import (
        RetryConfig as RetryConfigV2,
        RetryConfigManager,
        get_retry_config_manager,
        reset_retry_config_manager,
        DEFAULT_RETRY_CONFIG,
    )
    from .retry_strategy import retry_with_config
    from .error_capture import ErrorCapture, ErrorType
except ImportError:
    pass

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
        fetch_forex_quote,
        fetch_crypto_quote,
        fetch_etf_quote,
        ExtendedDataFetcher,
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

try:
    from .social import (
        SocialSource,
        SocialCategory,
        SocialPost,
        WeiboHotScraper,
        XueqiuDiscussionScraper,
        ThsWencaiScraper,
        fetch_weibo_hot,
        fetch_xueqiu_hot,
        fetch_ths_wencai_hot,
        fetch_all_social_hot,
    )
except ImportError:
    pass

try:
    from .schema_validator import (
        FinanceDataSchemaValidator,
        ValidationResult,
        SchemaValidationError,
        validate_finance_data,
        validate_finance_data_batch,
        get_validation_summary,
    )
except ImportError:
    pass

# 解析器模块
try:
    from .parsers import (
        parse_html_table,
        parse_html_text,
        parse_html_jsonp,
        parse_json_response,
        parse_eastmoney_json,
        parse_sina_jsonp,
        parse_sina_text,
        parse_eastmoney_text,
        safe_float,
        safe_int,
    )
except ImportError:
    pass

# 存储模块
try:
    from .storage import (
        FinanceDatabase,
        create_database,
    )
except ImportError:
    pass

# 数据质量验证集成
try:
    from .validation_adapter import (
        validate_quote_data,
        validate_kline_data,
        validate_financial_data,
        validate_capital_flow,
        validate_sector_data,
        validate_fund_data,
        validate_bond_data,
        ValidationPipeline,
        QualityReport,
    )
except ImportError:
    pass

# 基础输入校验模块（最小验证集）
try:
    from .input_validator import (
        InputValidator,
        ValidationResult,
        BatchResult,
        validate,
        validate_batch,
        list_schemas,
    )
except ImportError:
    pass

try:
    from .compliance_checker import (
        ComplianceChecker,
        ComplianceResult,
        Violation,
        ViolationLevel,
        ComplianceStatus,
        check_compliance,
        check_compliance_batch,
        get_compliance_summary,
    )
except ImportError:
    pass

# 数据质量验证体系 - 新增模块
try:
    from .validators import (
        DataIntegrityValidator,
        validate_quote,
        validate_kline,
        validate_financial,
        CrossSourceValidator,
        validate_multi_source_quote,
        validate_multi_source_kline,
    )
    from .monitoring import (
        TaskMonitor,
        TaskMetric,
        AlertLevel,
        MonitorConfig,
        DashboardRenderer,
        render_dashboard,
    )
    from .benchmark import (
        DataSourceBenchmark,
        SourceBenchmark,
        BaselineConfig,
        run_all_benchmarks,
    )
    from .reports import (
        QualityReportGenerator,
        generate_quality_report,
    )
except ImportError as e:
    import logging
    logging.getLogger(__name__).warning(f"数据质量验证模块导入失败: {e}")


# ========== 插件系统（步骤 4 新增） ==========
try:
    from .plugins import (
        BasePlugin,
        PluginConfig,
        DataType,
        HealthStatus,
        PluginManager,
        get_plugin_manager,
        PluginDiscovery,
        PluginError,
        PluginFetchError,
        PluginConfigError,
        SourceUnavailableError,
        SourceRateLimitedError,
        DataNotFoundError,
        DataQualityError,
    )
    from .plugins.manager import PluginManager, get_plugin_manager
    from .plugins.router import DataSourceRouter, PluginRoute
    from .config_loader import ConfigLoader
except ImportError as e:
    import logging
    logging.getLogger(__name__).warning(f"插件系统导入失败（可能尚未初始化）: {e}")

__version__ = '1.0.0'

__all__ = [
    # 核心
    'FinanceData',
    'BaseScraper',
    'register_scraper',
    'create_scraper',
    
    # 异常和容错
    'FinanceError',
    'SourceError',
    'SourceUnavailableError',
    'SourceRateLimitedError',
    'SourceAuthError',
    'DataError',
    'DataNotFoundError',
    'DataQualityError',
    'DataValidationError',
    'CircuitBreakerError',
    'FallbackError',
    'ConfigError',
    'CircuitBreaker',
    'FallbackManager',
    'retry_with_backoff',
    'retry_with_config',

    # 重试配置管理
    'RetryConfigV2',
    'RetryConfigManager',
    'get_retry_config_manager',
    'reset_retry_config_manager',
    'DEFAULT_RETRY_CONFIG',

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
    
    # 扩展数据获取（外汇/加密货币/ETF）
    'fetch_forex_quote',
    'fetch_crypto_quote',
    'fetch_etf_quote',
    'ExtendedDataFetcher',
    
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
    
    # 社交媒体/舆情
    'SocialSource',
    'SocialCategory',
    'SocialPost',
    'WeiboHotScraper',
    'XueqiuDiscussionScraper',
    'ThsWencaiScraper',
    'fetch_weibo_hot',
    'fetch_xueqiu_hot',
    'fetch_ths_wencai_hot',
    'fetch_all_social_hot',
    
    # 数据格式验证
    'FinanceDataSchemaValidator',
    'ValidationResult',
    'SchemaValidationError',
    'validate_finance_data',
    'validate_finance_data_batch',
    'get_validation_summary',

    # 合规检查
    'ComplianceChecker',
    'ComplianceResult',
    'Violation',
    'ViolationLevel',
    'ComplianceStatus',
    'check_compliance',
    'check_compliance_batch',
    'get_compliance_summary',
    
    # 解析器
    'parse_html_table',
    'parse_html_text',
    'parse_html_jsonp',
    'parse_json_response',
    'parse_eastmoney_json',
    'parse_sina_jsonp',
    'parse_sina_text',
    'parse_eastmoney_text',
    'safe_float',
    'safe_int',
    
    # 存储
    'FinanceDatabase',
    'create_database',

    # 数据质量验证集成
    'validate_quote_data',
    'validate_kline_data',
    'validate_financial_data',
    'validate_capital_flow',
    'validate_sector_data',
    'validate_fund_data',
    'validate_bond_data',
    'ValidationPipeline',
    'QualityReport',

    # 数据质量验证体系
    'DataIntegrityValidator',
    'validate_quote',
    'validate_kline',
    'validate_financial',
    'CrossSourceValidator',
    'validate_multi_source_quote',
    'validate_multi_source_kline',
    'TaskMonitor',
    'TaskMetric',
    'AlertLevel',
    'MonitorConfig',
    'DashboardRenderer',
    'render_dashboard',
    'DataSourceBenchmark',
    'SourceBenchmark',
    'BaselineConfig',
    'run_all_benchmarks',
    'QualityReportGenerator',
    'generate_quality_report',
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

