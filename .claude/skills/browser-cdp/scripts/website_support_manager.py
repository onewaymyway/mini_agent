#!/usr/bin/env python3
"""
网站支持列表管理器
管理已支持网站列表，跟踪支持状态和评估历史
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class WebsiteSupport:
    """网站支持信息"""
    name: str
    url: str
    category: str
    priority: str  # P0/P1/P2/P3
    status: str  # supported/partial/unsupported
    last_evaluated: Optional[str] = None
    overall_score: Optional[float] = None
    dimensions: Optional[Dict] = None
    notes: str = ""


class WebsiteSupportManager:
    """网站支持列表管理器"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.support_file = self.data_dir / "website_support_list.json"
        self.history_file = self.data_dir / "evaluation_history.json"
        
        self.websites: Dict[str, WebsiteSupport] = {}
        self.history: List[Dict] = []
        
        self._load_data()
    
    def _load_data(self):
        """加载数据"""
        if self.support_file.exists():
            with open(self.support_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for name, info in data.items():
                    self.websites[name] = WebsiteSupport(**info)
        
        if self.history_file.exists():
            with open(self.history_file, "r", encoding="utf-8") as f:
                self.history = json.load(f)
    
    def _save_data(self):
        """保存数据"""
        data = {name: asdict(site) for name, site in self.websites.items()}
        with open(self.support_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
    
    def add_website(self, website: WebsiteSupport):
        """添加网站"""
        self.websites[website.name] = website
        self._save_data()
    
    def update_evaluation(self, website_name: str, evaluation_result: Dict):
        """更新评估结果"""
        if website_name not in self.websites:
            return
        
        site = self.websites[website_name]
        site.last_evaluated = evaluation_result.get("timestamp")
        site.overall_score = evaluation_result.get("overall_score")
        site.dimensions = {k: v.get("score") for k, v in evaluation_result.items() 
                         if k not in ["overall_score", "timestamp", "target_url"]}
        
        # 根据得分更新状态
        score = evaluation_result.get("overall_score", 0)
        if score >= 75:
            site.status = "supported"
        elif score >= 50:
            site.status = "partial"
        else:
            site.status = "unsupported"
        
        # 记录历史
        self.history.append({
            "timestamp": evaluation_result.get("timestamp"),
            "website": website_name,
            "overall_score": score,
            "dimensions": site.dimensions,
        })
        
        self._save_data()
    
    def get_websites_by_priority(self, priority: str) -> List[WebsiteSupport]:
        """按优先级获取网站列表"""
        return [w for w in self.websites.values() if w.priority == priority]
    
    def get_websites_by_status(self, status: str) -> List[WebsiteSupport]:
        """按状态获取网站列表"""
        return [w for w in self.websites.values() if w.status == status]
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        stats = {
            "total": len(self.websites),
            "by_priority": {},
            "by_status": {},
            "by_category": {},
            "avg_score": 0,
        }
        
        scores = []
        for site in self.websites.values():
            stats["by_priority"][site.priority] = stats["by_priority"].get(site.priority, 0) + 1
            stats["by_status"][site.status] = stats["by_status"].get(site.status, 0) + 1
            stats["by_category"][site.category] = stats["by_category"].get(site.category, 0) + 1
            if site.overall_score:
                scores.append(site.overall_score)
        
        if scores:
            stats["avg_score"] = sum(scores) / len(scores)
        
        return stats
    
    def generate_report(self) -> str:
        """生成支持列表报告"""
        stats = self.get_statistics()
        
        report = f"""
# 网站支持列表报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 统计概览

| 指标 | 数值 |
|------|------|
| 总网站数 | {stats['total']} |
| 平均得分 | {stats['avg_score']:.1f} |

### 按优先级分布

"""
        for priority, count in sorted(stats["by_priority"].items()):
            report += f"- {priority}: {count} 个\n"
        
        report += "\n### 按状态分布\n\n"
        for status, count in stats["by_status"].items():
            report += f"- {status}: {count} 个\n"
        
        report += "\n### 按领域分布\n\n"
        for category, count in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
            report += f"- {category}: {count} 个\n"
        
        # 列出各优先级网站
        for priority in ["P0", "P1", "P2", "P3"]:
            websites = self.get_websites_by_priority(priority)
            if websites:
                report += f"\n## {priority} 优先级网站\n\n"
                report += "| 名称 | URL | 状态 | 得分 |\n"
                report += "|------|-----|------|------|\n"
                for w in websites:
                    score = f"{w.overall_score:.1f}" if w.overall_score else "N/A"
                    report += f"| {w.name} | {w.url} | {w.status} | {score} |\n"
        
        return report


if __name__ == "__main__":
    manager = WebsiteSupportManager()
    
    # 添加示例网站
    example_sites = [
        WebsiteSupport(name="Baidu", url="https://www.baidu.com", category="search", priority="P0", status="supported"),
        WebsiteSupport(name="Zhihu", url="https://www.zhihu.com", category="social", priority="P0", status="supported"),
        WebsiteSupport(name="Taobao", url="https://www.taobao.com", category="ecommerce", priority="P1", status="partial"),
    ]
    
    for site in example_sites:
        manager.add_website(site)
    
    # 生成报告
    report = manager.generate_report()
    print(report)
    
    # 保存报告
    report_path = Path("output/reports/website_support_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已保存至: {report_path}")
