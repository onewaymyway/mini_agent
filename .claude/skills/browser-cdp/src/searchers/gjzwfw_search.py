#!/usr/bin/env python
"""
gjzwfw_search.py - 国家政务服务平台搜索器

使用 browser-cdp skill 搜索国家政务服务平台，获取政务服务事项、办事指南、办理进度等信息。

用法:
    python gjzwfw_search.py "营业执照"
    python gjzwfw_search.py "社保查询" --province 北京市 --output-dir ./gjzwfw_results
    python gjzwfw_search.py "企业开办" --port 9333

示例:
    python gjzwfw_search.py "不动产登记"
    python gjzwfw_search.py "公积金提取" --province 上海市 --output-dir ./results
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
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 国家政务服务平台专用配置 ==========
GJZWFW_BASE = "https://gjzwfw.www.gov.cn"
GJZWFW_SEARCH_URL = f"{GJZWFW_BASE}/bsfw/query.html"
GJZWFW_SERVICE_URL = f"{GJZWFW_BASE}/bsfw/index.html"

# 默认输出目录
GJZWFW_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "gjzwfw"


class GjzwfwSearcher(BaseSearcher):
    """国家政务服务平台搜索器"""

    @property
    def source_name(self) -> str:
        return "gjzwfw"

    @property
    def supported_types(self) -> List[str]:
        return ["service_search", "guide_search", "progress_query", "province_search"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
        province: Optional[str] = None,
    ) -> List[Dict]:
        """搜索政务服务信息

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (service/guide/progress/province/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数
            province: 省份名称（可选）

        Returns:
            搜索结果列表
        """
        print(f"[国家政务服务平台] 正在搜索: {query}")

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
        if search_type in ["service", "all"]:
            print(f"  [搜索] 政务服务事项...")
            service_results = self._search_service(query, port, tab_id, max_results, stealth, wait_timeout, province)
            results.extend(service_results)

        if search_type in ["guide", "all"]:
            print(f"  [搜索] 办事指南...")
            guide_results = self._search_guide(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(guide_results)

        if search_type in ["progress", "all"]:
            print(f"  [搜索] 办理进度...")
            progress_results = self._search_progress(query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(progress_results)

        if search_type in ["province", "all"] and province:
            print(f"  [搜索] {province} 政务服务...")
            province_results = self._search_province(province, query, port, tab_id, max_results, stealth, wait_timeout)
            results.extend(province_results)

        # 保存结果
        if results and output_dir:
            path = save_results(
                results,
                output_dir or str(GJZWFW_OUTPUT_DIR),
                f"gjzwfw_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return results

    def _search_service(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
        province: Optional[str] = None,
    ) -> List[Dict]:
        """搜索政务服务事项"""
        # 构建搜索 URL
        search_url = f"{GJZWFW_BASE}/bsfw/query.html?keyword={quote(query)}"
        if province:
            search_url += f"&province={quote(province)}"
        
        print(f"    [URL] 服务搜索: {search_url}")

        # 导航到搜索页面
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".service-list, .result-list, .item, .query-result",
            "--timeout", str(wait_timeout),
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 服务搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取服务事项信息
        js_service = r"""
(() => {
  const results = [];
  const selectors = [
    '.service-list .item',
    '.result-list .item',
    '.query-result .item',
    '.service-item',
    '.list-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a, .service-name');
    const linkEl = item.querySelector('a');
    const deptEl = item.querySelector('.dept, .department, .org, .authority');
    const typeEl = item.querySelector('.type, .category, .service-type');
    const levelEl = item.querySelector('.level, .grade, .admin-level');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    const type = typeEl ? typeEl.innerText.trim() : '';
    const level = levelEl ? levelEl.innerText.trim() : '';
    
    if (title && title.length > 3) {
      results.push({
        title: title,
        url: url,
        department: dept,
        service_type: type,
        admin_level: level,
        type: 'service',
        source_site: 'gjzwfw',
      });
    }
  });
  
  return results;
})()
"""
        
        js_service = js_service.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_service,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_guide(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索办事指南"""
        search_url = f"{GJZWFW_BASE}/bsfw/guide.html?keyword={quote(query)}"
        print(f"    [URL] 指南搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".guide-list, .result-list, .guide-item",
            "--timeout", str(wait_timeout),
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 指南搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取办事指南信息
        js_guide = r"""
(() => {
  const results = [];
  const selectors = [
    '.guide-list .item',
    '.result-list .item',
    '.guide-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a, .guide-title');
    const linkEl = item.querySelector('a');
    const deptEl = item.querySelector('.dept, .department, .org');
    const materialEl = item.querySelector('.material, .docs, .required-docs');
    const timeEl = item.querySelector('.time, .duration, .handle-time');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const dept = deptEl ? deptEl.innerText.trim() : '';
    const material = materialEl ? materialEl.innerText.trim() : '';
    const time = timeEl ? timeEl.innerText.trim() : '';
    
    if (title && title.length > 3) {
      results.push({
        title: title,
        url: url,
        department: dept,
        required_materials: material,
        handle_time: time,
        type: 'guide',
        source_site: 'gjzwfw',
      });
    }
  });
  
  return results;
})()
"""
        
        js_guide = js_guide.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_guide,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_progress(
        self,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索办理进度"""
        search_url = f"{GJZWFW_BASE}/bsfw/progress.html?keyword={quote(query)}"
        print(f"    [URL] 进度搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".progress-list, .result-list, .progress-item",
            "--timeout", str(wait_timeout),
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 进度搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取办理进度信息
        js_progress = r"""
(() => {
  const results = [];
  const selectors = [
    '.progress-list .item',
    '.result-list .item',
    '.progress-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a');
    const statusEl = item.querySelector('.status, .progress-status, .step');
    const dateEl = item.querySelector('.date, .time, .update-date');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const status = statusEl ? statusEl.innerText.trim() : '';
    const date = dateEl ? dateEl.innerText.trim() : '';
    
    if (title && title.length > 3) {
      results.push({
        title: title,
        url: url,
        status: status,
        update_date: date,
        type: 'progress',
        source_site: 'gjzwfw',
      });
    }
  });
  
  return results;
})()
"""
        
        js_progress = js_progress.replace('max_results', str(max_results))
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_progress,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []

    def _search_province(
        self,
        province: str,
        query: str,
        port: int,
        tab_id: str,
        max_results: int,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """搜索指定省份的政务服务"""
        search_url = f"{GJZWFW_BASE}/bsfw/province.html?province={quote(province)}&keyword={quote(query)}"
        print(f"    [URL] 省份搜索: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".province-list, .result-list, .province-item",
            "--timeout", str(wait_timeout),
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"    [错误] 省份搜索导航失败")
            return []

        time.sleep(2.0)

        # 提取省份服务信息
        js_province = r"""
