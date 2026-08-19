"""
ArticleCrawlPipeline - 文章爬取管道

将ArticleParser集成到CrawlScheduler管道中，支持：
- 单篇/批量文章抓取
- 列表页自动发现+逐篇抓取
- 结果持久化
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .article_parser import (
    ArticleContent,
    ArticleParseResult,
    ArticleParser,
    create_article_parser,
)
from .crawl_scheduler import CrawlScheduler, CrawlTask, TaskStatus
from .url_dedup import UrlNormalizer

logger = logging.getLogger(__name__)


# ==================== 管道数据模型 ====================

@dataclass
class PipelineResult:
    """管道执行结果"""
    success: bool
    articles: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    total_discovered: int = 0
    total_fetched: int = 0
    total_success: int = 0
    total_failed: int = 0
    duration_ms: int = 0
    statistics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "articles": self.articles,
            "errors": self.errors,
            "total_discovered": self.total_discovered,
            "total_fetched": self.total_fetched,
            "total_success": self.total_success,
            "total_failed": self.total_failed,
            "duration_ms": self.duration_ms,
            "statistics": self.statistics,
        }

    @property
    def is_empty(self) -> bool:
        return not self.articles and not self.errors


@dataclass
class PipelineConfig:
    """管道配置"""
    max_articles_per_page: int = 50
    max_depth: int = 3  # 列表页->文章->相关文章的最大深度
    request_timeout: float = 30.0
    retry_count: int = 2
    delay_between_requests: float = 0.5
    platform: Optional[str] = None
    custom_selectors: Optional[Dict[str, Any]] = None
    save_output: bool = True
    output_dir: Optional[str] = None
    exclude_patterns: List[str] = field(default_factory=lambda: [
        "/login", "/register", "/comment", "/share",
        "javascript:void", "mailto:", "tel:",
    ])


# ==================== 核心管道 ====================

class ArticleCrawlPipeline:
    """文章爬取管道"""

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        scheduler: Optional[CrawlScheduler] = None,
    ):
        self.config = config or PipelineConfig()
        self._scheduler = scheduler
        self._parser = create_article_parser(
            platform=self.config.platform,
            custom_selectors=self.config.custom_selectors,
        )
        self._results: List[PipelineResult] = []
        self._discovered_urls: set = set()
        self._output_dir: Optional[Path] = None

        if self.config.save_output and self.config.output_dir:
            self._output_dir = Path(self.config.output_dir)
            self._output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"ArticleCrawlPipeline初始化: platform={self.config.platform}, max_depth={self.config.max_depth}")

    # -------------------- 单篇文章抓取 --------------------

    def crawl_article(
        self,
        html: str,
        url: str = "",
        title: str = "",
    ) -> ArticleParseResult:
        """抓取单篇文章（直接传入HTML）"""
        result = self._parser.parse(html, url)
        if result.success and result.article:
            if title and not result.article.title:
                result.article.title = title
            logger.info(f"文章解析成功: {result.article.title[:40]} | {url[:60]}")
        else:
            logger.warning(f"文章解析失败: {url[:60]} | {result.error}")
        return result

    def crawl_article_url(
        self,
        url: str,
        html_fetcher: Optional[Callable[[str], str]] = None,
    ) -> ArticleParseResult:
        """抓取单篇文章（通过URL）"""
        # 获取HTML
        if html_fetcher:
            html = html_fetcher(url)
        elif self._scheduler:
            # 通过调度器获取
            task = self._scheduler.enqueue(url, priority=1)
            if task:
                # 同步等待执行
                stats = self._scheduler.run_sync(urls=[url])
                html = None  # 需要通过回调获取
                # 实际使用时应通过callback获取html
                html = ""
            else:
                return ArticleParseResult(
                    success=False, error="任务入队失败",
                    raw_html_size=0,
                )
        else:
            return ArticleParseResult(
                success=False, error="未提供html_fetcher且无scheduler",
                raw_html_size=0,
            )

        if not html or len(html) < 100:
            return ArticleParseResult(
                success=False, error=f"HTML内容为空或过短: {len(html) if html else 0} bytes",
                raw_html_size=len(html) if html else 0,
            )

        return self.crawl_article(html, url)

    # -------------------- 列表页抓取 + 文章发现 --------------------

    def crawl_list_page(
        self,
        html: str,
        url: str = "",
    ) -> PipelineResult:
        """抓取列表页并发现文章链接"""
        start_time = time.time()
        discovered = self._parser.parse_article_list(html, url)

        # 去重过滤
        valid_articles = []
        for item in discovered:
            article_url = item.get("url", "")
            if not article_url:
                continue
            # 排除模式匹配
            if any(pattern in article_url.lower() for pattern in self.config.exclude_patterns):
                continue
            normalized = UrlNormalizer.normalize(article_url)
            if normalized in self._discovered_urls:
                continue
            self._discovered_urls.add(normalized)
            valid_articles.append(item)

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(f"列表页发现 {len(valid_articles)} 篇文章: {url[:60]}")

        return PipelineResult(
            success=True,
            articles=[{
                "url": a["url"],
                "title": a["title"],
                "source_url": url,
                "discovered_at": datetime.now().isoformat(),
            } for a in valid_articles],
            total_discovered=len(valid_articles),
            duration_ms=elapsed_ms,
        )

    def crawl_list_and_articles(
        self,
        list_url: str,
        html_fetcher: Optional[Callable[[str], str]] = None,
        max_depth: Optional[int] = None,
    ) -> PipelineResult:
        """抓取列表页并逐篇抓取文章"""
        depth = min(max_depth or self.config.max_depth, 3)
        return self._crawl_list_recursive(list_url, html_fetcher, 0, depth)

    def _crawl_list_recursive(
        self,
        list_url: str,
        html_fetcher: Optional[Callable[[str, str]], str],
        current_depth: int,
        max_depth: int,
    ) -> PipelineResult:
        """递归抓取列表页和文章"""
        start_time = time.time()
        all_articles = []
        all_errors = []

        # 获取列表页HTML
        if html_fetcher:
            list_html = html_fetcher(list_url)
        elif self._scheduler:
            stats = self._scheduler.run_sync(urls=[list_url])
            list_html = ""
        else:
            return PipelineResult(success=False, errors=[{"url": list_url, "error": "无html_fetcher"}])

        if not list_html or len(list_html) < 100:
            return PipelineResult(success=False, errors=[{"url": list_url, "error": "列表页HTML为空"}])

        # 发现文章
        list_result = self.crawl_list_page(list_html, list_url)
        articles_to_crawl = list_result.articles[: self.config.max_articles_per_page]

        # 逐篇抓取
        for idx, article_info in enumerate(articles_to_crawl):
            article_url = article_info["url"]
            logger.debug(f"抓取文章 [{idx+1}/{len(articles_to_crawl)}]: {article_url[:60]}")

            # 获取文章HTML
            if html_fetcher:
                article_html = html_fetcher(article_url)
            else:
                article_html = ""

            if not article_html or len(article_html) < 100:
                all_errors.append({"url": article_url, "error": "文章HTML为空"})
                continue

            # 解析文章
            parse_result = self.crawl_article(article_html, article_url, article_info.get("title", ""))
            if parse_result.success and parse_result.article:
                article_data = parse_result.article.to_dict()
                article_data["source_url"] = article_url
                article_data["list_source"] = list_url
                all_articles.append(article_data)
            else:
                all_errors.append({"url": article_url, "error": parse_result.error or "解析失败"})

            # 限流
            if idx < len(articles_to_crawl) - 1:
                time.sleep(self.config.delay_between_requests)

        # 递归抓取相关文章（仅当深度允许且调度器可用时）
        if current_depth < max_depth - 1 and self._scheduler:
            # 从已抓取的文章中发现新的列表/文章URL
            related_urls = set()
            for article in all_articles:
                # 从文章metadata中找相关链接
                for key in ("related_urls", "next_article"):
                    if key in article.get("metadata", {}):
                        related_urls.add(article["metadata"][key])
            if related_urls:
                rel_result = self._crawl_related(related_urls, html_fetcher, current_depth + 1, max_depth)
                all_articles.extend(rel_result.articles)
                all_errors.extend(rel_result.errors)

        elapsed_ms = int((time.time() - start_time) * 1000)
        total_discovered = list_result.total_discovered

        result = PipelineResult(
            success=len(all_errors) < len(all_articles) * 0.5,  # 成功率>50%视为成功
            articles=all_articles,
            errors=all_errors,
            total_discovered=total_discovered,
            total_fetched=len(all_articles) + len(all_errors),
            total_success=len(all_articles),
            total_failed=len(all_errors),
            duration_ms=elapsed_ms,
            statistics={
                "depth": current_depth,
                "max_depth": max_depth,
                "discovery_rate": round(len(all_articles) / max(1, total_discovered) * 100, 1),
            },
        )
        self._results.append(result)
        return result

    def _crawl_related(
        self,
        urls: set,
        html_fetcher: Optional[Callable[[str], str]],
        depth: int,
        max_depth: int,
    ) -> PipelineResult:
        """抓取相关文章"""
        all_articles = []
        all_errors = []
        for url in list(urls)[:10]:  # 限制数量
            if html_fetcher:
                html = html_fetcher(url)
            else:
                html = ""
            if not html:
                all_errors.append({"url": url, "error": "无法获取HTML"})
                continue
            parse_result = self.crawl_article(html, url)
            if parse_result.success and parse_result.article:
                data = parse_result.article.to_dict()
                data["source_url"] = url
                all_articles.append(data)
            else:
                all_errors.append({"url": url, "error": parse_result.error or "解析失败"})
        return PipelineResult(
            success=len(all_errors) < len(all_articles) * 0.5,
            articles=all_articles,
            errors=all_errors,
            duration_ms=0,
        )

    # -------------------- 批量抓取 --------------------

    def batch_crawl(
        self,
        urls: List[str],
        html_fetcher: Optional[Callable[[str], str]] = None,
    ) -> PipelineResult:
        """批量抓取多篇文章"""
        start_time = time.time()
        articles = []
        errors = []

        for idx, url in enumerate(urls):
            logger.info(f"批量抓取 [{idx+1}/{len(urls)}]: {url[:60]}")
            if html_fetcher:
                html = html_fetcher(url)
            elif self._scheduler:
                self._scheduler.run_sync(urls=[url])
                html = ""
            else:
                html = ""

            if not html or len(html) < 100:
                errors.append({"url": url, "error": "HTML为空或过短"})
                continue

            result = self.crawl_article(html, url)
            if result.success and result.article:
                data = result.article.to_dict()
                data["source_url"] = url
                data["detected_type"] = result.detected_type
                articles.append(data)
            else:
                errors.append({"url": url, "error": result.error or "解析失败"})

            if idx < len(urls) - 1:
                time.sleep(self.config.delay_between_requests)

        elapsed_ms = int((time.time() - start_time) * 1000)
        result = PipelineResult(
            success=len(errors) < len(urls) * 0.3,
            articles=articles,
            errors=errors,
            total_fetched=len(urls),
            total_success=len(articles),
            total_failed=len(errors),
            duration_ms=elapsed_ms,
        )
        self._results.append(result)
        return result

    # -------------------- 输出保存 --------------------

    def save_results(self, result: PipelineResult, filename: Optional[str] = None) -> Optional[Path]:
        """保存结果为JSON文件"""
        if not self.config.save_output or not self._output_dir:
            return None
        if not filename:
            filename = f"articles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = self._output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"结果已保存: {path} ({len(result.articles)} 篇文章)")
        return path

    def get_last_result(self) -> Optional[PipelineResult]:
        return self._results[-1] if self._results else None

    def get_all_results(self) -> List[PipelineResult]:
        return self._results

    def reset(self) -> None:
        self._results.clear()
        self._discovered_urls.clear()
        logger.info("Pipeline状态已重置")


# ==================== 工厂函数 ====================

def create_pipeline(
    platform: Optional[str] = None,
    output_dir: Optional[str] = None,
    **kwargs,
) -> ArticleCrawlPipeline:
    """创建文章爬取管道"""
    config = PipelineConfig(platform=platform, output_dir=output_dir, **kwargs)
    return ArticleCrawlPipeline(config=config)


def crawl_article_only(
    html: str,
    url: str = "",
    title: str = "",
) -> ArticleParseResult:
    """快速抓取单篇文章（不依赖管道）"""
    parser = ArticleParser()
    return parser.parse(html, url)


__all__ = [
    "ArticleCrawlPipeline",
    "PipelineConfig",
    "PipelineResult",
    "create_pipeline",
    "crawl_article_only",
]
