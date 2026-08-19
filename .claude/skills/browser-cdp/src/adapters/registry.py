"""
src/adapters/registry.py

适配器注册中心：管理所有已注册的网站适配器。
支持按 category/site_id/domain 查询和发现。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Type

from src.adapters.base import BaseWebsiteAdapter

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """
    适配器注册中心：管理所有已注册的网站适配器。
    
    使用示例：
        registry = AdapterRegistry()
        registry.register(JdAdapter())
        adapter = registry.get("jd")
        ecom_adapters = registry.get_by_category("ECOM")
    """
    
    _instance: Optional["AdapterRegistry"] = None
    
    def __init__(self):
        self._instances: Dict[str, BaseWebsiteAdapter] = {}
        self._category_index: Dict[str, List[str]] = defaultdict(list)
        self._domain_index: Dict[str, str] = {}
    
    @classmethod
    def get_instance(cls) -> "AdapterRegistry":
        """获取单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset(cls) -> None:
        """重置注册表（测试用）"""
        cls._instance = None
    
    def register(self, adapter: BaseAdapter) -> None:
        """
        注册适配器。
        
        Args:
            adapter: 待注册的适配器实例
        
        Raises:
            ValueError: 如果 site_id 已存在
        """
        site_id = adapter.descriptor.site_id
        if site_id in self._instances:
            logger.warning(f"适配器 {site_id} 已存在，将被覆盖")
        self._instances[site_id] = adapter
        category = adapter.descriptor.category
        if hasattr(category, "value"):
            category = category.value
        self._category_index[category].append(site_id)
        self._domain_index[adapter.descriptor.domain] = site_id
        logger.info(f"注册适配器: {site_id} ({adapter.descriptor.name}) [category={category}]")
    
    def get(self, site_id: str) -> Optional[BaseAdapter]:
        """按 site_id 获取适配器"""
        return self._instances.get(site_id)
    
    def get_by_domain(self, domain: str) -> Optional[BaseAdapter]:
        """按域名获取适配器"""
        site_id = self._domain_index.get(domain)
        return self._instances.get(site_id) if site_id else None
    
    def get_by_category(self, category: str) -> List[BaseAdapter]:
        """按分类获取所有适配器"""
        ids = self._category_index.get(category, [])
        return [self._instances[pid] for pid in ids if pid in self._instances]
    
    def list_all(self) -> List[BaseAdapter]:
        """列出所有已注册适配器"""
        return list(self._instances.values())
    
    def list_by_priority(self, priority: str) -> List[BaseAdapter]:
        """按优先级筛选"""
        return [
            a for a in self._instances.values()
            if a.descriptor.priority == priority
        ]
    
    def discover_from_package(self, package_path: str) -> int:
        """
        从Python包自动发现并注册适配器。
        扫描指定路径下所有以 _adapter.py 或 *_search.py 命名的文件。
        
        Returns:
            成功注册的数量
        """
        import importlib
        count = 0
        path = Path(package_path)
        if not path.exists():
            logger.warning(f"路径不存在: {path}")
            return 0
        
        for py_file in path.glob("*_adapter.py"):
            module_name = py_file.stem
            try:
                module = importlib.import_module(f"{package_path}.{module_name}")
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and 
                        issubclass(attr, BaseAdapter) and 
                        attr is not BaseAdapter):
                        self.register(attr())
                        count += 1
            except Exception as e:
                logger.error(f"加载模块 {module_name} 失败: {e}")
        
        return count
    
    def health_check_all(self) -> Dict[str, bool]:
        """对所有已注册适配器执行健康检查"""
        results = {}
        for site_id, adapter in self._instances.items():
            try:
                import asyncio
                results[site_id] = asyncio.run(adapter.health_check())
            except Exception as e:
                results[site_id] = False
                logger.error(f"健康检查失败 {site_id}: {e}")
        return results
    
    def summary(self) -> Dict:
        """返回注册表摘要"""
        by_category: Dict[str, int] = {}
        for cat, ids in self._category_index.items():
            by_category[cat] = len([i for i in ids if i in self._instances])
        
        by_priority: Dict[str, int] = {}
        for a in self._instances.values():
            p = a.descriptor.priority
            by_priority[p] = by_priority.get(p, 0) + 1
        
        return {
            "total": len(self._instances),
            "by_category": by_category,
            "by_priority": by_priority,
            "registered_at": list(self._instances.keys()),
        }
    
    def __len__(self) -> int:
        return len(self._instances)
    
    def __contains__(self, site_id: str) -> bool:
        return site_id in self._instances
    
    def __repr__(self) -> str:
        return f"<AdapterRegistry({len(self)} adapters)>"


# 全局单例
_default_registry: Optional[AdapterRegistry] = None


def get_registry() -> AdapterRegistry:
    """获取默认注册表实例"""
    global _default_registry
    if _default_registry is None:
        _default_registry = AdapterRegistry.get_instance()
    return _default_registry


def register(adapter: BaseAdapter) -> None:
    """快捷注册函数"""
    get_registry().register(adapter)


def get_adapter(site_id: str) -> Optional[BaseAdapter]:
    """快捷获取函数"""
    return get_registry().get(site_id)


__all__ = [
    "AdapterRegistry",
    "get_registry",
    "register",
    "get_adapter",
]
