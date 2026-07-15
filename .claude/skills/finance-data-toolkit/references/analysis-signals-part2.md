# 技术指标与基本面信号计算模块 (第 2 部分)

## 4. 因子选股与多因子打分

### 4.1 因子预处理

```python
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
            return (factor - factor.mean()) / factor.std()
        elif method == 'rank':
            return factor.rank(pct=True) * 2 - 1  # [-1, 1]
        elif method == 'minmax':
            return (factor - factor.min()) / (factor.max() - factor.min()) * 2 - 1
        return factor
    
    @staticmethod
    def neutralize(factor: pd.Series, 
                   market_cap: pd.Series = None,
                   industry: pd.Series = None) -> pd.Series:
        """因子中性化: 回归残差法
        factor = alpha + beta1*ln(mcap) + beta2*industry_dummy + epsilon
        返回 epsilon
        """
        import statsmodels.api as sm
        
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
        
        # 仅对有效样本回归
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
        if method == 'gram_schmidt':
            # 简化：逐个因子正交化
            result = factors.copy()
            for i, col in enumerate(factors.columns):
                if i == 0:
                    continue
                # 对前 i 个因子回归，取残差
                X = sm.add_constant(result.iloc[:, :i])
                y = factors[col]
                valid = y.notna() & X.notna().all(axis=1)
                if valid.sum() > 10:
                    model = sm.OLS(y[valid], X[valid]).fit()
                    result.loc[valid, col] = model.resid
            return result
        elif method == 'pca':
            from sklearn.decomposition import PCA
            pca = PCA()
            valid = factors.notna().all(axis=1)
            transformed = pca.fit_transform(factors[valid])
            result = pd.DataFrame(transformed, index=factors[valid].index,
                                  columns=[f'PC{i+1}' for i in range(factors.shape[1])])
            return result
```

### 4.2 多因子打分模型

```python
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
            ic = self.factors[factor].groupby(level='date').corr(
                self.returns, method=method
            )
            ic_series[factor] = ic
        return pd.DataFrame(ic_series)
    
    def ic_weighted_score(self, ic_window: int = 252) -> pd.Series:
        """IC 加权打分: 权重 = 近期 IC 均值 / IC 标准差 (IR)"""
        ic_df = self.calc_ic()
        
        # 计算每日权重
        weights = {}
        for date in self.factors.index.get_level_values('date').unique():
            hist_ic = ic_df.loc[:date].tail(ic_window)
            if len(hist_ic) < 20:
                continue
            ir = hist_ic.mean() / hist_ic.std()
            ir = ir.clip(lower=0)  # 负 IR 置 0
            weights[date] = ir / ir.sum() if ir.sum() > 0 else pd.Series(1/len(ir), index=ir.index)
        
        # 计算加权得分
        scores = []
        for date, weight in weights.items():
            factor_slice = self.factors.xs(date, level='date')
            score = (factor_slice * weight).sum(axis=1)
            scores.append(score.rename(date))
        
        return pd.concat(scores).rename('ic_weighted_score')
    
    def equal_weight_score(self) -> pd.Series:
        """等权打分 (标准化后求和)"""
        standardized = self.factors.groupby(level='date').apply(
            lambda x: x.apply(FactorProcessor.standardize)
        )
        return standardized.sum(axis=1).rename('equal_weight_score')
    
    def ml_score(self, model_type: str = 'lightgbm') -> pd.Series:
        """机器学习模型打分 (LightGBM / XGBoost / Ridge)"""
        from sklearn.model_selection import TimeSeriesSplit
        import lightgbm as lgb
        
        # 准备训练数据
        data = self.factors.join(self.returns.rename('target')).dropna()
        X = data.drop('target', axis=1)
        y = data['target']
        
        # 时间序列切分
        tscv = TimeSeriesSplit(n_splits=5)
        scores = []
        
        for train_idx, test_idx in tscv.split(X):
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
            
            if model_type == 'lightgbm':
                model = lgb.LGBMRegressor(n_estimators=200, max_depth=5, 
                                          learning_rate=0.05, verbose=-1)
            elif model_type == 'ridge':
                from sklearn.linear_model import Ridge
                model = Ridge(alpha=1.0)
            
            model.fit(X_train, y_train)
            pred = model.predict(X_test)
            scores.append(pd.Series(pred, index=X_test.index, name='ml_score'))
        
        return pd.concat(scores).sort_index()
```

