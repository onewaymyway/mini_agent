# -*- coding: utf-8 -*-
"""
因子回测框架 - 基于技术指标的量化回测
==========================================

功能：
- 因子预处理 (缩尾、标准化、中性化、正交化)
- 多因子打分 (IC加权、等权、Rank IC)
- 多空/纯多头策略回测
- 绩效指标计算 (年化收益、夏普、最大回撤、胜率)

符合 finance-data-toolkit 统一数据契约
"""

import sys
import json
import argparse
import warnings
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# 抑制不必要的警告
warnings.filterwarnings('ignore', category=pd.errors.SettingWithCopyWarning)
warnings.filterwarnings('ignore', category=UserWarning, module='pandas')
warnings.filterwarnings('ignore', category=FutureWarning)

# 尝试导入可选依赖
try:
    import statsmodels.api as sm
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

try:
    import scipy.stats  # noqa: F401
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from sklearn.decomposition import PCA
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ============== 数据结构 ==============

@dataclass
class BacktestResult:
    """回测结果"""
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    avg_holding_period: float
    returns_series: List[float]
    positions: List[Dict]
    timestamp: str
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FactorData:
    """因子数据"""
    symbol: str
    date: str
    factors: Dict[str, float]
    forward_return: float = 0.0


# ============== 因子预处理 ==============

class FactorProcessor:
    """因子标准化、中性化、正交化"""
    
    @staticmethod
    def winsorize(factor: pd.Series, lower: float = 0.025, upper: float = 0.975) -> pd.Series:
        """缩尾处理 (去极值)"""
        low_val = factor.quantile(lower)
        high_val = factor.quantile(upper)
        return factor.clip(low_val, high_val)
    
    @staticmethod
    def standardize(factor: pd.Series, method: str = 'zscore') -> pd.Series:
        """标准化: z-score / rank / min-max"""
        if method == 'zscore':
            std = factor.std()
            if std == 0:
                return factor * 0
            return (factor - factor.mean()) / std
        elif method == 'rank':
            return factor.rank(pct=True) * 2 - 1  # [-1, 1]
        elif method == 'minmax':
            fmin, fmax = factor.min(), factor.max()
            if fmax == fmin:
                return factor * 0
            return (factor - fmin) / (fmax - fmin) * 2 - 1
        return factor
    
    @staticmethod
    def neutralize(factor: pd.Series, 
                   market_cap: pd.Series = None,
                   industry: pd.Series = None) -> pd.Series:
        """因子中性化: 回归残差法"""
        if not HAS_STATSMODELS:
            return factor
        
        X = []
        if market_cap is not None:
            X.append(np.log(market_cap).rename('ln_mcap'))
        if industry is not None:
            dummies = pd.get_dummies(industry, prefix='ind')
            X.append(dummies)
        
        if not X:
            return factor
        
        X = pd.concat(X, axis=1)
        X = sm.add_constant(X)
        
        valid = factor.notna() & X.notna().all(axis=1)
        if valid.sum() < 10:
            return factor
        
        model = sm.OLS(factor[valid], X[valid]).fit()
        residuals = factor.copy()
        residuals[valid] = model.resid
        return residuals
    
    @staticmethod
    def orthogonalize(factors: pd.DataFrame, method: str = 'gram_schmidt') -> pd.DataFrame:
        """因子正交化 (Gram-Schmidt / PCA)"""
        if not HAS_STATSMODELS:
            return factors
        
        if method == 'gram_schmidt':
            result = factors.copy()
            for i, col in enumerate(factors.columns):
                if i == 0:
                    continue
                X = sm.add_constant(result.iloc[:, :i])
                y = factors[col]
                valid = y.notna() & X.notna().all(axis=1)
                if valid.sum() > 10:
                    model = sm.OLS(y[valid], X[valid]).fit()
                    result.loc[valid, col] = model.resid
            return result
        elif method == 'pca':
            if not HAS_SKLEARN:
                return factors
            pca = PCA()
            valid = factors.notna().all(axis=1)
            transformed = pca.fit_transform(factors[valid])
            result = pd.DataFrame(transformed, index=factors[valid].index,
                                  columns=[f'PC{i+1}' for i in range(factors.shape[1])])
            return result


