"""
selector_version_manager.py - 选择器版本管理

提供选择器版本控制、自动更新和回滚机制：
- 选择器版本追踪
- 自动检测网站改版
- 选择器健康度评估
- 版本回滚支持
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SelectorVersion:
    """选择器版本信息"""
    selector: str
    version: int
    created_at: float
    success_count: int = 0
    failure_count: int = 0
    last_used: float = 0.0
    health_score: float = 1.0  # 0-1，健康度
    
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total
    
    def to_dict(self) -> Dict:
        return {
            "selector": self.selector,
            "version": self.version,
            "created_at": self.created_at,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used": self.last_used,
            "health_score": self.health_score,
            "success_rate": self.success_rate,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "SelectorVersion":
        return cls(
            selector=data["selector"],
            version=data["version"],
            created_at=data["created_at"],
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            last_used=data.get("last_used", 0.0),
            health_score=data.get("health_score", 1.0),
        )


@dataclass
class SelectorRegistry:
    """选择器注册表"""
    site_name: str
    selector_type: str
    versions: List[SelectorVersion] = field(default_factory=list)
    current_version: int = 1
    last_updated: float = 0.0
    
    @property
    def current_selector(self) -> Optional[str]:
        if self.versions:
            return self.versions[-1].selector
        return None
    
    @property
    def best_selector(self) -> Optional[str]:
        """获取健康度最高的选择器"""
        if not self.versions:
            return None
        return max(self.versions, key=lambda v: v.health_score).selector
    
    def to_dict(self) -> Dict:
        return {
            "site_name": self.site_name,
            "selector_type": self.selector_type,
            "versions": [v.to_dict() for v in self.versions],
            "current_version": self.current_version,
            "last_updated": self.last_updated,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "SelectorRegistry":
        return cls(
            site_name=data["site_name"],
            selector_type=data["selector_type"],
            versions=[SelectorVersion.from_dict(v) for v in data.get("versions", [])],
            current_version=data.get("current_version", 1),
            last_updated=data.get("last_updated", 0.0),
        )


class SelectorVersionManager:
    """选择器版本管理器"""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path(__file__).parent.parent / "data" / "selector_versions"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._registries: Dict[str, Dict[str, SelectorRegistry]] = {}
        self._load_cache()
    
    def _get_cache_file(self, site_name: str) -> Path:
        safe_name = site_name.replace(".", "_").replace("/", "_")
        return self.cache_dir / f"{safe_name}_selectors.json"
    
    def _load_cache(self):
        """加载缓存"""
        if not self.cache_dir.exists():
            return
        
        for cache_file in self.cache_dir.glob("*_selectors.json"):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    site_name = cache_file.stem.replace("_selectors", "").replace("_", ".")
                    self._registries[site_name] = {
                        reg.selector_type: SelectorRegistry.from_dict(reg)
                        for reg in data.get("registries", [])
                    }
            except Exception as e:
                logger.warning(f"Failed to load selector cache {cache_file}: {e}")
    
    def _save_cache(self, site_name: str):
        """保存缓存"""
        if site_name not in self._registries:
            return
        
        cache_file = self._get_cache_file(site_name)
        data = {
            "site_name": site_name,
            "registries": [reg.to_dict() for reg in self._registries[site_name].values()],
            "updated_at": time.time(),
        }
        
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save selector cache {cache_file}: {e}")
    
    def get_registry(self, site_name: str, selector_type: str) -> SelectorRegistry:
        """获取选择器注册表"""
        if site_name not in self._registries:
            self._registries[site_name] = {}
        
        if selector_type not in self._registries[site_name]:
            self._registries[site_name][selector_type] = SelectorRegistry(
                site_name=site_name,
                selector_type=selector_type,
            )
        
        return self._registries[site_name][selector_type]
    
    def record_success(self, site_name: str, selector_type: str, selector: str):
        """记录选择器成功使用"""
        registry = self.get_registry(site_name, selector_type)
        
        # 查找或创建版本
        version = None
        for v in registry.versions:
            if v.selector == selector:
                version = v
                break
        
        if not version:
            version = SelectorVersion(
                selector=selector,
                version=registry.current_version,
                created_at=time.time(),
            )
            registry.versions.append(version)
            registry.current_version += 1
        
        version.success_count += 1
        version.last_used = time.time()
        version.health_score = self._calculate_health(version)
        registry.last_updated = time.time()
        
        self._save_cache(site_name)
        logger.debug(f"记录选择器成功: {site_name}/{selector_type} -> {selector}")
    
    def record_failure(self, site_name: str, selector_type: str, selector: str):
        """记录选择器失败"""
        registry = self.get_registry(site_name, selector_type)
        
        for v in registry.versions:
            if v.selector == selector:
                v.failure_count += 1
                v.last_used = time.time()
                v.health_score = self._calculate_health(v)
                registry.last_updated = time.time()
                break
        
        self._save_cache(site_name)
        logger.debug(f"记录选择器失败: {site_name}/{selector_type} -> {selector}")
    
    def _calculate_health(self, version: SelectorVersion) -> float:
        """计算选择器健康度"""
        total = version.success_count + version.failure_count
        if total == 0:
            return 1.0
        
        # 基础成功率
        base_rate = version.success_count / total
        
        # 时间衰减因子（最近使用更可靠）
        time_factor = 1.0
        if version.last_used > 0:
            days_since_use = (time.time() - version.last_used) / 86400
            time_factor = max(0.5, 1.0 - days_since_use * 0.1)
        
        # 健康度 = 成功率 * 时间因子
        return base_rate * time_factor
    
    def get_best_selector(self, site_name: str, selector_type: str) -> Optional[str]:
        """获取最佳选择器"""
        registry = self.get_registry(site_name, selector_type)
        if not registry.versions:
            return None
        
        # 过滤健康度低于阈值的选择器
        valid_versions = [v for v in registry.versions if v.health_score >= 0.3]
        if not valid_versions:
            return registry.current_selector
        
        return max(valid_versions, key=lambda v: v.health_score).selector
    
    def should_update(self, site_name: str, selector_type: str, threshold: float = 0.3) -> bool:
        """判断是否需要更新选择器"""
        registry = self.get_registry(site_name, selector_type)
        if not registry.versions:
            return False
        
        # 检查当前选择器健康度
        current = registry.current_selector
        if current:
            for v in registry.versions:
                if v.selector == current and v.health_score < threshold:
                    return True
        
        return False
    
    def get_selector_stats(self, site_name: str, selector_type: str) -> Dict:
        """获取选择器统计信息"""
        registry = self.get_registry(site_name, selector_type)
        
        return {
            "site_name": site_name,
            "selector_type": selector_type,
            "total_versions": len(registry.versions),
            "current_selector": registry.current_selector,
            "best_selector": registry.best_selector,
            "versions": [v.to_dict() for v in sorted(registry.versions, key=lambda x: x.health_score, reverse=True)[:5]],
            "last_updated": registry.last_updated,
        }
    
    def rollback_to_version(self, site_name: str, selector_type: str, version_num: int) -> bool:
        """回滚到指定版本"""
        registry = self.get_registry(site_name, selector_type)
        
        for v in registry.versions:
            if v.version == version_num:
                # 将指定版本设为当前
                registry.versions = [v] + [x for x in registry.versions if x.version != version_num]
                registry.last_updated = time.time()
                self._save_cache(site_name)
                logger.info(f"回滚选择器到版本 {version_num}: {site_name}/{selector_type}")
                return True
        
        return False
    
    def get_all_stats(self) -> Dict:
        """获取所有选择器统计"""
        stats = {}
        for site_name, types in self._registries.items():
            stats[site_name] = {
                selector_type: self.get_selector_stats(site_name, selector_type)
                for selector_type in types
            }
        return stats


# 全局单例
_selector_manager: Optional[SelectorVersionManager] = None


def get_selector_manager() -> SelectorVersionManager:
    """获取全局选择器管理器单例"""
    global _selector_manager
    if _selector_manager is None:
        _selector_manager = SelectorVersionManager()
    return _selector_manager


def record_selector_success(site_name: str, selector_type: str, selector: str):
    """便捷函数：记录选择器成功"""
    get_selector_manager().record_success(site_name, selector_type, selector)


def record_selector_failure(site_name: str, selector_type: str, selector: str):
    """便捷函数：记录选择器失败"""
    get_selector_manager().record_failure(site_name, selector_type, selector)


def get_best_selector(site_name: str, selector_type: str) -> Optional[str]:
    """便捷函数：获取最佳选择器"""
    return get_selector_manager().get_best_selector(site_name, selector_type)
