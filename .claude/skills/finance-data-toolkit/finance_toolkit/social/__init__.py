# -*- coding: utf-8 -*-
"""
社交媒体/舆情数据抓取模块
覆盖：微博热搜、雪球讨论、同花顺问财
"""

from .models import (
    SocialSource,
    SocialCategory,
    SocialPost,
    SocialAggregation,
)

from .scrapers import (
    WeiboHotScraper,
    XueqiuDiscussionScraper,
    ThsWencaiScraper,
    fetch_weibo_hot,
    fetch_xueqiu_hot,
    fetch_ths_wencai_hot,
    fetch_all_social_hot,
)

__all__ = [
    # 模型
    'SocialSource',
    'SocialCategory',
    'SocialPost',
    'SocialAggregation',
    
    # 抓取器
    'WeiboHotScraper',
    'XueqiuDiscussionScraper',
    'ThsWencaiScraper',
    
    # 便捷函数
    'fetch_weibo_hot',
    'fetch_xueqiu_hot',
    'fetch_ths_wencai_hot',
    'fetch_all_social_hot',
]