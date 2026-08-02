"""
实时新闻流式处理器
生产者-消费者模式：定时轮询 -> 情感分析 -> 实体识别 -> 告警 -> 入库
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Callable
from dataclasses import dataclass, field
from enum import Enum

from .models import FinanceNews, NewsSource
from .aggregator import NewsAggregator

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class NewsAlert:
    """新闻告警"""
    alert_id: str
    level: AlertLevel
    news: FinanceNews
    reason: str
    triggered_at: datetime = field(default_factory=datetime.utcnow)
    acknowledged: bool = False


class SentimentAnalyzer:
    """情感分析器 (简易版，可替换为 BERT/FinBERT)"""
    
    # 正面词汇
    POSITIVE_WORDS = {
        '上涨', '大涨', '暴涨', '涨停', '突破', '创新高', '利好', '看好', '买入', '增持',
        '超预期', '业绩增长', '盈利', '分红', '回购', '并购', '重组', '中标', '签约',
        '扩产', '产能释放', '需求旺盛', '政策支持', '利好政策', '降准', '降息',
        'bullish', 'surge', 'rally', 'breakthrough', 'record high', 'positive',
        'buy', 'upgrade', 'outperform', 'beat', 'exceed', 'growth', 'profit',
    }
    
    # 负面词汇
    NEGATIVE_WORDS = {
        '下跌', '大跌', '暴跌', '跌停', '破位', '创新低', '利空', '看空', '卖出', '减持',
        '不及预期', '业绩下滑', '亏损', '违约', '暴雷', '退市', 'ST', '风险', '调查',
        '处罚', '诉讼', '违规', '造假', '财务造假', '实控人变更', '质押', '平仓',
        'bearish', 'plunge', 'crash', 'breakdown', 'record low', 'negative',
        'sell', 'downgrade', 'underperform', 'miss', 'loss', 'default', 'risk',
    }
    
    def analyze(self, text: str) -> float:
        """分析情感得分 [-1, 1]"""
        if not text:
            return 0.0
        
        text_lower = text.lower()
        pos_count = sum(1 for w in self.POSITIVE_WORDS if w in text_lower)
        neg_count = sum(1 for w in self.NEGATIVE_WORDS if w in text_lower)
        
        total = pos_count + neg_count
        if total == 0:
            return 0.0
        
        score = (pos_count - neg_count) / total
        return max(-1.0, min(1.0, score))
    
    def extract_entities(self, text: str) -> List[Dict]:
        """简单实体识别 (股票代码、公司名)"""
        import re
        entities = []
        
        # 股票代码
        patterns = [
            (r'\b([036]\d{5})\b', 'stock_code'),
            (r'\b([SHSZ]\d{6})\b', 'stock_code'),
            (r'\b(\d{6}\.[SZSH])\b', 'stock_code'),
        ]
        for pat, etype in patterns:
            for match in re.finditer(pat, text, re.IGNORECASE):
                entities.append({
                    'type': etype,
                    'name': match.group(1).upper(),
                    'code': match.group(1).upper(),
                })
        
        # 简单公司名识别 (可扩展为 NER 模型)
        company_suffixes = ['集团', '股份', '有限公司', '科技', '银行', '保险', '证券', '基金']
        for suffix in company_suffixes:
            pattern = f'([\u4e00-\u9fa5]{{2,10}}{suffix})'
            for match in re.finditer(pattern, text):
                entities.append({
                    'type': 'company',
                    'name': match.group(1),
                })
        
        # 去重
        seen = set()
        unique = []
        for e in entities:
            key = (e['type'], e.get('name', e.get('code', '')))
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique


class NewsStreamProcessor:
    """实时新闻流式处理器"""
    
    def __init__(self,
        aggregator: NewsAggregator,
        sentiment_analyzer: SentimentAnalyzer = None,
        alert_webhooks: List[str] = None,
        alert_callback: Callable = None,
        store_callback: Callable = None,
        keywords: List[str] = None,
        sources: List[NewsSource] = None,
        interval: int = 60,
        since_hours: int = 1,
    ):
        """
        初始化流式处理器
        
        Args:
            aggregator: 新闻聚合器
            sentiment_analyzer: 情感分析器
            alert_webhooks: 告警 webhook URL 列表
            alert_callback: 自定义告警回调函数
            store_callback: 自定义入库回调函数
            keywords: 关键词过滤
            sources: 数据源过滤
            interval: 轮询间隔(秒)
            since_hours: 只抓取最近 N 小时
        """
        self.aggregator = aggregator
        self.sentiment_analyzer = sentiment_analyzer or SentimentAnalyzer()
        self.alert_webhooks = alert_webhooks or []
        self.alert_callback = alert_callback
        self.store_callback = store_callback
        self.keywords = keywords
        self.sources = sources
        self.interval = interval
        self.since_hours = since_hours
        
        self._running = False
        self._task = None
        self.stats = {
            'total_fetched': 0,
            'total_processed': 0,
            'total_alerts': 0,
            'total_stored': 0,
            'errors': 0,
            'last_run': None,
        }
    
    async def start(self):
        """启动流式处理"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("新闻流式处理器已启动")
    
    async def stop(self):
        """停止流式处理"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("新闻流式处理器已停止")
    
    async def _run_loop(self):
        """主循环"""
        while self._running:
            try:
                await self._process_once()
            except Exception as e:
                logger.error(f"流式处理异常: {e}")
                self.stats['errors'] += 1
            
            await asyncio.sleep(self.interval)
    
    async def _process_once(self):
        """单次处理周期"""
        # 1. 抓取新闻
        news_list = await self.aggregator.fetch_all(
            sources=self.sources,
            keywords=self.keywords,
            since_hours=self.since_hours,
        )
        
        self.stats['total_fetched'] += len(news_list)
        self.stats['last_run'] = datetime.utcnow()
        
        # 2. 处理每条新闻
        for news in news_list:
            try:
                # 情感分析
                news.sentiment = self.sentiment_analyzer.analyze(news.content or news.summary or news.title)
                
                # 实体识别
                text = ' '.join([news.title, news.summary, news.content[:2000]])
                news.entities = self.sentiment_analyzer.extract_entities(text)
                
                # 3. 告警判断
                await self._check_alerts(news)
                
                # 4. 入库
                if self.store_callback:
                    await self.store_callback(news)
                    self.stats['total_stored'] += 1
                
                self.stats['total_processed'] += 1
                
            except Exception as e:
                logger.error(f"处理新闻 {news.news_id} 失败: {e}")
                self.stats['errors'] += 1
    
    async def _check_alerts(self, news: FinanceNews):
        """检查是否触发告警"""
        alerts = []
        
        # 负面情感 + 高重要性
        if news.sentiment is not None and news.sentiment < -0.6 and (news.importance or 0) >= 3:
            alerts.append(NewsAlert(
                alert_id=f"alert_{news.news_id}",
                level=AlertLevel.CRITICAL,
                news=news,
                reason=f"强负面情感({news.sentiment:.2f}) + 高重要性({news.importance})",
            ))
        
        # 关键词触发
        if self.keywords:
            text = (news.title + ' ' + news.summary + ' ' + news.content).lower()
            for kw in self.keywords:
                if kw.lower() in text:
                    alerts.append(NewsAlert(
                        alert_id=f"alert_{news.news_id}_{kw}",
                        level=AlertLevel.WARNING,
                        news=news,
                        reason=f"命中关键词: {kw}",
                    ))
                    break
        
        # 发送告警
        for alert in alerts:
            await self._send_alert(alert)
            self.stats['total_alerts'] += 1
    
    async def _send_alert(self, alert: NewsAlert):
        """发送告警"""
        # 自定义回调
        if self.alert_callback:
            try:
                await self.alert_callback(alert)
            except Exception as e:
                logger.error(f"告警回调失败: {e}")
        
        # Webhook
        for webhook in self.alert_webhooks:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(webhook, json={
                        'alert_id': alert.alert_id,
                        'level': alert.level.value,
                        'news_id': alert.news.news_id,
                        'title': alert.news.title,
                        'source': alert.news.source.value,
                        'sentiment': alert.news.sentiment,
                        'importance': alert.news.importance,
                        'symbols': alert.news.symbols,
                        'reason': alert.reason,
                        'triggered_at': alert.triggered_at.isoformat(),
                        'url': alert.news.url,
                    })
            except Exception as e:
                logger.error(f"Webhook 告警发送失败 {webhook}: {e}")
    
    def get_stats(self) -> Dict:
        """获取运行统计"""
        return self.stats.copy()


async def run_news_monitor(
    keywords: List[str] = None,
    sources: List[NewsSource] = None,
    interval: int = 60,
    since_hours: int = 1,
    proxy: str = None,
    wallstreetcn_token: str = None,
    xueqiu_cookie: str = None,
    cdp_endpoint: str = 'http://127.0.0.1:9222',
    alert_webhooks: List[str] = None,
    alert_callback: Callable = None,
    store_callback: Callable = None,
    duration: int = None,
) -> Dict:
    """
    一键启动新闻监控 (便捷函数)
    
    Args:
        keywords: 关键词过滤
        sources: 数据源过滤
        interval: 轮询间隔(秒)
        since_hours: 只抓取最近 N 小时
        proxy: 代理
        wallstreetcn_token: 华尔街见闻 token
        xueqiu_cookie: 雪球 cookie
        cdp_endpoint: CDP 浏览器端点
        alert_webhooks: 告警 webhook 列表
        alert_callback: 自定义告警回调
        store_callback: 自定义入库回调
        duration: 运行时长(秒)，None 表示一直运行
    
    Returns:
        运行统计字典
    """
    from .aggregator import NewsAggregatorBuilder
    
    # 创建聚合器
    aggregator = NewsAggregatorBuilder.create_full(
        proxy=proxy,
        wallstreetcn_token=wallstreetcn_token,
        xueqiu_cookie=xueqiu_cookie,
        cdp_endpoint=cdp_endpoint,
    )
    
    # 创建处理器
    processor = NewsStreamProcessor(
        aggregator=aggregator,
        alert_webhooks=alert_webhooks,
        alert_callback=alert_callback,
        store_callback=store_callback,
        keywords=keywords,
        sources=sources,
        interval=interval,
        since_hours=since_hours,
    )
    
    try:
        await processor.start()
        
        if duration:
            await asyncio.sleep(duration)
        else:
            # 一直运行直到被取消
            while True:
                await asyncio.sleep(3600)
    
    except asyncio.CancelledError:
        pass
    finally:
        await processor.stop()
        await NewsAggregatorBuilder.close_all(aggregator)
    
    return processor.get_stats()