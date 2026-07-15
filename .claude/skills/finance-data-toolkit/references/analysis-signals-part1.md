# 技术指标与基本面信号计算模块 (第 1 部分)

覆盖：MA/EMA/MACD/RSI/BOLL/KDJ、资金流向、估值模型、因子选股、多因子打分、回测框架。

## 1. 技术指标库 (基于 talib / pandas 实现)

### 1.1 趋势类指标

```python
import pandas as pd
import numpy as np
import talib

class TrendIndicators:
    """趋势跟踪指标"""
    
    @staticmethod
    def sma(close: pd.Series, period: int) -> pd.Series:
        """简单移动平均"""
        return close.rolling(period).mean()
    
    @staticmethod
    def ema(close: pd.Series, period: int) -> pd.Series:
        """指数移动平均"""
        return close.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def ma(close: pd.Series, periods: List[int] = [5, 10, 20, 30, 60, 120, 250]) -> pd.DataFrame:
        """多周期均线系统"""
        df = pd.DataFrame(index=close.index)
        for p in periods:
            df[f'MA{p}'] = TrendIndicators.sma(close, p)
        return df
    
    @staticmethod
    def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """MACD 指标
        返回: DIF, DEA, MACD柱状图 (2*(DIF-DEA))
        """
        dif = TrendIndicators.ema(close, fast) - TrendIndicators.ema(close, slow)
        dea = TrendIndicators.ema(dif, signal)
        hist = 2 * (dif - dea)
        return pd.DataFrame({'DIF': dif, 'DEA': dea, 'MACD': hist}, index=close.index)
    
    @staticmethod
    def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """平均趋向指数 (趋势强度)"""
        return pd.Series(talib.ADX(high, low, close, period), index=close.index)
    
    @staticmethod
    def sar(high: pd.Series, low: pd.Series, acceleration: float = 0.02, maximum: float = 0.2) -> pd.Series:
        """抛物线转向指标 (止损点)"""
        return pd.Series(talib.SAR(high, low, acceleration, maximum), index=close.index)
```

### 1.2 动量/超买超卖类指标

```python
class MomentumIndicators:
    """动量震荡指标"""
    
    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """相对强弱指标"""
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def stoch(high: pd.Series, low: pd.Series, close: pd.Series,
              k_period: int = 9, d_period: int = 3) -> pd.DataFrame:
        """KDJ 随机指标
        返回: K, D, J (J = 3*K - 2*D)
        """
        lowest_low = low.rolling(k_period).min()
        highest_high = high.rolling(k_period).max()
        rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
        k = rsv.ewm(com=d_period-1, adjust=False).mean()
        d = k.ewm(com=d_period-1, adjust=False).mean()
        j = 3 * k - 2 * d
        return pd.DataFrame({'K': k, 'D': d, 'J': j}, index=close.index)
    
    @staticmethod
    def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """顺势指标"""
        tp = (high + low + close) / 3
        ma = tp.rolling(period).mean()
        md = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())))
        return (tp - ma) / (0.015 * md)
    
    @staticmethod
    def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """威廉指标"""
        highest_high = high.rolling(period).max()
        lowest_low = low.rolling(period).min()
        return (highest_high - close) / (highest_high - lowest_low) * -100
```

### 1.3 波动率/通道类指标

```python
class VolatilityIndicators:
    """波动率与价格通道"""
    
    @staticmethod
    def bollinger(close: pd.Series, period: int = 20, std_dev: float = 2) -> pd.DataFrame:
        """布林带
        返回: UPPER, MIDDLE, LOWER, BANDWIDTH, %B
        """
        middle = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        bandwidth = (upper - lower) / middle
        pct_b = (close - lower) / (upper - lower)
        return pd.DataFrame({
            'UPPER': upper, 'MIDDLE': middle, 'LOWER': lower,
            'BANDWIDTH': bandwidth, 'PCT_B': pct_b
        }, index=close.index)
    
    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """平均真实波幅"""
        return pd.Series(talib.ATR(high, low, close, period), index=close.index)
    
    @staticmethod
    def keltner(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 20, atr_mult: float = 2) -> pd.DataFrame:
        """肯特纳通道 (基于 ATR)"""
        middle = close.ewm(span=period, adjust=False).mean()
        atr_val = VolatilityIndicators.atr(high, low, close, period)
        upper = middle + atr_mult * atr_val
        lower = middle - atr_mult * atr_val
        return pd.DataFrame({'UPPER': upper, 'MIDDLE': middle, 'LOWER': lower}, index=close.index)
    
    @staticmethod
    def donchian(high: pd.Series, low: pd.Series, period: int = 20) -> pd.DataFrame:
        """唐奇安通道 (突破系统常用)"""
        upper = high.rolling(period).max()
        lower = low.rolling(period).min()
        middle = (upper + lower) / 2
        return pd.DataFrame({'UPPER': upper, 'MIDDLE': middle, 'LOWER': lower}, index=close.index)
```

