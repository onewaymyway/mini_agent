# -*- coding: utf-8 -*-
"""
P0 站点测试运行器

从 p0_sites_config.json 读取配置，执行真实浏览器测试。
支持 --mode/--concurrency/--sites/--output-dir 参数覆盖。
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_DIR))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('run_p0_tests')


def load_config(config_path: str) -> dict:
    """加载 P0 站点配置文件"""
    path = Path(config_path)
    if not path.exists():
        logger.error(f"配置文件不存在: {path}")
        sys.exit(1)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def apply_overrides(config: dict, args: argparse.Namespace) -> dict:
    """应用命令行参数覆盖"""
    if args.mode:
        config['run_config']['mode'] = args.mode
    if args.concurrency:
        config['run_config']['concurrency'] = args.concurrency
    if args.sites:
        site_ids = [s.strip() for s in args.sites.split(',')]
        config['p0_sites'] = [s for s in config['p0_sites'] if s['site_id'] in site_ids]
        config['execution_order'] = [sid for sid in config['execution_order'] if sid in site_ids]
    if args.output_dir:
        config['run_config']['output_dir'] = args.output_dir
    return config


def print_config_summary(config: dict):
    """打印配置摘要"""
    rc = config.get('run_config', {})
    sites = config.get('p0_sites', [])
    logger.info('=' * 60)
    logger.info('P0 站点测试框架配置摘要')
    logger.info('=' * 60)
    logger.info(f'  测试模式: {rc.get("mode", "real")}')
    logger.info(f'  并发数: {rc.get("concurrency", 3)}')
    logger.info(f'  单测试超时: {rc.get("timeout_per_test_seconds", 60)}s')
    logger.info(f'  最大重试: {rc.get("max_retries", 2)}')
    logger.info(f'  站点数量: {len(sites)}')
    logger.info(f'  输出目录: {rc.get("output_dir", "./output")}')
    logger.info('-' * 60)
    for site in sites:
        scenarios = len(site.get('test_scenarios', []))
        logger.info(f'  [{site["priority"]}] {site["name"]} ({site["site_id"]}) — {scenarios}个场景')
    logger.info('=' * 60)


def validate_config(config: dict) -> list:
    """验证配置完整性，返回错误列表"""
    errors = []
    rc = config.get('run_config', {})

    # 必需字段检查
    required_site_fields = ['site_id', 'name', 'url', 'priority', 'category', 'test_scenarios']
    for site in config.get('p0_sites', []):
        for field in required_site_fields:
            if field not in site:
                errors.append(f'站点 {site.get("site_id", "?")} 缺少字段: {field}')
        for scenario in site.get('test_scenarios', []):
            if 'scenario_id' not in scenario or 'action' not in scenario:
                errors.append(f'站点 {site["site_id"]} 的场景 {scenario.get("scenario_id", "?")} 缺少必要字段')

    # 模式验证
    valid_modes = ['mock', 'real', 'stress', 'hybrid']
    if rc.get('mode') not in valid_modes:
        errors.append(f'无效测试模式: {rc.get("mode")}，可选: {valid_modes}')

    # 并发数验证
    if rc.get('concurrency', 3) < 1:
        errors.append('并发数必须 >= 1')

    return errors


def main():
    parser = argparse.ArgumentParser(description='P0 站点自动化测试运行器')
    parser.add_argument('--config', default=str(SKILL_DIR / 'tests' / 'config' / 'p0_sites_config.json'),
                        help='P0站点配置文件路径')
    parser.add_argument('--mode', choices=['mock', 'real', 'stress', 'hybrid'],
                        help='覆盖测试模式')
    parser.add_argument('--concurrency', type=int, help='覆盖并发数')
    parser.add_argument('--sites', help='逗号分隔的站点ID列表（覆盖全量）')
    parser.add_argument('--output-dir', help='覆盖输出目录')
    parser.add_argument('--dry-run', action='store_true', help='仅验证配置，不执行测试')
    args = parser.parse_args()

    logger.info('P0 站点测试框架初始化...')

    # 加载配置
    config = load_config(args.config)

    # 应用覆盖
    config = apply_overrides(config, args)

    # 验证配置
    errors = validate_config(config)
    if errors:
        logger.error(f'配置验证失败 ({len(errors)} 个错误):')
        for e in errors:
            logger.error(f'  - {e}')
        sys.exit(1)

    # 打印摘要
    print_config_summary(config)

    # Dry-run 模式
    if args.dry_run:
        logger.info('Dry-run 模式：配置验证通过，框架就绪。')
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = SKILL_DIR / 'tests' / 'output' / f'p0_config_validation_{ts}.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = {
            'status': 'config_valid',
            'timestamp': ts,
            'mode': config['run_config']['mode'],
            'concurrency': config['run_config']['concurrency'],
            'sites_count': len(config['p0_sites']),
            'total_scenarios': sum(len(s.get('test_scenarios', [])) for s in config['p0_sites']),
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logger.info(f'验证结果已保存: {output_path}')
        return 0

    # TODO: 步骤 6 实现真实测试执行逻辑
    logger.warning('真实测试执行逻辑将在步骤 6 中实现。当前为框架配置阶段。')
    logger.info('框架配置初始化完成 ✅')
    return 0


if __name__ == '__main__':
    sys.exit(main())