### 4.3 选股策略回测

```python
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
                        n_long: int = 50,
                        n_short: int = 50,
                        rebalance_freq: str = 'W') -> Dict:
        """多空策略回测"""
        # 调仓日
        rebalance_dates = self.scores.index.get_level_values('date').unique()
        rebalance_dates = pd.DatetimeIndex(rebalance_dates).to_period(rebalance_freq).drop_duplicates().to_timestamp()
        
        portfolio_returns = []
        positions = []
        
        for i, date in enumerate(rebalance_dates[:-1]):
            next_date = rebalance_dates[i + 1]
            
            # 当日得分
            daily_scores = self.scores.xs(date, level='date').dropna()
            
            # 选股
            long_stocks = daily_scores.nlargest(n_long).index.tolist()
            short_stocks = daily_scores.nsmallest(n_short).index.tolist()
            
            # 计算区间收益
            long_ret = self._calc_period_return(long_stocks, date, next_date)
            short_ret = self._calc_period_return(short_stocks, date, next_date)
            
            # 多空组合收益 (等权)
            port_ret = (long_ret - short_ret) / 2 - self.fee * 2  # 双边手续费
            portfolio_returns.append(port_ret)
            positions.append({'date': date, 'long': long_stocks, 'short': short_stocks})
        
        returns_series = pd.Series(portfolio_returns, index=rebalance_dates[:-1])
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
            except:
                rets.append(0)
        return np.mean(rets) if rets else 0
    
    def _calc_metrics(self, returns: pd.Series, positions: List) -> Dict:
        """计算绩效指标"""
        cum_ret = (1 + returns).prod() - 1
        ann_ret = (1 + returns.mean()) ** (252 / len(returns)) - 1 if len(returns) > 0 else 0
        ann_vol = returns.std() * np.sqrt(252 / len(returns)) if len(returns) > 0 else 0
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        max_dd = (returns.cumsum().expanding().max() - returns.cumsum()).max()
        
        return {
            'cumulative_return': cum_ret,
            'annualized_return': ann_ret,
            'annualized_volatility': ann_vol,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'win_rate': (returns > 0).mean(),
            'returns_series': returns,
            'positions': positions,
        }
```

## 5. 信号合成与交易规则

