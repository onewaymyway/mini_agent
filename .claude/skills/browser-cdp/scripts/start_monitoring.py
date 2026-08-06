"""
监控告警系统启动脚本

启动所有监控组件：
- 监控覆盖率追踪
- 告警响应处理器
- 实时告警监控器
- 监控调度器
"""

import json
import logging
import sys
from pathlib import Path

# 添加scripts目录到路径
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(
            str(Path(__file__).parent.parent / "logs" / "monitoring_startup.log"),
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main():
    """主函数"""
    logger.info("Starting monitoring system...")
    
    # 1. 初始化监控覆盖率追踪
    from monitoring_coverage import get_coverage_tracker
    coverage = get_coverage_tracker()
    
    # 注册所有组件
    from setup_monitoring import setup_monitoring
    monitoring_report = setup_monitoring()
    coverage_report = coverage.get_coverage_report()
    logger.info(f"Coverage: {coverage_report['overall_coverage_rate']:.2%}")
    
    # 2. 初始化告警响应处理器
    from alert_response_handler import get_alert_handler
    handler = get_alert_handler()
    
    # 3. 启动实时告警监控器
    from realtime_alert_monitor import start_realtime_monitor
    monitor = start_realtime_monitor()
    logger.info("Realtime alert monitor started")
    
    # 4. 生成初始报告
    report = monitor.generate_report()
    logger.info(f"Initial report: {json.dumps(report, indent=2, ensure_ascii=False)}")
    
    logger.info("Monitoring system started successfully")
    
    # 保持运行
    try:
        import time
        while True:
            time.sleep(60)
            # 每小时生成一次报告
            if int(time.time()) % 3600 < 5:
                report = monitor.generate_report()
                logger.info(f"Hourly report: {json.dumps(report, indent=2, ensure_ascii=False)}")
    except KeyboardInterrupt:
        logger.info("Shutting down monitoring system...")
        monitor.stop()
        logger.info("Monitoring system stopped")


if __name__ == "__main__":
    main()