# ============== 多因子模型 ==============

class MultiFactorModel:
    """多因子选股模型 (IC加权 / 等权 / 机器学习)"""
    
    def __init__(self, factor_data: pd.DataFrame, forward_returns: pd.Series):
        """
        factor_data: index=[date, symbol], columns=因子名
        forward_returns: index=[date, symbol], 未来 N 日收益率
        """
        self.factors = factor_data
        self.returns = forward_returns
    
    def calc_ic(self, method: str = 'spearman') -> pd.DataFrame:
        """计算各因子 IC (信息系数) 序列"""
        ic_series = {}
        for factor in self.factors.columns:
            ic = self.factors[factor].groupby(level=0).corr(
                self.returns, method=method
            )
            ic_series[factor] = ic
        return pd.DataFrame(ic_series)
    
    def ic_weighted_score(self, ic_window: int = 252) -> pd.Series:
        """IC 加权打分: 权重 = 近期 IC 均值 / IC 标准差 (IR)"""
        ic_df = self.calc_ic()
        
        weights = {}
        for date in self.factors.index.get_level_values(0).unique():
            hist_ic = ic_df.loc[:date].tail(ic_window)
            if len(hist_ic) < 20:
                continue
            ir = hist_ic.mean() / hist_ic.std()
            ir = ir.clip(lower=0)  # 负 IR 置 0
            weights[date] = ir / ir.sum() if ir.sum() > 0 else pd.Series(1/len(ir), index=ir.index)
        
        scores = []
        for date, weight in weights.items():
            factor_slice = self.factors.xs(date, level=0)
            score = (factor_slice * weight).sum(axis=1)
            score = score.reset_index()
            score['date'] = date
            score = score.set_index(['date', 'symbol'])[0]
            scores.append(score)
        
        if not scores:
            return pd.Series(dtype=float, name='ic_weighted_score')
        return pd.concat(scores).rename('ic_weighted_score')
    
    def equal_weight_score(self) -> pd.Series:
        """等权打分 (标准化后求和)"""
        scores = []
        for date in self.factors.index.get_level_values(0).unique():
            factor_slice = self.factors.xs(date, level=0)
            standardized = factor_slice.apply(FactorProcessor.standardize)
            score = standardized.sum(axis=1)
            score = score.reset_index()
            score['date'] = date
            score = score.set_index(['date', 'symbol'])[0]
            scores.append(score)
        
        return pd.concat(scores).rename('equal_weight_score')
    
    def rank_ic_score(self) -> pd.Series:
        """基于 Rank IC 的简单加权"""
        ic_df = self.calc_ic('spearman')
        mean_ic = ic_df.mean()
        mean_ic = mean_ic.clip(lower=0)
        weights = mean_ic / mean_ic.sum() if mean_ic.sum() > 0 else pd.Series(1/len(mean_ic), index=mean_ic.index)
        
        scores = []
        for date in self.factors.index.get_level_values(0).unique():
            factor_slice = self.factors.xs(date, level=0)
            standardized = factor_slice.apply(FactorProcessor.standardize, method='rank')
            score = (standardized * weights).sum(axis=1)
            score = score.reset_index()
            score['date'] = date
            score = score.set_index(['date', 'symbol'])[0]
            scores.append(score)
        
        return pd.concat(scores).rename('rank_ic_score')

# ============== 回测框架 ==============

