"""
缺失值处理模块
支持前向填充、线性插值、零值填充、标记 NaN 等策略
"""

from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np

from .pipeline import BaseCleaner, CleanLevel, CleanResult


class FillStrategy(Enum):
    """填充策略"""
    FORWARD = "ffill"           # 前向填充
    BACKWARD = "bfill"          # 后向填充
    LINEAR = "linear"           # 线性插值
    ZERO = "zero"               # 零值填充
    MEAN = "mean"               # 均值填充
    MEDIAN = "median"           # 中位数填充
    CONSTANT = "constant"       # 常数填充
    MARK_NAN = "mark_nan"       # 标记为 NaN (不填充)
    DROP = "drop"               # 删除行


@dataclass
class FieldFillConfig:
    """字段填充配置"""
    strategy: FillStrategy = FillStrategy.FORWARD
    constant_value: Any = None
    max_gap: Optional[int] = None  # 最大填充间隔 (行数)
    limit: Optional[int] = None    # 最大连续填充数


class MissingValueHandler(BaseCleaner):
    """L3: 缺失值处理"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['quote', 'kline', 'financial']
    
    # 默认策略配置
    DEFAULT_STRATEGY = {
        'quote': {
            'price': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'open': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'high': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'low': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'close': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'pre_close': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'volume': FieldFillConfig(strategy=FillStrategy.ZERO),
            'amount': FieldFillConfig(strategy=FillStrategy.ZERO),
            'pct_chg': FieldFillConfig(strategy=FillStrategy.MARK_NAN),
            'change': FieldFillConfig(strategy=FillStrategy.MARK_NAN),
            'turnover_rate': FieldFillConfig(strategy=FillStrategy.MARK_NAN),
        },
        'kline': {
            'open': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'high': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'low': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'close': FieldFillConfig(strategy=FillStrategy.FORWARD),
            'volume': FieldFillConfig(strategy=FillStrategy.ZERO),
            'amount': FieldFillConfig(strategy=FillStrategy.ZERO),
        },
        'financial': {
            # 财务指标不填充，仅标记
        },
    }
    
    def __init__(self, custom_strategy: Dict = None):
        self.strategy = custom_strategy or self.DEFAULT_STRATEGY
    
    def clean(self, raw_data: Dict) -> CleanResult:
        # 单条数据处理：仅标记缺失字段
        payload = raw_data.get('payload', {})
        data_type = raw_data.get('data_type', 'quote')
        
        field_config = self.strategy.get(data_type, {})
        missing_fields = []
        
        for field, config in field_config.items():
            if field in payload and (payload[field] is None or payload[field] == ''):
                missing_fields.append(field)
                if config.strategy == FillStrategy.MARK_NAN:
                    payload[field] = np.nan
        
        if missing_fields:
            raw_data['_missing_fields'] = missing_fields
        
        return CleanResult(data=raw_data, level=self.level, passed=True)
    
    @staticmethod
    def fill_dataframe(df: pd.DataFrame, 
                       strategy_map: Dict[str, FillStrategy] = None,
                       constant_values: Dict[str, Any] = None) -> pd.DataFrame:
        """批量填充 DataFrame 缺失值"""
        df = df.copy()
        
        for col in df.columns:
            if col not in df.columns:
                continue
            
            strategy = FillStrategy.FORWARD
            if strategy_map and col in strategy_map:
                strategy = strategy_map[col]
            
            if df[col].isna().all():
                continue
            
            if strategy == FillStrategy.FORWARD:
                df[col] = df[col].ffill(limit=10)
            elif strategy == FillStrategy.BACKWARD:
                df[col] = df[col].bfill(limit=10)
            elif strategy == FillStrategy.LINEAR:
                df[col] = df[col].interpolate(method='linear', limit=10)
            elif strategy == FillStrategy.ZERO:
                df[col] = df[col].fillna(0)
            elif strategy == FillStrategy.MEAN:
                df[col] = df[col].fillna(df[col].mean())
            elif strategy == FillStrategy.MEDIAN:
                df[col] = df[col].fillna(df[col].median())
            elif strategy == FillStrategy.CONSTANT:
                val = constant_values.get(col, 0) if constant_values else 0
                df[col] = df[col].fillna(val)
            elif strategy == FillStrategy.MARK_NAN:
                pass  # 保持 NaN
            elif strategy == FillStrategy.DROP:
                df = df.dropna(subset=[col])
        
        return df
    
    @staticmethod
    def detect_missing_patterns(df: pd.DataFrame) -> Dict:
        """检测缺失模式"""
        missing = df.isnull().sum()
        missing_pct = (missing / len(df) * 100).round(2)
        
        # 连续缺失段
        consecutive = {}
        for col in df.columns:
            if df[col].isnull().any():
                is_null = df[col].isnull()
                groups = (is_null != is_null.shift()).cumsum()
                null_groups = groups[is_null]
                if len(null_groups) > 0:
                    max_consecutive = null_groups.value_counts().max()
                    consecutive[col] = int(max_consecutive)
        
        return {
            'missing_count': missing.to_dict(),
            'missing_pct': missing_pct.to_dict(),
            'max_consecutive_missing': consecutive,
            'total_rows': len(df),
            'complete_rows': int((~df.isnull().any(axis=1)).sum()),
        }


class TimeSeriesMissingHandler:
    """时间序列专用缺失值处理"""
    
    @staticmethod
    def resample_and_fill(df: pd.DataFrame,
                          freq: str = '1min',
                          method: str = 'ffill',
                          trading_hours_only: bool = True) -> pd.DataFrame:
        """重采样并填充缺失时间点"""
        df = df.copy()
        
        # 确保索引是 datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        
        # 时区处理
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        else:
            df.index = df.index.tz_convert('UTC')
        
        if trading_hours_only and freq.endswith(('min', 'H', 'T')):
            # 生成仅交易时段的时间网格
            grid = TimeSeriesMissingHandler._generate_trading_grid(
                df.index.min(), df.index.max(), freq
            )
        else:
            grid = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz='UTC')
        
        # 重新索引
        df = df.reindex(grid)
        
        # 填充
        if method == 'ffill':
            df = df.ffill()
        elif method == 'bfill':
            df = df.bfill()
        elif method == 'interpolate':
            df = df.interpolate(method='time')
        
        return df
    
    @staticmethod
    def _generate_trading_grid(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
        """生成仅包含交易时段的时间网格"""
        grids = []
        current = start.normalize()
        
        while current <= end:
            if current.weekday() < 5:  # 周一至周五
                # 上午 9:30-11:30
                morning = pd.date_range(
                    current + pd.Timedelta('9:30'),
                    current + pd.Timedelta('11:30'),
                    freq=freq, tz='UTC'
                )
                # 下午 13:00-15:00
                afternoon = pd.date_range(
                    current + pd.Timedelta('13:00'),
                    current + pd.Timedelta('15:00'),
                    freq=freq, tz='UTC'
                )
                grids.append(morning)
                grids.append(afternoon)
            current += pd.Timedelta('1D')
        
        if grids:
            return pd.DatetimeIndex(pd.concat(grids)).sort_values()
        return pd.DatetimeIndex([], tz='UTC')
    
    @staticmethod
    def fill_suspended_stocks(df: pd.DataFrame,
                              calendar: pd.DatetimeIndex,
                              suspend_dates: Dict[str, List[pd.Timestamp]] = None) -> pd.DataFrame:
        """处理停牌股票：按交易日历填充"""
        # 实现停牌填充逻辑
        # suspend_dates: {symbol: [suspend_dates]}
        return df