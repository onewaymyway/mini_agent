# -*- coding: utf-8 -*-
"""
测试配置加载器

从 JSON 配置文件加载测试环境配置，支持多站点、多优先级的配置管理。
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SiteConfig:
    """单个站点的测试配置"""
    site_id: str
    name: str
    url: str
    priority: str
    category: str
    test_cases: List[str]
    specific_config: Dict[str, Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SiteConfig":
        return cls(
            site_id=data["site_id"],
            name=data["name"],
            url=data["url"],
            priority=data["priority"],
            category=data["category"],
            test_cases=data.get("test_cases", []),
            specific_config=data.get("specific_config", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "name": self.name,
            "url": self.url,
            "priority": self.priority,
            "category": self.category,
            "test_cases": self.test_cases,
            "specific_config": self.specific_config,
        }


@dataclass
class TestEnvironmentConfig:
    """测试环境完整配置"""
    version: str
    browser: Dict[str, Any]
    retry: Dict[str, Any]
    rate_limiter: Dict[str, Any]
    proxy: Dict[str, Any]
    screenshot: Dict[str, Any]
    reporting: Dict[str, Any]
    concurrency: Dict[str, Any]
    phase1_sites: List[SiteConfig]

    @classmethod
    def from_file(cls, config_path: str) -> "TestEnvironmentConfig":
        """从 JSON 文件加载配置"""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        env_config = data.get("test_environment", {})
        sites = [
            SiteConfig.from_dict(site) for site in data.get("phase1_sites", [])
        ]

        return cls(
            version=env_config.get("version", "1.0.0"),
            browser=data.get("browser", {}),
            retry=data.get("retry", {}),
            rate_limiter=data.get("rate_limiter", {}),
            proxy=data.get("proxy", {}),
            screenshot=data.get("screenshot", {}),
            reporting=data.get("reporting", {}),
            concurrency=data.get("concurrency", {}),
            phase1_sites=sites,
        )

    def get_sites_by_priority(self, priority: str) -> List[SiteConfig]:
        """按优先级筛选站点"""
        return [s for s in self.phase1_sites if s.priority == priority]

    def get_sites_by_category(self, category: str) -> List[SiteConfig]:
        """按分类筛选站点"""
        return [s for s in self.phase1_sites if s.category == category]

    def get_site_by_id(self, site_id: str) -> Optional[SiteConfig]:
        """按站点ID查找配置"""
        for site in self.phase1_sites:
            if site.site_id == site_id:
                return site
        return None

    def get_test_cases_for_site(self, site_id: str) -> List[str]:
        """获取指定站点的所有测试用例"""
        site = self.get_site_by_id(site_id)
        return site.test_cases if site else []

    def get_all_test_case_ids(self) -> set:
        """获取所有测试用例ID"""
        all_cases = set()
        for site in self.phase1_sites:
            all_cases.update(site.test_cases)
        return all_cases


def load_test_config(config_path: str = None) -> TestEnvironmentConfig:
    """加载测试配置（支持环境变量覆盖路径）"""
    import os
    default_path = Path(__file__).parent.parent.parent / "config" / "test_environment_config.json"
    custom_path = os.environ.get("BROWSER_CDP_TEST_CONFIG")
    path = custom_path or str(default_path)
    logger.info(f"Loading test config from: {path}")
    return TestEnvironmentConfig.from_file(path)


if __name__ == "__main__":
    import sys
    config = load_test_config()
    print(f"Version: {config.version}")
    print(f"Browser: {config.browser.get('default_type')}")
    print(f"Phase1 Sites: {len(config.phase1_sites)}")
    for site in config.phase1_sites:
        print(f"  - {site.name} ({site.priority})")