class FactorBacktest:
    """因子选股回测框架"""
    
    def __init__(self, scores: pd.Series, prices: pd.DataFrame,
                 benchmark: pd.Series = None, fee: float = 0.001):
        """
        scores: 多因子得分, index=[date, symbol]
        prices: 价格数据, columns=[open, high, low, close, volume]
        benchmark: 基准指数收益率
        fee: 单边手续费率
        """
        self.scores = scores
        self.prices = prices
        self.benchmark = benchmark
        self.fee = fee
    
    def run_long_short(self, 
                        n_long: int = 20,
                        n_short: int = 20,
                        rebalance_freq: str = 'W') -> BacktestResult:
        """多空策略回测"""
        # 调仓日：从 scores 中获取所有日期，按频率重采样，只保留实际存在的日期
        all_dates = self.scores.index.get_level_values(0).unique()
        rebalance_dates = pd.DatetimeIndex(all_dates).to_period(rebalance_freq).drop_duplicates().to_timestamp()
        rebalance_dates = rebalance_dates[rebalance_dates.isin(all_dates)]
        
        portfolio_returns = []
        positions = []
        valid_rebalance_dates = []
        
        for i, date in enumerate(rebalance_dates[:-1]):
            next_date = rebalance_dates[i + 1]
            
            # 当日得分
            daily_scores = self.scores.xs(date, level=0).dropna()
            
            if len(daily_scores) < n_long + n_short:
                continue
            
            # 选股
            long_stocks = daily_scores.nlargest(n_long).index.tolist()
            short_stocks = daily_scores.nsmallest(n_short).index.tolist()
            
            # 计算区间收益
            long_ret = self._calc_period_return(long_stocks, date, next_date)
            short_ret = self._calc_period_return(short_stocks, date, next_date)
            
            # 多空组合收益 (等权)
            port_ret = (long_ret - short_ret) / 2 - self.fee * 2  # 双边手续费
            portfolio_returns.append(port_ret)
            positions.append({'date': date.strftime('%Y-%m-%d'), 'long': long_stocks, 'short': short_stocks})
            valid_rebalance_dates.append(date)
        
        if not portfolio_returns:
            return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, [], [], datetime.utcnow().isoformat())
        
        returns_series = pd.Series(portfolio_returns, index=pd.DatetimeIndex(valid_rebalance_dates))
        return self._calc_metrics(returns_series, positions)
    
    def run_long_only(self, 
                       n_long: int = 20,
                       rebalance_freq: str = 'W') -> BacktestResult:
        """纯多头策略回测"""
        all_dates = self.scores.index.get_level_values(0).unique()
        rebalance_dates = pd.DatetimeIndex(all_dates).to_period(rebalance_freq).drop_duplicates().to_timestamp()
        rebalance_dates = rebalance_dates[rebalance_dates.isin(all_dates)]
        
        portfolio_returns = []
        positions = []
        valid_rebalance_dates = []
        
        for i, date in enumerate(rebalance_dates[:-1]):
            next_date = rebalance_dates[i + 1]
            
            daily_scores = self.scores.xs(date, level=0).dropna()
            
            if len(daily_scores) < n_long:
                continue
            
            long_stocks = daily_scores.nlargest(n_long).index.tolist()
            long_ret = self._calc_period_return(long_stocks, date, next_date)
            
            port_ret = long_ret - self.fee * 2
            portfolio_returns.append(port_ret)
            positions.append({'date': date.strftime('%Y-%m-%d'), 'long': long_stocks, 'short': []})
            valid_rebalance_dates.append(date)
        
        if not portfolio_returns:
            return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, [], [], datetime.utcnow().isoformat())
        
        returns_series = pd.Series(portfolio_returns, index=pd.DatetimeIndex(valid_rebalance_dates))
        return self._calc_metrics(returns_series, positions)
    
    def _calc_period_return(self, symbols: List[str], start: pd.Timestamp, end: pd.Timestamp) -> float:
        """计算股票池区间收益率 (等权)"""
        rets = []
        for sym in symbols:
            try:
                price_data = self.prices.xs(sym, level='symbol')
                start_price = price_data.loc[start:start, 'close'].iloc[0]
                end_price = price_data.loc[end:end, 'close'].iloc[-1]
                rets.append((end_price - start_price) / start_price)
            except Exception:
                rets.append(0)
        return np.mean(rets) if rets else 0
    
    def _calc_metrics(self, returns: pd.Series, positions: List) -> BacktestResult:
        """计算绩效指标"""
        if len(returns) == 0:
            return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, [], [], datetime.utcnow().isoformat())
        
        cum_ret = (1 + returns).prod() - 1
        ann_ret = (1 + returns.mean()) ** (252 / len(returns)) - 1
        ann_vol = returns.std() * np.sqrt(252 / len(returns))
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        max_dd = (returns.cumsum().expanding().max() - returns.cumsum()).max()
        win_rate = (returns > 0).mean()
        
        return BacktestResult(
            cumulative_return=round(cum_ret, 4),
            annualized_return=round(ann_ret, 4),
            annualized_volatility=round(ann_vol, 4),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown=round(max_dd, 4),
            win_rate=round(win_rate, 4),
            total_trades=len(returns),
            avg_holding_period=0,  # 简化
            returns_series=returns.tolist(),
            positions=positions,
            timestamp=datetime.utcnow().isoformat(),
        )


