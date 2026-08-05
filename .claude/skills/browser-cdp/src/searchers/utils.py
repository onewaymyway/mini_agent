#!/usr/bin/env python
"""
utils.py - 搜索器通用工具函数

提供随机延迟、UA 轮换、结果去重、文件保存等通用功能。
"""

import random
import time
import json
import hashlib
from typing import List, Dict, Optional, Any
from pathlib import Path
from datetime import datetime


# 常用 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.2088.76",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def random_delay(min_sec: float = 2.0, max_sec: float = 5.0) -> float:
    """随机延迟，返回实际延迟时间"""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    return delay


def get_random_ua() -> str:
    """获取随机 User-Agent"""
    return random.choice(USER_AGENTS)


def get_ua_by_index(index: int) -> str:
    """按索引获取 User-Agent（用于轮换）"""
    return USER_AGENTS[index % len(USER_AGENTS)]


def compute_simhash(text: str, dim: int = 64) -> int:
    """计算 SimHash（用于内容去重）
    
    简化实现：使用 MD5 哈希作为占位
    实际生产环境可使用 simhash 库
    """
    return int(hashlib.md5(text.encode('utf-8')).hexdigest(), 16)


def hamming_distance(hash1: int, hash2: int, dim: int = 64) -> int:
    """计算汉明距离"""
    xor = hash1 ^ hash2
    return bin(xor).count('1')


def dedup_by_url(results: List[Dict]) -> List[Dict]:
    """基于 URL 去重"""
    seen = set()
    unique = []
    for r in results:
        url = r.get('url', '')
        if url and url not in seen:
            seen.add(url)
            unique.append(r)
    return unique


def dedup_by_title(results: List[Dict], threshold: float = 0.9, dim: int = 64) -> List[Dict]:
    """基于标题相似度去重"""
    seen = []
    unique = []
    for r in results:
        title = r.get('title', '')
        is_dup = False
        for s in seen:
            # threshold=1.0 时使用精确匹配
            if threshold >= 1.0:
                if title == s.get('title', ''):
                    is_dup = True
                    break
            else:
                if hamming_distance(
                    compute_simhash(title),
                    compute_simhash(s.get('title', '')),
                    dim=dim
                ) < dim * (1 - threshold):
                    is_dup = True
                    break
        if not is_dup:
            unique.append(r)
            seen.append(r)
    return unique


def dedup_results(results: List[Dict], by: str = "url", threshold: float = 0.9) -> List[Dict]:
    """结果去重（统一入口）"""
    if by == "url":
        return dedup_by_url(results)
    elif by == "title":
        return dedup_by_title(results, threshold)
    else:
        return results


def save_results(
    results: List[Dict],
    output_dir: str,
    filename: Optional[str] = None,
    fmt: str = "json"
) -> str:
    """保存结果到文件"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"results_{timestamp}.{fmt}"
    
    path = Path(output_dir) / filename
    
    if fmt == "json":
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    elif fmt == "csv":
        import csv
        if not results:
            path.touch()
            return str(path)
        fieldnames = list(results[0].keys())
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
    elif fmt == "markdown":
        lines = ["# 搜索结果", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"## {i}. {r.get('title', 'N/A')}")
            lines.append(f"- 来源: {r.get('source', 'N/A')}")
            lines.append(f"- 链接: {r.get('url', 'N/A')}")
            if r.get('snippet'):
                lines.append(f"- 摘要: {r['snippet'][:100]}...")
            lines.append("")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    else:
        raise ValueError(f"不支持的格式: {fmt}")
    
    return str(path)


def parse_pagination_url(base_url: str, page: int) -> str:
    """解析分页 URL（通用模板）"""
    # 常见分页模式
    patterns = [
        ("page={}", lambda p: base_url.format(p)),
        ("&page={}", lambda p: base_url + f"&page={p}"),
        ("/p/{}", lambda p: base_url.format(p)),
        ("?p={}", lambda p: base_url + f"?p={p}"),
    ]
    for pattern, func in patterns:
        if pattern in base_url:
            return func(page)
    return base_url


def extract_domain(url: str) -> str:
    """提取域名"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split('/')[0]


def clean_text(text: str) -> str:
    """清理文本（去除多余空白、换行等）"""
    import re
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text


def truncate_text(text: str, max_len: int = 200) -> str:
    """截断文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def print_results(results: List[Dict]) -> None:
    """打印搜索结果（格式化输出）"""
    if not results:
        print("未找到结果")
        return
    
    print(f"\n共找到 {len(results)} 条结果：\n")
    for i, r in enumerate(results, 1):
        title = r.get('title', 'N/A')
        url = r.get('url', 'N/A')
        snippet = r.get('snippet', '')
        source = r.get('source', 'N/A')
        
        print(f"{i}. {title}")
        print(f"   来源: {source}")
        print(f"   链接: {url}")
        if snippet:
            print(f"   摘要: {snippet[:100]}...")
        print()


# 导出公共接口
__all__ = [
    "random_delay",
    "get_random_ua",
    "get_ua_by_index",
    "compute_simhash",
    "hamming_distance",
    "dedup_by_url",
    "dedup_by_title",
    "dedup_results",
    "save_results",
    "print_results",
    "parse_pagination_url",
    "extract_domain",
    "clean_text",
    "truncate_text",
]
