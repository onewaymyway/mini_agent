"""
回测框架模块
提供统一的因子回测接口
"""

from .backtest_framework import (
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

__all__ = [
    'BacktestResult',
    'FactorData',
    'FactorProcessor',
    'MultiFactorModel',
    'FactorBacktest',
    'load_kline_data',
    'calc_technical_factors',
    'calc_forward_returns',
    'prepare_factor_data',
]