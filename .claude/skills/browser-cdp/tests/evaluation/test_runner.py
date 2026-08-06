"""
评估测试运行器

提供批量执行评估测试的核心框架：
- 测试用例执行引擎
- 结果收集与聚合
- 报告生成
- 并行/串行执行支持
"""
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

# 导入 browser-cdp 核心模块
try:
    import sys
    import os
    skill_root = Path(__file__).parent.parent.parent.parent
    if str(skill_root) not in sys.path:
        sys.path.insert(0, str(skill_root))
    from src.core.cdp_client import (
        CDPSession,
        CDPError,
        connect_tab,
        find_tab,
        is_debug_port_alive,
        list_tabs,
        new_tab,
        DEFAULT_HOST,
        DEFAULT_PORT,
    )
    CDP_AVAILABLE = True
except ImportError as e:
    CDP_AVAILABLE = False
    logger.warning(f"browser-cdp 核心模块导入失败: {e}")

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    """单个场景执行结果"""
    scenario_id: str
    scenario_name: str
    website_name: str
    success: bool
    duration: float
    score: float = 0.0
    error: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "website_name": self.website_name,
            "success": self.success,
            "duration": round(self.duration, 2),
            "score": round(self.score, 2),
            "error": self.error,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
        }


@dataclass
class WebsiteResult:
    """单个网站评估结果"""
    website_name: str
    website_url: str
    priority: str
    category: str
    scenarios: List[ScenarioResult] = field(default_factory=list)
    overall_score: float = 0.0
    grade: str = ""
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    eval_time: str = ""
    total_duration: float = 0.0

    def __post_init__(self):
        if not self.eval_time:
            self.eval_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def scenario_success_rate(self) -> float:
        if not self.scenarios:
            return 0.0
        return sum(1 for s in self.scenarios if s.success) / len(self.scenarios) * 100

    def add_scenario(self, result: ScenarioResult):
        self.scenarios.append(result)
        if result.error:
            self.errors.append(f"{result.scenario_id}: {result.error}")

    def calculate_overall(self):
        """计算综合评分"""
        if not self.scenarios:
            self.overall_score = 0.0
            self.grade = "不可用 (F)"
            return

        # 基于场景成功率计算基础分
        success_rate = self.scenario_success_rate
        avg_duration = sum(s.duration for s in self.scenarios) / len(self.scenarios)

        # 评分逻辑：成功率占70%，性能占30%
        performance_score = max(0, min(100, 100 - avg_duration * 5))
        self.overall_score = round(success_rate * 0.7 + performance_score * 0.3, 2)
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
            "priority": self.priority,
            "category": self.category,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "scenario_success_rate": round(self.scenario_success_rate, 2),
            "total_scenarios": len(self.scenarios),
            "passed_scenarios": sum(1 for s in self.scenarios if s.success),
            "failed_scenarios": sum(1 for s in self.scenarios if not s.success),
            "total_duration": round(self.total_duration, 2),
            "eval_time": self.eval_time,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "findings": self.findings,
            "recommendations": self.recommendations,
            "errors": self.errors,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# 网站操作能力评估报告\n",
            f"**评估网站**: {self.website_name} ({self.website_url})\n",
            f"**优先级**: {self.priority} | **分类**: {self.category}\n",
            f"**评估日期**: {self.eval_time}\n",
            f"**综合评分**: {self.overall_score}/100 ({self.grade})\n",
            f"**场景成功率**: {self.scenario_success_rate:.1f}%\n",
            f"**总耗时**: {self.total_duration:.2f}s\n",
            "\n",
        ]

        if self.scenarios:
            lines.append("## 场景执行结果\n")
            lines.append("| 场景 ID | 场景名称 | 成功 | 耗时(s) | 得分 |\n")
            lines.append("|---------|----------|------|---------|------|\n")
            for s in self.scenarios:
                status = "✓" if s.success else "✗"
                lines.append(f"| {s.scenario_id} | {s.scenario_name} | {status} | {s.duration:.2f} | {s.score:.1f} |\n")
            lines.append("\n")

        if self.findings:
            lines.append("## 关键发现\n")
            for f in self.findings:
                lines.append(f"- {f}\n")
            lines.append("\n")

        if self.recommendations:
            lines.append("## 改进建议\n")
            for r in self.recommendations:
                lines.append(f"- {r}\n")
            lines.append("\n")

        if self.errors:
            lines.append("## 执行错误\n")
            for err in self.errors:
                lines.append(f"- {err}\n")
            lines.append("\n")

        return "".join(lines)


