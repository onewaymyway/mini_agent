# -*- coding: utf-8 -*-
"""
Phase 1 P0 站点抓取配置

定义十个 P0 站点的完整配置信息，包括：
- 基本信息（URL、搜索入口、等待选择器）
- 反检测配置
- 结果提取规则
- 重试策略
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class AntiDetectionLevel(Enum):
    """反检测级别"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    STEALTH = "stealth"


class WaitStrategy(Enum):
    """等待策略"""
    DOM_CONTENT_LOADED = "domcontentloaded"
    LOAD = "load"
    NETWORKIDLE = "networkidle"
    SMART_WAIT = "smart_wait"


@dataclass
class SiteConfig:
    """站点通用配置"""
    # 基本信息
    site_id: str
    site_name: str
    base_url: str
    search_url_template: str
    
    # 搜索相关
    search_selector: str = ""
    search_input_selector: str = ""
    search_submit_selector: str = ""
    result_list_selector: str = ""
    result_item_selector: str = ""
    
    # 等待配置
    wait_selector: str = ""
    wait_timeout: int = 30
    wait_strategy: WaitStrategy = WaitStrategy.SMART_WAIT
    
    # 反检测配置
    anti_detection_level: AntiDetectionLevel = AntiDetectionLevel.MEDIUM
    enable_stealth: bool = True
    random_delay_range: tuple = (1.0, 3.0)
    
    # 结果提取配置
    extract_fields: Dict[str, str] = field(default_factory=dict)
    js_extractor: Optional[str] = None
    font_encryption: bool = False  # 是否启用字体加密处理
    
    # 重试配置
    max_retries: int = 3
    retry_delay: float = 2.0
    
    # 性能配置
    concurrency: int = 3
    rate_limit: float = 1.0  # 每秒最大请求数
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "site_name": self.site_name,
            "base_url": self.base_url,
            "search_url_template": self.search_url_template,
            "wait_timeout": self.wait_timeout,
            "anti_detection_level": self.anti_detection_level.value,
            "enable_stealth": self.enable_stealth,
            "max_retries": self.max_retries,
            "concurrency": self.concurrency,
        }


# ========== Phase 1 P0 站点配置 ==========

