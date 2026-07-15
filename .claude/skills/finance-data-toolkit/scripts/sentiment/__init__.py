"""
舆情分析模块
提供统一的情感分析和舆情聚合接口
"""

from .sentiment_analyzer import (
    SentimentResult,
    FinanceData,
    LexiconSentimentAnalyzer,
    StockSentimentAggregator,
    analyze_sentiment,
    analyze_stock_sentiment,
)

__all__ = [
    'SentimentResult',
    'FinanceData',
    'LexiconSentimentAnalyzer',
    'StockSentimentAggregator',
    'analyze_sentiment',
    'analyze_stock_sentiment',
]