"""
时间对齐模块
多源数据时间对齐、重采样、交易时段过滤
"""

from typing import Any, Dict, List, Optional, Union
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta


class TimeAligner:
    """多源数据时间对齐器"""
    
    # A股交易时段
    TRADING_SESSIONS = [
        (time(9, 30), time(11, 30)),   # 上午
        (time(13, 0), time(15, 0)),    # 下午
    ]
    
    @staticmethod
    def align_to_grid(df: pd.DataFrame,
                      freq: str = '1min',
                      method: str = 'ffill',
                      trading_hours_only: bool = True,
                      fill_limit: Optional[int] = None) -> pd.DataFrame:
        """
        将不规则时间序列对齐到固定频率网格
        
        Args:
            df: 输入 DataFrame，索引为 DatetimeIndex
            freq: 目标频率 (如 '1min', '5min', '1H', '1D')
            method: 填充方法 ('ffill', 'bfill', 'interpolate', 'zero')
            trading_hours_only: 是否仅保留交易时段
            fill_limit: 最大连续填充数
        
        Returns:
            对齐后的 DataFrame
        """
        if df.empty:
            return df
        
        df = df.copy()
        
        # 确保索引是 DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        
        # 时区统一为 UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        else:
            df.index = df.index.tz_convert('UTC')
        
        # 生成时间网格
        if trading_hours_only and freq.endswith(('min', 'H', 'T', 'S')):
            grid = TimeAligner._generate_trading_grid(
                df.index.min(), df.index.max(), freq
            )
        else:
            grid = pd.date_range(df.index.min(), df.index.max(), freq=freq, tz='UTC')
        
        # 重新索引
        df = df.reindex(grid)
        
        # 填充缺失值
        if method == 'ffill':
            df = df.ffill(limit=fill_limit)
        elif method == 'bfill':
            df = df.bfill(limit=fill_limit)
        elif method == 'interpolate':
            df = df.interpolate(method='time', limit=fill_limit)
        elif method == 'zero':
            df = df.fillna(0)
        
        return df
    
    @staticmethod
    def _generate_trading_grid(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
        """生成仅包含交易时段的时间网格"""
        grids = []
        current = start.normalize()
        
        while current <= end:
            if current.weekday() < 5:  # 周一至周五
                for session_start, session_end in TimeAligner.TRADING_SESSIONS:
                    session_start_dt = current.replace(
                        hour=session_start.hour, minute=session_start.minute, second=0, microsecond=0
                    )
                    session_end_dt = current.replace(
                        hour=session_end.hour, minute=session_end.minute, second=0, microsecond=0
                    )
                    
                    if session_start_dt < start:
                        session_start_dt = start
                    if session_end_dt > end:
                        session_end_dt = end
                    
                    if session_start_dt < session_end_dt:
                        session_grid = pd.date_range(
                            session_start_dt, session_end_dt, freq=freq, tz='UTC'
                        )
                        grids.append(session_grid)
            current += pd.Timedelta('1D')
        
        if grids:
            return pd.DatetimeIndex(pd.concat(grids)).sort_values()
        return pd.DatetimeIndex([], tz='UTC')
    
    @staticmethod
    def align_multiple_sources(dfs: Dict[str, pd.DataFrame],
                                freq: str = '1min',
                                method: str = 'ffill',
                                trading_hours_only: bool = True,
                                common_range: bool = True) -> Dict[str, pd.DataFrame]:
        """
        多源数据时间对齐到同一网格
        
        Args:
            dfs: {source_name: DataFrame} 字典
            freq: 目标频率
            method: 填充方法
            trading_hours_only: 是否仅交易时段
            common_range: 是否使用公共时间范围 (取交集)
        
        Returns:
            对齐后的 {source_name: DataFrame} 字典
        """
        if not dfs:
            return {}
        
        # 找到公共时间范围
        if common_range:
            all_starts = [df.index.min() for df in dfs.values() if not df.empty]
            all_ends = [df.index.max() for df in dfs.values() if not df.empty]
            
            if not all_starts:
                return {k: pd.DataFrame() for k in dfs}
            
            common_start = max(all_starts)
            common_end = min(all_ends)
            
            # 先裁剪到公共范围
            trimmed = {}
            for name, df in dfs.items():
                if df.empty:
                    trimmed[name] = df
                    continue
                mask = (df.index >= common_start) & (df.index <= common_end)
                trimmed[name] = df.loc[mask]
        else:
            trimmed = dfs
        
        # 对齐每个源
        aligned = {}
        for name, df in trimmed.items():
            aligned[name] = TimeAligner.align_to_grid(
                df, freq=freq, method=method, trading_hours_only=trading_hours_only
            )
        
        return aligned
    
    @staticmethod
    def resample_ohlcv(df: pd.DataFrame,
                       freq: str,
                       trading_hours_only: bool = True) -> pd.DataFrame:
        """
        OHLCV 数据重采样 (如 1min -> 5min, 1D -> 1W)
        
        Args:
            df: 包含 open, high, low, close, volume, amount 列的 DataFrame
            freq: 目标频率
            trading_hours_only: 是否仅交易时段
        
        Returns:
            重采样后的 OHLCV DataFrame
        """
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"缺少必需列: {col}")
        
        df = df.copy()
        
        # 确保索引是 DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        else:
            df.index = df.index.tz_convert('UTC')
        
        # 重采样聚合
        ohlcv_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
        }
        
        if 'amount' in df.columns:
            ohlcv_dict['amount'] = 'sum'
        
        # 仅交易时段重采样
        if trading_hours_only and freq.endswith(('min', 'H', 'T', 'S')):
            # 先对齐到交易时段网格
            df = TimeAligner.align_to_grid(df, freq=freq, method=None, trading_hours_only=True)
            # 再聚合
            resampled = df.resample(freq).agg(ohlcv_dict)
        else:
            resampled = df.resample(freq).agg(ohlcv_dict)
        
        # 删除全空行
        resampled = resampled.dropna(how='all')
        
        return resampled
    
    @staticmethod
    def synchronize_timestamps(timestamps: List[pd.Timestamp],
                                target_freq: str = '1min',
                                tolerance: str = '30s') -> List[pd.Timestamp]:
        """
        将时间戳列表同步到目标频率网格
        
        Args:
            timestamps: 原始时间戳列表
            target_freq: 目标频率
            tolerance: 容差范围 (如 '30s', '1min')
        
        Returns:
            同步后的时间戳列表
        """
        if not timestamps:
            return []
        
        ts_index = pd.DatetimeIndex(timestamps)
        if ts_index.tz is None:
            ts_index = ts_index.tz_localize('UTC')
        else:
            ts_index = ts_index.tz_convert('UTC')
        
        # 生成目标网格
        grid = pd.date_range(ts_index.min(), ts_index.max(), freq=target_freq, tz='UTC')
        
        # 找到每个时间戳最近的网格点
        tolerance_td = pd.Timedelta(tolerance)
        synchronized = []
        
        for ts in ts_index:
            # 找到最近的网格点
            idx = grid.get_indexer([ts], method='nearest')[0]
            nearest = grid[idx]
            
            # 检查是否在容差范围内
            if abs((ts - nearest).total_seconds()) <= tolerance_td.total_seconds():
                synchronized.append(nearest)
            else:
                synchronized.append(ts)  # 超出容差，保留原值
        
        return synchronized
    
    @staticmethod
    def is_trading_time(ts: pd.Timestamp) -> bool:
        """判断时间戳是否在交易时段内"""
        if ts.tz is not None:
            ts = ts.tz_convert('Asia/Shanghai')
        else:
            ts = ts.tz_localize('UTC').tz_convert('Asia/Shanghai')
        
        # 周末非交易
        if ts.weekday() >= 5:
            return False
        
        t = ts.time()
        for start, end in TimeAligner.TRADING_SESSIONS:
            if start <= t <= end:
                return True
        return False
    
    @staticmethod
    def filter_trading_hours(df: pd.DataFrame) -> pd.DataFrame:
        """过滤仅保留交易时段数据"""
        if df.empty:
            return df
        
        mask = df.index.to_series().apply(TimeAligner.is_trading_time)
        return df[mask]
    
    @staticmethod
    def get_trading_days(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        """获取交易日列表 (简化版，不考虑节假日)"""
        all_days = pd.date_range(start.normalize(), end.normalize(), freq='D', tz='UTC')
        return all_days[all_days.weekday < 5]


class CalendarAligner:
    """交易日历对齐器 (需接入真实交易日历)"""
    
    def __init__(self, calendar: Optional[pd.DatetimeIndex] = None):
        """
        Args:
            calendar: 交易日历 DatetimeIndex (日期级别)
        """
        self.calendar = calendar
    
    def set_calendar(self, calendar: pd.DatetimeIndex):
        """设置交易日历"""
        self.calendar = calendar
    
    def is_trading_day(self, date: pd.Timestamp) -> bool:
        """判断是否为交易日"""
        if self.calendar is None:
            # 简化版：周一至周五
            return date.weekday() < 5
        return date.normalize() in self.calendar
    
    def get_trading_days(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        """获取区间内交易日"""
        if self.calendar is None:
            return TimeAligner.get_trading_days(start, end)
        
        mask = (self.calendar >= start.normalize()) & (self.calendar <= end.normalize())
        return self.calendar[mask]
    
    def align_to_trading_days(self, df: pd.DataFrame) -> pd.DataFrame:
        """将数据对齐到交易日 (日线级别)"""
        if df.empty or self.calendar is None:
            return df
        
        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        
        # 仅保留交易日
        mask = df.index.normalize().isin(self.calendar)
        return df[mask]
    
    def fill_non_trading_days(self, df: pd.DataFrame, method: str = 'ffill') -> pd.DataFrame:
        """非交易日填充 (如周末填充为前一交易日)"""
        if df.empty or self.calendar is None:
            return df
        
        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        
        # 生成完整日期范围
        full_range = pd.date_range(df.index.min(), df.index.max(), freq='D', tz=df.index.tz)
        
        # 重新索引
        df = df.reindex(full_range)
        
        # 标记交易日
        is_trading = full_range.normalize().isin(self.calendar)
        
        # 填充
        if method == 'ffill':
            df = df.ffill()
        elif method == 'interpolate':
            df = df.interpolate(method='time')
        
        # 非交易日标记
        df['_is_trading_day'] = is_trading_day
        
        return df