#!/usr/bin/env python3
"""
浏览器CDP网站配置兼容性测试脚本

测试步骤5：验证新增的10个网站配置的正确性和兼容性
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# 配置目录
CONFIG_DIR = Path(__file__).parent.parent / "config" / "websites"
REFERENCE_DIR = Path(__file__).parent.parent / "references"
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "compatibility_test"


class CompatibilityChecker:
    """兼容性检查器"""
    
    def __init__(self):
        self.results = []
        self.errors = []
        self.warnings = []
    
    def load_config(self, domain: str) -> dict:
        """加载网站配置"""
        config_file = CONFIG_DIR / f"{domain}.json"
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_file}")
        
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _strip_tld(self, domain: str) -> str:
        """去掉顶级域名后缀，例如 'meituan.com' -> 'meituan'"""
        parts = domain.rsplit('.', 1)
        return parts[0] if len(parts) == 2 else domain

    def check_reference_exists(self, domain: str, name: str) -> bool:
        """检查Reference文档是否存在"""
        subdomain = self._strip_tld(domain)
        ref_file = REFERENCE_DIR / f"{subdomain}-search.md"
        if not ref_file.exists():
            self.warnings.append(f"缺少Reference文档: {subdomain}-search.md")
            return False
        return True
    
    def validate_config_fields(self, config: dict) -> list:
        """验证配置字段完整性"""
        required_fields = [
            'name', 'domain', 'url', 'category', 'priority'
        ]
        issues = []
        
        for field in required_fields:
            if field not in config:
                issues.append(f"缺少必填字段: {field}")
        
        # 检查分类合法性
        valid_categories = [
            'search_engine', 'news', 'social', 'ecommerce', 'finance',
            'gov', 'recruitment', 'property', 'travel', 'health', 'legal',
            'education', 'maps', 'music', 'video', 'shopping'
        ]
        if config.get('category') not in valid_categories:
            issues.append(f"非法分类: {config.get('category')}")
        
        # 检查优先级合法性
        valid_priorities = ['P0', 'P1', 'P2', 'P3']
        if config.get('priority') not in valid_priorities:
            issues.append(f"非法优先级: {config.get('priority')}")
        
        # 检查反爬等级
        anti_level = config.get('anti_crawl_level', 1)
        if not isinstance(anti_level, int) or anti_level < 0 or anti_level > 3:
            issues.append(f"非法反爬等级: {anti_level}")
        
        return issues
    
    def test_site(self, domain: str) -> dict:
        """测试单个站点"""
        result = {
            'domain': domain,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'PASS',
            'errors': [],
            'warnings': []
        }
        
        try:
            # 1. 加载配置
            config = self.load_config(domain)
            
            # 2. 验证字段
            issues = self.validate_config_fields(config)
            if issues:
                result['status'] = 'FAIL'
                result['errors'].extend(issues)
            else:
                result['warnings'].extend(issues)
            
            # 3. 检查Reference文档
            if not self.check_reference_exists(domain, config.get('name', '')):
                result['warnings'].append('缺少Reference文档')
            
            # 4. 检查自定义配置
            custom = config.get('custom_config', {})
            if custom:
                selectors = [
                    'search_box', 'search_button', 'results', 'result_item',
                    'title', 'price', 'pagination'
                ]
                missing_selectors = [s for s in selectors if s not in custom]
                if missing_selectors:
                    result['warnings'].append(f"缺少选择器: {missing_selectors}")
            
            self.results.append(result)
            
        except Exception as e:
            result['status'] = 'ERROR'
            result['errors'].append(str(e))
            self.results.append(result)
        
        return result
    
    def run_tests(self, domains: list) -> dict:
        """运行所有测试"""
        print(f"\n{'='*60}")
        print(f"开始兼容性测试 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")
        
        for domain in domains:
            print(f"测试站点: {domain}... ", end='')
            result = self.test_site(domain)
            
            if result['status'] == 'PASS':
                print("✓ PASS")
            elif result['status'] == 'FAIL':
                print(f"✗ FAIL: {', '.join(result['errors'])}")
            else:
                print(f"⚠ ERROR: {', '.join(result['errors'])}")
            
            if result['warnings']:
                for warning in result['warnings']:
                    print(f"   警告: {warning}")
        
        return self.generate_report(domains)
    
    def generate_report(self, domains: list) -> dict:
        """生成测试报告"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r['status'] == 'PASS')
        failed = sum(1 for r in self.results if r['status'] == 'FAIL')
        errors = sum(1 for r in self.results if r['status'] == 'ERROR')
        
        report = {
            'test_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_sites': total,
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'success_rate': f"{passed/total*100:.1f}%" if total > 0 else 'N/A',
            'details': self.results
        }
        
        # 保存报告
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_file = OUTPUT_DIR / f"compatibility_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"\n{'='*60}")
        print(f"测试完成！")
        print(f"总计: {total} | 通过: {passed} | 失败: {failed} | 错误: {errors}")
        print(f"成功率: {report['success_rate']}")
        print(f"报告已保存: {report_file}")
        print(f"{'='*60}\n")
        
        return report


def main():
    """主函数"""
    checker = CompatibilityChecker()
    
    # 新增的10个网站配置（使用完整域名）
    new_sites = [
        'pinduoyun.com', 'kuaishou.com', '51job.com', 'meituan.com', 'anjuke.com',
        'lagou.com', 'dianping.com', 'ctrip.com', 'cnki.net', 'xianyu.com'
    ]
    
    report = checker.run_tests(new_sites)
    
    # 返回退出码
    if report['failed'] == 0 and report['errors'] == 0:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
