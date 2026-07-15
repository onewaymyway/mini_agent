"""
L3 业务校验器
行情数据、财务数据、新闻数据的业务逻辑校验
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .pipeline import BaseCleaner, CleanLevel, CleanResult


class QuoteValidator(BaseCleaner):
    """L3: 行情数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['quote', 'kline']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 价格逻辑校验
        for field in ['open', 'high', 'low', 'close', 'price', 'pre_close']:
            val = payload.get(field)
            if val is not None and val <= 0:
                issues.append(f"{field} 必须 > 0: {val}")
        
        # 2. 高低价包含关系
        if all(payload.get(f) is not None for f in ['open', 'high', 'low', 'close']):
            o, h, l, c = payload['open'], payload['high'], payload['low'], payload['close']
            if not (l <= o <= h and l <= c <= h):
                issues.append(f"高低价包含关系异常: O={o} H={h} L={l} C={c}")
        
        # 3. 涨跌幅校验
        if payload.get('pre_close') and payload.get('close'):
            calc_pct = (payload['close'] - payload['pre_close']) / payload['pre_close'] * 100
            if payload.get('pct_chg') and abs(payload['pct_chg'] - calc_pct) > 0.02:
                warnings.append(f"涨跌幅不匹配: 字段={payload['pct_chg']:.2f}% 计算={calc_pct:.2f}%")
        
        # 4. 成交量/额非负
        for field in ['volume', 'amount']:
            if payload.get(field) is not None and payload[field] < 0:
                issues.append(f"{field} 不能为负: {payload[field]}")
        
        # 5. 换手率合理性 (0-100%)
        if payload.get('turnover_rate') is not None:
            if not (0 <= payload['turnover_rate'] <= 100):
                warnings.append(f"换手率异常: {payload['turnover_rate']}%")
        
        # 6. 振幅合理性
        if payload.get('amplitude') is not None:
            if not (0 <= payload['amplitude'] <= 100):
                warnings.append(f"振幅异常: {payload['amplitude']}%")
        
        # 7. 静默价格异动检测 (> 20% 或 < -20%)
        if payload.get('pre_close') and payload.get('close'):
            pct = (payload['close'] - payload['pre_close']) / payload['pre_close'] * 100
            if abs(pct) > 20:
                warnings.append(f"价格异动: {pct:.2f}% (可能除权/除息/数据错误)")
        
        # 8. 量价配合检查
        if payload.get('volume') is not None and payload.get('pct_chg') is not None:
            if payload['volume'] == 0 and abs(payload['pct_chg']) > 0.1:
                warnings.append(f"零成交量但有涨跌幅: {payload['pct_chg']}%")
        
        metrics = {
            'price_range': f"{payload.get('low', 'N/A')} - {payload.get('high', 'N/A')}",
            'pct_chg': payload.get('pct_chg'),
            'volume': payload.get('volume'),
            'turnover_rate': payload.get('turnover_rate'),
        }
        
        return CleanResult(
            data=raw_data,
            level=self.level,
            passed=len(issues) == 0,
            issues=issues + warnings,
            metrics=metrics
        )


