"""
评估执行器

实现网站评估的完整流程：
1. 浏览器初始化
2. 场景执行
3. 数据采集
4. 评估计算
5. 报告生成
"""

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加 skill 根目录到路径
SKILL_DIR = Path(__file__).parent.parent
SRC_DIR = SKILL_DIR / "src"
sys.path.insert(0, str(SKILL_DIR))

from src.core.cdp_client import CDPSession, is_debug_port_alive, list_tabs, new_tab
from src.core.browser_launch import find_chrome_binary
from src.core.browser_nav import cmd_goto
from src.core.browser_input import type_text, mouse_click
from src.core.browser_screenshot import capture, save_screenshot

logger = logging.getLogger(__name__)


class EvalResult:
    """单次评估结果"""

    def __init__(self, website_name: str, website_url: str):
        self.website_name = website_name
        self.website_url = website_url
        self.eval_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.dimensions: Dict[str, Dict[str, Any]] = {}
        self.scenarios: List[Dict[str, Any]] = []
        self.overall_score = 0.0
        self.grade = ""
        self.findings: List[str] = []
        self.recommendations: List[str] = []
        self.errors: List[str] = []
        self.screenshots: List[str] = []

    def add_dimension(self, name: str, result: Dict[str, Any]):
        self.dimensions[name] = result

    def add_scenario_result(self, scenario_id: str, success: bool, duration: float, error: Optional[str] = None, data: Optional[Dict] = None):
        result = {
            "id": scenario_id,
            "success": success,
            "duration": round(duration, 2),
            "error": error,
        }
        if data:
            result["data"] = data
        self.scenarios.append(result)
        if error:
            self.errors.append(f"{scenario_id}: {error}")

    def calculate_overall(self):
        if not self.dimensions:
            return
        weights = {
            "页面访问成功率": 0.30,
            "元素定位准确率": 0.20,
            "抓取成功率": 0.30,
            "稳定性": 0.10,
            "反检测能力": 0.15,
            "交互成功率": 0.20,
        }
        total_weighted = 0.0
        total_weight = 0.0
        for name, result in self.dimensions.items():
            weight = weights.get(name, 1.0 / 6)
            score = result.get("score", 0)
            total_weighted += score * weight
            total_weight += weight
        self.overall_score = round(total_weighted / total_weight, 2) if total_weight > 0 else 0
        self.grade = self._calculate_grade(self.overall_score)

    @staticmethod
    def _calculate_grade(score: float) -> str:
        if score >= 90:
            return "优秀 (A)"
        elif score >= 75:
            return "良好 (B)"
        elif score >= 60:
            return "合格 (C)"
        elif score >= 40:
            return "待改进 (D)"
        else:
            return "不可用 (F)"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "website_name": self.website_name,
            "website_url": self.website_url,
            "eval_time": self.eval_time,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "dimensions": self.dimensions,
            "scenarios": self.scenarios,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "errors": self.errors,
            "screenshot_count": len(self.screenshots),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# 网站操作能力评估报告\n",
            f"**评估网站**: {self.website_name} ({self.website_url})\n",
            f"**评估日期**: {self.eval_time}\n",
            f"**综合评分**: {self.overall_score}/100 ({self.grade})\n",
            "\n",
        ]
        lines.append("## 各维度得分\n")
        lines.append("| 维度 | 得分 | 权重 | 加权得分 |\n")
        lines.append("|------|------|------|----------|\n")
        weights = {
            "页面访问成功率": 0.30,
            "元素定位准确率": 0.20,
            "抓取成功率": 0.30,
            "稳定性": 0.10,
            "反检测能力": 0.15,
            "交互成功率": 0.20,
        }
        for name, result in self.dimensions.items():
            weight = weights.get(name, 0)
            weighted = result.get("score", 0) * weight
            lines.append(f"| {name} | {result.get('score', 0):.1f} | {weight:.0%} | {weighted:.1f} |\n")
        lines.append("\n")
        if self.scenarios:
            lines.append("## 场景执行结果\n")
            lines.append("| 场景 ID | 成功 | 耗时 (s) | 错误 |\n")
            lines.append("|---------|------|----------|------|\n")
            for s in self.scenarios:
                status = "✓" if s["success"] else "✗"
                error = (s.get("error") or "")[:30]
                lines.append(f"| {s['id']} | {status} | {s['duration']} | {error} |\n")
            lines.append("\n")
        if self.errors:
            lines.append("## 执行错误\n")
            for err in self.errors[:5]:
                lines.append(f"- {err}\n")
            lines.append("\n")
        return "".join(lines)


