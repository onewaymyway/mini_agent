#!/usr/bin/env python
"""
gsxt_search.py - 国家企业信用信息公示系统搜索器

使用 browser-cdp skill 搜索国家企业信用信息公示系统，获取企业工商信息、经营异常、严重违法等。

用法:
    python gsxt_search.py "阿里巴巴"
    python gsxt_search.py "腾讯" --type abnormal --output-dir ./gsxt_results
    python gsxt_search.py "华为" --type illegal --port 9333

示例:
    python gsxt_search.py "百度"
    python gsxt_search.py "经营异常" --type abnormal --output-dir ./results
"""

import argparse
import json
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
from src.searchers.utils import random_delay, save_results
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 国家企业信用信息公示系统专用配置 ==========
GSXT_BASE = "https://www.gsxt.gov.cn"
GSXT_SEARCH_URL = f"{GSXT_BASE}/corp-query-search-1.html"
GSXT_ABNORMAL_URL = f"{GSXT_BASE}/corp-query-put-enterprise-abnormal-list-1.html"
GSXT_ILLEGAL_URL = f"{GSXT_BASE}/corp-query-put-enterprise-illegal-list-1.html"

# 默认输出目录
GSXT_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "gsxt"


class GSXTSearcher(BaseSearcher):
    """国家企业信用信息公示系统搜索器"""

    @property
    def source_name(self) -> str:
        return "gsxt"

    @property
    def supported_types(self) -> List[str]:
        return ["enterprise_search", "abnormal_search", "illegal_search", "enterprise_info"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        province: Optional[str] = None,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索企业信息

        Args:
            query: 搜索关键词（企业名称）
            search_type: 搜索类型 (enterprise/abnormal/illegal/all)
            province: 省份
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[国家企业信用信息公示系统] 正在搜索: {query}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        results = []

        # 根据搜索类型执行不同搜索
        if search_type in ["enterprise", "all"]:
            print(f"  [搜索] 企业信息...")
            enterprise_results = self._search_enterprise(query, province, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(enterprise_results)

        if search_type in ["abnormal", "all"]:
            print(f"  [搜索] 经营异常...")
            abnormal_results = self._search_abnormal(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(abnormal_results)

        if search_type in ["illegal", "all"]:
            print(f"  [搜索] 严重违法...")
            illegal_results = self._search_illegal(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(illegal_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(GSXT_OUTPUT_DIR),
                f"gsxt_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_enterprise(
        self,
        query: str,
        province: Optional[str],
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索企业信息"""
        search_url = f"{GSXT_SEARCH_URL}?searchword={quote(query)}"
        if province:
            search_url += f"&province={quote(province)}"
        print(f"    [URL] 企业搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result, .result-list, .enterprise-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 企业搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取企业信息
        js_enterprise = r"""
(() => {
  const results = [];
  const selectors = [
    '.search-result .item',
    '.result-list .item',
    '.enterprise-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .enterprise-name, h3, h4, .title');
    const linkEl = item.querySelector('a');
    const codeEl = item.querySelector('.code, .reg-code, .统一社会信用代码');
    const legalEl = item.querySelector('.legal, .legal-rep, .法定代表人');
    const capitalEl = item.querySelector('.capital, .registered-capital, .注册资本');
    const dateEl = item.querySelector('.date, .establish-date, .成立日期');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const code = codeEl ? codeEl.innerText.trim() : '';
    const legal = legalEl ? legalEl.innerText.trim() : '';
    const capital = capitalEl ? capitalEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        reg_code: code,
        legal_representative: legal,
        registered_capital: capital,
        establish_date: date,
        search_type: 'enterprise',
        source_site: 'gsxt',
      });
    }
  });
  
  return results;
})()
"""
        
        js_enterprise = js_enterprise.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_enterprise,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_abnormal(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索经营异常"""
        search_url = f"{GSXT_ABNORMAL_URL}?searchword={quote(query)}"
        print(f"    [URL] 经营异常搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".abnormal-list, .result-list, .abnormal-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 经营异常搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取经营异常信息
        js_abnormal = r"""
(() => {
  const results = [];
  const selectors = [
    '.abnormal-list .item',
    '.result-list .item',
    '.abnormal-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .enterprise-name, h3, h4');
    const linkEl = item.querySelector('a');
    const reasonEl = item.querySelector('.reason, .abnormal-reason, .经营异常原因');
    const dateEl = item.querySelector('.date, .add-date, .列入日期');
    const deptEl = item.querySelector('.dept, .department, .作出机关');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const reason = reasonEl ? reasonEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        abnormal_reason: reason,
        add_date: date,
        department: dept,
        search_type: 'abnormal',
        source_site: 'gsxt',
      });
    }
  });
  
  return results;
})()
"""
        
        js_abnormal = js_abnormal.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_abnormal,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_illegal(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索严重违法"""
        search_url = f"{GSXT_ILLEGAL_URL}?searchword={quote(query)}"
        print(f"    [URL] 严重违法搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".illegal-list, .result-list, .illegal-item, table",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 严重违法搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取严重违法信息
        js_illegal = r"""
(() => {
  const results = [];
  const selectors = [
    '.illegal-list .item',
    '.result-list .item',
    '.illegal-item',
    'table tbody tr'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const nameEl = item.querySelector('.name, .enterprise-name, h3, h4');
    const linkEl = item.querySelector('a');
    const reasonEl = item.querySelector('.reason, .illegal-reason, .严重违法原因');
    const dateEl = item.querySelector('.date, .add-date, .列入日期');
    const deptEl = item.querySelector('.dept, .department, .作出机关');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const reason = reasonEl ? reasonEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    
    if (name && name.length > 2) {
      results.push({
        name: name,
        url: url,
        illegal_reason: reason,
        add_date: date,
        department: dept,
        search_type: 'illegal',
        source_site: 'gsxt',
      });
    }
  });
  
  return results;
})()
"""
        
        js_illegal = js_illegal.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_illegal,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> Dict:
        """获取企业详情"""
        print(f"[国家企业信用信息公示系统] 正在获取详情: {url}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".content, .detail, article, .enterprise-detail",
            "--timeout", "30",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 详情页导航失败")
            return {}

        time.sleep(2.0)

        # 提取详情内容
        js_detail = r"""
(() => {
  const result = {
    title: document.title,
    url: window.location.href,
    content: '',
    name: '',
    reg_code: '',
    legal_representative: '',
    registered_capital: '',
    establish_date: '',
    address: '',
    business_scope: '',
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .enterprise-detail, .text');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 3000);
  }
  
  // 提取企业名称
  const nameEl = document.querySelector('.enterprise-name, .name, h1, h2');
  if (nameEl) {
    result.name = nameEl.innerText.trim();
  }
  
  // 提取统一社会信用代码
  const codeEl = document.querySelector('.reg-code, .统一社会信用代码');
  if (codeEl) {
    result.reg_code = codeEl.innerText.trim();
  }
  
  // 提取法定代表人
  const legalEl = document.querySelector('.legal-rep, .法定代表人');
  if (legalEl) {
    result.legal_representative = legalEl.innerText.trim();
  }
  
  // 提取注册资本
  const capitalEl = document.querySelector('.registered-capital, .注册资本');
  if (capitalEl) {
    result.registered_capital = capitalEl.innerText.trim();
  }
  
  // 提取成立日期
  const dateEl = document.querySelector('.establish-date, .成立日期');
  if (dateEl) {
    result.establish_date = dateEl.innerText.trim();
  }
  
  // 提取地址
  const addressEl = document.querySelector('.address, .地址');
  if (addressEl) {
    result.address = addressEl.innerText.trim();
  }
  
  // 提取经营范围
  const scopeEl = document.querySelector('.business-scope, .经营范围');
  if (scopeEl) {
    result.business_scope = scopeEl.innerText.trim().substring(0, 1000);
  }
  
  return result;
})()
"""
        
        detail_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_detail,
        ])

        if detail_result.returncode == 0:
            try:
                return json.loads(detail_result.stdout)
            except json.JSONDecodeError:
                pass

        return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="国家企业信用信息公示系统搜索器 - 获取企业工商信息、经营异常、严重违法",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python gsxt_search.py "阿里巴巴"
    python gsxt_search.py "腾讯" --type abnormal --output-dir ./gsxt_results
    python gsxt_search.py "华为" --type illegal --port 9333
"""
    )

    parser.add_argument("query", help="搜索关键词（企业名称）")
    parser.add_argument("--type", type=str, default="all",
                        choices=["enterprise", "abnormal", "illegal", "all"],
                        help="搜索类型 (默认: all)")
    parser.add_argument("--province", type=str, default=None, help="省份")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = GSXTSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        province=args.province,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        max_results=args.max_results,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条企业信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到企业信息")


if __name__ == "__main__":
    main()