```python
class SignalGenerator:
    """综合信号生成器"""
    
    def __init__(self, quote: pd.DataFrame, factors: pd.DataFrame = None):
        self.quote = quote
        self.factors = factors
    
    def trend_following_signals(self) -> pd.DataFrame:
        """趋势跟踪信号: 均线多头排列 + MACD 金叉 + 价格站上布林带中轨"""
        close = self.quote['close']
        high = self.quote['high']
        low = self.quote['low']
        
        # 均线系统
        ma5 = TrendIndicators.sma(close, 5)
        ma20 = TrendIndicators.sma(close, 20)
        ma60 = TrendIndicators.sma(close, 60)
        
        # MACD
        macd_df = TrendIndicators.macd(close)
        macd_golden = (macd_df['DIF'] > macd_df['DEA']) & (macd_df['DIF'].shift(1) <= macd_df['DEA'].shift(1))
        macd_dead = (macd_df['DIF'] < macd_df['DEA']) & (macd_df['DIF'].shift(1) >= macd_df['DEA'].shift(1))
        
        # 布林带
        bb = VolatilityIndicators.bollinger(close)
        price_above_mid = close > bb['MIDDLE']
        
        # 综合信号
        signals = pd.DataFrame(index=close.index)
        signals['ma_bull'] = (ma5 > ma20) & (ma20 > ma60)
        signals['macd_golden'] = macd_golden
        signals['price_above_bb_mid'] = price_above_mid
        signals['trend_score'] = signals[['ma_bull', 'macd_golden', 'price_above_bb_mid']].sum(axis=1)
        signals['signal'] = np.where(signals['trend_score'] >= 2, 1,  # 买入
                            np.where(signals['trend_score'] <= 1, -1, 0))  # 卖出/持有
        return signals
    
    def mean_reversion_signals(self) -> pd.DataFrame:
        """均值回归信号: RSI 超卖 + 价格触及布林带下轨 + KDJ 金叉"""
        close = self.quote['close']
        high = self.quote['high']
        low = self.quote['low']
        
        rsi = MomentumIndicators.rsi(close)
        rsi_oversold = rsi < 30
        rsi_overbought = rsi > 70
        
        bb = VolatilityIndicators.bollinger(close)
        price_at_lower = close <= bb['LOWER'] * 1.01
        price_at_upper = close >= bb['UPPER'] * 0.99
        
        kdj = MomentumIndicators.stoch(high, low, close)
        kdj_golden = (kdj['K'] > kdj['D']) & (kdj['K'].shift(1) <= kdj['D'].shift(1)) & (kdj['K'] < 30)
        kdj_dead = (kdj['K'] < kdj['D']) & (kdj['K'].shift(1) >= kdj['D'].shift(1)) & (kdj['K'] > 70)
        
        signals = pd.DataFrame(index=close.index)
        signals['rsi_oversold'] = rsi_oversold
        signals['price_at_lower'] = price_at_lower
        signals['kdj_golden'] = kdj_golden
        signals['mr_buy_score'] = signals[['rsi_oversold', 'price_at_lower', 'kdj_golden']].sum(axis=1)
        
        signals['rsi_overbought'] = rsi_overbought
        signals['price_at_upper'] = price_at_upper
        signals['kdj_dead'] = kdj_dead
        signals['mr_sell_score'] = signals[['rsi_overbought', 'price_at_upper', 'kdj_dead']].sum(axis=1)
        
        signals['signal'] = np.where(signals['mr_buy_score'] >= 2, 1,
                            np.where(signals['mr_sell_score'] >= 2, -1, 0))
        return signals
    
    def breakout_signals(self) -> pd.DataFrame:
        """突破信号: 唐奇安通道突破 + 成交量放大 + 动量确认"""
        close = self.quote['close']
        high = self.quote['high']
        low = self.quote['low']
        volume = self.quote['volume']
        
        dc = VolatilityIndicators.donchian(high, low, 20)
        breakout_up = close > dc['UPPER'].shift(1)
        breakout_down = close < dc['LOWER'].shift(1)
        
        vol_ma = volume.rolling(20).mean()
        vol_surge = volume > vol_ma * 1.5
        
        mom = close.pct_change(10)
        mom_positive = mom > 0
        mom_negative = mom < 0
        
        signals = pd.DataFrame(index=close.index)
        signals['breakout_up'] = breakout_up
        signals['vol_surge'] = vol_surge
        signals['mom_positive'] = mom_positive
        signals['breakout_score'] = signals[['breakout_up', 'vol_surge', 'mom_positive']].sum(axis=1)
        
        signals['breakout_down'] = breakout_down
        signals['mom_negative'] = mom_negative
        signals['breakdown_score'] = signals[['breakout_down', 'vol_surge', 'mom_negative']].sum(axis=1)
        
        signals['signal'] = np.where(signals['breakout_score'] >= 2, 1,
                            np.where(signals['breakdown_score'] >= 2, -1, 0))
        return signals
    
    def composite_signals(self, weights: Dict = None) -> pd.DataFrame:
        """多策略组合信号"""
        weights = weights or {'trend': 0.4, 'mean_reversion': 0.3, 'breakout': 0.3}
        
        trend = self.trend_following_signals()['signal']
        mr = self.mean_reversion_signals()['signal']
        breakout = self.breakout_signals()['signal']
        
        composite = (trend * weights['trend'] + 
                    mr * weights['mean_reversion'] + 
                    breakout * weights['breakout'])
        
        signals = pd.DataFrame({'composite_score': composite}, index=close.index)
        signals['signal'] = np.where(composite > 0.3, 1,
                            np.where(composite < -0.3, -1, 0))
        return signals
```

## 6. 风险控制模块