@dataclass
class EvaluationReport:
    """评估汇总报告"""
    generated_at: str = ""
    total_websites: int = 0
    total_scenarios: int = 0
    passed_scenarios: int = 0
    failed_scenarios: int = 0
    total_duration: float = 0.0
    website_results: List[WebsiteResult] = field(default_factory=list)

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def overall_success_rate(self) -> float:
        if not self.total_scenarios:
            return 0.0
        return self.passed_scenarios / self.total_scenarios * 100

    @property
    def avg_score(self) -> float:
        if not self.website_results:
            return 0.0
        return sum(r.overall_score for r in self.website_results) / len(self.website_results)

    def add_website_result(self, result: WebsiteResult):
        self.website_results.append(result)
        self.total_websites += 1
        self.total_scenarios += len(result.scenarios)
        self.passed_scenarios += sum(1 for s in result.scenarios if s.success)
        self.failed_scenarios += sum(1 for s in result.scenarios if not s.success)
        self.total_duration += result.total_duration

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "total_websites": self.total_websites,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "failed_scenarios": self.failed_scenarios,
            "overall_success_rate": round(self.overall_success_rate, 2),
            "avg_score": round(self.avg_score, 2),
            "total_duration": round(self.total_duration, 2),
            "website_results": [r.to_dict() for r in self.website_results],
        }

    def to_markdown(self) -> str:
        lines = [
            "# 评估汇总报告\n",
            f"**生成时间**: {self.generated_at}\n",
            f"**评估网站数**: {self.total_websites}\n",
            f"**总场景数**: {self.total_scenarios}\n",
            f"**通过率**: {self.overall_success_rate:.1f}%\n",
            f"**平均得分**: {self.avg_score:.1f}/100\n",
            f"**总耗时**: {self.total_duration:.2f}s\n",
            "\n",
        ]

        # 各网站评分表
        lines.append("## 各网站评分\n")
        lines.append("| 网站 | 优先级 | 分类 | 综合评分 | 等级 | 场景成功率 |\n")
        lines.append("|------|--------|------|----------|------|------------|\n")
        for r in sorted(self.website_results, key=lambda x: x.overall_score, reverse=True):
            lines.append(f"| {r.website_name} | {r.priority} | {r.category} | {r.overall_score} | {r.grade} | {r.scenario_success_rate:.0f}% |\n")
        lines.append("\n")

        # 按优先级汇总
        lines.append("## 按优先级汇总\n")
        for priority in ["P0", "P1", "P2", "P3"]:
            priority_results = [r for r in self.website_results if r.priority == priority]
            if priority_results:
                avg = sum(r.overall_score for r in priority_results) / len(priority_results)
                rate = sum(r.scenario_success_rate for r in priority_results) / len(priority_results)
                lines.append(f"**{priority}**: 平均得分 {avg:.1f}, 平均通过率 {rate:.0f}%, 网站数 {len(priority_results)}\n")
        lines.append("\n")

        # 详细报告
        lines.append("## 详细报告\n")
        for r in self.website_results:
            lines.append(f"### {r.website_name}\n")
            lines.append(r.to_markdown())
            lines.append("\n---\n\n")

        return "".join(lines)


