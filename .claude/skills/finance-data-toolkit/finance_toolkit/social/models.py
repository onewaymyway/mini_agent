"""
社交媒体数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any


class SocialSource(Enum):
    """社交媒体数据源"""
    WEIBO_HOT = "weibo_hot"          # 微博热搜
    XUEQIU_DISCUSSION = "xueqiu_discussion"  # 雪球讨论
    THS_WENCAI = "ths_wencai"        # 同花顺问财


class SocialCategory(Enum):
    """社交媒体内容分类"""
    HOT_TOPIC = "hot_topic"          # 热门话题/热搜
    STOCK_DISCUSSION = "stock_discussion"  # 个股讨论
    MARKET_SENTIMENT = "market_sentiment"  # 大盘情绪
    QA = "qa"                        # 问答/问财


@dataclass
class SocialPost:
    """标准化社交媒体帖子/热搜数据结构"""
    post_id: str                      # 唯一ID: source + 原始ID
    source: SocialSource              # 数据源
    category: SocialCategory          # 分类
    title: str                        # 标题/话题
    content: str                      # 正文内容
    url: str                          # 原文链接
    author: Optional[str] = None      # 作者/用户
    publish_time: datetime = field(default_factory=datetime.utcnow)  # 发布时间
    crawl_time: datetime = field(default_factory=datetime.utcnow)    # 抓取时间
    
    # 热度指标
    topic_heat: Optional[int] = None          # 话题热度/阅读数/讨论数
    like_count: Optional[int] = None          # 点赞数
    comment_count: Optional[int] = None       # 评论数
    repost_count: Optional[int] = None        # 转发数
    
    # 情感指标
    sentiment_score: Optional[float] = None   # 情感得分 [-1, 1]
    sentiment_label: Optional[str] = None     # 情感标签: positive/negative/neutral
    
    # 结构化标签
    symbols: List[str] = field(default_factory=list)      # 涉及标的: ['000001.SZ', '600000.SH']
    keywords: List[str] = field(default_factory=list)     # 关键词
    entities: List[Dict[str, Any]] = field(default_factory=list)  # 实体: [{type: 'company', name: '平安银行', code: '000001.SZ'}]
    
    # 原始数据 (调试用)
    raw: Optional[Dict] = None
    
    # 元信息
    meta: Optional[Dict] = None


@dataclass
class SocialAggregation:
    """股票/板块级舆情聚合结果"""
    symbol: str
    source: SocialSource
    total_posts: int
    avg_sentiment: float
    sentiment_distribution: Dict[str, int]  # {'positive': 10, 'negative': 5, 'neutral': 3}
    top_keywords: List[tuple]               # [(keyword, count), ...]
    heat_trend: List[Dict]                  # 热度趋势: [{time, heat}, ...]
    signal: Dict[str, Any]                  # 交易信号: {signal: 'bullish/bearish/neutral', strength: 0.7, reason: '...'}
    timestamp: datetime = field(default_factory=datetime.utcnow)
