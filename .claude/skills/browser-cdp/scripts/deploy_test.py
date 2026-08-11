#!/usr/bin/env python
"""
部署脚本 - 部署稳定性测试系统

用于部署72小时稳定性测试
"""

import sys
import json
import subprocess
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DeploymentManager:
    """部署管理器"""
    
    def __init__(self, skill_dir: str = "."):
        self.skill_dir = Path(skill_dir)
        self.scripts_dir = self.skill_dir / "scripts"
        self.config_dir = self.skill_dir / "config"
        self.test_results_dir = self.skill_dir / "test_results"
        
    def check_prerequisites(self) -> bool:
        """检查前置条件"""
        logger.info("检查前置条件...")
        
        checks = [
            ("Python 3.8+", sys.version_info >= (3, 8)),
            ("scripts目录", self.scripts_dir.exists()),
            ("config目录", self.config_dir.exists()),
            ("stability_test.py", (self.scripts_dir / "stability_test.py").exists()),
            ("quick_validation.py", (self.scripts_dir / "quick_validation.py").exists()),
            ("monitor.py", (self.scripts_dir / "monitor.py").exists()),
        ]
        
        all_passed = True
        for name, passed in checks:
            status = "✅" if passed else "❌"
            logger.info(f"  {status} {name}")
            if not passed:
                all_passed = False
        
        return all_passed
    
    def run_quick_validation(self) -> bool:
        """运行快速验证"""
        logger.info("运行快速验证测试...")
        
        try:
            result = subprocess.run(
                [sys.executable, str(self.scripts_dir / "quick_validation.py")],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            logger.info(result.stdout)
            if result.stderr:
                logger.warning(result.stderr)
            
            return result.returncode == 0
        
        except subprocess.TimeoutExpired:
            logger.error("快速验证超时")
            return False
        except Exception as e:
            logger.error(f"快速验证失败: {e}")
            return False
    
    def start_stability_test(self, hours: int = 72, background: bool = True) -> bool:
        """启动稳定性测试"""
        logger.info(f"启动 {hours} 小时稳定性测试...")
        
        cmd = [
            sys.executable,
            str(self.scripts_dir / "stability_test.py"),
            "--hours", str(hours),
            "--interval", "300"
        ]
        
        try:
            if background:
                subprocess.Popen(cmd, cwd=str(self.skill_dir))
                logger.info("稳定性测试已在后台启动")
            else:
                result = subprocess.run(cmd, cwd=str(self.skill_dir))
                return result.returncode == 0
            
            return True
        
        except Exception as e:
            logger.error(f"启动稳定性测试失败: {e}")
            return False
    
    def start_monitor(self, background: bool = True) -> bool:
        """启动监控"""
        logger.info("启动监控...")
        
        cmd = [
            sys.executable,
            str(self.scripts_dir / "monitor.py"),
            "--interval", "60"
        ]
        
        try:
            if background:
                subprocess.Popen(cmd, cwd=str(self.skill_dir))
                logger.info("监控已在后台启动")
            else:
                result = subprocess.run(cmd, cwd=str(self.skill_dir))
                return result.returncode == 0
            
            return True
        
        except Exception as e:
            logger.error(f"启动监控失败: {e}")
            return False
    
    def generate_deployment_report(self) -> dict:
        """生成部署报告"""
        report = {
            "deployment_time": datetime.now().isoformat(),
            "skill_dir": str(self.skill_dir),
            "scripts": {
                "stability_test.py": (self.scripts_dir / "stability_test.py").exists(),
                "quick_validation.py": (self.scripts_dir / "quick_validation.py").exists(),
                "monitor.py": (self.scripts_dir / "monitor.py").exists(),
            },
            "config": {
                "stability_test_config.json": (self.config_dir / "stability_test_config.json").exists(),
            },
            "test_results_dir": str(self.test_results_dir),
            "status": "deployed"
        }
        
        return report
    
    def deploy(self, hours: int = 72) -> bool:
        """执行完整部署"""
        logger.info("开始部署稳定性测试系统...")
        
        # 1. 检查前置条件
        if not self.check_prerequisites():
            logger.error("前置条件检查失败")
            return False
        
        # 2. 运行快速验证
        if not self.run_quick_validation():
            logger.error("快速验证失败，请检查配置")
            return False
        
        # 3. 启动稳定性测试
        if not self.start_stability_test(hours=hours, background=True):
            logger.error("启动稳定性测试失败")
            return False
        
        # 4. 启动监控
        if not self.start_monitor(background=True):
            logger.error("启动监控失败")
            return False
        
        # 5. 生成部署报告
        report = self.generate_deployment_report()
        report_path = self.skill_dir / "deployment_report.json"
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"部署完成！报告已保存: {report_path}")
        logger.info(f"稳定性测试将在后台运行 {hours} 小时")
        logger.info(f"监控将每60秒检查一次测试结果")
        
        return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description='部署稳定性测试系统')
    parser.add_argument('--hours', type=int, default=72, help='测试时长（小时）')
    parser.add_argument('--skill-dir', default='.', help='skill目录')
    parser.add_argument('--no-background', action='store_true', help='前台运行测试')
    args = parser.parse_args()
    
    manager = DeploymentManager(skill_dir=args.skill_dir)
    success = manager.deploy(hours=args.hours)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
