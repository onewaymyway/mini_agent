# browser-cdp searchers package
# 统一导出所有搜索器

from src.searchers.base import (
    SearcherConfig,
    SearchResult,
    BaseSearcher,
    AsyncBaseSearcher,
)

from src.searchers.utils import (
    random_delay,
    get_random_ua,
    get_ua_by_index,
    compute_simhash,
    hamming_distance,
    dedup_by_url,
    dedup_by_title,
    dedup_results,
    save_results,
    parse_pagination_url,
    extract_domain,
    clean_text,
    truncate_text,
)

# 新增：电商
from src.searchers.jd_search import JDSearcher
from src.searchers.pdd_search import PDDSearcher

# 新增：新闻
from src.searchers.sina_news import SinaNewsSearcher

# 新增：社交
from src.searchers.douban_search import DoubanSearcher

# 新增：金融
from src.searchers.eastmoney_guba import EastmoneyGubaSearcher
from src.searchers.xueqiu_search import XueqiuSearcher

# 新增：学术
from src.searchers.scholar_search import ScholarSearcher

# 新增：房产
from src.searchers.lianjia_search import LianjiaSearcher

# 新增：视频
from src.searchers.youku_search import YoukuSearcher, YoukuConfig

# 新增：新闻（财联社）
from src.searchers.cls_news import ClsNewsSearcher

# 新增：新闻（澎湃新闻）
from src.searchers.thp_news import THPNewsSearcher

# 新增：社交（微博）
from src.searchers.weibo_search import WeiboSearcher

# 新增：招聘（拉勾网）
from src.searchers.lagou_search import LagouSearcher, LagouConfig

# 新增：天气
from src.searchers.weather_search import WeatherSearcher

# 新增：教育（MOOC）
from src.searchers.mooc_search import MoocSearcher

__all__ = [
    # 基础类
    "SearcherConfig",
    "SearchResult",
    "BaseSearcher",
    "AsyncBaseSearcher",
    # 工具函数
    "random_delay",
    "get_random_ua",
    "get_ua_by_index",
    "compute_simhash",
    "hamming_distance",
    "dedup_by_url",
    "dedup_by_title",
    "dedup_results",
    "save_results",
    "parse_pagination_url",
    "extract_domain",
    "clean_text",
    "truncate_text",
    # 新增搜索器
    "JDSearcher",
    "PDDSearcher",
    "SinaNewsSearcher",
    "DoubanSearcher",
    "EastmoneyGubaSearcher",
    "ScholarSearcher",
    "XueqiuSearcher",
    "LianjiaSearcher",
    "ClsNewsSearcher",
    "YoukuSearcher",
    "YoukuConfig",
    "THPNewsSearcher",
    "WeiboSearcher",
    "LagouSearcher",
    "LagouConfig",
    "WeatherSearcher",
    "MoocSearcher",
]