class FinancialValidator(BaseCleaner):
    """L3: 财务数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['financial']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 报表日期合理性
        if payload.get('report_date'):
            rd = payload['report_date']
            if isinstance(rd, datetime):
                if rd > datetime.now(timezone.utc) + timedelta(days=30):
                    warnings.append(f"报告期在未来: {rd}")
                if rd.year < 2000:
                    issues.append(f"报告期异常: {rd}")
        
        # 2. 利润表恒等式校验 (允许 1% 误差)
        revenue = payload.get('revenue')
        net_profit = payload.get('net_profit')
        if revenue and net_profit and revenue != 0:
            margin = net_profit / revenue
            if margin > 1 or margin < -5:  # 净利率 > 100% 或 < -500%
                warnings.append(f"净利率异常: {margin*100:.1f}%")
        
        # 3. ROE 合理性
        roe = payload.get('roe')
        if roe is not None and (roe > 100 or roe < -100):
            warnings.append(f"ROE 异常: {roe}%")
        
        # 4. 同比/环比字段一致性
        for field in ['revenue_yoy', 'net_profit_yoy', 'eps_yoy', 'revenue_qoq', 'net_profit_qoq']:
            if payload.get(field) is not None and abs(payload[field]) > 1000:
                warnings.append(f"{field} 同比/环比异常: {payload[field]}%")
        
        # 5. 资产负债表平衡
        total_assets = payload.get('total_assets')
        total_liab = payload.get('total_liab')
        equity = payload.get('equity')
        if all(v is not None for v in [total_assets, total_liab, equity]):
            if abs(total_assets - (total_liab + equity)) / total_assets > 0.01:
                warnings.append(f"资产负债表不平衡: 资产={total_assets} 负债+权益={total_liab + equity}")
        
        # 6. 每股指标合理性
        eps = payload.get('eps')
        bps = payload.get('bps')  # 每股净资产
        if eps is not None and bps is not None and bps != 0:
            if eps / bps > 5:  # EPS/BPS > 5 异常
                warnings.append(f"EPS/BPS 比异常: {eps/bps:.2f}")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


class NewsValidator(BaseCleaner):
    """L3: 新闻/文本数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['news', 'guba', 'report']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 标题非空
        if not payload.get('title') or len(payload['title'].strip()) < 2:
            issues.append("标题为空或过短")
        
        # 2. 正文长度
        content = payload.get('content', '')
        if len(content) < 50:
            warnings.append(f"正文过短: {len(content)} 字符")
        if len(content) > 500000:
            warnings.append(f"正文过长: {len(content)} 字符，建议截断")
        
        # 3. 发布时间不晚于抓取时间
        pub_time = payload.get('publish_time')
        crawl_time = raw_data.get('crawl_time')
        if pub_time and crawl_time and pub_time > crawl_time + timedelta(hours=1):
            warnings.append(f"发布时间晚于抓取时间: {pub_time} > {crawl_time}")
        
        # 4. URL 格式
        url = payload.get('url', '')
        if url and not url.startswith(('http://', 'https://')):
            issues.append(f"URL 格式无效: {url}")
        
        # 5. 股票代码格式校验
        symbols = payload.get('symbols', [])
        for sym in symbols:
            if not re.match(r'^\d{6}\.(SZ|SH|BJ)$', sym) and not re.match(r'^[A-Z]{1,5}-?[A-Z]{0,5}$', sym):
                warnings.append(f"股票代码格式可疑: {sym}")
        
        # 6. 来源字段
        if not payload.get('author') and not payload.get('source'):
            warnings.append("缺少作者/来源信息")
        
        # 7. 关键词/实体
        if not payload.get('keywords') and not payload.get('entities'):
            warnings.append("缺少关键词/实体标注")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


class ReportValidator(BaseCleaner):
    """L3: 研报数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['report']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 标题
        if not payload.get('title') or len(payload['title'].strip()) < 5:
            issues.append("研报标题为空或过短")
        
        # 2. 机构/作者
        if not payload.get('institution') and not payload.get('author'):
            warnings.append("缺少机构/作者信息")
        
        # 3. 评级
        rating = payload.get('rating')
        valid_ratings = ['买入', '增持', '中性', '减持', '卖出', 'Buy', 'Hold', 'Sell', 'Overweight', 'Underweight']
        if rating and rating not in valid_ratings:
            warnings.append(f"评级格式非标准: {rating}")
        
        # 4. 目标价
        target_price = payload.get('target_price')
        if target_price is not None and target_price <= 0:
            issues.append(f"目标价异常: {target_price}")
        
        # 5. 发布日期
        pub_date = payload.get('publish_date')
        if pub_date and isinstance(pub_date, datetime):
            if pub_date > datetime.now(timezone.utc) + timedelta(days=1):
                warnings.append(f"发布日期在未来: {pub_date}")
        
        # 6. 正文长度
        content = payload.get('content', '')
        if len(content) < 200:
            warnings.append(f"研报正文过短: {len(content)} 字符")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


class GubaValidator(BaseCleaner):
    """L3: 股吧帖子校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['guba']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 标题
        if not payload.get('title') or len(payload['title'].strip()) < 2:
            issues.append("帖子标题为空或过短")
        
        # 2. 内容
        content = payload.get('content', '')
        if len(content) < 10:
            warnings.append(f"帖子内容过短: {len(content)} 字符")
        
        # 3. 阅读/评论数非负
        for field in ['read_count', 'comment_count']:
            if payload.get(field) is not None and payload[field] < 0:
                issues.append(f"{field} 不能为负: {payload[field]}")
        
        # 4. 用户信息
        if not payload.get('user_id') and not payload.get('user_name'):
            warnings.append("缺少用户信息")
        
        # 5. 发布时间
        pub_time = payload.get('publish_time')
        if pub_time and isinstance(pub_time, datetime):
            if pub_time > datetime.now(timezone.utc) + timedelta(hours=1):
                warnings.append(f"发布时间在未来: {pub_time}")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)