### 1.4 成交量指标

```python
class VolumeIndicators:
    """成交量分析指标"""
    
    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """能量潮指标"""
        return pd.Series(talib.OBV(close, volume), index=close.index)
    
    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """成交量加权平均价 (日内)"""
        typical = (high + low + close) / 3
        return (typical * volume).cumsum() / volume.cumsum()
    
    @staticmethod
    def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
        """资金流量指标 (量价 RSI)"""
        return pd.Series(talib.MFI(high, low, close, volume, period), index=close.index)
    
    @staticmethod
    def volume_profile(close: pd.Series, volume: pd.Series, bins: int = 50) -> pd.DataFrame:
        """成交量分布 (价位成交量)"""
        hist, bin_edges = np.histogram(close.dropna(), bins=bins, weights=volume.loc[close.dropna().index])
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        return pd.DataFrame({'price': bin_centers, 'volume': hist})
```

## 2. 资金流向分析

```python
class MoneyFlowAnalyzer:
    """资金流向多维度分析"""
    
    def __init__(self, quote_data: pd.DataFrame, money_flow_data: pd.DataFrame):
        """
        quote_data: columns=[open,high,low,close,volume,amount]
        money_flow_data: columns=[main_net_inflow, super_large_net_inflow, large_net_inflow, medium_net_inflow, small_net_inflow]
        """
        self.quote = quote_data
        self.flow = money_flow_data
    
    def main_force_signal(self, lookback: int = 5) -> pd.Series:
        """主力资金信号: 连续流入/流出天数、净流入占比"""
        main_inflow = self.flow['main_net_inflow']
        signal = pd.Series(0, index=main_inflow.index)
        signal[main_inflow > 0] = 1
        signal[main_inflow < 0] = -1
        
        # 连续天数
        streak = signal.groupby((signal != signal.shift()).cumsum()).cumcount() + 1
        streak = streak * signal  # 正负表示方向
        
        # 净流入率
        inflow_rate = main_inflow / self.quote['amount']
        
        return pd.DataFrame({
            'main_inflow': main_inflow,
            'inflow_rate': inflow_rate,
            'streak': streak,
            'signal': np.where((streak >= lookback) & (signal == 1), 1,
                      np.where((streak <= -lookback) & (signal == -1), -1, 0))
        }, index=main_inflow.index)
    
    def smart_money_index(self) -> pd.Series:
        """聪明钱指数 (SMI): 尾盘资金流向 - 早盘资金流向"""
        # 简化版：收盘前30分钟 vs 开盘后30分钟
        # 需分钟级数据，此处用日线近似
        close_change = self.quote['close'].pct_change()
        volume_change = self.quote['volume'].pct_change()
        return (close_change * volume_change).rolling(20).sum()
    
    def chip_distribution(self, periods: List[int] = [5, 20, 60]) -> pd.DataFrame:
        """筹码分布估算 (基于成交量加权)"""
        # 简化：用均线作为筹码成本参考
        result = pd.DataFrame(index=self.quote.index)
        for p in periods:
            cost = self.quote['close'].rolling(p).apply(
                lambda x: np.average(x, weights=self.quote.loc[x.index, 'volume'])
            )
            result[f'chip_cost_{p}'] = cost
            result[f'profit_ratio_{p}'] = (self.quote['close'] - cost) / cost
        return result
```

## 3. 基本面因子与估值模型

### 3.1 核心财务因子

