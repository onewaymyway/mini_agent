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
from src.searchers.baidu_search import BaiduSearcher
from src.searchers.bing_search import BingSearcher
from src.searchers.sogou_search import SogouSearcher
from src.searchers.google_search import GoogleSearcher
from src.searchers.yahoo_search import YahooSearcher
from src.searchers.yandex_search import YandexSearcher
from src.searchers.duckduckgo_search import DuckDuckGoSearcher

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
from src.searchers.douban_movie_search import DoubanMovieSearcher
from src.searchers.douban_book_search import DoubanBookSearcher
from src.searchers.douban_music_search import DoubanMusicSearcher
from src.searchers.douban_event_search import DoubanEventSearcher
from src.searchers.douban_group_search import DoubanGroupSearcher
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
import importlib
FiveOneJobModule = importlib.import_module('src.searchers.51job_search')
FiveOneJobSearcher = FiveOneJobModule.FiveOneJobSearcher
FiveOneJobConfig = FiveOneJobModule.FiveOneJobConfig

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

# ========== 生活服务 ==========
from src.searchers.meituan_search import MeituanSearcher, MeituanConfig
from src.searchers.dianping_search import DianpingSearcher

# ========== 房产 ==========
from src.searchers.beike_search import BeikeSearcher, BeikeConfig

# ========== 海外平台 ==========
from src.searchers.reddit_search import RedditSearcher, RedditConfig
from src.searchers.amazon_search import AmazonSearcher

# ========== 国际视频/社交 ==========
from src.searchers.youtube_search import YouTubeSearcher
from src.searchers.twitter_search import TwitterSearcher
from src.searchers.linkedin_search import LinkedInSearcher

# ========== 国际新闻/学术 ==========
from src.searchers.bbc_news_search import BBCNewsSearcher
from src.searchers.pubmed_search import PubMedSearcher

# ========== 地图/百科/翻译 ==========
from src.searchers.google_maps_search import GoogleMapsSearcher
from src.searchers.wiki_search import WikipediaSearcher
from src.searchers.translate_search import TranslationSearcher

# ========== 学术 ==========
from src.searchers.sematic_scholar_search import SematicScholarSearcher
from src.searchers.cnki_search import CnkSearcher

# ========== 技术社区 ==========
from src.searchers.github_search import GitHubSearcher
from src.searchers.stackoverflow_search import StackOverflowSearcher

# ========== 政务 ==========
from src.searchers.gov_service_search import GovServiceSearcher
from src.searchers.stats_search import StatsSearcher

# ========== 政府服务 ==========
from src.searchers.gov_cn_search import GovCnSearcher
from src.searchers.court_search import CourtSearcher

# ========== 政务信用 ==========
from src.searchers.creditchina_search import CreditChinaSearcher
from src.searchers.gsxt_search import GSXTSearcher

# ========== 医疗 ==========
from src.searchers.medical_search import MedicalSearcher
from src.searchers.dxy_hospital_search import DXYHospitalSearcher
from src.searchers.hospital_search import HospitalSearcher

# ========== 法律 ==========
from src.searchers.legal_search import LegalSearcher
from src.searchers.law66_search import Law66Searcher
from src.searchers.huilv_search import HuilvSearcher

# ========== 体育 ==========
from src.searchers.sports_search import SportsSearcher
from src.searchers.hupu_search import HupuSearcher
from src.searchers.dongqiudi_search import DongqiudiSearcher

# ========== 美食 ==========
from src.searchers.food_search import FoodSearcher
from src.searchers.xiachufang_search import XiachufangSearcher
from src.searchers.meishi_search import MeishiSearcher

# ========== 二手交易 ==========
from src.searchers.zhuanzhuan_search import ZhuanZhuanSearcher

# ========== 音乐 ==========
from src.searchers.migu_search import MiguSearcher
from src.searchers.qq_music_search import QQMusicSearcher

# ========== 体育 ==========
from src.searchers.zhibo8_search import Zhibo8Searcher

# ========== 交通 ==========
from src.searchers.train_search import TrainSearcher

# ========== 医疗 ==========
from src.searchers.haodf_search import HaodfSearcher

# ========== 汽车 ==========
from src.searchers.autohome_search import AutohomeSearcher
from src.searchers.dongchedi_search import DongchediSearcher

# ========== 二手 ==========
from src.searchers.xianyu_search import XianyuSearcher

# ========== 天气 ==========
from src.searchers.weather_search import WeatherSearcher

# ========== 地图/出行 ==========
from src.searchers.amap_poi_search import AmapPOISearcher

# ========== 视频 ==========
from src.searchers.iqiyi_search import IqiyiSearcher
from src.searchers.tencent_video_search import TencentVideoSearcher

# ========== 社交/职场 ==========
from src.searchers.maimai_search import MaimaiSearcher

# ========== 资讯 ==========
from src.searchers.toutiao_search import ToutiaoSearcher

# ========== 音乐 ==========
from src.searchers.kugou_search import KugouSearcher
from src.searchers.kuwo_search import KuwoSearcher

# ========== 外卖 ==========
from src.searchers.eleme_search import ElemeSearcher
from src.searchers.meituan_waimai_search import MeituanWaimaiSearcher
from src.searchers.taobao_waimai_search import TaobaoWaimaiSearcher