P0_SITE_CONFIGS: Dict[str, SiteConfig] = {
    # 1. 中国政府网
    "gov_cn": SiteConfig(
        site_id="gov_cn",
        site_name="中国政府网",
        base_url="https://www.gov.cn",
        search_url_template="https://www.gov.cn/zhengce/zhengceku/search.html?keyword={keyword}",
        search_selector=".search-input, input[type='search'], #searchInput",
        search_input_selector=".search-input, input[type='search']",
        search_submit_selector=".search-btn, button[type='submit']",
        result_list_selector=".search-result, .result-list, .news-list",
        result_item_selector=".item, .result-item, .news-item",
        wait_selector=".search-result .item, .result-list .item",
        wait_timeout=30,
        anti_detection_level=AntiDetectionLevel.LOW,
        enable_stealth=True,
        extract_fields={
            "title": ".title, h3, h4, a",
            "url": "a[href]",
            "date": ".date, .time, .publish-date",
            "source": ".source, .dept, .department",
        },
        max_retries=3,
        concurrency=2,
    ),
    
    # 2. 国家数据（国家统计局）
    "stats_gov_cn": SiteConfig(
        site_id="stats_gov_cn",
        site_name="国家数据",
        base_url="https://www.stats.gov.cn",
        search_url_template="https://www.stats.gov.cn/sj/zsk/{keyword}",
        search_selector=".search-box input, #searchKey",
        search_input_selector=".search-box input",
        search_submit_selector=".search-box button, .search-btn",
        result_list_selector=".search-result, .data-list, .result-list",
        result_item_selector=".item, .data-item",
        wait_selector=".data-list .item, .result-list .item",
        wait_timeout=25,
        anti_detection_level=AntiDetectionLevel.LOW,
        enable_stealth=True,
        extract_fields={
            "title": ".title, .name, a",
            "url": "a[href]",
            "date": ".date, .time",
            "value": ".value, .data-value",
        },
        max_retries=3,
        concurrency=2,
    ),
    
    # 3. 国家企业信用信息公示系统
    "gsxt_gov_cn": SiteConfig(
        site_id="gsxt_gov_cn",
        site_name="国家企业信用信息公示",
        base_url="https://www.gsxt.gov.cn",
        search_url_template="https://www.gsxt.gov.cn/corp-query-search-1.html?searchword={keyword}",
        search_selector="#searchword, input[name='searchword']",
        search_input_selector="#searchword",
        search_submit_selector="#searchbutton, button[type='submit']",
        result_list_selector=".search-result, .result-list, #searchResultList",
        result_item_selector=".item, .result-item, .search-result-item",
        wait_selector="#searchResultList .item, .search-result .item",
        wait_timeout=30,
        anti_detection_level=AntiDetectionLevel.HIGH,
        enable_stealth=True,
        extract_fields={
            "title": ".title, .name, .corp-name, a",
            "url": "a[href]",
            "credit_code": ".credit-code, .reg-no",
            "legal_person": ".legal-person, .法人",
            "status": ".status, .register-status",
        },
        max_retries=5,
        concurrency=1,
    ),
    
    # 4. BOSS直聘
    "boss_zhipin": SiteConfig(
        site_id="boss_zhipin",
        site_name="BOSS直聘",
        base_url="https://www.zhipin.com",
        search_url_template="https://www.zhipin.com/web/geek/job?query={keyword}&city={city}",
        search_selector=".search-input, #query",
        search_input_selector=".search-input, #query",
        search_submit_selector=".search-btn, button[type='submit']",
        result_list_selector=".job-card-list, .job-list",
        result_item_selector=".job-card, .job-item",
        wait_selector=".job-card-list .job-card, .job-list .job-item",
        wait_timeout=25,
        anti_detection_level=AntiDetectionLevel.HIGH,
        enable_stealth=True,
        font_encryption=True,
        extract_fields={
            "title": ".job-title, .job-name, .job-limit-title",
            "company": ".company-name, .company",
            "salary": ".salary, .job-salary",
            "location": ".job-area, .work-location",
            "experience": ".job-experience, .working-exp",
            "education": ".job-education, .edu-level",
            "tags": ".tag span, .job-tags span",
        },
        max_retries=5,
        concurrency=2,
    ),
    
    # 5. 前程无忧（51job）
    "51job": SiteConfig(
        site_id="51job",
        site_name="前程无忧",
        base_url="https://www.51job.com",
        search_url_template="https://search.51job.com/list/000000,000000,0000,00,9,99,{keyword},2,1.html",
        search_selector="#keyword, #searchtext",
        search_input_selector="#keyword, #searchtext",
        search_submit_selector="#searchbtn, button[type='submit']",
        result_list_selector="#resultList, .joblist",
        result_item_selector=".el, .job-item",
        wait_selector="#resultList .el, .joblist .job-item",
        wait_timeout=25,
        anti_detection_level=AntiDetectionLevel.MEDIUM,
        enable_stealth=True,
        extract_fields={
            "title": ".job-title, .t1 span, .job-name",
            "company": ".t2 a, .company-name",
            "salary": ".t4, .salary",
            "location": ".t3, .location",
            "experience": ".t5, .experience",
            "publish_date": ".t5, .date",
        },
        max_retries=3,
        concurrency=2,
    ),
    
    # 6. 拉勾网
    "lagou": SiteConfig(
        site_id="lagou",
        site_name="拉勾网",
        base_url="https://www.lagou.com",
        search_url_template="https://www.lagou.com/jobs/list_{keyword}.html",
        search_selector="#search_input, .search-input",
        search_input_selector="#search_input, .search-input input",
        search_submit_selector="#search_button, .search-btn",
        result_list_selector="#s_result_list, ..job-list-box",
        result_item_selector=".job-item, .(job-item-view|lg-mainbox)",
        wait_selector="#s_result_list .job-item, .job-list-box .job-item",
        wait_timeout=25,
        anti_detection_level=AntiDetectionLevel.HIGH,
        enable_stealth=True,
        extract_fields={
            "title": ".p_top .position-label, .job-title, h3",
            "company": ".company_name, .company-name",
            "salary": ".money, .salary",
            "location": ".work_addr a, .location",
            "experience": ".li_label, .tag span",
            "stage": ".finance-stage, .stage",
        },
        max_retries=5,
        concurrency=2,
    ),
    
    # 7. 京东
    "jd_com": SiteConfig(
        site_id="jd_com",
        site_name="京东",
        base_url="https://www.jd.com",
        search_url_template="https://search.jd.com/Search?keyword={keyword}&enc=utf-8",
        search_selector="#key, .search-input",
        search_input_selector="#key, .search-input input",
        search_submit_selector=".search-btn, button[type='submit']",
        result_list_selector="#J_goodsList, .goods-list",
        result_item_selector=".gl-item, .goods-item",
        wait_selector="#J_goodsList .gl-item, .goods-list .goods-item",
        wait_timeout=20,
        anti_detection_level=AntiDetectionLevel.MEDIUM,
        enable_stealth=True,
        extract_fields={
            "title": ".gl-item .p-name em, .product-title",
            "price": ".p-price strong, .price",
            "rating": ".p-rating em, .rating",
            "shop": ".p-shop a, .shop-name",
            "sales": ".p-commit a, .sales",
            "image": ".gl-img img[data-lazy-img], .product-image",
        },
        max_retries=3,
        concurrency=3,
    ),
    
    # 8. 财联社
    "cls_cn": SiteConfig(
        site_id="cls_cn",
        site_name="财联社",
        base_url="https://www.cls.cn",
        search_url_template="https://www.cls.cn/searchPage?type=post&keyword={keyword}",
        search_selector=".search-input, input[type='search']",
        search_input_selector=".search-input, input[type='search']",
        search_submit_selector=".search-btn, button[type='submit']",
        result_list_selector=".search-result-list, .news-list",
        result_item_selector=".search-result-item, .news-item",
        wait_selector=".search-result-list .search-result-item, .news-list .news-item",
        wait_timeout=20,
        anti_detection_level=AntiDetectionLevel.LOW,
        enable_stealth=True,
        extract_fields={
            "title": ".title, .news-title, h3, h4",
            "url": "a[href]",
            "time": ".time, .publish-time, .date",
            "source": ".source, .origin",
            "summary": ".summary, .abstract, .desc",
        },
        max_retries=3,
        concurrency=3,
    ),
    
    # 9. 知乎
    "zhihu": SiteConfig(
        site_id="zhihu",
        site_name="知乎",
        base_url="https://www.zhihu.com",
        search_url_template="https://www.zhihu.com/search?type=content&q={keyword}",
        search_selector=".SearchBox-input, input[type='search']",
        search_input_selector=".SearchBox-input, input[type='search']",
        search_submit_selector=".SearchBox-submit, button[type='submit']",
        result_list_selector=".SearchResult-content, .result-list",
        result_item_selector=".SearchResult-item, .result-item",
        wait_selector=".SearchResult-content .SearchResult-item, .result-list .result-item",
        wait_timeout=25,
        anti_detection_level=AntiDetectionLevel.HIGH,
        enable_stealth=True,
        extract_fields={
            "title": ".RichContent-title, .content, .title, h2, h3",
            "url": "a[href]",
            "author": ".AuthorInfo-name, .author-name, a[href*='/people/']",
            "votes": ".VoteButton, .vote-count, .number",
            "comments": ".CommentButton, .comment-count",
            "excerpt": ".RichContent-excerpt, .excerpt, .content-excerpt",
        },
        max_retries=5,
        concurrency=2,
    ),
    
    # 10. 百度健康
    "baidu_health": SiteConfig(
        site_id="baidu_health",
        site_name="百度健康",
        base_url="https://health.baidu.com",
        search_url_template="https://health.baidu.com/m/detail/{keyword}",
        search_selector="#kw, .search-input",
        search_input_selector="#kw, .search-input input",
        search_submit_selector="#search, .search-btn",
        result_list_selector=".result-list, .health-results",
        result_item_selector=".result-item, .health-item",
        wait_selector=".result-list .result-item, .health-results .health-item",
        wait_timeout=20,
        anti_detection_level=AntiDetectionLevel.LOW,
        enable_stealth=False,
        extract_fields={
            "title": ".title, h3, h4, .article-title",
            "url": "a[href]",
            "source": ".source, .article-source",
            "summary": ".summary, .abstract, .description",
            "views": ".views, .view-count",
            "date": ".date, .publish-date",
        },
        max_retries=3,
        concurrency=3,
    ),
}


def get_site_config(site_id: str) -> Optional[SiteConfig]:
    """根据站点ID获取配置"""
    return P0_SITE_CONFIGS.get(site_id)


def get_all_p0_configs() -> List[SiteConfig]:
    """获取所有P0站点配置"""
    return list(P0_SITE_CONFIGS.values())


def get_config_by_keyword(keyword: str) -> List[SiteConfig]:
    """根据关键词匹配适用站点配置"""
    keywords_map = {
        "gov": ["gov_cn", "stats_gov_cn", "gsxt_gov_cn"],
        "job": ["boss_zhipin", "51job", "lagou"],
        "shop": ["jd_com"],
        "news": ["cls_cn", "baidu_health"],
        "knowledge": ["zhihu"],
    }
    
    results = []
    for key, site_ids in keywords_map.items():
        if key in keyword.lower():
            results.extend([P0_SITE_CONFIGS[sid] for sid in site_ids if sid in P0_SITE_CONFIGS])
    
    return results if results else list(P0_SITE_CONFIGS.values())
