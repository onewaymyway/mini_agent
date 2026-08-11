#!/usr/bin/env python
"""
稳定性监控器 - 实时监控测试状态

用于监控72小时稳定性测试的进度和成功率
"""

import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StabilityMonitor:
    """稳定性监控器"""
    
    def __init__(self, test_results_dir: str = "./test_results"):
        self.test_results_dir = Path(test_results_dir)
        self.test_results_dir.mkdir(parents=True, exist_ok=True)
        self.alert_threshold = 0.95  # 成功率阈值
        self.alert_count = 0
        self.last_check_time = None
    
    def load_latest_report(self) -> Dict[str, Any]:
        """加载最新的测试报告"""
        reports = list(self.test_results_dir.glob("*.json"))
        if not reports:
            return None
        
        latest = max(reports, key=lambda p: p.stat().st_mtime)
        with open(latest, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def check_success_rate(self, report: Dict[str, Any]) -> bool:
        """检查成功率是否达标"""
        rate = report.get('overall_success_rate', 0)
        
        if rate < self.alert_threshold:
            self.alert_count += 1
            logger.warning(f"⚠️  成功率低于阈值: {rate:.2%} < {self.alert_threshold:.2%}")
            return False
        else:
            if self.alert_count > 0:
                logger.info(f"✅ 成功率已恢复: {rate:.2%}")
                self.alert_count = 0
            return True
    
    def get_website_status(self, report: Dict[str, Any]) -> List[Dict]:
        """获取各网站状态"""
        website_stats = report.get('website_stats', {})
        status_list = []
        
        for website, stats in website_stats.items():
            rate = stats.get('success_rate', 0)
            status = "✅" if rate >= self.alert_threshold else "⚠️" if rate >= 0.8 else "❌"
            status_list.append({
                "website": website,
                "total": stats.get('total', 0),
                "success": stats.get('success', 0),
                "fail": stats.get('fail', 0),
                "rate": rate,
                "status": status
            })
        
        return sorted(status_list, key=lambda x: x['rate'])
    
    def print_status(self, report: Dict[str, Any]):
        """打印当前状态"""
        print("\n" + "="*60)
        print(f"稳定性测试监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60)
        
        # 总体统计
        print(f"\n📊 总体统计:")
        print(f"  总测试数: {report.get('total_tests', 0)}")
        print(f"  成功数: {report.get('total_success', 0)}")
        print(f"  成功率: {report.get('overall_success_rate', 0):.2%}")
        print(f"  运行时长: {report.get('duration_hours', 0):.2f} 小时")
        
        # 各网站状态
        print(f"\n🌐 各网站状态:")
        website_status = self.get_website_status(report)
        for ws in website_status:
            print(f"  {ws['status']} {ws['website']:20s} - {ws['rate']:5.1%} ({ws['success']}/{ws['total']})")
        
        # 告警信息
        if self.alert_count > 0:
            print(f"\n⚠️  告警次数: {self.alert_count}")
        
        print("="*60 + "\n")
    
    def monitor_loop(self, interval: int = 60):
        """监控循环"""
        logger.info(f"开始监控，每 {interval} 秒检查一次")
        
        while True:
            try:
                report = self.load_latest_report()
                if report:
                    self.print_status(report)
                    self.check_success_rate(report)
                else:
                    logger.info("暂无测试报告，等待中...")
            except Exception as e:
                logger.error(f"监控出错: {e}")
            
            time.sleep(interval)
    
    def generate_alert(self, report: Dict[str, Any]) -> str:
        """生成告警信息"""
        rate = report.get('overall_success_rate', 0)
        low_rate_sites = [
            ws for ws in self.get_website_status(report)
            if ws['rate'] < self.alert_threshold
        ]
        
        alert = f"⚠️  稳定性告警：成功率 {rate:.2%} 低于阈值 {self.alert_threshold:.2%}\n"
        if low_rate_sites:
            alert += "低成功率网站:\n"
            for ws in low_rate_sites[:5]:
                alert += f"  - {ws['website']}: {ws['rate']:.1%}\n"
        
        return alert


def main():
    import argparse
    parser = argparse.ArgumentParser(description='稳定性监控器')
    parser.add_argument('--interval', type=int, default=60, help='检查间隔（秒）')
    parser.add_argument('--once', action='store_true', help='只检查一次')
    parser.add_argument('--threshold', type=float, default=0.95, help='成功率阈值')
    args = parser.parse_args()
    
    monitor = StabilityMonitor()
    monitor.alert_threshold = args.threshold
    
    if args.once:
        report = monitor.load_latest_report()
        if report:
            monitor.print_status(report)
            monitor.check_success_rate(report)
        else:
            print("暂无测试报告")
    else:
        monitor.monitor_loop(interval=args.interval)


if __name__ == "__main__":
    main()