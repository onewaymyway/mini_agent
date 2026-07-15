"""
新闻数据模型定义
统一的财经新闻数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum
import hashlib


class NewsSource(Enum):
    """新闻数据源枚举"""
    SINA = "sina"              # 新浪财经
    THS = "ths"                # 同花顺
    XUEQIU = "xueqiu"          # 雪球
    WALLSTREETCN = "wallstreetcn"  # 华尔街见闻
    CLS = "cls"                # 财联社
    BLOOMBERG = "bloomberg"    # 彭博
    REUTERS = "reuters"        # 路透
    WECHAT = "wechat"          # 微信公众号
    ARXIV = "arxiv"            # arXiv 学术预印本
    REGULATOR = "regulator"    # 监管公告


class NewsCategory(Enum):
    """新闻分类枚举"""
    MARKET = "market"           # 大盘行情
    STOCK = "stock"             # 个股消息
    INDUSTRY = "industry"       # 行业动态
    MACRO = "macro"             # 宏观政策
    FINANCIAL = "financial"     # 财报业绩
    RESEARCH = "research"       # 研报观点
    BLOCKCHAIN = "blockchain"   # 加密货币
    ACADEMIC = "academic"       # 学术前沿


@dataclass
class FinanceNews:
    """统一财经新闻数据结构"""
    news_id: str                    # 唯一ID (source + 原始ID)
    source: NewsSource
    category: NewsCategory
    title: str
    summary: str                    # 摘要/导语
    content: str                    # 正文 (HTML/Markdown/纯文本)
    url: str                        # 原文链接
    author: Optional[str] = None
    publish_time: Optional[datetime] = None
    crawl_time: datetime = field(default_factory=datetime.utcnow)
    
    # 结构化标签
    symbols: List[str] = field(default_factory=list)      # 涉及标的: ['000001.SZ', 'BTC-USDT']
    keywords: List[str] = field(default_factory=list)     # 关键词
    entities: List[dict] = field(default_factory=list)    # 实体: [{type: 'company', name: '平安银行', code: '000001.SZ'}]
    
    # 质量指标
    sentiment: Optional[float] = None     # 情感得分 [-1, 1]
    importance: Optional[int] = None      # 重要性 1-5
    credibility: Optional[float] = None   # 可信度 0-1
    
    # 多媒体
    images: List[str] = field(default_factory=list)
    videos: List[str] = field(default_factory=list)
    
    # 原始数据 (调试用)
    raw: Optional[dict] = None
    
    def to_dict(self) -> dict:
        """转换为字典，用于存储/序列化"""
        return {
            'news_id': self.news_id,
            'source': self.source.value,
            'category': self.category.value,
            'title': self.title,
            'summary': self.summary,
            'content': self.content,
            'url': self.url,
            'author': self.author,
            'publish_time': self.publish_time.isoformat() if self.publish_time else None,
            'crawl_time': self.crawl_time.isoformat(),
            'symbols': self.symbols,
            'keywords': self.keywords,
            'entities': self.entities,
            'sentiment': self.sentiment,
            'importance': self.importance,
            'credibility': self.credibility,
            'images': self.images,
            'videos': self.videos,
            'raw': self.raw,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'FinanceNews':
        """从字典创建实例"""
        data = data.copy()
        data['source'] = NewsSource(data['source'])
        data['category'] = NewsCategory(data['category'])
        if data.get('publish_time'):
            data['publish_time'] = datetime.fromisoformat(data['publish_time'])
        data['crawl_time'] = datetime.fromisoformat(data['crawl_time'])
        return cls(**data)
    
    def fingerprint(self) -> str:
        """生成内容指纹，用于去重"""
        text = (self.title + self.content[:500]).encode('utf-8')
        return hashlib.md5(text).hexdigest()
    
    def __hash__(self):
        return hash(self.news_id)
    
    def __eq__(self, other):
        if not isinstance(other, FinanceNews):
            return False
        return self.news_id == other.news_id