# ============== 数据加载与因子计算 ==============

def load_kline_data(data_dir: str, symbols: List[str]) -> pd.DataFrame:
    """加载 K 线数据 (从 fetch_kline_sina.py 生成的 JSON)"""
    all_data = []
    
    for symbol in symbols:
        # 查找最新的原始数据文件
        raw_files = list(Path(data_dir).glob(f"{symbol}_kline_raw_*.json"))
        if not raw_files:
            print(f"[WARN] 未找到 {symbol} 的 K 线数据", file=sys.stderr)
            continue
        
        latest_file = max(raw_files, key=lambda f: f.stat().st_mtime)
        with open(latest_file, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        # raw_data 是 dict，实际 K 线在 kline_raw 字段
        kline_list = raw_data.get('kline_raw', [])
        for row in kline_list:
            all_data.append({
                'symbol': symbol,
                'date': row['date'],
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            })
    
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index(['date', 'symbol']).sort_index()
    return df


def calc_technical_factors(kline_df: pd.DataFrame) -> pd.DataFrame:
    """计算技术指标因子"""
    if kline_df.empty:
        return pd.DataFrame()
    
    factors_list = []
    
    for symbol in kline_df.index.get_level_values('symbol').unique():
        try:
            stock_data = kline_df.xs(symbol, level='symbol')
            close = stock_data['close']
            high = stock_data['high']
            low = stock_data['low']
            volume = stock_data['volume']
            
            if len(close) < 60:
                continue
            
            # 计算各项指标
            factor_dict = {}
            
            # MA 系统
            for period in [5, 10, 20, 30, 60]:
                ma = close.rolling(period).mean()
                factor_dict[f'MA{period}'] = ma
                # 价格相对均线位置
                factor_dict[f'PRICE_TO_MA{period}'] = (close - ma) / ma
            
            # EMA
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            factor_dict['EMA12'] = ema12
            factor_dict['EMA26'] = ema26
            
            # MACD
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            macd = 2 * (dif - dea)
            factor_dict['MACD_DIF'] = dif
            factor_dict['MACD_DEA'] = dea
            factor_dict['MACD_HIST'] = macd
            factor_dict['MACD_SIGNAL'] = (dif > dea).astype(int)  # 金叉/死叉
            
            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = -delta.where(delta < 0, 0).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            factor_dict['RSI14'] = rsi
            
            # 布林带
            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std
            factor_dict['BB_UPPER'] = bb_upper
            factor_dict['BB_LOWER'] = bb_lower
            factor_dict['BB_MID'] = bb_mid
            factor_dict['BB_WIDTH'] = (bb_upper - bb_lower) / bb_mid
            factor_dict['BB_POS'] = (close - bb_lower) / (bb_upper - bb_lower)  # %B
            
            # KDJ
            low_min = low.rolling(9).min()
            high_max = high.rolling(9).max()
            rsv = (close - low_min) / (high_max - low_min) * 100
            k = rsv.ewm(com=2, adjust=False).mean()
            d = k.ewm(com=2, adjust=False).mean()
            j = 3 * k - 2 * d
            factor_dict['KDJ_K'] = k
            factor_dict['KDJ_D'] = d
            factor_dict['KDJ_J'] = j
            factor_dict['KDJ_SIGNAL'] = (k > d).astype(int)
            
            # 成交量指标
            vol_ma5 = volume.rolling(5).mean()
            vol_ma20 = volume.rolling(20).mean()
            factor_dict['VOL_RATIO_5'] = volume / vol_ma5
            factor_dict['VOL_RATIO_20'] = volume / vol_ma20
            
            # 动量
            for period in [5, 10, 20, 60]:
                factor_dict[f'MOM_{period}'] = close.pct_change(period)
            
            # 波动率
            factor_dict['VOLATILITY_20'] = close.pct_change().rolling(20).std()
            
            # 转换为 DataFrame
            factor_df = pd.DataFrame(factor_dict, index=stock_data.index)
            factor_df['symbol'] = symbol
            factor_df = factor_df.reset_index().set_index(['date', 'symbol'])
            factors_list.append(factor_df)
            
        except Exception as e:
            print(f"[WARN] {symbol} 因子计算失败: {e}", file=sys.stderr)
    
    if not factors_list:
        return pd.DataFrame()
    
    all_factors = pd.concat(factors_list)
    return all_factors.sort_index()


def calc_forward_returns(kline_df: pd.DataFrame, periods: int = 5) -> pd.Series:
    """计算未来 N 日收益率"""
    if kline_df.empty:
        return pd.Series(dtype=float, name='forward_return')
    
    returns_list = []
    
    for symbol in kline_df.index.get_level_values('symbol').unique():
        stock_data = kline_df.xs(symbol, level='symbol')
        close = stock_data['close']
        forward_ret = close.pct_change(periods).shift(-periods)
        forward_ret.name = 'forward_return'
        # xs 后索引只有 date，需要加回 symbol
        forward_ret = forward_ret.reset_index()
        forward_ret['symbol'] = symbol
        forward_ret = forward_ret.set_index(['date', 'symbol'])['forward_return']
        returns_list.append(forward_ret)
    
    if not returns_list:
        return pd.Series(dtype=float, name='forward_return')
    
    return pd.concat(returns_list).sort_index()


def prepare_factor_data(factors: pd.DataFrame, 
                        forward_returns: pd.Series,
                        factor_names: List[str] = None) -> Tuple[pd.DataFrame, pd.Series]:
    """准备因子数据：对齐、去除 NaN、预处理"""
    if factor_names is None:
        factor_names = factors.columns.tolist()
    
    # 对齐
    common_idx = factors.index.intersection(forward_returns.index)
    factors_aligned = factors.loc[common_idx, factor_names]
    returns_aligned = forward_returns.loc[common_idx]
    
    # 去除全 NaN 行
    valid = factors_aligned.notna().all(axis=1) & returns_aligned.notna()
    factors_clean = factors_aligned[valid]
    returns_clean = returns_aligned[valid]
    
    # 确保索引级别名正确
    if factors_clean.index.names != ['date', 'symbol']:
        factors_clean.index.names = ['date', 'symbol']
    if returns_clean.index.names != ['date', 'symbol']:
        returns_clean.index.names = ['date', 'symbol']
    
    # 逐日预处理
    processed_factors = []
    processed_returns = []
    dates = factors_clean.index.get_level_values('date').unique()
    
    for date in dates:
        day_factors = factors_clean.xs(date, level='date').copy()
        day_returns = returns_clean.xs(date, level='date')
        
        # 缩尾
        for col in day_factors.columns:
            day_factors.loc[:, col] = FactorProcessor.winsorize(day_factors[col])
        
        # 标准化
        day_factors = day_factors.apply(FactorProcessor.standardize, method='rank')
        
        # 因子处理后可能产生 NaN（如全 NaN 列标准化后），同步剔除对应收益
        valid_symbols = day_factors.notna().all(axis=1)
        day_factors = day_factors[valid_symbols]
        day_returns = day_returns[valid_symbols]
        
        # 重建 MultiIndex，确保顺序正确：date 在前，symbol 在后
        day_factors = day_factors.reset_index()
        day_factors['date'] = date
        day_factors = day_factors.set_index(['date', 'symbol'])
        day_factors.index.names = ['date', 'symbol']
        
        day_returns = day_returns.reset_index()
        day_returns['date'] = date
        day_returns = day_returns.set_index(['date', 'symbol'])['forward_return']
        day_returns.index.names = ['date', 'symbol']
        
        processed_factors.append(day_factors)
        processed_returns.append(day_returns)
    
    if not processed_factors:
        return pd.DataFrame(), pd.Series(dtype=float)
    
    final_factors = pd.concat(processed_factors).sort_index()
    final_returns = pd.concat(processed_returns).sort_index()
    
    # 最终确保索引名正确
    final_factors.index.names = ['date', 'symbol']
    final_returns.index.names = ['date', 'symbol']
    
    return final_factors, final_returns

# ============== 主程序 ==============

def main():
    parser = argparse.ArgumentParser(description='因子回测框架')
    parser.add_argument('symbols', nargs='+', help='股票代码列表 (如 603000 600000)')
    parser.add_argument('--data-dir', default='temp/kline_results', help='K 线数据目录')
    parser.add_argument('--periods', type=int, default=5, help='前瞻收益期数 (默认 5 日)')
    parser.add_argument('--n-long', type=int, default=3, help='多头持仓数 (默认 3)')
    parser.add_argument('--n-short', type=int, default=3, help='空头持仓数 (默认 3)')
    parser.add_argument('--rebalance', default='W', help='调仓频率: W=周, M=月 (默认 W)')
    parser.add_argument('--fee', type=float, default=0.001, help='单边手续费率 (默认 0.001)')
    parser.add_argument('--method', choices=['ic_weighted', 'equal_weight', 'rank_ic'], default='equal_weight', help='打分方法')
    parser.add_argument('--long-only', action='store_true', help='纯多头模式')
    parser.add_argument('--output', '-o', help='输出文件路径')
    
    args = parser.parse_args()
    
    print("=== 因子回测 ===")
    print("股票: " + args.symbols)
    print("数据目录: " + args.data_dir)
    print("前瞻期: " + str(args.periods) + " 日")
    print("调仓频率: " + args.rebalance)
    print("打分方法: " + args.method)
    print("模式: " + ("纯多头" if args.long_only else "多空"))
    print()
    
    # 1. 加载 K 线数据
    print("[1/5] 加载 K 线数据...")
    kline_df = load_kline_data(args.data_dir, args.symbols)
    if kline_df.empty:
        print("[ERROR] 无可用 K 线数据", file=sys.stderr)
        return 1
    print(f"    加载完成: {len(kline_df)} 条记录, {kline_df.index.get_level_values('symbol').nunique()} 只股票")
    
    # 2. 计算技术指标因子
    print("[2/5] 计算技术指标因子...")
    factors = calc_technical_factors(kline_df)
    if factors.empty:
        print("[ERROR] 因子计算失败", file=sys.stderr)
        return 1
    print(f"    因子数量: {len(factors.columns)}, 样本数: {len(factors)}")
    
    # 3. 计算前瞻收益率
    print("[3/5] 计算前瞻收益率...")
    forward_returns = calc_forward_returns(kline_df, args.periods)
    print(f"    样本数: {len(forward_returns)}")
    
    # 4. 准备因子数据
    print("[4/5] 准备因子数据...")
    factor_names = [c for c in factors.columns if c not in ['symbol']]
    factors_clean, returns_clean = prepare_factor_data(factors, forward_returns, factor_names)
    if factors_clean.empty:
        print("[ERROR] 无有效因子数据", file=sys.stderr)
        return 1
    print(f"    有效样本: {len(factors_clean)}")
    
    # 5. 多因子打分
    print("[5/5] 多因子打分与回测...")
    model = MultiFactorModel(factors_clean, returns_clean)
    
    if args.method == 'ic_weighted':
        scores = model.ic_weighted_score()
    elif args.method == 'rank_ic':
        scores = model.rank_ic_score()
    else:
        scores = model.equal_weight_score()
    
    print(f"    评分样本数: {len(scores)}")
    
    # 6. 回测
    backtest = FactorBacktest(scores, kline_df, fee=args.fee)
    
    if args.long_only:
        result = backtest.run_long_only(n_long=args.n_long, rebalance_freq=args.rebalance)
    else:
        result = backtest.run_long_short(n_long=args.n_long, n_short=args.n_short, rebalance_freq=args.rebalance)
    
    # 输出结果
    print("\n=== 回测结果 ===")
    print("累计收益率: " + f"{result.cumulative_return:.2%}")
    print("年化收益率: " + f"{result.annualized_return:.2%}")
    print("年化波动率: " + f"{result.annualized_volatility:.2%}")
    print("夏普比率: " + f"{result.sharpe_ratio:.4f}")
    print("最大回撤: " + f"{result.max_drawdown:.2%}")
    print("胜率: " + f"{result.win_rate:.2%}")
    print("总调仓次数: " + str(result.total_trades))
    
    # 保存结果
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存至: {output_path}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())