```python
class RiskManager:
    """仓位管理、止损、风险预算"""
    
    def __init__(self, capital: float, max_position_pct: float = 0.1,
                 max_drawdown_pct: float = 0.15, var_confidence: float = 0.95):
        self.capital = capital
        self.max_position_pct = max_position_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.var_confidence = var_confidence
    
    def kelly_position(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Kelly 公式计算最优仓位"""
        if avg_loss == 0:
            return 0
        return (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
    
    def volatility_target_position(self, target_vol: float, current_vol: float,
                                    max_leverage: float = 1.0) -> float:
        """波动率目标仓位"""
        if current_vol == 0:
            return 0
        return min(target_vol / current_vol, max_leverage)
    
    def atr_stop_loss(self, entry_price: float, atr: float, multiplier: float = 2.0,
                       direction: str = 'long') -> float:
        """ATR 止损价"""
        if direction == 'long':
            return entry_price - multiplier * atr
        else:
            return entry_price + multiplier * atr
    
    def trailing_stop(self, entry_price: float, current_price: float,
                       atr: float, multiplier: float = 3.0) -> float:
        """移动止损 (基于 ATR)"""
        if current_price > entry_price:  # 多头
            return max(entry_price, current_price - multiplier * atr)
        else:  # 空头
            return min(entry_price, current_price + multiplier * atr)
    
    def var_limit(self, returns: pd.Series, position_value: float) -> float:
        """VaR 限额检查"""
        var = -returns.quantile(1 - self.var_confidence) * position_value
        max_var = self.capital * self.max_drawdown_pct
        return min(position_value, max_var / var * position_value) if var > 0 else position_value
```

## 7. 常用指标参数速查表

| 指标 | 默认参数 | 短线参数 | 中线参数 | 长线参数 | 典型用途 |
|------|----------|----------|----------|----------|----------|
| MA | 5,10,20,60 | 5,10 | 20,30 | 60,120,250 | 趋势判断、支撑阻力 |
| EMA | 12,26 | 5,13 | 12,26 | 50,200 | 趋势跟踪、MACD 组件 |
| MACD | 12,26,9 | 6,13,5 | 12,26,9 | 19,39,9 | 趋势转折、动量确认 |
| RSI | 14 | 7,9 | 14 | 21,28 | 超买超卖、背离 |
| KDJ | 9,3,3 | 5,3,3 | 9,3,3 | 14,3,3 | 短线买卖点 |
| BOLL | 20,2 | 10,1.5 | 20,2 | 50,2.5 | 波动率、突破、均值回归 |
| ATR | 14 | 7 | 14 | 21 | 止损、仓位控制 |
| ADX | 14 | 7 | 14 | 28 | 趋势强度过滤 |
| CCI | 20 | 10 | 20 | 40 | 周期性行情、背离 |
| OBV | - | - | - | - | 量价配合、主力动向 |
| MFI | 14 | 7 | 14 | 28 | 量价 RSI、资金流向 |

## 8. 因子库分类标准 (WorldQuant / Alpha158 风格)

```python
FACTOR_CATEGORIES = {
    'price': [
        'close', 'open', 'high', 'low', 'vwap',
        'return_1d', 'return_5d', 'return_20d',
        'log_return', 'overnight_return',
    ],
    'volume': [
        'volume', 'amount', 'turnover', 'turnover_rate',
        'volume_ma_5', 'volume_ma_20', 'volume_ratio',
        'obv', 'vwap_deviation',
    ],
    'technical': [
        'rsi_14', 'rsi_6', 'macd', 'macd_hist',
        'boll_upper', 'boll_lower', 'boll_width', 'boll_pct_b',
        'kdj_k', 'kdj_d', 'kdj_j',
        'cci_20', 'adx_14', 'atr_14',
        'ma_5', 'ma_10', 'ma_20', 'ma_60',
        'ma_bias_5', 'ma_bias_20',
    ],
    'fundamental': [
        'pe_ttm', 'pb', 'ps', 'pcf', 'dividend_yield',
        'roe', 'roa', 'roic', 'gross_margin', 'net_margin',
        'revenue_yoy', 'profit_yoy', 'eps_yoy',
        'debt_to_equity', 'current_ratio', 'interest_coverage',
        'fcf_yield', 'ev_ebitda',
    ],
    'money_flow': [
        'main_net_inflow', 'main_inflow_rate',
        'super_large_inflow', 'large_inflow',
        'smart_money_index', 'chip_profit_20', 'chip_profit_60',
    ],
    'alternative': [
        'sentiment_score', 'news_count', 'guba_heat',
        'analyst_rating', 'target_price_upside',
        'short_interest', 'insider_trading',
    ],
}
```