```python
class FundamentalFactors:
    """基本面因子计算 (需财务报表数据)"""
    
    def __init__(self, financial_data: pd.DataFrame, quote_data: pd.DataFrame):
        """
        financial_data: 多期财务报表，index=[symbol, report_date]
        quote_data: 对应期间的行情数据
        """
        self.fin = financial_data
        self.quote = quote_data
    
    # 盈利能力
    def roe(self) -> pd.Series:
        """净资产收益率"""
        return self.fin['net_profit'] / self.fin['total_equity']
    
    def roa(self) -> pd.Series:
        """总资产收益率"""
        return self.fin['net_profit'] / self.fin['total_assets']
    
    def gross_margin(self) -> pd.Series:
        """毛利率"""
        return (self.fin['revenue'] - self.fin['cost_of_goods']) / self.fin['revenue']
    
    def net_margin(self) -> pd.Series:
        """净利率"""
        return self.fin['net_profit'] / self.fin['revenue']
    
    # 成长能力
    def revenue_yoy(self) -> pd.Series:
        """营收同比增长率"""
        return self.fin.groupby('symbol')['revenue'].pct_change(4)  # 季报同比
    
    def profit_yoy(self) -> pd.Series:
        """净利润同比增长率"""
        return self.fin.groupby('symbol')['net_profit'].pct_change(4)
    
    def eps_growth(self) -> pd.Series:
        """EPS 增长率"""
        return self.fin.groupby('symbol')['eps'].pct_change(4)
    
    # 估值因子
    def pe_ttm(self) -> pd.Series:
        """滚动市盈率"""
        # 需结合最新总市值 / TTM 净利润
        pass
    
    def pb(self) -> pd.Series:
        """市净率"""
        return self.quote['total_mv'] / self.fin['total_equity']
    
    def ps(self) -> pd.Series:
        """市销率"""
        return self.quote['total_mv'] / self.fin['revenue']
    
    def dividend_yield(self) -> pd.Series:
        """股息率"""
        return self.fin['dividend_per_share'] / self.quote['close']
    
    # 财务风险
    def debt_to_equity(self) -> pd.Series:
        """资产负债率"""
        return self.fin['total_liability'] / self.fin['total_equity']
    
    def current_ratio(self) -> pd.Series:
        """流动比率"""
        return self.fin['current_assets'] / self.fin['current_liability']
    
    def interest_coverage(self) -> pd.Series:
        """利息保障倍数"""
        return self.fin['ebit'] / self.fin['interest_expense']
    
    def all_factors(self) -> pd.DataFrame:
        """计算所有因子并合并"""
        factors = pd.DataFrame(index=self.fin.index)
        for name in dir(self):
            if not name.startswith('_') and callable(getattr(self, name)):
                if name != 'all_factors':
                    try:
                        factors[name] = getattr(self, name)()
                    except:
                        pass
        return factors
```

### 3.2 估值模型

```python
class ValuationModels:
    """经典估值模型实现"""
    
    @staticmethod
    def dcf(fcf: float, growth_rate: float, discount_rate: float,
            terminal_growth: float = 0.02, years: int = 10) -> float:
        """DCF 现金流折现模型
        fcf: 当前自由现金流
        growth_rate: 显性预测期增长率
        discount_rate: 折现率 (WACC)
        terminal_growth: 永续增长率
        """
        # 显性预测期
        pv = 0
        for i in range(1, years + 1):
            fcf_future = fcf * (1 + growth_rate) ** i
            pv += fcf_future / (1 + discount_rate) ** i
        
        # 终值
        terminal_fcf = fcf * (1 + growth_rate) ** years * (1 + terminal_growth)
        terminal_value = terminal_fcf / (discount_rate - terminal_growth)
        pv_terminal = terminal_value / (1 + discount_rate) ** years
        
        return pv + pv_terminal
    
    @staticmethod
    def ddm(dividend: float, growth_rate: float, required_return: float) -> float:
        """DDM 股息贴现模型 (Gordon Growth Model)"""
        if required_return <= growth_rate:
            return float('inf')
        return dividend * (1 + growth_rate) / (required_return - growth_rate)
    
    @staticmethod
    def residual_income(bvps: float, roe: float, cost_of_equity: float,
                        growth_rate: float = 0, years: int = 10) -> float:
        """剩余收益模型 (RIM)"""
        value = bvps
        for i in range(1, years + 1):
            ri = bvps * (roe - cost_of_equity)
            value += ri / (1 + cost_of_equity) ** i
            bvps *= (1 + growth_rate)
        return value
    
    @staticmethod
    def relative_valuation(peers: pd.DataFrame, target_metric: str = 'pe_ttm') -> Dict:
        """相对估值 (可比公司法)"""
        valid = peers[target_metric].dropna()
        valid = valid[(valid > 0) & (valid < valid.quantile(0.95))]  # 剔除异常值
        return {
            'mean': valid.mean(),
            'median': valid.median(),
            'pct_25': valid.quantile(0.25),
            'pct_75': valid.quantile(0.75),
            'min': valid.min(),
            'max': valid.max(),
        }
```