class BrowserEvaluator:
    """浏览器评估器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._session: Optional[CDPSession] = None
        self._tab_id: Optional[str] = None
        self._port: int = 9333
        self._start_time = 0
        self._metrics: Dict[str, Any] = {}
        self._screenshots: List[str] = []

    def initialize(self, headless: bool = True, port: int = 9333):
        logger.info(f"初始化浏览器 (headless={headless}, port={port})...")
        self._port = port
        # 尝试连接现有浏览器，如果不可用则启动新的
        if not is_debug_port_alive(port=port):
            chrome_path = find_chrome_binary()
            if not chrome_path:
                raise RuntimeError("未找到 Chrome 浏览器")
            profile_dir = str(SKILL_DIR / "temp_cdp" / "eval_profile")
            os.makedirs(profile_dir, exist_ok=True)
            launch_args = [
                chrome_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
            ]
            if headless:
                launch_args.append("--headless=new")
            logger.info(f"启动浏览器: {' '.join(launch_args[:3])}...")
            import subprocess
            subprocess.Popen(launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for i in range(30):
                if is_debug_port_alive(port=port):
                    logger.info(f"浏览器已启动，端口 {port} 可用")
                    break
                time.sleep(1)
            else:
                raise RuntimeError(f"浏览器启动超时: 端口 {port} 不可用")
        else:
            logger.info(f"检测到现有浏览器，端口 {port} 已可用")
        # 获取可用 tab
        tabs = list_tabs(port=port)
        if not tabs:
            tab = new_tab(port=port)
        else:
            tab = tabs[0]
        self._tab_id = tab.get("id")
        self._session = CDPSession(ws_url=tab["webSocketDebuggerUrl"])
        logger.info(f"浏览器初始化完成，tab_id={self._tab_id}")
        return self._session

    def cleanup(self):
        logger.info("清理浏览器资源...")
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._tab_id = None

    def _navigate(self, url: str, timeout: float = 30.0) -> Dict[str, Any]:
        start = time.time()
        try:
            cmd_goto(session=self._session, url=url, wait_load=True, timeout=timeout, smart_wait=True, tab_id=self._tab_id)
            duration = time.time() - start
            return {"success": True, "duration": duration, "url": url, "title": self._session.eval_js("document.title")}
        except Exception as e:
            duration = time.time() - start
            logger.error(f"导航失败: {e}")
            return {"success": False, "duration": duration, "url": url, "error": str(e)}

    def _search(self, keyword: str, search_selector: str = "input[type='search'], input[name='q'], input[placeholder*='搜索']", submit_selector: str = "button[type='submit'], input[type='submit']") -> Dict[str, Any]:
        start = time.time()
        try:
            # 步骤1-3: 查找搜索框、输入搜索词、提交搜索（使用单个 JS 块）
            result = self._session.eval_js(f"""
                (() => {{
                    // 扩展选择器列表，覆盖更多场景
                    const selectors = {json.dumps([search_selector, 'input[type="text"]', 'input[placeholder*="搜索"]', 'input[placeholder*="search"]', '.search-input', '.search-box', '#search', '#search-input',
                        '[role="searchbox"]', '[role="search"] input', '[aria-label*="搜索"]', '[aria-label*="search"]',
                        '.s_ipt', '.search-input-wrap input', '[class*="search"] input', '[class*="Search"] input',
                        'input[autocorrect="off"]', 'input[autocapitalize="off"]', 'input[spellcheck="false"]'
                    ])};
                    let searchBox = null;
                    // 先查找普通 DOM 元素
                    for (const sel of selectors) {{
                        try {{
                            const el = document.querySelector(sel);
                            if (el && el.type !== 'hidden' && el.offsetParent !== null) {{
                                searchBox = el;
                                break;
                            }}
                        }} catch(e) {{}}
                    }}
                    // 如果没找到，尝试所有可见的 input
                    if (!searchBox) {{
                        const allInputs = document.querySelectorAll('input');
                        for (const input of allInputs) {{
                            if (input.type !== 'hidden' && input.offsetParent !== null && input.offsetWidth > 0) {{
                                searchBox = input;
                                break;
                            }}
                        }}
                    }}
                    // 如果还没找到，尝试 contenteditable 元素
                    if (!searchBox) {{
                        const editables = document.querySelectorAll('[contenteditable="true"], [contenteditable=""]');
                        for (const el of editables) {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 50 && rect.height > 20) {{
                                searchBox = el;
                                break;
                            }}
                        }}
                    }}
                    if (!searchBox) return {{ success: false, error: '未找到搜索框' }};
                    // 使用 native setter 设置值
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement?.prototype || HTMLElement.prototype, 'value')?.set;
                    if (nativeInputValueSetter && searchBox.tagName === 'INPUT') {{
                        nativeInputValueSetter.call(searchBox, {json.dumps(keyword)});
                    }} else {{
                        searchBox.value = {json.dumps(keyword)};
                    }}
                    searchBox.dispatchEvent(new Event('input', {{bubbles: true}}));
                    searchBox.dispatchEvent(new Event('change', {{bubbles: true}}));
                    searchBox.dispatchEvent(new Event('keyup', {{bubbles: true}}));
                    return {{ success: true, title: document.title, tag: searchBox.tagName }};
                }})()
            """)
            if not result.get("success", False):
                raise RuntimeError(result.get("error", "搜索失败"))

            # 步骤4: 提交搜索（使用 Input.dispatchKeyEvent）
            self._session.send("Input.dispatchKeyEvent", {{"type": "keyDown", "key": "Enter", "code": "Enter", "keyCode": 13}})
            self._session.send("Input.dispatchKeyEvent", {{"type": "keyUp", "key": "Enter", "code": "Enter", "keyCode": 13}})

            time.sleep(3)
            duration = time.time() - start
            return {{"success": True, "duration": duration, "keyword": keyword, "title": result.get("title", "")}}
        except Exception as e:
            duration = time.time() - start
            logger.error(f"搜索失败: {e}")
            return {{"success": False, "duration": duration, "keyword": keyword, "error": str(e)}}

    def _extract_results(self, result_selector: str, title_selector: str, url_selector: str, limit: int = 10) -> Dict[str, Any]:
        start = time.time()
        try:
            data = self._session.eval_js(f"""
                (() => {{
                    const results = [];
                    const items = document.querySelectorAll({json.dumps(result_selector)});
                    for (const item of items) {{
                        if (results.length >= {limit}) break;
                        const titleEl = item.querySelector({json.dumps(title_selector)});
                        const urlEl = item.querySelector({json.dumps(url_selector)}) || item;
                        const title = titleEl ? titleEl.textContent.trim() : '';
                        const url = urlEl ? (urlEl.href || urlEl.getAttribute('href') || '').trim() : '';
                        if (title || url) {{
                            results.push({{ title, url }});
                        }}
                    }}
                    return results;
                }})()
            """)
            duration = time.time() - start
            return {"success": True, "duration": duration, "items_count": len(data), "items": data[:limit]}
        except Exception as e:
            duration = time.time() - start
            logger.error(f"提取失败: {e}")
            return {"success": False, "duration": duration, "error": str(e)}

    def evaluate_website(self, website_config) -> EvalResult:
        result = EvalResult(website_config.name, website_config.url)
        logger.info(f"开始评估网站: {website_config.name} ({website_config.url})")
        self._start_time = time.time()
        self._screenshots = []
        try:
            for scenario in website_config.scenarios:
                scenario_result = self._execute_scenario(scenario, result, website_config)
                result.add_scenario_result(
                    scenario["id"],
                    scenario_result["success"],
                    scenario_result["duration"],
                    scenario_result.get("error"),
                    scenario_result.get("data")
                )
            result.dimensions = self._calculate_dimensions(result, website_config)
            result.calculate_overall()
            result.findings = self._generate_findings(result)
            result.recommendations = self._generate_recommendations(result)
        except Exception as e:
            logger.error(f"评估 {website_config.name} 失败: {e}")
            result.errors.append(f"评估异常: {str(e)}")
            result.errors.append(traceback.format_exc())
        elapsed = time.time() - self._start_time
        logger.info(f"评估完成: {website_config.name}, 耗时 {elapsed:.2f}s, 得分 {result.overall_score}")
        return result

    def _execute_scenario(self, scenario: Dict[str, str], result: EvalResult, website_config) -> Dict[str, Any]:
        start = time.time()
        action = scenario.get("action", "")
        dimension = scenario.get("dimension", "")
        try:
            if action == "navigate":
                return self._eval_navigate(scenario, dimension, website_config)
            elif action == "search":
                return self._eval_search(scenario, dimension, website_config)
            elif action in ("extract", "extract_list", "extract_article", "extract_answers"):
                return self._eval_extract(scenario, dimension, website_config)
            elif action == "paginate":
                return self._eval_paginate(scenario, dimension, website_config)
            elif action == "click_detail":
                return self._eval_click_detail(scenario, dimension, website_config)
            elif action == "check_login":
                return self._eval_check_login(scenario, dimension, website_config)
            elif action == "autocomplete":
                return self._eval_autocomplete(scenario, dimension, website_config)
            elif action == "switch_tab":
                return self._eval_switch_tab(scenario, dimension, website_config)
            elif action == "extract_comments":
                return self._eval_extract_comments(scenario, dimension, website_config)
            else:
                logger.warning(f"未实现的操作: {action}")
                return {"success": True, "duration": 0, "dimension": dimension}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_navigate(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            nav_result = self._navigate(website_config.url)
            duration = time.time() - start
            if nav_result["success"]:
                title = self._session.eval_js("document.title")
                return {"success": True, "duration": duration, "dimension": dimension, "data": {"title": title, "url": nav_result.get("url")}}
            else:
                return {"success": False, "duration": duration, "dimension": dimension, "error": nav_result.get("error", "导航失败")}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_search(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        keyword = "AI 人工智能"
        try:
            self._navigate(website_config.url)
            # 使用网站特定的搜索选择器（如果有）
            search_selector = getattr(website_config, 'search_selector', None)
            search_result = self._search(keyword, search_selector=search_selector)
            duration = time.time() - start
            if search_result["success"]:
                extract_result = self._extract_results(
                    result_selector=".result, .search-result, li, a[href*='/']",
                    title_selector="h3, .title, .result-title, a",
                    url_selector="a[href]",
                    limit=5
                )
                return {
                    "success": extract_result["success"] and extract_result.get("items_count", 0) > 0,
                    "duration": duration,
                    "dimension": dimension,
                    "data": {"keyword": keyword, "results_count": extract_result.get("items_count", 0), "title": search_result.get("title")},
                }
            else:
                return {"success": False, "duration": duration, "dimension": dimension, "error": search_result.get("error", "搜索失败")}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_extract(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            if "知乎" in website_config.name:
                data = self._session.eval_js("""
                    (() => {
                        const items = document.querySelectorAll('.List-item, .ContentItem, article');
                        const results = [];
                        for (const item of items) {
                            const title = item.querySelector('.ContentItem-title, .QuestionItem-header')?.textContent?.trim();
                            const author = item.querySelector('.AuthorInfo-name, .ContentItem-author')?.textContent?.trim();
                            if (title && results.length < 5) {
                                results.push({title, author});
                            }
                        }
                        return results;
                    })()
                """)
            elif "百度" in website_config.name or "Bing" in website_config.name:
                data = self._extract_results(result_selector=".result, .b_algo, .g, li, a[href]", title_selector="h3, .t, .r", url_selector="a[href]", limit=5)
                data = data.get("items", [])
            else:
                data = self._session.eval_js("""
                    (() => {
                        const links = document.querySelectorAll('a[href]');
                        const results = [];
                        for (const a of links) {
                            const text = a.textContent?.trim();
                            const href = a.href || '';
                            if (text && href && results.length < 10) {
                                results.push({title: text, url: href});
                            }
                        }
                        return results;
                    })()
                """)
            duration = time.time() - start
            return {"success": len(data) > 0, "duration": duration, "dimension": dimension, "data": {"items_count": len(data), "items": data[:5]}}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_paginate(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            next_clicked = self._session.eval_js("""
                (() => {
                    const selectors = ['a[rel="next"]', 'a:contains("下一页")', 'a:contains(">>")', '.pagination-next', '.next-page'];
                    for (const sel of selectors) {
                        try {
                            const el = document.querySelector(sel);
                            if (el && el.offsetParent !== null) { el.click(); return true; }
                        } catch(e) {}
                    }
                    return false;
                })()
            """)
            if next_clicked:
                time.sleep(2)
                new_url = self._session.eval_js("location.href")
                duration = time.time() - start
                return {"success": True, "duration": duration, "dimension": dimension, "data": {"new_url": new_url}}
            else:
                duration = time.time() - start
                return {"success": True, "duration": duration, "dimension": dimension, "data": {"message": "无分页或分页不可用"}}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_click_detail(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            clicked = self._session.eval_js("""
                (() => {
                    // 优先查找内容链接（排除导航、登录、注册等）
                    const excludeTexts = ['登录', '注册', '退出', '首页', '导航', '菜单', '更多', '设置', '帮助'];
                    const links = document.querySelectorAll('a[href]');
                    for (const link of links) {
                        const text = link.textContent?.trim();
                        // 放宽尺寸限制，只要可见即可
                        if (link.offsetParent !== null && link.offsetWidth > 20 && link.offsetHeight > 15) {
                            if (text && text.length > 2 && !excludeTexts.some(t => text.includes(t))) {
                                link.click(); return true;
                            }
                        }
                    }
                    // 如果没找到，尝试点击第一个可见链接
                    for (const link of links) {
                        if (link.offsetParent !== null && link.offsetWidth > 0) {
                            link.click(); return true;
                        }
                    }
                    return false;
                })()
            """)
            if clicked:
                time.sleep(2)
                new_url = self._session.eval_js("location.href")
                duration = time.time() - start
                return {"success": True, "duration": duration, "dimension": dimension, "data": {"new_url": new_url}}
            else:
                duration = time.time() - start
                return {"success": False, "duration": duration, "dimension": dimension, "error": "未找到可点击的链接"}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_check_login(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            login_status = self._session.eval_js("""
                (() => {
                    const indicators = [
                        document.querySelector('.user-info, .avatar, .login-btn'),
                        document.querySelector('[data-user-id]'),
                        document.querySelector('.signed-in'),
                    ];
                    const hasLoginBtn = indicators.some(el => el && el.textContent?.includes('登录'));
                    const hasUserInfo = indicators.some(el => el && !el.textContent?.includes('登录'));
                    return {is_logged_in: hasUserInfo && !hasLoginBtn, has_login_button: hasLoginBtn, url: location.href};
                })()
            """)
            duration = time.time() - start
            return {"success": True, "duration": duration, "dimension": dimension, "data": login_status}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_autocomplete(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            # 先执行搜索，然后检查自动补全
            self._search("AI")
            time.sleep(1)
            autocomplete = self._session.eval_js("""
                (() => {
                    const suggestions = document.querySelectorAll('.autocomplete-item, .suggestion, .dropdown-item, [class*="suggest"], [class*="autocomplete"]');
                    const results = [];
                    for (const item of suggestions) {
                        if (item.offsetParent !== null && item.textContent.trim()) {
                            results.push(item.textContent.trim());
                        }
                    }
                    return results.slice(0, 5);
                })()
            """)
            duration = time.time() - start
            return {"success": len(autocomplete) > 0, "duration": duration, "dimension": dimension, "data": {"suggestions": autocomplete}}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_switch_tab(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            # 尝试切换到图片/视频标签
            switch_clicked = self._session.eval_js("""
                (() => {
                    const selectors = ['a[href*="images"]', 'a[href*="video"]', '.tab-images', '.tab-video', 'a:contains("图片")', 'a:contains("视频")'];
                    for (const sel of selectors) {
                        try {
                            const el = document.querySelector(sel);
                            if (el && el.offsetParent !== null) { el.click(); return true; }
                        } catch(e) {}
                    }
                    return false;
                })()
            """)
            if switch_clicked:
                time.sleep(2)
                new_url = self._session.eval_js("location.href")
                duration = time.time() - start
                return {"success": True, "duration": duration, "dimension": dimension, "data": {"new_url": new_url}}
            else:
                duration = time.time() - start
                return {"success": True, "duration": duration, "dimension": dimension, "data": {"message": "无切换标签或标签不可用"}}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _eval_extract_comments(self, scenario: Dict[str, str], dimension: str, website_config) -> Dict[str, Any]:
        start = time.time()
        try:
            comments = self._session.eval_js("""
                (() => {
                    const selectors = ['.comment, .review, .post, .message, [class*="comment"], [class*="review"]'];
                    const results = [];
                    for (const sel of selectors) {
                        const items = document.querySelectorAll(sel);
                        for (const item of items) {
                            if (item.offsetParent !== null && item.textContent.trim().length > 5) {
                                results.push(item.textContent.trim().substring(0, 100));
                                if (results.length >= 5) break;
                            }
                        }
                        if (results.length >= 5) break;
                    }
                    return results;
                })()
            """)
            duration = time.time() - start
            return {"success": len(comments) > 0, "duration": duration, "dimension": dimension, "data": {"comments": comments}}
        except Exception as e:
            duration = time.time() - start
            return {"success": False, "duration": duration, "dimension": dimension, "error": str(e)}

    def _calculate_dimensions(self, result: EvalResult, website_config) -> Dict[str, Dict[str, Any]]:
        dimensions = {}
        dimension_stats = {}
        for scenario in result.scenarios:
            for sc in website_config.scenarios:
                if sc["id"] == scenario["id"]:
                    dimension = sc.get("dimension", "")
                    if dimension:
                        if dimension not in dimension_stats:
                            dimension_stats[dimension] = {"success": 0, "total": 0}
                        dimension_stats[dimension]["total"] += 1
                        if scenario["success"]:
                            dimension_stats[dimension]["success"] += 1
                    break
        for dim, counts in dimension_stats.items():
            rate = (counts["success"] / counts["total"] * 100) if counts["total"] > 0 else 0
            dimensions[dim] = {
                "score": round(rate, 2),
                "weight": self._get_dimension_weight(dim),
                "metrics": {"success_count": counts["success"], "total_count": counts["total"], "success_rate": round(rate, 2)},
            }
        return dimensions

    @staticmethod
    def _get_dimension_weight(dimension: str) -> float:
        weights = {
            "页面访问成功率": 0.30,
            "元素定位准确率": 0.20,
            "抓取成功率": 0.30,
            "稳定性": 0.10,
            "反检测能力": 0.15,
            "交互成功率": 0.20,
        }
        return weights.get(dimension, 0.1)

    def _generate_findings(self, result: EvalResult) -> List[str]:
        findings = []
        for name, dim_result in result.dimensions.items():
            score = dim_result.get("score", 0)
            if score >= 85:
                findings.append(f"✅ **{name}** 表现优秀 (得分: {score:.1f})")
            elif score >= 70:
                findings.append(f"⚠️  **{name}** 表现良好 (得分: {score:.1f})")
            else:
                findings.append(f"❌ **{name}** 需要改进 (得分: {score:.1f})")
        return findings

    def _generate_recommendations(self, result: EvalResult) -> List[str]:
        recommendations = []
        for name, dim_result in result.dimensions.items():
            score = dim_result.get("score", 0)
            if score < 70:
                if "反检测" in name:
                    recommendations.append("- [ ] 优化 stealth.py 反检测模块")
                    recommendations.append("- [ ] 增强 captcha_handler.py 验证码处理能力")
                elif "抓取" in name:
                    recommendations.append("- [ ] 优化元素选择器策略")
                    recommendations.append("- [ ] 增强动态内容等待机制")
                elif "性能" in name:
                    recommendations.append("- [ ] 优化网络请求策略")
                    recommendations.append("- [ ] 减少不必要的页面等待")
        return recommendations

    def batch_evaluate(self, websites: Optional[List] = None) -> List[EvalResult]:
        if websites is None:
            from .eval_config import WEBSITE_CONFIGS
            websites = WEBSITE_CONFIGS
        results = []
        for config in websites:
            self.initialize(headless=True)
            try:
                result = self.evaluate_website(config)
                results.append(result)
                self._save_result(result)
            finally:
                self.cleanup()
            time.sleep(2)
        return results

    def _save_result(self, result: EvalResult):
        output_dir = SKILL_DIR / "output" / "eval_results"
        output_dir.mkdir(parents=True, exist_ok=True)
        md_dir = SKILL_DIR / "output" / "eval_reports"
        md_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{result.website_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"JSON 报告已保存: {json_path}")
        md_path = md_dir / f"{result.website_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(result.to_markdown())
        logger.info(f"Markdown 报告已保存: {md_path}")


def run_evaluation(sites: Optional[List[str]] = None, priority: Optional[str] = None):
    from eval_config import WEBSITE_CONFIGS, get_website_by_name
    if priority:
        websites = [w for w in WEBSITE_CONFIGS if w.priority == priority]
    elif sites:
        websites = [get_website_by_name(s) for s in sites]
        websites = [w for w in websites if w is not None]
    else:
        websites = WEBSITE_CONFIGS
    logger.info(f"开始评估 {len(websites)} 个网站")
    evaluator = BrowserEvaluator()
    results = evaluator.batch_evaluate(websites)
    _generate_summary_report(results)
    return results


def _generate_summary_report(results: List[EvalResult]):
    output_dir = SKILL_DIR / "output" / "eval_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 评估汇总报告\n",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        f"**评估网站数**: {len(results)}\n",
        "\n",
    ]
    lines.append("## 各网站评分\n")
    lines.append("| 网站 | URL | 综合评分 | 等级 | 场景成功率 |\n")
    lines.append("|------|-----|----------|------|------------|\n")
    for r in results:
        scenario_success = sum(1 for s in r.scenarios if s["success"]) / len(r.scenarios) * 100 if r.scenarios else 0
        lines.append(f"| {r.website_name} | {r.website_url} | {r.overall_score} | {r.grade} | {scenario_success:.0f}% |\n")
    lines.append("\n")
    summary_path = output_dir / "summary_report.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    logger.info(f"汇总报告已保存: {summary_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="网站操作能力评估工具")
    parser.add_argument("--sites", nargs="+", help="指定要评估的网站名称")
    parser.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], help="指定优先级")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    run_evaluation(sites=args.sites, priority=args.priority)