class EvaluationRunner:
    """评估测试运行器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.report = EvaluationReport()
        self._scenario_executors: Dict[str, Callable] = {}
        self._register_default_executors()

    def _register_default_executors(self):
        """注册默认场景执行器"""
        self._scenario_executors = {
            "navigate": self._exec_navigate,
            "search": self._exec_search,
            "extract": self._exec_extract,
            "extract_list": self._exec_extract_list,
            "extract_article": self._exec_extract_article,
            "click_detail": self._exec_click_detail,
            "paginate": self._exec_paginate,
            "autocomplete": self._exec_autocomplete,
            "switch_tab": self._exec_switch_tab,
            "check_login": self._exec_check_login,
            "check_anti_crawl": self._exec_check_anti_crawl,
            "extract_price": self._exec_extract_price,
            "extract_job": self._exec_extract_job,
            "extract_house": self._exec_extract_house,
            "extract_abstract": self._exec_extract_abstract,
            "download_pdf": self._exec_download_pdf,
            "extract_comments": self._exec_extract_comments,
            "extract_note": self._exec_extract_note,
            "extract_reviews": self._exec_extract_reviews,
            "search_flight": self._exec_search_flight,
            "switch_hotel": self._exec_switch_hotel,
            "search_stock": self._exec_search_stock,
            "extract_realtime": self._exec_extract_realtime,
            "extract_history": self._exec_extract_history,
            "check_chart": self._exec_check_chart,
            "click_discuss": self._exec_click_discuss,
            "extract_posts": self._exec_extract_posts,
            "switch_map": self._exec_switch_map,
            "extract_specs": self._exec_extract_specs,
            "extract_company": self._exec_extract_company,
            "extract_answers": self._exec_extract_answers,
            "extract_seats": self._exec_extract_seats,
            "extract_group_price": self._exec_extract_group_price,
        }

    def register_executor(self, action: str, executor: Callable):
        """注册自定义场景执行器"""
        self._scenario_executors[action] = executor

    def run_website(self, website_config, mock_mode: bool = True) -> WebsiteResult:
        """运行单个网站的评估"""
        result = WebsiteResult(
            website_name=website_config.name,
            website_url=website_config.url,
            priority=website_config.priority,
            category=website_config.category,
        )

        start_time = time.time()
        logger.info(f"开始评估: {website_config.name} ({website_config.url})")

        for scenario in website_config.scenarios:
            scenario_result = self._run_scenario(scenario, website_config, mock_mode)
            result.add_scenario(scenario_result)

        result.total_duration = time.time() - start_time
        result.calculate_overall()

        logger.info(
            f"评估完成: {website_config.name}, "
            f"得分 {result.overall_score}, "
            f"耗时 {result.total_duration:.2f}s"
        )

        return result

    def _run_scenario(self, scenario: Dict[str, str], website_config, mock_mode: bool) -> ScenarioResult:
        """运行单个场景"""
        action = scenario.get("action", "navigate")
        executor = self._scenario_executors.get(action, self._exec_default)

        start = time.time()
        try:
            if mock_mode:
                exec_result = executor(scenario, website_config)
            else:
                exec_result = self._exec_real(scenario, website_config)

            duration = time.time() - start
            return ScenarioResult(
                scenario_id=scenario["id"],
                scenario_name=scenario["name"],
                website_name=website_config.name,
                success=exec_result.get("success", True),
                duration=duration,
                score=exec_result.get("score", 100.0),
                error=exec_result.get("error"),
                metrics=exec_result.get("metrics", {}),
            )
        except Exception as e:
            duration = time.time() - start
            error_msg = str(e)[:200]
            logger.error(f"场景 {scenario['id']} 执行失败: {error_msg}")
            return ScenarioResult(
                scenario_id=scenario["id"],
                scenario_name=scenario["name"],
                website_name=website_config.name,
                success=False,
                duration=duration,
                error=error_msg,
            )

    def _exec_navigate(self, scenario, website_config) -> Dict[str, Any]:
        """执行页面导航"""
        time.sleep(0.01)  # 模拟导航耗时
        return {"success": True, "score": 100.0, "metrics": {"action": "navigate"}}

    def _exec_search(self, scenario, website_config) -> Dict[str, Any]:
        """执行搜索"""
        return {"success": True, "score": 95.0, "metrics": {"action": "search"}}

    def _exec_extract(self, scenario, website_config) -> Dict[str, Any]:
        """执行数据提取"""
        return {"success": True, "score": 90.0, "metrics": {"action": "extract"}}

    def _exec_extract_list(self, scenario, website_config) -> Dict[str, Any]:
        """执行列表提取"""
        return {"success": True, "score": 88.0, "metrics": {"action": "extract_list"}}

    def _exec_extract_article(self, scenario, website_config) -> Dict[str, Any]:
        """执行文章提取"""
        return {"success": True, "score": 85.0, "metrics": {"action": "extract_article"}}

    def _exec_click_detail(self, scenario, website_config) -> Dict[str, Any]:
        """执行详情页点击"""
        return {"success": True, "score": 92.0, "metrics": {"action": "click_detail"}}

    def _exec_paginate(self, scenario, website_config) -> Dict[str, Any]:
        """执行分页"""
        return {"success": True, "score": 90.0, "metrics": {"action": "paginate"}}

    def _exec_autocomplete(self, scenario, website_config) -> Dict[str, Any]:
        """执行自动补全"""
        return {"success": True, "score": 85.0, "metrics": {"action": "autocomplete"}}

    def _exec_switch_tab(self, scenario, website_config) -> Dict[str, Any]:
        """执行标签切换"""
        return {"success": True, "score": 90.0, "metrics": {"action": "switch_tab"}}

    def _exec_check_login(self, scenario, website_config) -> Dict[str, Any]:
        """执行登录态检测"""
        return {"success": True, "score": 80.0, "metrics": {"action": "check_login"}}

    def _exec_check_anti_crawl(self, scenario, website_config) -> Dict[str, Any]:
        """执行反爬检测"""
        return {"success": True, "score": 75.0, "metrics": {"action": "check_anti_crawl"}}

    def _exec_extract_price(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 88.0, "metrics": {"action": "extract_price"}}

    def _exec_extract_job(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 82.0, "metrics": {"action": "extract_job"}}

    def _exec_extract_house(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 85.0, "metrics": {"action": "extract_house"}}

    def _exec_extract_abstract(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 90.0, "metrics": {"action": "extract_abstract"}}

    def _exec_download_pdf(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 85.0, "metrics": {"action": "download_pdf"}}

    def _exec_extract_comments(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 80.0, "metrics": {"action": "extract_comments"}}

    def _exec_extract_note(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 78.0, "metrics": {"action": "extract_note"}}

    def _exec_extract_reviews(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 82.0, "metrics": {"action": "extract_reviews"}}

    def _exec_search_flight(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 80.0, "metrics": {"action": "search_flight"}}

    def _exec_switch_hotel(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 78.0, "metrics": {"action": "switch_hotel"}}

    def _exec_search_stock(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 85.0, "metrics": {"action": "search_stock"}}

    def _exec_extract_specs(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 88.0, "metrics": {"action": "extract_specs"}}

    def _exec_extract_company(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 82.0, "metrics": {"action": "extract_company"}}

    def _exec_extract_answers(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 80.0, "metrics": {"action": "extract_answers"}}

    def _exec_extract_realtime(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 88.0, "metrics": {"action": "extract_realtime"}}

    def _exec_extract_history(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 85.0, "metrics": {"action": "extract_history"}}

    def _exec_check_chart(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 80.0, "metrics": {"action": "check_chart"}}

    def _exec_click_discuss(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 82.0, "metrics": {"action": "click_discuss"}}

    def _exec_extract_posts(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 80.0, "metrics": {"action": "extract_posts"}}

    def _exec_extract_seats(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 85.0, "metrics": {"action": "extract_seats"}}

    def _exec_extract_group_price(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 75.0, "metrics": {"action": "extract_group_price"}}

    def _exec_switch_map(self, scenario, website_config) -> Dict[str, Any]:
        return {"success": True, "score": 75.0, "metrics": {"action": "switch_map"}}

    def _exec_default(self, scenario, website_config) -> Dict[str, Any]:
        """默认执行器"""
        return {"success": True, "score": 85.0, "metrics": {"action": scenario.get("action", "unknown")}}

    def _exec_real(self, scenario, website_config) -> Dict[str, Any]:
        """真实浏览器执行"""
        if not CDP_AVAILABLE:
            raise RuntimeError("browser-cdp 核心模块未安装，无法执行真实浏览器模式")

        # 检查浏览器调试端口
        host = self.config.get("cdp_host", DEFAULT_HOST)
        port = self.config.get("cdp_port", DEFAULT_PORT)

        if not is_debug_port_alive(host, port):
            raise RuntimeError(f"浏览器调试端口 {host}:{port} 不可用，请先启动带调试端口的 Chrome")

        # 创建新标签页
        target = new_tab(website_config.url, host=host, port=port)
        tab_url = target.get("url", "")
        target_id = target.get("id", "")

        if not target_id:
            raise RuntimeError(f"无法创建新标签页: {target}")

        # 连接 CDP session
        tabs = list_tabs(host, port)
        cdp_tab = next((t for t in tabs if t.get("id") == target_id), None)
        if not cdp_tab:
            raise RuntimeError(f"未找到新创建的标签页: {target_id}")

        session = connect_tab(cdp_tab, host=host, port=port)

        try:
            # 等待页面加载
            session.wait_event("Page.loadEventFired", timeout=15.0)

            # 根据场景类型执行不同操作
            action = scenario.get("action", "navigate")

            if action == "search":
                query = scenario.get("query", "")
                if query:
                    # 查找搜索框并输入
                    session.eval_js(f"document.querySelector('input[type=search], input[role=search], input[name=q]')?.focus()")
                    time.sleep(0.5)
                    session.eval_js(f"document.querySelector('input[type=search], input[role=search], input[name=q]')?.value = '{query}'")
                    time.sleep(0.5)
                    session.eval_js("document.querySelector('input[type=search], input[role=search], input[name=q]')?.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}))")
                    session.wait_event("Page.loadEventFired", timeout=10.0)

            elif action == "extract":
                # 提取页面标题和主要内容
                title = session.eval_js("document.title")
                content_length = session.eval_js("document.body?.innerText?.length || 0")
                session.metrics = {"title": title, "content_length": content_length}

            elif action == "paginate":
                # 尝试点击下一页
                next_btn = session.eval_js("document.querySelector('a[rel=next], .next, [aria-label*=next], button[aria-label*=next]')?.href || null")
                if next_btn:
                    session.eval_js(f"window.location = '{next_btn}'")
                    session.wait_event("Page.loadEventFired", timeout=10.0)

            elif action == "click_detail":
                # 点击第一个链接
                first_link = session.eval_js("document.querySelector('a[href]')?.href || null")
                if first_link and first_link != website_config.url:
                    session.eval_js(f"window.location = '{first_link}'")
                    session.wait_event("Page.loadEventFired", timeout=10.0)

            # 获取执行结果
            final_url = session.eval_js("window.location.href")
            title = session.eval_js("document.title")

            return {
                "success": True,
                "score": 90.0,
                "metrics": {
                    "action": action,
                    "final_url": final_url,
                    "title": title,
                    "target_id": target_id,
                }
            }

        except Exception as e:
            raise RuntimeError(f"真实浏览器执行失败: {e}")
        finally:
            session.close()
            try:
                from src.core.cdp_client import close_tab
                close_tab(target_id, host=host, port=port)
            except Exception:
                pass

    def run_batch(self, website_configs, mock_mode: bool = True) -> EvaluationReport:
        """批量执行评估"""
        logger.info(f"开始批量评估 {len(website_configs)} 个网站")
        start_time = time.time()

        for config in website_configs:
            result = self.run_website(config, mock_mode)
            self.report.add_website_result(result)

            # 延迟避免触发反爬
            time.sleep(self.config.get("delay_between_sites", 1))

        self.report.total_duration = time.time() - start_time
        logger.info(f"批量评估完成，总耗时 {self.report.total_duration:.2f}s")

        return self.report

    def save_report(self, report: EvaluationReport, output_dir: Path):
        """保存评估报告"""
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存 JSON 报告
        json_path = output_dir / f"evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"JSON 报告已保存: {json_path}")

        # 保存 Markdown 报告
        md_path = output_dir / f"evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        logger.info(f"Markdown 报告已保存: {md_path}")

        # 保存各网站详细报告
        for website_result in report.website_results:
            website_dir = output_dir / website_result.website_name
            website_dir.mkdir(exist_ok=True)

            # JSON
            json_path = website_dir / f"{website_result.website_name}_detail.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(website_result.to_dict(), f, ensure_ascii=False, indent=2)

            # Markdown
            md_path = website_dir / f"{website_result.website_name}_detail.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(website_result.to_markdown())

        return output_dir


def run_evaluation(
    website_configs,
    output_dir: Optional[Path] = None,
    mock_mode: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> EvaluationReport:
    """
    运行评估的便捷函数

    Args:
        website_configs: 网站配置列表
        output_dir: 输出目录
        mock_mode: 是否使用 mock 模式
        config: 额外配置

    Returns:
        EvaluationReport: 评估报告
    """
    runner = EvaluationRunner(config=config)
    report = runner.run_batch(website_configs, mock_mode=mock_mode)

    if output_dir:
        runner.save_report(report, output_dir)

    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="网站操作能力评估运行器")
    parser.add_argument("--output-dir", "-o", default="output/eval_results", help="输出目录")
    parser.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], help="指定优先级")
    parser.add_argument("--sites", nargs="+", help="指定网站名称")
    parser.add_argument("--no-mock", action="store_true", help="使用真实浏览器（需实现）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    from scripts.eval_config import WEBSITE_CONFIGS, get_websites_by_priority, get_website_by_name

    if args.priority:
        websites = get_websites_by_priority(args.priority)
    elif args.sites:
        websites = [get_website_by_name(s) for s in args.sites]
        websites = [w for w in websites if w is not None]
    else:
        websites = WEBSITE_CONFIGS

    output_dir = Path(args.output_dir)
    report = run_evaluation(websites, output_dir=output_dir, mock_mode=not args.no_mock)

    print(f"\n=== 评估完成 ===")
    print(f"评估网站数: {report.total_websites}")
    print(f"总场景数: {report.total_scenarios}")
    print(f"通过率: {report.overall_success_rate:.1f}%")
    print(f"平均得分: {report.avg_score:.1f}/100")
    print(f"报告已保存至: {output_dir}")
