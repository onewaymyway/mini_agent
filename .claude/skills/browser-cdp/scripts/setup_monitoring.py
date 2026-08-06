"""
监控告警系统初始化脚本

用于初始化监控覆盖率追踪和告警响应系统。
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_monitoring():
    """初始化监控告警系统"""
    try:
        from .monitoring_system import get_monitoring_system
        from .monitoring_coverage import get_coverage_tracker
    except ImportError:
        from monitoring_system import get_monitoring_system
        from monitoring_coverage import get_coverage_tracker
    
    # 获取实例
    monitoring = get_monitoring_system()
    coverage = get_coverage_tracker()
    
    # 注册所有搜索器为监控组件
    searchers = [
        # 搜索引擎
        ("baidu_search", "搜索引擎"),
        ("bing_search", "搜索引擎"),
        ("arxiv_search", "学术搜索"),
        ("arxiv_multi_search", "学术搜索"),
        # 新闻资讯
        ("sina_news", "新闻资讯"),
        ("wangyi_news", "新闻资讯"),
        ("cls_news", "新闻资讯"),
        ("thp_news", "新闻资讯"),
        # 社交媒体
        ("zhihu_search", "社交媒体"),
        ("weibo_search", "社交媒体"),
        ("xiaohongshu_search", "社交媒体"),
        ("reddit_search", "社交媒体"),
        # 电商购物
        ("taobao_search", "电商购物"),
        ("jd_search", "电商购物"),
        ("pdd_search", "电商购物"),
        ("amazon_search", "电商购物"),
        ("xianyu_search", "电商购物"),
        # 招聘求职
        ("boss_zhipin_search", "招聘求职"),
        ("lagou_search", "招聘求职"),
        ("zhilian_search", "招聘求职"),
        ("liepin_search", "招聘求职"),
        ("51job_search", "招聘求职"),
        # 房产家居
        ("lianjia_search", "房产家居"),
        ("anjuke_search", "房产家居"),
        ("beike_search", "房产家居"),
        # 旅游出行
        ("ctrip_search", "旅游出行"),
        ("fliggy_search", "旅游出行"),
        ("qunar_search", "旅游出行"),
        ("mafengwo_search", "旅游出行"),
        # 生活服务
        ("meituan_search", "生活服务"),
        ("dianping_search", "生活服务"),
        ("amap_poi_search", "生活服务"),
        # 学术教育
        ("cnki_search", "学术教育"),
        ("xuetangx_search", "学术教育"),
        ("mooc_search", "学术教育"),
        ("wangyi_open_search", "学术教育"),
        # 金融投资
        ("xueqiu_search", "金融投资"),
        ("eastmoney_guba", "金融投资"),
        # 医疗健康
        ("haodf_search", "医疗健康"),
        ("dxy_hospital_search", "医疗健康"),
        ("hospital_search", "医疗健康"),
        # 法律政务
        ("gov_cn_search", "法律政务"),
        ("court_search", "法律政务"),
        ("creditchina_search", "法律政务"),
        ("gsxt_search", "法律政务"),
        # 汽车交通
        ("autohome_search", "汽车交通"),
        ("dongchedi_search", "汽车交通"),
        ("train_search", "汽车交通"),
        # 视频音乐
        ("bilibili_search", "视频音乐"),
        ("youku_search", "视频音乐"),
        ("xigua_search", "视频音乐"),
        ("douyin_search", "视频音乐"),
        ("kuaishou_search", "视频音乐"),
        ("music163_search", "视频音乐"),
        # 技术社区
        ("github_search", "技术社区"),
        ("stackoverflow_search", "技术社区"),
        # 体育健康
        ("hupu_search", "体育健康"),
        ("dongqiudi_search", "体育健康"),
        # 美食生活
        ("xiachufang_search", "美食生活"),
        ("food_search", "美食生活"),
        # 天气查询
        ("weather_search", "天气查询"),
        # 二手交易
        ("zhuanzhuan_search", "二手交易"),
    ]
    
    for searcher, category in searchers:
        coverage.mark_monitored("searchers", searcher)
    
    # 注册核心模块
    core_modules = [
        "browser_browse", "browser_console", "browser_download", "browser_extract",
        "browser_form", "browser_input", "browser_launch", "browser_nav",
        "browser_screenshot", "browser_tabs", "browser_watch",
        "captcha_handler", "cdp_client", "cdp_connection_pool",
        "cloudflare_bypass", "complex_dom", "dom_observer",
        "dynamic_loader", "dynamic_page_support", "enhanced_cdp_session",
        "enhanced_dynamic_loader", "oauth_handler", "playwright_session",
        "proxy_pool", "rate_limiter", "request_headers", "retry_handler",
        "smart_wait", "spa_detector", "stealth", "turnstile_handler",
        "utils", "virtual_list_loader",
    ]
    for module in core_modules:
        coverage.mark_monitored("core_modules", module)
    
    # 注册可靠性模块
    reliability_modules = [
        "alert", "dashboard", "error", "health", "log_query",
        "logging", "metrics", "middleware", "retry", "searcher_utils", "wait",
    ]
    for module in reliability_modules:
        coverage.mark_monitored("reliability_modules", module)
    
    # 注册评估器
    evaluators = [
        "anti_detection_evaluator", "base_evaluator", "data_quality_evaluator",
        "element_evaluator", "error_recovery_evaluator", "performance_evaluator",
        "report_generator", "stability_evaluator", "success_rate_evaluator",
        "test_evaluator", "website_evaluator",
    ]
    for evaluator in evaluators:
        coverage.mark_monitored("evaluators", evaluator)
    
    # 生成覆盖率报告
    report = coverage.save_report()
    
    # 设置初始指标
    monitoring.set_metric("success_rate", 0.765)
    monitoring.set_metric("error_rate", 0.235)
    monitoring.set_metric("retry_failure_rate", 0.86)
    monitoring.set_metric("monitoring_coverage_rate", report["overall_coverage_rate"])
    
    # 生成监控报告
    monitoring_report = monitoring.generate_report()
    
    logger.info("监控告警系统初始化完成")
    logger.info(f"监控覆盖率: {report['overall_coverage_rate']:.2%}")
    logger.info(f"告警响应时间 (平均): {monitoring_report['alert_response']['avg_response_time']:.1f}s")
    
    return monitoring_report


if __name__ == "__main__":
    report = setup_monitoring()
    print(json.dumps(report, indent=2, ensure_ascii=False))