# ========== 二手 ==========
from src.searchers.aihui_search import AihuiSearcher
from src.searchers.duozhuayu_search import DuozhuayuSearcher

# ========== 美食 ==========
from src.searchers.meishi_search import MeishiSearcher

# ========== 教育 ==========
from src.searchers.mooc_search import MoocSearcher
from src.searchers.xuetangx_search import XuetangxSearcher
from src.searchers.wangyi_open_search import WangyiOpenSearcher

# ========== 知乎 ==========
# zhihu_search.py 只有函数，没有类
from src.searchers.zhihu_search_simple import ZhihuSearchSimple
# zhihu_search_with_login.py, zhihu_column_search.py, zhihu_hot.py, zhihu_publish_answer.py 只有函数，没有类

# ========== API 搜索 ==========
from src.searchers.api_searcher import RESTAPISearcher, GraphQLSearcher, APISearcherFactory, APIConfig
from src.searchers.realtime_searcher import StockSearcher, CryptoSearcher, NewsSearcher, RealtimeSearcherFactory, RealtimeDataConfig

# ========== 搜索优化模块 ==========
from src.searchers.query_builder import QueryBuilder, QueryParams, build_query, expand_query, split_query
from src.searchers.pagination import (
    PaginationType,
    PaginationInfo,
    PageResult,
    PaginationDetector,
    PaginationHandler,
    detect_pagination,
    create_pagination_handler,
)
from src.searchers.result_parser import ResultParser, ParsedResult, parse_search_results, extract_page_metadata

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
    # 搜索引擎
    "BaiduSearcher",
    "BingSearcher",
    "SogouSearcher",
    "GoogleSearcher",
    "YahooSearcher",
    "YandexSearcher",
    "DuckDuckGoSearcher",
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
    "DoubanMovieSearcher",
    "DoubanBookSearcher",
    "DoubanMusicSearcher",
    "DoubanEventSearcher",
    "DoubanGroupSearcher",
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
    "FiveOneJobSearcher",
    "FiveOneJobConfig",
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
    # 生活服务
    "MeituanSearcher",
    "MeituanConfig",
    "DianpingSearcher",
    # 房产
    "BeikeSearcher",
    "BeikeConfig",
    # 海外平台
    "RedditSearcher",
    "RedditConfig",
    "AmazonSearcher",
    "AmazonConfig",
    # 国际视频/社交
    "YouTubeSearcher",
    "TwitterSearcher",
    "LinkedInSearcher",
    # 国际新闻/学术
    "BBCNewsSearcher",
    "PubMedSearcher",
    # 地图/百科/翻译
    "GoogleMapsSearcher",
    "WikipediaSearcher",
    "TranslationSearcher",
    # 学术
    "SematicScholarSearcher",
    "CnkSearcher",
    # 技术社区
    "GitHubSearcher",
    "StackOverflowSearcher",
    # 政府服务
    "GovServiceSearcher",
    "StatsSearcher",
    "GovCnSearcher",
    "CourtSearcher",
    # 政务信用
    "CreditChinaSearcher",
    "GSXTSearcher",
    # 法律
    "LegalSearcher",
    "Law66Searcher",
    "HuilvSearcher",
    # 医疗
    "MedicalSearcher",
    "DXYHospitalSearcher",
    "HospitalSearcher",
    "HaodfSearcher",
    # 汽车
    "AutohomeSearcher",
    "DongchediSearcher",
    # 二手
    "XianyuSearcher",
    "ZhuanZhuanSearcher",
    # 天气
    "WeatherSearcher",
    # 地图/出行
    "AmapPOISearcher",
    # 视频
    "IqiyiSearcher",
    "TencentVideoSearcher",
    # 社交/职场
    "MaimaiSearcher",
    # 资讯
    "ToutiaoSearcher",
    # 音乐
    "KugouSearcher",
    "KuwoSearcher",
    # 外卖
    "ElemeSearcher",
    "MeituanWaimaiSearcher",
    "TaobaoWaimaiSearcher",
    # 二手
    "AihuiSearcher",
    "DuozhuayuSearcher",
    # 美食
    "MeishiSearcher",
    # 体育
    "SportsSearcher",
    "HupuSearcher",
    "DongqiudiSearcher",
    # 美食
    "FoodSearcher",
    "XiachufangSearcher",
    "MeishiSearcher",
    # 音乐
    "MiguSearcher",
    "QQMusicSearcher",
    # 体育
    "Zhibo8Searcher",
    # 交通
    "TrainSearcher",
    # 教育
    "MoocSearcher",
    "XuetangxSearcher",
    "WangyiOpenSearcher",
    # 知乎
    "ZhihuSearchSimple",
    # API 搜索
    "RESTAPISearcher",
    "GraphQLSearcher",
    "APISearcherFactory",
    "APIConfig",
    "RealtimeSearcherFactory",
    "StockSearcher",
    "CryptoSearcher",
    "NewsSearcher",
    "RealtimeDataConfig",
    # 搜索优化模块
    "QueryBuilder",
    "QueryParams",
    "build_query",
    "expand_query",
    "split_query",
    "PaginationType",
    "PaginationInfo",
    "PageResult",
    "PaginationDetector",
    "PaginationHandler",
    "detect_pagination",
    "create_pagination_handler",
    "ResultParser",
    "ParsedResult",
    "parse_search_results",
    "extract_page_metadata",
]
