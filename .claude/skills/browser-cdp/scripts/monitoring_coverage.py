"""
监控覆盖率追踪器

提供监控覆盖率的计算和追踪，确保核心模块都被监控：
- 搜索器覆盖率
- 核心模块覆盖率
- 可靠性模块覆盖率
- 告警响应时间追踪
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class MonitoringCoverageTracker:
    """
    监控覆盖率追踪器。
    
    追踪哪些组件被监控，计算覆盖率，确保达到 90% 目标。
    """
    
    # 需要监控的核心组件定义
    CORE_COMPONENTS = {
        # 搜索器（按领域分类）
        "searchers": [
            # 搜索引擎
            "baidu_search", "bing_search", "arxiv_search", "arxiv_multi_search",
            # 新闻资讯
            "sina_news", "wangyi_news", "cls_news", "thp_news",
            # 社交媒体
            "zhihu_search", "weibo_search", "xiaohongshu_search", "reddit_search",
            # 电商购物
            "taobao_search", "jd_search", "pdd_search", "amazon_search", "xianyu_search",
            # 招聘求职
            "boss_zhipin_search", "lagou_search", "zhilian_search", "liepin_search", "51job_search",
            # 房产家居
            "lianjia_search", "anjuke_search", "beike_search",
            # 旅游出行
            "ctrip_search", "fliggy_search", "qunar_search", "mafengwo_search",
            # 生活服务
            "meituan_search", "dianping_search", "amap_poi_search",
            # 学术教育
            "cnki_search", "xuetangx_search", "mooc_search", "wangyi_open_search",
            # 金融投资
            "xueqiu_search", "eastmoney_guba",
            # 医疗健康
            "haodf_search", "dxy_hospital_search", "hospital_search",
            # 法律政务
            "gov_cn_search", "court_search", "creditchina_search", "gsxt_search",
            # 汽车交通
            "autohome_search", "dongchedi_search", "train_search",
            # 视频音乐
            "bilibili_search", "youku_search", "xigua_search", "douyin_search", "kuaishou_search", "music163_search",
            # 技术社区
            "github_search", "stackoverflow_search",
            # 体育健康
            "hupu_search", "dongqiudi_search",
            # 美食生活
            "xiachufang_search", "food_search",
            # 天气查询
            "weather_search",
            # 二手交易
            "zhuanzhuan_search",
        ],
        # 核心模块
        "core_modules": [
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
        ],
        # 可靠性模块
        "reliability_modules": [
            "alert", "dashboard", "error", "health", "log_query",
            "logging", "metrics", "middleware", "retry", "searcher_utils", "wait",
        ],
        # 评估器
        "evaluators": [
            "anti_detection_evaluator", "base_evaluator", "data_quality_evaluator",
            "element_evaluator", "error_recovery_evaluator", "performance_evaluator",
            "report_generator", "stability_evaluator", "success_rate_evaluator",
            "test_evaluator", "website_evaluator",
        ],
    }
    
    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 监控状态
        self._monitored: Dict[str, Set[str]] = {k: set() for k in self.CORE_COMPONENTS}
        self._unmonitored: Dict[str, Set[str]] = {k: set() for k in self.CORE_COMPONENTS}
        
        # 告警响应时间
        self._alert_response_times: List[float] = []
        self._max_response_times = 1000
        
        # 覆盖率历史
        self._coverage_history: List[Dict[str, Any]] = []
        self._max_history = 100
        
        # 加载历史数据
        self._load_history()
    
    def mark_monitored(self, component_type: str, component_id: str):
        """标记组件已监控"""
        if component_type in self._monitored:
            self._monitored[component_type].add(component_id)
            if component_id in self._unmonitored.get(component_type, set()):
                self._unmonitored[component_type].discard(component_id)
    
    def mark_unmonitored(self, component_type: str, component_id: str):
        """标记组件未监控"""
        if component_type in self._unmonitored:
            self._unmonitored[component_type].add(component_id)
            if component_id in self._monitored.get(component_type, set()):
                self._monitored[component_type].discard(component_id)
    
    def track_alert_response(self, response_time_seconds: float):
        """追踪告警响应时间"""
        self._alert_response_times.append(response_time_seconds)
        if len(self._alert_response_times) > self._max_response_times:
            self._alert_response_times = self._alert_response_times[-self._max_response_times:]
    
    def get_coverage_report(self) -> Dict[str, Any]:
        """获取覆盖率报告"""
        total_components = 0
        monitored_components = 0
        by_type: Dict[str, Dict[str, Any]] = {}
        
        for comp_type, all_components in self.CORE_COMPONENTS.items():
            type_total = len(all_components)
            type_monitored = len(self._monitored.get(comp_type, set()))
            type_unmonitored = len(self._unmonitored.get(comp_type, set()))
            
            total_components += type_total
            monitored_components += type_monitored
            
            by_type[comp_type] = {
                "total": type_total,
                "monitored": type_monitored,
                "unmonitored": type_unmonitored,
                "coverage_rate": round(type_monitored / type_total, 4) if type_total > 0 else 0,
            }
        
        overall_rate = monitored_components / total_components if total_components > 0 else 0
        
        # 告警响应时间统计
        response_stats = self._get_response_stats()
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "overall_coverage_rate": round(overall_rate, 4),
            "total_components": total_components,
            "monitored_components": monitored_components,
            "unmonitored_components": total_components - monitored_components,
            "target_coverage_rate": 0.9,
            "target_met": overall_rate >= 0.9,
            "by_type": by_type,
            "alert_response": response_stats,
        }
        
        # 记录历史
        self._coverage_history.append(report)
        if len(self._coverage_history) > self._max_history:
            self._coverage_history = self._coverage_history[-self._max_history:]
        
        return report
    
    def _get_response_stats(self) -> Dict[str, Any]:
        """获取告警响应时间统计"""
        if not self._alert_response_times:
            return {
                "avg_response_time": 0,
                "max_response_time": 0,
                "min_response_time": 0,
                "p50_response_time": 0,
                "p95_response_time": 0,
                "within_5min_rate": 0,
                "total_responded": 0,
            }
        
        sorted_times = sorted(self._alert_response_times)
        p50_idx = int(len(sorted_times) * 0.5)
        p95_idx = int(len(sorted_times) * 0.95)
        within_5min = sum(1 for t in sorted_times if t <= 300) / len(sorted_times)
        
        return {
            "avg_response_time": round(sum(sorted_times) / len(sorted_times), 2),
            "max_response_time": round(max(sorted_times), 2),
            "min_response_time": round(min(sorted_times), 2),
            "p50_response_time": round(sorted_times[p50_idx], 2),
            "p95_response_time": round(sorted_times[min(p95_idx, len(sorted_times) - 1)], 2),
            "within_5min_rate": round(within_5min, 4),
            "total_responded": len(sorted_times),
            "target_met": within_5min >= 0.9,
        }
    
    def get_unmonitored_list(self) -> Dict[str, List[str]]:
        """获取未监控组件列表"""
        result = {}
        for comp_type, components in self._unmonitored.items():
            if components:
                result[comp_type] = sorted(components)
        return result
    
    def _load_history(self):
        """加载历史数据"""
        history_file = self.data_dir / "monitoring_coverage_history.json"
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    self._coverage_history = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load coverage history: {e}")
    
    def save_report(self, output_path: Optional[str] = None):
        """保存覆盖率报告"""
        report = self.get_coverage_report()
        
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.data_dir / f"monitoring_coverage_{timestamp}.json")
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        # 保存历史
        history_file = self.data_dir / "monitoring_coverage_history.json"
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(self._coverage_history, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Coverage report saved to {output_path}")
        return report


# 全局追踪器实例
_global_coverage_tracker: Optional[MonitoringCoverageTracker] = None


def get_coverage_tracker() -> MonitoringCoverageTracker:
    """获取覆盖率追踪器实例"""
    global _global_coverage_tracker
    if _global_coverage_tracker is None:
        _global_coverage_tracker = MonitoringCoverageTracker()
    return _global_coverage_tracker


def reset_coverage_tracker():
    """重置全局覆盖率追踪器"""
    global _global_coverage_tracker
    _global_coverage_tracker = None
