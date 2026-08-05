# browser-cdp searchers package
# 统一导出所有搜索器

from src.searchers.base import (
    SearcherConfig,
    SearchResult,
    SearchResults,
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

# ========== 搜索引擎 ==========
# baidu_search.py 和 bing_search.py 只有函数，没有类

# ========== 电商 ==========
from src.searchers.jd_search import JDSearcher
from src.searchers.pdd_search import PDDSearcher
from src.searchers.taobao_search import TaobaoSearcher

# ========== 新闻 ==========
from src.searchers.sina_news import SinaNewsSearcher
from src.searchers.cls_news import ClsNewsSearcher
from src.searchers.thp_news import THPNewsSearcher
from src.searchers.wangyi_news import WangyiNewsSearcher

# ========== 社交 ==========
from src.searchers.douban_search import DoubanSearcher
from src.searchers.weibo_search import WeiboSearcher
from src.searchers.xiaohongshu_search import XiaohongshuSearcher
# wechat_search.py 只有函数，没有类

# ========== 金融 ==========
from src.searchers.eastmoney_guba import EastmoneyGubaSearcher
from src.searchers.xueqiu_search import XueqiuSearcher

# ========== 学术 ==========
from src.searchers.scholar_search import ScholarSearcher
# arxiv_search.py 和 arxiv_multi_search.py 只有函数，没有类

# ========== 房产 ==========
from src.searchers.lianjia_search import LianjiaSearcher

# ========== 视频 ==========
from src.searchers.youku_search import YoukuSearcher, YoukuConfig
from src.searchers.bilibili_search import BilibiliSearcher

# ========== 招聘 ==========
from src.searchers.lagou_search import LagouSearcher, LagouConfig
from src.searchers.boss_zhipin_search import BossZhipinSearcher
from src.searchers.zhilian_search import ZhilianSearcher
from src.searchers.liepin_search import LiepinSearcher

# ========== 旅游 ==========
from src.searchers.ctrip_search import CtripSearcher
from src.searchers.fliggy_search import FliggySearcher
from src.searchers.qunar_search import QunarSearcher
from src.searchers.mafengwo_search import MafengwoSearcher

# ========== 房产 ==========
from src.searchers.anjuke_search import AnjukeSearcher

# ========== 视频/音乐 ==========
from src.searchers.douyin_search import DouyinSearcher
from src.searchers.kuaishou_search import KuaishouSearcher
from src.searchers.xigua_search import XiguaSearcher
from src.searchers.music163_search import Music163Searcher

# ========== 学术 ==========
from src.searchers.scholar_search import ScholarSearcher
from src.searchers.sematic_scholar_search import SematicScholarSearcher
from src.searchers.cnki_search import CnkSearcher

# ========== 旅游 ==========
from src.searchers.ctrip_search import CtripSearcher
from src.searchers.fliggy_search import FliggySearcher
from src.searchers.qunar_search import QunarSearcher
from src.searchers.mafengwo_search import MafengwoSearcher

# ========== 技术社区 ==========
from src.searchers.github_search import GitHubSearcher
from src.searchers.stackoverflow_search import StackOverflowSearcher

# ========== 天气 ==========
from src.searchers.weather_search import WeatherSearcher

# ========== 教育 ==========
from src.searchers.mooc_search import MoocSearcher

# ========== 知乎 ==========
# zhihu_search.py 只有函数，没有类
from src.searchers.zhihu_search_simple import ZhihuSearchSimple
# zhihu_search_with_login.py, zhihu_column_search.py, zhihu_hot.py, zhihu_publish_answer.py 只有函数，没有类

# ========== API 搜索 ==========
from src.searchers.api_searcher import RESTAPISearcher, GraphQLSearcher, APISearcherFactory, APIConfig
from src.searchers.realtime_searcher import StockSearcher, CryptoSearcher, NewsSearcher, RealtimeSearcherFactory, RealtimeDataConfig

__all__ = [
    # 基础类
    "SearcherConfig",
    "SearchResult",
    "SearchResults",
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
    # 电商
    "JDSearcher",
    "PDDSearcher",
    "TaobaoSearcher",
    # 新闻
    "SinaNewsSearcher",
    "ClsNewsSearcher",
    "THPNewsSearcher",
    "WangyiNewsSearcher",
    # 社交
    "DoubanSearcher",
    "WeiboSearcher",
    "XiaohongshuSearcher",
    # 金融
    "EastmoneyGubaSearcher",
    "XueqiuSearcher",
    # 学术
    "ScholarSearcher",
    # 房产
    "LianjiaSearcher",
    # 视频
    "YoukuSearcher",
    "YoukuConfig",
    "BilibiliSearcher",
    # 招聘
    "LagouSearcher",
    "LagouConfig",
    "BossZhipinSearcher",
    "ZhilianSearcher",
    "LiepinSearcher",
    # 旅游
    "CtripSearcher",
    "FliggySearcher",
    "QunarSearcher",
    "MafengwoSearcher",
    # 房产
    "AnjukeSearcher",
    # 视频/音乐
    "DouyinSearcher",
    "KuaishouSearcher",
    "XiguaSearcher",
    "Music163Searcher",
    # 学术
    "ScholarSearcher",
    "SematicScholarSearcher",
    "CnkSearcher",
    # 技术社区
    "GitHubSearcher",
    "StackOverflowSearcher",
    # 天气
    "WeatherSearcher",
    # 教育
    "MoocSearcher",
    # 知乎
    "ZhihuSearchSimple",
    # API 搜索
    "APISearcher",
    "RESTAPISearcher",
    "GraphQLSearcher",
    "APIConfig",
    "RealtimeSearcher",
    "StockSearcher",
    "CryptoSearcher",
    "NewsSearcher",
    "RealtimeDataConfig",
]
