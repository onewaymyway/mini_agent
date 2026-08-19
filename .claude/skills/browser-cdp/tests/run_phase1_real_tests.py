# -*- coding: utf-8 -*-
"""
Phase1 真实浏览器测试执行脚本（异步版本）

集成 PlaywrightSession，执行十个 P0 站点的实际搜索测试。
每个站点独立使用一个浏览器实例，避免并发 goto 冲突。
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / 'src'))

from fixtures.test_config_loader import load_test_config
from core.playwright_session import PlaywrightSession, PlaywrightConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "logs" / "phase1_real_tests.log", encoding='utf-8'),
    ]
)
logger = logging.getLogger(__name__)


PHASE1_SITES = [
    {"site_id": "gov_cn", "name": "中国政府网", "url": "https://www.gov.cn", "search_keyword": "国务院"},
    {"site_id": "stats_gov_cn", "name": "国家数据", "url": "https://www.stats.gov.cn", "search_keyword": "GDP"},
    {"site_id": "gsxt_gov_cn", "name": "国家企业信用信息公示", "url": "https://www.gsxt.gov.cn", "search_keyword": "阿里巴巴"},
    {"site_id": "boss_zhipin", "name": "BOSS直聘", "url": "https://www.zhipin.com", "search_keyword": "Python"},
    {"site_id": "51job", "name": "前程无忧", "url": "https://www.51job.com", "search_keyword": "数据分析"},
    {"site_id": "lagou", "name": "拉勾网", "url": "https://www.lagou.com", "search_keyword": "前端"},
    {"site_id": "jd_com", "name": "京东", "url": "https://www.jd.com", "search_keyword": "手机"},
    {"site_id": "cls_cn", "name": "财联社", "url": "https://www.cls.cn", "search_keyword": "股市"},
    {"site_id": "zhihu", "name": "知乎", "url": "https://www.zhihu.com", "search_keyword": "AI"},
    {"site_id": "baidu_health", "name": "百度健康", "url": "https://health.baidu.com", "search_keyword": "感冒"},
]

SEARCH_SELECTORS = [
    "input[type='search']", "input[placeholder*='搜索']", "input[placeholder*='search']",
    "input[name='keyword']", "input[name='q']", "#searchInput", ".search-input",
]

RESULT_SELECTORS = [
    ".result-item", ".search-result", "li.result", ".item", "[class*='result']",
]


async def run_site_search_test(site_config: Dict, session: PlaywrightSession) -> Dict:
    """为单个站点执行搜索测试（使用独立 page）"""
    site_id = site_config["site_id"]
    site_name = site_config["name"]
    url = site_config["url"]
    keyword = site_config["search_keyword"]

    result = {
        "site_id": site_id,
        "site_name": site_name,
        "url": url,
        "keyword": keyword,
        "status": "unknown",
        "duration_seconds": 0,
        "error_message": None,
        "metrics": {},
        "timestamp": datetime.now().isoformat(),
    }

    start_time = time.time()
    try:
        # 为每个站点创建独立 page
        context = session._async_context
        page = await context.new_page()
        page.set_default_timeout(30000)

        logger.info(f"正在访问 {site_name} ({url})")
        await page.goto(url, wait_until="domcontentloaded")

        title = await page.title()
        result["metrics"]["page_title"] = title

        if not title:
            result["status"] = "failed"
            result["error_message"] = "页面标题为空"
            await page.close()
            return result

        # 查找搜索框
        search_box = None
        for selector in SEARCH_SELECTORS:
            try:
                elements = await page.query_selector_all(selector)
                if elements:
                    search_box = elements[0]
                    break
            except Exception:
                continue

        if search_box:
            await search_box.click()
            await search_box.fill(keyword)
            await search_box.press("Enter")
            await asyncio.sleep(2)

            result["metrics"]["search_performed"] = True
            result["metrics"]["result_url"] = page.url

            result_items = await page.query_selector_all(
                ", ".join(RESULT_SELECTORS)
            )
            result["metrics"]["results_found"] = len(result_items)

            if len(result_items) > 0:
                result["status"] = "passed"
            else:
                result["status"] = "warning"
                result["error_message"] = "搜索框存在但无结果项"
        else:
            result["status"] = "partial"
            result["error_message"] = "未找到搜索框元素"
            result["metrics"]["search_performed"] = False

        await page.close()

    except Exception as e:
        result["status"] = "error"
        result["error_message"] = str(e)
        logger.error(f"{site_name} 测试失败: {e}")
    finally:
        result["duration_seconds"] = round(time.time() - start_time, 3)

    return result


async def main():
    start_time = time.time()

    try:
        config = load_test_config()
        logger.info(f"已加载配置: {len(config.phase1_sites)} 个 Phase1 站点")
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        return 1

    output_dir = SKILL_DIR / "output" / "test_reports" / "phase1"
    output_dir.mkdir(parents=True, exist_ok=True)

    playwright_config = PlaywrightConfig(
        headless=True, viewport_width=1920, viewport_height=1080,
        default_timeout=30000, enable_stealth=True
    )
    session = PlaywrightSession(playwright_config)
    await session.async_launch()

    try:
        # 顺序执行，每个站点独立 page
        site_results = []
        for site in PHASE1_SITES:
            r = await run_site_search_test(site, session)
            site_results.append(r)
            logger.info(f"  [{site['site_id']}] status={r['status']} duration={r['duration_seconds']}s")
    finally:
        await session.async_close()

    total_tests = len(site_results)
    passed = sum(1 for r in site_results if r['status'] == 'passed')
    failed = sum(1 for r in site_results if r['status'] == 'failed')
    errors = sum(1 for r in site_results if r['status'] == 'error')
    warnings = sum(1 for r in site_results if r['status'] == 'warning')
    partial = sum(1 for r in site_results if r['status'] == 'partial')

    overall_summary = {
        "run_timestamp": datetime.now().isoformat(),
        "total_duration_seconds": round(time.time() - start_time, 2),
        "total_sites": total_tests,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "warnings": warnings,
        "partial": partial,
        "pass_rate": round(passed / max(total_tests, 1) * 100, 2),
        "site_details": list(site_results),
    }

    summary_path = output_dir / f"phase1_real_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_path.write_text(json.dumps(overall_summary, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    print("\n" + "=" * 60)
    print("Phase1 真实浏览器测试执行摘要")
    print("=" * 60)
    print(f"总站点数: {overall_summary['total_sites']}")
    print(f"通过: {overall_summary['passed']}")
    print(f"失败: {overall_summary['failed']}")
    print(f"错误: {overall_summary['errors']}")
    print(f"警告: {overall_summary['warnings']}")
    print(f"部分通过: {overall_summary['partial']}")
    print(f"通过率: {overall_summary['pass_rate']}%")
    print(f"执行时长: {overall_summary['total_duration_seconds']}秒")
    print(f"\n详细报告: {summary_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
