#!/usr/bin/env python3
"""
网站覆盖盲区调研工具
识别当前未覆盖的网站领域，生成调研清单
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class WebsiteCandidate:
    """候选网站信息"""
    name: str
    url: str
    category: str
    priority: str  # P0/P1/P2/P3
    reason: str
    estimated_difficulty: str  # easy/medium/hard
    expected_value: str


class BlindSpotResearcher:
    """盲区调研器"""
    
    # 已覆盖的网站领域
    COVERED_CATEGORIES = {
        "search": ["baidu", "bing", "google", "duckduckgo"],
        "ecommerce": ["taobao", "jd", "pdd", "amazon"],
        "social": ["weibo", "zhihu", "xiaohongshu", "douyin", "kuaishou"],
        "news": ["sina", "wangyi", "cls", "thp"],
        "finance": ["xueqiu", "eastmoney", "eastmoney_guba"],
        "job": ["boss_zhipin", "lagou", "zhilian", "liepin", "51job"],
        "travel": ["ctrip", "qunar", "fliggy", "mafengwo"],
        "property": ["anjuke", "beike", "lianjia", "autohome", "dongchedi"],
        "education": ["arxiv", "cnki", "mooc", "xuetangx", "bilibili"],
        "entertainment": ["iqiyi", "youku", "xigua", "music163"],
        "academic": ["scholar", "semantic_scholar", "github"],
        "lifestyle": ["dianping", "meituan", "xianyu"],
        "government": ["gov_service"],
        "weather": ["weather"],
        "transport": ["train"],
    }
    
    # 待调研的领域
    TARGET_CATEGORIES = [
        {"name": "短视频", "keywords": ["抖音", "快手", "B站", "视频号", "tiktok"], "priority": "P1"},
        {"name": "直播", "keywords": ["斗鱼", "虎牙", "bilibili直播", " twitch"], "priority": "P1"},
        {"name": "音乐", "keywords": ["网易云音乐", "QQ音乐", "酷狗", "spotify"], "priority": "P2"},
        {"name": "阅读", "keywords": ["微信读书", "起点", "晋江", "豆瓣阅读"], "priority": "P2"},
        {"name": "知识付费", "keywords": ["得到", "知乎盐选", "喜马拉雅"], "priority": "P2"},
        {"name": "本地生活", "keywords": ["美团", "大众点评", "饿了么", "口碑"], "priority": "P1"},
        {"name": "招聘", "keywords": ["脉脉", "猎聘", "前程无忧"], "priority": "P1"},
        {"name": "汽车", "keywords": ["汽车之家", "懂车帝", "汽车之家"], "priority": "P1"},
        {"name": "房产", "keywords": ["链家", "贝壳", "安居客"], "priority": "P1"},
        {"name": "旅游", "keywords": ["携程", "去哪儿", "飞猪", "马蜂窝"], "priority": "P1"},
        {"name": "医疗", "keywords": ["好大夫", "丁香园", "微医"], "priority": "P2"},
        {"name": "金融", "keywords": ["雪球", "东方财富", "同花顺"], "priority": "P1"},
        {"name": "学术", "keywords": ["知网", "万方", "维普", "jstor"], "priority": "P2"},
        {"name": "政府", "keywords": ["政府服务", "政务公开", "数据开放"], "priority": "P3"},
        {"name": "国际", "keywords": ["reddit", "twitter", "linkedin", "medium"], "priority": "P2"},
    ]
    
    def __init__(self, output_dir: str = "output/research"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.candidates: List[WebsiteCandidate] = []
    
    def analyze_coverage(self) -> Dict[str, any]:
        """分析当前覆盖情况"""
        covered = set()
        for category, sites in self.COVERED_CATEGORIES.items():
            for site in sites:
                covered.add(site.lower())
        
        return {
            "covered_categories": list(self.COVERED_CATEGORIES.keys()),
            "covered_sites": list(covered),
            "total_covered": len(covered),
            "target_categories": len(self.TARGET_CATEGORIES),
        }
    
    def generate_research_list(self) -> List[WebsiteCandidate]:
        """生成调研清单"""
        candidates = []
        
        for category_info in self.TARGET_CATEGORIES:
            for keyword in category_info["keywords"]:
                candidate = WebsiteCandidate(
                    name=keyword,
                    url=f"https://{keyword.replace(' ', '')}.com",
                    category=category_info["name"],
                    priority=category_info["priority"],
                    reason=f"{category_info['name']}领域重要网站",
                    estimated_difficulty="medium",
                    expected_value="拓展网站覆盖范围"
                )
                candidates.append(candidate)
        
        return candidates
    
    def save_research_list(self, candidates: List[WebsiteCandidate], filename: Optional[str] = None) -> Path:
        """保存调研清单"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"blind_spot_research_{timestamp}.json"
        
        filepath = self.output_dir / filename
        data = {
            "generated_at": datetime.now().isoformat(),
            "total_candidates": len(candidates),
            "candidates": [asdict(c) for c in candidates],
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return filepath
    
    def print_summary(self, candidates: List[WebsiteCandidate]):
        """打印调研摘要"""
        print("\n" + "=" * 60)
        print("网站覆盖盲区调研摘要")
        print("=" * 60)
        
        # 按优先级分组
        by_priority = {}
        for c in candidates:
            if c.priority not in by_priority:
                by_priority[c.priority] = []
            by_priority[c.priority].append(c)
        
        for priority in ["P0", "P1", "P2", "P3"]:
            if priority in by_priority:
                print(f"\n{priority} 优先级 ({len(by_priority[priority])} 个网站):")
                for c in by_priority[priority][:5]:  # 只显示前5个
                    print(f"  - {c.name} ({c.category})")
                if len(by_priority[priority]) > 5:
                    print(f"  ... 还有 {len(by_priority[priority]) - 5} 个")
        
        print(f"\n总计: {len(candidates)} 个候选网站")
        print("=" * 60)


async def main():
    researcher = BlindSpotResearcher()
    
    # 分析覆盖情况
    coverage = researcher.analyze_coverage()
    print("\n当前覆盖情况:")
    print(f"  已覆盖领域: {len(coverage['covered_categories'])} 个")
    print(f"  已覆盖网站: {coverage['total_covered']} 个")
    print(f"  目标领域: {coverage['target_categories']} 个")
    
    # 生成调研清单
    candidates = researcher.generate_research_list()
    
    # 保存清单
    filepath = researcher.save_research_list(candidates)
    print(f"\n调研清单已保存至: {filepath}")
    
    # 打印摘要
    researcher.print_summary(candidates)
    
    return candidates


if __name__ == "__main__":
    asyncio.run(main())