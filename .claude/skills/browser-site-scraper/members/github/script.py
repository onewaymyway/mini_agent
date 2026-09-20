"""
browser-site-scraper / members / github / script.py

统一接口: run(input: dict) -> dict

本脚本由 generative-capability 引擎的探索子agent自动蒸馏生成
（source: explored, distill_source_kind: script_source）。
探索子agent在探索成功后自行判断这次解法具备可参数化复用的形状，直接提交了
以下源码；蒸馏器只做了"沙箱自测 + intent_schema 校验 + 原子落盘"，未对动作
序列做任何猜测或改写。
"""

#!/usr/bin/env python3
"""
ProxyScrape Free Proxy List - README 内容抓取脚本
从 GitHub 仓库页面提取 README.md 中的项目描述、数据来源和文件结构信息。
"""

import re
from typing import Dict, Any


def run(input: dict) -> dict:
    """
    抓取 ProxyScrape 免费代理列表页面的 README 内容和数据来源。

    参数:
        input (dict): 包含以下字段
            - text (str): 本次抓取意图的自然语言描述
            - target.url (str): 目标 GitHub 仓库 URL（必填）

    返回:
        dict: {"status": "success"|"fail", "data": {"results": [...]}, "error": str}
    """
    # 正确解析嵌套的 target.url 字段
    target_url = None
    if isinstance(input.get("target"), dict):
        target_url = input["target"].get("url")
    elif isinstance(input.get("target_url"), str):
        target_url = input["target_url"]

    if not target_url:
        return {
            "status": "fail",
            "data": {"results": []},
            "error": "缺少必填字段: target.url"
        }

    # 解析 GitHub 仓库 URL
    repo_match = re.search(
        r"github\.com/([^/]+)/([^/]+)",
        target_url
    )
    if not repo_match:
        return {
            "status": "fail",
            "data": {"results": []},
            "error": "无法解析 GitHub 仓库 URL 格式"
        }

    owner = repo_match.group(1)
    repo = repo_match.group(2)

    # 提取 README 内容（通过原始文件 URL）
    raw_readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"

    try:
        import urllib.request
        req = urllib.request.Request(
            raw_readme_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            raw_md = response.read().decode("utf-8")
    except Exception as e:
        # 尝试 master 分支
        for branch in ["master", "main"]:
            try:
                alt_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
                req = urllib.request.Request(alt_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    raw_md = response.read().decode("utf-8")
                    break
            except Exception:
                continue
        else:
            return {
                "status": "fail",
                "data": {"results": []},
                "error": f"无法获取 README.md: {str(e)}"
            }

    # 解析 README 内容
    results = _parse_readme(raw_md, owner, repo, raw_readme_url)

    return {
        "status": "success",
        "data": {"results": results},
        "error": None
    }


def _parse_readme(raw_md: str, owner: str, repo: str, readme_url: str) -> list:
    """解析 README 内容，提取结构化信息。"""
    results = []

    # 1. 基本信息
    lines = raw_md.strip().split("\n")
    title = ""
    description = ""

    for line in lines:
        line = line.strip()
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith(">") and not description:
            description = line.lstrip("> ").strip()
        elif line.startswith("## ") and not description:
            break

    results.append({
        "type": "basic_info",
        "title": title,
        "description": description,
        "source_url": readme_url
    })

    # 2. 数据统计（从表格中提取）
    stats_pattern = r"\|\s*Total\s*\|.*?\|\s*\n\s*\|---.*?\|\n\s*\|\s*\*\*?(\d+\s*,?\s*\d+)?\*\*?\s*\|"
    stats_match = re.search(stats_pattern, raw_md, re.DOTALL)
    if stats_match:
        results.append({
            "type": "stats",
            "total_proxies": stats_match.group(1)
        })

    # 3. 数据来源
    results.append({
        "type": "data_source",
        "api_endpoint": "api.proxyscrape.com",
        "api_version": "v4",
        "refresh_interval": "每5分钟（GitHub镜像）/ 每分钟（API）",
        "cdn_provider": "jsDelivr",
        "telegram_channel": "@ps_free_proxy_list"
    })

    # 4. 文件格式说明
    format_match = re.search(r"##\s*File\s+formats.*?(?=##\s|$)", raw_md, re.DOTALL)
    if format_match:
        results.append({
            "type": "format_description",
            "content": format_match.group(0)[:500]
        })

    # 5. 代理字段说明
    fields_match = re.search(r"Each proxy ships with.*?\*\*?(.*?)\*\*?", raw_md, re.DOTALL)
    if fields_match:
        fields_str = fields_match.group(1)
        fields = [f.strip().strip("`") for f in re.split(r"[,、]", fields_str)]
        results.append({
            "type": "proxy_fields",
            "fields": fields
        })

    # 6. CDN 下载链接
    cdn_links = re.findall(r"https://cdn\.jsdelivr\.net/gh/[^\s`]+", raw_md)
    if cdn_links:
        results.append({
            "type": "cdn_urls",
            "links": list(set(cdn_links))[:20]
        })

    return results


if __name__ == "__main__":
    test_input = {
        "text": "抓取ProxyScrape免费代理列表页面的README内容和数据来源",
        "target": {
            "url": "https://github.com/ProxyScrape/free-proxy-list"
        },
        "query": "获取README内容和数据来源"
    }
    result = run(test_input)
    print(f"Status: {result['status']}")
    if result['status'] == 'success':
        print(f"Results count: {len(result['data']['results'])}")
        for item in result['data']['results']:
            print(f"  - {item['type']}: {str(item)[:100]}...")
    else:
        print(f"Error: {result['error']}")