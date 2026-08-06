#!/usr/bin/env python
"""
lianjia_search.py - 链家房产搜索自动化脚本

使用 browser-cdp skill 搜索链家房源数据，支持二手房、租房、小区信息抓取。

用法:
    python lianjia_search.py --city bj --type ershoufang --max-results 20
    python lianjia_search.py --city sh --type zufang --district 朝阳 --max-results 10
    python lianjia_search.py --city gz --xiaoqu 天河北 --output-dir ./results
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import (
    random_delay, get_random_ua, save_results, clean_text, truncate_text
)
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 链家专用配置 ==========
LJ_BASE_URLS = {
    "bj": "https://bj.lianjia.com",
    "sh": "https://sh.lianjia.com",
    "gz": "https://gz.lianjia.com",
    "sz": "https://sz.lianjia.com",
    "cd": "https://cd.lianjia.com",
    "wh": "https://wh.lianjia.com",
    "nj": "https://nj.lianjia.com",
    "hz": "https://hz.lianjia.com",
    "xa": "https://xa.lianjia.com",
    "tl": "https://tl.lianjia.com",
}

DEFAULT_CITIES = list(LJ_BASE_URLS.keys())

# 幽灵房过滤特征
GHOST_FEATURES = [
    "暂无房源", "已下架", "暂无在售", "暂无出租",
    "价格异常", "图片缺失", "描述空洞",
]


class LianjiaSearcher(BaseSearcher):
    """链家房产搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._city = "bj"
        self._type = "ershoufang"  # ershoufang/zufang/xiaoqu
        self._district = None
    
    @property
    def source_name(self) -> str:
        return "lianjia"
    
    @property
    def supported_types(self) -> List[str]:
        return ["ershoufang", "zufang", "xiaoqu"]
    
    def search(
        self,
        query: str = "",
        city: str = "bj",
        type: str = "ershoufang",
        district: Optional[str] = None,
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索链家房源
        
        Args:
            query: 搜索关键词（小区名/地段）
            city: 城市代码（bj/sh/gz/sz等）
            type: 房源类型（ershoufang/zufang/xiaoqu）
            district: 区域（如"朝阳"、"海淀"）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            房源列表
        """
        self._city = city.lower() if city else "bj"
        self._type = type
        self._district = district
        
        print(f"[链家搜索] 城市: {city}, 类型: {type}, 关键词: {query}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")
        
        # 构建搜索 URL
        base_url = LJ_BASE_URLS.get(self._city, f"https://{self._city}.lianjia.com")
        
        if type == "ershoufang":
            search_path = "/ershoufang/"
        elif type == "zufang":
            search_path = "/zufang/"
        elif type == "xiaoqu":
            search_path = "/xiaoqu/"
        else:
            search_path = "/ershoufang/"
        
        # 构建查询参数
        params = []
        if query:
            params.append(f"title={quote(query)}")
        if district:
            params.append(f"district={quote(district)}")
        
        search_url = f"{base_url}{search_path}"
        if params:
            search_url += f"?{'&'.join(params)}"
        
        print(f"  [URL] {search_url}")
        
        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", search_url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ] + (["--stealth"] if stealth else []))
        
        if nav_result.get("error"):
            print(f"[错误] 导航失败: {nav_result['error']}")
            return []
        
        # 抓取列表页数据
        results = self._parse_list_page(port, tab_id, max_results)
        
        # 分页抓取（如果需要更多结果）
        page = 2
        while len(results) < max_results:
            page_url = f"{search_url}&page={page}"
            page_result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--port", str(port),
                "--tab", str(tab_id),
                "--goto", page_url,
                "--wait-for", "networkidle",
                "--timeout", str(wait_timeout),
            ] + (["--stealth"] if stealth else []))
            
            if page_result.get("error"):
                print(f"[警告] 第 {page} 页导航失败: {page_result['error']}")
                break
            
            page_data = self._parse_list_page(port, tab_id, max_results - len(results))
            if not page_data:
                break
            results.extend(page_data)
            page += 1
            
            delay = random_delay(2.0, 4.0)
            print(f"  [延迟] 翻页等待 {delay:.1f} 秒")
        
        # 过滤幽灵房
        results = self._filter_ghost_houses(results)
        
        # 限制结果数量
        results = results[:max_results]
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"lianjia_{city}_{type}", "json")
            save_results(results, output_dir, f"lianjia_{city}_{type}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条房源")
        return results
    
    def _parse_list_page(self, port: int, tab_id: str, limit: int) -> List[Dict]:
        """解析列表页数据"""
        # 使用 JS 提取房源数据
        js_code = f'''
        (function() {{
            var results = [];
            var items = document.querySelectorAll('.sellListContent li, .listItem, .ulContent li, .houseList li');
            if (items.length === 0) {{
                // 备用选择器
                items = document.querySelectorAll('[class*="list"][class*="item"], [class*="house"][class*="info"]');
            }}
            var count = 0;
            items.forEach(function(item) {{
                if (count >= {limit}) return;
                var titleEl = item.querySelector('.title, .sellListContentItemTitle, .listItemTitle');
                var priceEl = item.querySelector('.totalPrice, .price, .total');
                var unitPriceEl = item.querySelector('.unitPrice, .unitPriceValue');
                var infoEl = item.querySelector('.houseInfo, .listItemDescription');
                var positionEl = item.querySelector('.positionInfo, .flood .positionInfo');
                var followInfoEl = item.querySelector('.followInfo');
                
                if (titleEl) {{
                    var title = titleEl.textContent.trim();
                    var href = titleEl.querySelector('a') ? titleEl.querySelector('a').href : '';
                    var price = priceEl ? priceEl.textContent.trim() : '';
                    var unitPrice = unitPriceEl ? unitPriceEl.textContent.trim() : '';
                    var info = infoEl ? infoEl.textContent.trim() : '';
                    var position = positionEl ? positionEl.textContent.trim() : '';
                    var follow = followInfoEl ? followInfoEl.textContent.trim() : '';
                    
                    // 过滤幽灵房
                    var isGhost = false;
                    var combined = (title + info + position).toLowerCase();
                    {json.dumps(GHOST_FEATURES)}.forEach(function(f) {{
                        if (combined.indexOf(f.toLowerCase()) !== -1) isGhost = true;
                    }});
                    if (isGhost) return;
                    
                    results.push({{
                        title: title,
                        url: href,
                        price: price,
                        unit_price: unitPrice,
                        info: info,
                        position: position,
                        follow: follow,
                        source: 'lianjia',
                        scraped_at: new Date().toISOString()
                    }});
                    count++;
                }}
            }});
            return results;
        }})()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        if result.get("error"):
            print(f"[警告] JS 执行失败: {result['error']}")
            return []
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def _filter_ghost_houses(self, results: List[Dict]) -> List[Dict]:
        """过滤幽灵房"""
        filtered = []
        for r in results:
            is_ghost = False
            combined = (r.get('title', '') + r.get('info', '') + r.get('position', '')).lower()
            for feature in GHOST_FEATURES:
                if feature.lower() in combined:
                    is_ghost = True
                    break
            if not is_ghost:
                filtered.append(r)
        return filtered
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取房源详情页"""
        raise NotImplementedError("链家详情页抓取通过列表页已覆盖")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            # 检查链家首页是否可访问
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", "https://bj.lianjia.com",
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="链家房产搜索器")
    parser.add_argument("--city", default="bj", help="城市代码 (bj/sh/gz/sz等)")
    parser.add_argument("--type", default="ershoufang", choices=["ershoufang", "zufang", "xiaoqu"])
    parser.add_argument("--district", help="区域名称")
    parser.add_argument("--query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    
    args = parser.parse_args()
    
    searcher = LianjiaSearcher()
    results = searcher.search(
        query=args.query,
        city=args.city,
        type=args.type,
        district=args.district,
        max_results=args.max_results,
        port=args.port,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.timeout,
    )
    
    if results:
        print("\n" + json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
