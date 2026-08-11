# -*- coding: utf-8 -*-
"""
新数据源测试脚本
测试凤凰财经、新浪新闻、宏观数据等新抓取模块
"""

import sys
import logging
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from finance_toolkit.data_fetching import (
    # 凤凰财经
    fetch_fenghuang_quote,
    fetch_fenghuang_news,
    fetch_fenghuang_hot_news,
    FenghuangFetcher,
    # 新浪新闻
    fetch_sina_news,
    fetch_sina_stock_news,
    fetch_sina_hot_news,
    SinaNewsFetcher,
    # 宏观数据
    fetch_gdp_data,
    fetch_cpi_data,
    fetch_pmi_data,
    fetch_interest_rate_data,
    fetch_money_supply_data,
    fetch_exchange_rate_data,
    fetch_all_macro_data,
    MacroFetcher,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_fenghuang_quote():
    """测试凤凰财经股票行情"""
    logger.info("=" * 50)
    logger.info("测试凤凰财经股票行情...")
    logger.info("=" * 50)
    
    symbols = ['600000', '000001', '600519']
    results = fetch_fenghuang_quote(symbols)
    
    logger.info(f"获取到 {len(results)} 条数据")
    for r in results:
        logger.info(f"  {r.symbol}: {r.payload.get('name', 'N/A')} - 价格: {r.payload.get('price', 'N/A')}")
    
    assert len(results) > 0, "凤凰财经行情数据为空"
    return results


def test_fenghuang_news():
    """测试凤凰财经新闻"""
    logger.info("\n" + "=" * 50)
    logger.info("测试凤凰财经新闻...")
    logger.info("=" * 50)
    
    results = fetch_fenghuang_hot_news()
    
    logger.info(f"获取到 {len(results)} 条新闻")
    for r in results[:5]:
        title = r.payload.get('title', 'N/A')[:40]
        logger.info(f"  {title}...")
    
    return results


def test_sina_news():
    """测试新浪财经新闻"""
    logger.info("\n" + "=" * 50)
    logger.info("测试新浪财经新闻...")
    logger.info("=" * 50)
    
    # 测试财经新闻
    results = fetch_sina_hot_news()
    logger.info(f"财经新闻: 获取到 {len(results)} 条")
    for r in results[:3]:
        title = r.payload.get('title', 'N/A')[:40]
        logger.info(f"  {title}...")
    
    # 测试股票新闻
    stock_news = fetch_sina_stock_news('600000.SH')
    logger.info(f"股票新闻: 获取到 {len(stock_news)} 条")
    for r in stock_news[:3]:
        title = r.payload.get('title', 'N/A')[:40]
        logger.info(f"  {title}...")
    
    return results


def test_macro_data():
    """测试宏观经济数据"""
    logger.info("\n" + "=" * 50)
    logger.info("测试宏观经济数据...")
    logger.info("=" * 50)
    
    # GDP数据
    gdp = fetch_gdp_data()
    logger.info(f"GDP数据: 获取到 {len(gdp)} 条")
    for r in gdp[:2]:
        logger.info(f"  {r.payload.get('quarter')}: GDP={r.payload.get('gdp')}, 同比={r.payload.get('yoy')}%")
    
    # CPI数据
    cpi = fetch_cpi_data()
    logger.info(f"CPI数据: 获取到 {len(cpi)} 条")
    for r in cpi[:2]:
        logger.info(f"  {r.payload.get('date')}: CPI={r.payload.get('cpi')}, 同比={r.payload.get('cpi_yoy')}%")
    
    # PMI数据
    pmi = fetch_pmi_data()
    logger.info(f"PMI数据: 获取到 {len(pmi)} 条")
    for r in pmi[:2]:
        logger.info(f"  {r.payload.get('date')}: 制造业PMI={r.payload.get('manufacturing_pmi')}")
    
    # 利率数据
    rate = fetch_interest_rate_data()
    logger.info(f"利率数据: 获取到 {len(rate)} 条")
    for r in rate[:2]:
        logger.info(f"  {r.payload.get('date')}: 1年LPR={r.payload.get('lpr_1y')}%, 5年LPR={r.payload.get('lpr_5y')}%")
    
    # 货币供应量
    money = fetch_money_supply_data()
    logger.info(f"货币供应量: 获取到 {len(money)} 条")
    for r in money[:2]:
        logger.info(f"  {r.payload.get('date')}: M2={r.payload.get('m2')}, 同比={r.payload.get('m2_yoy')}%")
    
    # 汇率数据
    fx = fetch_exchange_rate_data()
    logger.info(f"汇率数据: 获取到 {len(fx)} 条")
    for r in fx[:3]:
        logger.info(f"  {r.payload.get('currency')}: 中间价={r.payload.get('center_price')}")
    
    return {
        'gdp': gdp,
        'cpi': cpi,
        'pmi': pmi,
        'interest_rate': rate,
        'money_supply': money,
        'exchange_rate': fx,
    }


def test_all_macro_data():
    """测试批量获取宏观数据"""
    logger.info("\n" + "=" * 50)
    logger.info("测试批量获取宏观数据...")
    logger.info("=" * 50)
    
    all_data = fetch_all_macro_data()
    
    for key, data in all_data.items():
        logger.info(f"{key}: {len(data)} 条")
    
    return all_data


def test_fetcher_classes():
    """测试Fetcher类"""
    logger.info("\n" + "=" * 50)
    logger.info("测试Fetcher类...")
    logger.info("=" * 50)
    
    # 凤凰财经Fetcher
    fh_fetcher = FenghuangFetcher()
    quotes = fh_fetcher.get_quote(['600000'])
    logger.info(f"FenghuangFetcher.get_quote: {len(quotes)} 条")
    
    news = fh_fetcher.get_hot_news()
    logger.info(f"FenghuangFetcher.get_hot_news: {len(news)} 条")
    
    # 新浪新闻Fetcher
    sina_fetcher = SinaNewsFetcher()
    news = sina_fetcher.get_hot_news()
    logger.info(f"SinaNewsFetcher.get_hot_news: {len(news)} 条")
    
    # 宏观数据Fetcher
    macro_fetcher = MacroFetcher()
    all_data = macro_fetcher.get_all()
    logger.info(f"MacroFetcher.get_all: {len(all_data)} 个数据类型")
    
    return True


def main():
    """主测试函数"""
    logger.info("开始测试新数据源...")
    logger.info(f"测试时间: {__import__('datetime').datetime.utcnow().isoformat()}")
    
    results = {
        'fenghuang_quote': None,
        'fenghuang_news': None,
        'sina_news': None,
        'macro_data': None,
        'all_macro': None,
        'fetcher_classes': None,
    }
    
    errors = []
    
    # 测试凤凰财经行情
    try:
        results['fenghuang_quote'] = test_fenghuang_quote()
    except Exception as e:
        errors.append(f"凤凰财经行情测试失败: {e}")
        logger.error(f"凤凰财经行情测试失败: {e}")
    
    # 测试凤凰财经新闻
    try:
        results['fenghuang_news'] = test_fenghuang_news()
    except Exception as e:
        errors.append(f"凤凰财经新闻测试失败: {e}")
        logger.error(f"凤凰财经新闻测试失败: {e}")
    
    # 测试新浪新闻
    try:
        results['sina_news'] = test_sina_news()
    except Exception as e:
        errors.append(f"新浪新闻测试失败: {e}")
        logger.error(f"新浪新闻测试失败: {e}")
    
    # 测试宏观数据
    try:
        results['macro_data'] = test_macro_data()
    except Exception as e:
        errors.append(f"宏观数据测试失败: {e}")
        logger.error(f"宏观数据测试失败: {e}")
    
    # 测试批量宏观数据
    try:
        results['all_macro'] = test_all_macro_data()
    except Exception as e:
        errors.append(f"批量宏观数据测试失败: {e}")
        logger.error(f"批量宏观数据测试失败: {e}")
    
    # 测试Fetcher类
    try:
        results['fetcher_classes'] = test_fetcher_classes()
    except Exception as e:
        errors.append(f"Fetcher类测试失败: {e}")
        logger.error(f"Fetcher类测试失败: {e}")
    
    # 输出总结
    logger.info("\n" + "=" * 50)
    logger.info("测试总结")
    logger.info("=" * 50)
    
    success_count = sum(1 for v in results.values() if v is not None)
    total_count = len(results)
    
    logger.info(f"测试通过: {success_count}/{total_count}")
    
    if errors:
        logger.warning(f"\n错误列表:")
        for err in errors:
            logger.warning(f"  - {err}")
    
    # 保存测试结果
    import json
    test_report = {
        'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
        'success_count': success_count,
        'total_count': total_count,
        'errors': errors,
        'results_summary': {
            'fenghuang_quote': len(results['fenghuang_quote']) if results['fenghuang_quote'] else 0,
            'fenghuang_news': len(results['fenghuang_news']) if results['fenghuang_news'] else 0,
            'sina_news': len(results['sina_news']) if results['sina_news'] else 0,
            'macro_gdp': len(results['macro_data']['gdp']) if results['macro_data'] else 0,
            'macro_cpi': len(results['macro_data']['cpi']) if results['macro_data'] else 0,
            'macro_pmi': len(results['macro_data']['pmi']) if results['macro_data'] else 0,
        }
    }
    
    output_path = Path(__file__).parent.parent.parent / 'temp' / 'test_new_sources_report.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(test_report, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"\n测试报告已保存至: {output_path}")
    
    return len(errors) == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