(() => {
  const results = [];
  const selectors = [
    '.province-list .item',
    '.result-list .item',
    '.province-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= max_results) return;
    
    const titleEl = item.querySelector('.title, .name, h3, h4, a');
    const linkEl = item.querySelector('a');
    const cityEl = item.querySelector('.city, .location, .region');
    const typeEl = item.querySelector('.type, .category');
    
    const title = titleEl ? titleEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    const city = cityEl ? cityEl.innerText.trim() : '';
    const type = typeEl ? typeEl.innerText.trim() : '';
    
    if (title && title.length > 3) {
      results.push({
        title: title,
        url: url,
        city: city,
        service_type: type,
        province: province,
        type: 'province_service',
        source_site: 'gjzwfw',
      });
    }
  });
  
  return results;
})()
"""
        
        js_province = js_province.replace('max_results', str(max_results))
        js_province = js_province.replace('province', province)
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_province,
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
        """获取指定页面详情"""
        print(f"[国家政务服务平台] 正在获取详情: {url}")

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
            "--wait-selector", ".content, .detail, article, .text, .service-detail",
            "--timeout", "30",
            "--stealth" if stealth else "",
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
    department: '',
    service_type: '',
    handle_time: '',
    materials: [],
    process: [],
  };
  
  // 提取正文
  const contentEl = document.querySelector('.content, .detail, article, .text, .service-detail, .main-content');
  if (contentEl) {
    result.content = contentEl.innerText.trim().substring(0, 5000);
  }
  
  // 提取办理部门
  const deptEl = document.querySelector('.dept, .department, .org, .authority, .handle-dept');
  if (deptEl) {
    result.department = deptEl.innerText.trim();
  }
  
  // 提取服务类型
  const typeEl = document.querySelector('.type, .category, .service-type');
  if (typeEl) {
    result.service_type = typeEl.innerText.trim();
  }
  
  // 提取办理时限
  const timeEl = document.querySelector('.time, .duration, .handle-time, .limit-time');
  if (timeEl) {
    result.handle_time = timeEl.innerText.trim();
  }
  
  // 提取所需材料
  const materialEls = document.querySelectorAll('.material, .docs, .required-docs, .material-item');
  materialEls.forEach(el => {
    const text = el.innerText.trim();
    if (text) result.materials.push(text);
  });
  
  // 提取办理流程
  const processEls = document.querySelectorAll('.process, .step, .flow-item');
  processEls.forEach(el => {
    const text = el.innerText.trim();
    if (text) result.process.push(text);
  });
  
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

    def get_province_list(
        self,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
    ) -> List[Dict]:
        """获取省份列表"""
        print(f"[国家政务服务平台] 正在获取省份列表...")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 导航到首页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", GJZWFW_BASE,
            "--wait-selector", ".province-list, .region-list, .province-item",
            "--timeout", "30",
            "--stealth" if stealth else "",
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 首页导航失败")
            return []

        time.sleep(2.0)

        # 提取省份列表
        js_province_list = r"""
(() => {
  const provinces = [];
  const selectors = [
    '.province-list .item',
    '.region-list .item',
    '.province-item',
    '.region-item'
  ];
  
  let items = [];
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    const nameEl = item.querySelector('.name, .title, a');
    const linkEl = item.querySelector('a');
    
    const name = nameEl ? nameEl.innerText.trim() : '';
    const url = linkEl ? linkEl.href : '';
    
    if (name && name.length > 0) {
      provinces.push({
        name: name,
        url: url,
        type: 'province'
      });
    }
  });
  
  return provinces;
})()
"""
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_province_list,
        ])

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass

        return []


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="国家政务服务平台搜索器 - 获取政务服务事项、办事指南、办理进度",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python gjzwfw_search.py "营业执照"
    python gjzwfw_search.py "社保查询" --province 北京市 --output-dir ./gjzwfw_results
    python gjzwfw_search.py "企业开办" --port 9333
    python gjzwfw_search.py "不动产登记" --type guide
"""
    )

    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", type=str, default="all",
                        choices=["service", "guide", "progress", "province", "all"],
                        help="搜索类型 (默认: all)")
    parser.add_argument("--province", type=str, default=None, help="省份名称（可选）")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = GjzwfwSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
        max_results=args.max_results,
        province=args.province,
    )

    if results:
        print(f"\n[结果] 共获取 {len(results)} 条政务服务信息")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到政务服务信息")


if __name__ == "__main__":
    main()