## 9. 使用示例：完整选股流程

```python
# 1. 准备数据
from finance_toolkit import create_scraper
from finance_toolkit.analysis import (
    TrendIndicators, MomentumIndicators, VolatilityIndicators,
    MoneyFlowAnalyzer, FundamentalFactors, MultiFactorModel,
    FactorBacktest, SignalGenerator, RiskManager
)

async def run_factor_investing():
    # 获取数据
    async with create_scraper('akshare') as scraper:
        symbols = await get_universe('hs300')  # 沪深300成分股
        
        # 行情数据
        quotes = {}
        klines = {}
        money_flows = {}
        financials = {}
        
        for sym in symbols:
            async for data in scraper.fetch([sym], 'quote'):
                quotes[sym] = data.payload
            async for data in scraper.fetch([sym], 'kline', period='daily', start='20230101'):
                klines[sym] = data.payload
            async for data in scraper.fetch([sym], 'money_flow'):
                money_flows[sym] = data.payload
            async for data in scraper.fetch([sym], 'financial'):
                financials[sym] = data.payload
    
    # 2. 计算技术指标
    all_factors = {}
    for sym in symbols:
        kline = klines[sym]
        factors = pd.DataFrame(index=kline.index)
        
        # 趋势
        factors['ma5'] = TrendIndicators.sma(kline['close'], 5)
        factors['ma20'] = TrendIndicators.sma(kline['close'], 20)
        factors['ma60'] = TrendIndicators.sma(kline['close'], 60)
        macd = TrendIndicators.macd(kline['close'])
        factors['macd_dif'] = macd['DIF']
        factors['macd_dea'] = macd['DEA']
        factors['macd_hist'] = macd['MACD']
        
        # 动量
        factors['rsi'] = MomentumIndicators.rsi(kline['close'])
        kdj = MomentumIndicators.stoch(kline['high'], kline['low'], kline['close'])
        factors['kdj_k'] = kdj['K']
        factors['kdj_d'] = kdj['D']
        factors['kdj_j'] = kdj['J']
        
        # 波动率
        bb = VolatilityIndicators.bollinger(kline['close'])
        factors['bb_upper'] = bb['UPPER']
        factors['bb_lower'] = bb['LOWER']
        factors['bb_width'] = bb['BANDWIDTH']
        factors['bb_pct_b'] = bb['PCT_B']
        
        # 资金流向
        if sym in money_flows:
            mf = MoneyFlowAnalyzer(kline, money_flows[sym])
            mf_signal = mf.main_force_signal()
            factors['main_inflow'] = mf_signal['main_inflow']
            factors['inflow_rate'] = mf_signal['inflow_rate']
        
        all_factors[sym] = factors
    
    # 3. 面板数据整理
    panel = pd.concat(all_factors, names=['symbol', 'date']).swaplevel().sort_index()
    
    # 4. 计算前瞻收益率 (未来 5 日)
    forward_ret = panel.groupby('symbol')['close'].pct_change(5).shift(-5)
    
    # 5. 多因子模型
    model = MultiFactorModel(panel.drop('close', axis=1), forward_ret)
    scores = model.ic_weighted_score()
    
    # 6. 回测
    prices_panel = pd.concat({s: k['close'] for s, k in klines.items()}, names=['symbol', 'date'])
    backtest = FactorBacktest(scores, prices_panel)
    results = backtest.run_long_short(n_long=30, n_short=30, rebalance_freq='W')
    
    print(f"年化收益: {results['annualized_return']:.2%}")
    print(f"夏普比率: {results['sharpe_ratio']:.2f}")
    print(f"最大回撤: {results['max_drawdown']:.2%}")
    
    # 7. 实盘信号生成
    latest_date = panel.index.get_level_values('date').max()
    latest_scores = scores.xs(latest_date, level='date')
    top_long = latest_scores.nlargest(10)
    top_short = latest_scores.nsmallest(10)
    
    print(f"\n做多标的: {top_long.index.tolist()}")
    print(f"做空标的: {top_short.index.tolist()}")
    
    return results
```