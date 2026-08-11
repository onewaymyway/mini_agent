"""
smart_selector.py - 智能选择器适配器

提供多选择器备选 + 内容验证的智能选择机制：
- 支持主选择器和多个备选选择器
- 自动验证提取内容有效性
- 动态适配网站结构变化
- 缓存有效选择器提升性能
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .selector_version_manager import get_selector_manager

logger = logging.getLogger(__name__)


@dataclass
class SelectorConfig:
    """选择器配置"""
    primary: str  # 主选择器
    fallbacks: List[str] = field(default_factory=list)  # 备选选择器列表
    min_content_length: int = 10  # 最小内容长度
    max_content_length: int = 10000  # 最大内容长度
    required_attributes: List[str] = field(default_factory=list)  # 必需属性
    validation_js: Optional[str] = None  # 自定义验证脚本

    def to_dict(self) -> Dict:
        return {
            "primary": self.primary,
            "fallbacks": self.fallbacks,
            "min_content_length": self.min_content_length,
            "max_content_length": self.max_content_length,
            "required_attributes": self.required_attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SelectorConfig":
        return cls(
            primary=data.get("primary", ""),
            fallbacks=data.get("fallbacks", []),
            min_content_length=data.get("min_content_length", 10),
            max_content_length=data.get("max_content_length", 10000),
            required_attributes=data.get("required_attributes", []),
            validation_js=data.get("validation_js"),
        )


class SelectorCache:
    """选择器缓存 - 记录有效选择器避免重复测试"""

    def __init__(self, cache_file: Optional[Path] = None):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.cache_file = cache_file
        if cache_file and cache_file.exists():
            self._load()

    def _load(self):
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load selector cache: {e}")

    def _save(self):
        if self.cache_file:
            try:
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(self.cache, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Failed to save selector cache: {e}")

    def get_effective_selector(self, site: str, selector_type: str) -> Optional[str]:
        """获取已验证有效的选择器"""
        key = f"{site}:{selector_type}"
        if key in self.cache:
            entry = self.cache[key]
            if entry.get("valid", False):
                # 返回最近有效的选择器
                for sel in entry.get("successful_selectors", []):
                    if entry.get("last_success") and entry["last_success"] > time.time() - 86400 * 7:
                        return sel["selector"]
        return None

    def record_success(self, site: str, selector_type: str, selector: str, content_length: int):
        """记录成功的选择器"""
        key = f"{site}:{selector_type}"
        if key not in self.cache:
            self.cache[key] = {
                "successful_selectors": [],
                "last_success": 0,
                "valid": True,
            }
        
        entry = self.cache[key]
        # 更新成功记录
        existing = next((s for s in entry["successful_selectors"] if s["selector"] == selector), None)
        if existing:
            existing["success_count"] = existing.get("success_count", 0) + 1
            existing["last_success"] = time.time()
        else:
            entry["successful_selectors"].append({
                "selector": selector,
                "success_count": 1,
                "last_success": time.time(),
                "content_length": content_length,
            })
        entry["last_success"] = time.time()
        entry["valid"] = True
        self._save()

    def record_failure(self, site: str, selector_type: str, selector: str):
        """记录失败的选择器"""
        key = f"{site}:{selector_type}"
        if key not in self.cache:
            self.cache[key] = {
                "successful_selectors": [],
                "last_success": 0,
                "valid": False,
            }
        
        entry = self.cache[key]
        entry["valid"] = False
        self._save()


class SmartSelector:
    """智能选择器适配器"""

    def __init__(
        self,
        site_name: str,
        configs: Dict[str, SelectorConfig],
        cache: Optional[SelectorCache] = None,
    ):
        self.site_name = site_name
        self.configs = configs
        self.cache = cache or SelectorCache()
        self._js_cache = {}

    def find(self, page, selector_type: str, timeout: int = 10000) -> Optional[Dict]:
        """
        智能查找元素
        
        Args:
            page: Playwright page 对象
            selector_type: 选择器类型（如 'search_box', 'results', 'article'）
            timeout: 超时时间（毫秒）
            
        Returns:
            成功时返回 {"selector": str, "count": int, "content": str, "elements": list}
            失败时返回 None
        """
        config = self.configs.get(selector_type)
        if not config:
            logger.warning(f"No config for selector type: {selector_type}")
            return None

        # 获取版本管理器
        version_manager = get_selector_manager()
        
        # 先尝试版本管理器推荐的最佳选择器
        best_selector = version_manager.get_best_selector(self.site_name, selector_type)
        if best_selector:
            result = self._try_selector(page, best_selector, config, timeout)
            if result:
                version_manager.record_success(self.site_name, selector_type, best_selector)
                return result

        # 再尝试缓存的有效选择器
        cached = self.cache.get_effective_selector(self.site_name, selector_type)
        if cached and cached != best_selector:
            result = self._try_selector(page, cached, config, timeout)
            if result:
                version_manager.record_success(self.site_name, selector_type, cached)
                return result

        # 尝试主选择器和备选选择器
        selectors_to_try = [config.primary] + config.fallbacks
        # 移除已尝试的选择器
        if best_selector and best_selector in selectors_to_try:
            selectors_to_try.remove(best_selector)
        if cached and cached in selectors_to_try:
            selectors_to_try.remove(cached)
        
        for selector in selectors_to_try:
            result = self._try_selector(page, selector, config, timeout)
            if result:
                # 记录成功
                self.cache.record_success(
                    self.site_name, selector_type, selector, len(result.get("content", ""))
                )
                version_manager.record_success(self.site_name, selector_type, selector)
                return result
        
        # 记录所有选择器失败
        for selector in selectors_to_try:
            self.cache.record_failure(self.site_name, selector_type, selector)
            version_manager.record_failure(self.site_name, selector_type, selector)
        
        return None

    def _try_selector(
        self, page, selector: str, config: SelectorConfig, timeout: int
    ) -> Optional[Dict]:
        """尝试单个选择器"""
        try:
            # 等待元素出现
            elements = page.wait_for_selector(selector, state="visible", timeout=timeout)
            if not elements:
                # 尝试 query_selector
                elements = page.query_selector_all(selector)
            
            if not elements:
                return None

            # 验证内容
            content = self._extract_content(elements, config)
            if not self._validate_content(content, config):
                return None

            # 提取元素信息
            element_info = []
            for el in (elements if isinstance(elements, list) else [elements]):
                try:
                    info = {
                        "selector": selector,
                        "tag": el.evaluate("el => el.tagName") if hasattr(el, 'evaluate') else None,
                        "class": el.evaluate("el => el.className") if hasattr(el, 'evaluate') else None,
                        "text_preview": (el.inner_text()[:100] if hasattr(el, 'inner_text') else None),
                    }
                    element_info.append(info)
                except Exception as e:
                    logger.debug(f"Failed to extract element info: {e}")

            return {
                "selector": selector,
                "count": len(elements) if isinstance(elements, list) else 1,
                "content": content,
                "elements": element_info,
            }
        except Exception as e:
            logger.debug(f"Selector '{selector}' failed: {e}")
            return None

    def _extract_content(self, elements, config: SelectorConfig) -> str:
        """提取元素内容"""
        if isinstance(elements, list):
            texts = []
            for el in elements:
                try:
                    text = el.inner_text() if hasattr(el, 'inner_text') else str(el)
                    texts.append(text)
                except:
                    pass
            return "\n".join(texts)
        else:
            try:
                return elements.inner_text() if hasattr(elements, 'inner_text') else str(elements)
            except:
                return ""

    def _validate_content(self, content: str, config: SelectorConfig) -> bool:
        """验证内容有效性"""
        if not content:
            return False
        
        content = content.strip()
        if len(content) < config.min_content_length:
            return False
        
        if len(content) > config.max_content_length:
            return False
        
        # 检查必需属性
        if config.required_attributes:
            # 这里可以添加更多属性验证逻辑
            pass
        
        return True

    def find_all(self, page, selector_type: str, timeout: int = 10000) -> List[Dict]:
        """
        查找所有匹配元素
        
        Returns:
            元素列表，每个元素包含 selector, text, attributes
        """
        config = self.configs.get(selector_type)
        if not config:
            return []

        results = []
        selectors_to_try = [config.primary] + config.fallbacks
        
        for selector in selectors_to_try:
            try:
                elements = page.query_selector_all(selector)
                if elements:
                    for el in elements:
                        try:
                            text = el.inner_text()[:500] if hasattr(el, 'inner_text') else ""
                            if len(text.strip()) >= config.min_content_length:
                                results.append({
                                    "selector": selector,
                                    "text": text.strip(),
                                    "tag": el.evaluate("el => el.tagName") if hasattr(el, 'evaluate') else None,
                                })
                        except:
                            pass
                    if results:
                        return results
            except:
                continue
        
        return results

    def auto_discover(self, page, patterns: List[str], timeout: int = 5000) -> Dict[str, str]:
        """
        自动发现有效选择器
        
        Args:
            page: Playwright page
            patterns: 选择器模式列表
            timeout: 超时时间
            
        Returns:
            {selector_type: effective_selector} 映射
        """
        discovered = {}
        for pattern in patterns:
            try:
                elements = page.query_selector_all(pattern)
                if elements and len(elements) > 0:
                    # 验证内容
                    text = elements[0].inner_text()[:100] if hasattr(elements[0], 'inner_text') else ""
                    if len(text.strip()) > 5:
                        discovered[pattern] = pattern
            except:
                continue
        return discovered


class WebsiteSelectorManager:
    """网站选择器管理器 - 统一管理多个网站的选择器配置"""

    def __init__(self, config_dir: str = "config/websites"):
        self.config_dir = Path(config_dir)
        self.managers: Dict[str, SmartSelector] = {}
        self._load_all_configs()

    def _load_all_configs(self):
        """加载所有网站配置"""
        if not self.config_dir.exists():
            return
        
        for config_file in self.config_dir.glob("*.json"):
            if config_file.name == "template.json":
                continue
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                site_name = config.get("domain", config_file.stem)
                self.managers[site_name] = self._create_selector_manager(site_name, config)
            except Exception as e:
                logger.warning(f"Failed to load config {config_file}: {e}")

    def _create_selector_manager(self, site_name: str, config: Dict) -> SmartSelector:
        """创建选择器管理器"""
        selectors = {}
        custom_config = config.get("custom_config", {})
        
        # 标准选择器映射
        selector_mappings = {
            "search_box": "search_box",
            "search_input": "search_box",
            "submit_button": "submit_button",
            "results": "results",
            "result_items": "results",
            "article": "article",
            "article_list": "article_list",
            "content": "content",
            "navigation": "navigation",
            "footer": "footer",
        }
        
        for key, value in custom_config.items():
            if key in selector_mappings:
                selector_type = selector_mappings[key]
                selectors[selector_type] = SelectorConfig(
                    primary=value,
                    fallbacks=[],
                    min_content_length=5,
                )
        
        return SmartSelector(site_name, selectors)

    def get_manager(self, site_name: str) -> Optional[SmartSelector]:
        """获取网站选择器管理器"""
        return self.managers.get(site_name)

    def add_site_config(self, site_name: str, config: Dict):
        """添加网站配置"""
        self.managers[site_name] = self._create_selector_manager(site_name, config)


# 预定义常用选择器模式
COMMON_SELECTORS = {
    "search_box": [
        "input[type='search']",
        "input[name='q']",
        "input[name='wd']",
        "input[placeholder*='搜索']",
        "input[placeholder*='search']",
        "input#kw",
        "input[name='keyword']",
    ],
    "submit_button": [
        "button[type='submit']",
        "input[type='submit']",
        "button[type='button']",
        "a[type='submit']",
    ],
    "results": [
        ".result",
        ".result-item",
        ".search-result",
        "[class*='result']",
    ],
    "article": [
        "article",
        ".article",
        ".post",
        "[class*='article']",
        "[class*='post']",
    ],
    "content": [
        ".content",
        "#content",
        "[class*='content']",
        "main",
        "article",
    ],
}


def get_common_selector(selector_type: str) -> List[str]:
    """获取常用选择器列表"""
    return COMMON_SELECTORS.get(selector_type, [])


def create_smart_selector(site_name: str, config_path: Optional[str] = None) -> SmartSelector:
    """
    创建智能选择器
    
    Args:
        site_name: 网站名称
        config_path: 配置文件路径
        
    Returns:
        SmartSelector 实例
    """
    configs = {}
    
    # 加载配置文件
    if config_path:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            custom_config = config.get("custom_config", {})
            for key, value in custom_config.items():
                configs[key] = SelectorConfig(primary=value, fallbacks=[])
        except Exception as e:
            logger.warning(f"Failed to load config {config_path}: {e}")
    
    # 添加常用选择器
    for sel_type, selectors in COMMON_SELECTORS.items():
        if sel_type not in configs:
            configs[sel_type] = SelectorConfig(
                primary=selectors[0] if selectors else "",
                fallbacks=selectors[1:],
            )
    
    return SmartSelector(site_name, configs)
