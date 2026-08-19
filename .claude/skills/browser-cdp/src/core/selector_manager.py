"""
selector_manager.py - 统一选择器管理器

提供按域名索引的选择器注册、解析、缓存功能。
支持 CSS/XPath/TEXT/ATTRIBUTE/SEMANTIC/AI 六类选择器。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SelectorType(Enum):
    """选择器类型枚举"""
    CSS = "css"
    XPATH = "xpath"
    TEXT = "text"
    ATTRIBUTE = "attribute"
    SEMANTIC = "semantic"
    AI = "ai"


@dataclass
class Selector:
    """选择器数据类"""
    type: SelectorType
    value: str
    timeout: float = 15.0
    description: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "value": self.value,
            "timeout": self.timeout,
            "description": self.description,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Selector":
        return cls(
            type=SelectorType(data["type"]),
            value=data["value"],
            timeout=data.get("timeout", 15.0),
            description=data.get("description", ""),
        )


class SelectorManager:
    """选择器注册表（按域名索引）"""
    
    _instance: Optional["SelectorManager"] = None
    
    def __init__(self, config_dir: Optional[Path] = None):
        self._registry: Dict[str, Dict[str, Selector]] = {}
        self._config_dir = config_dir or Path(__file__).parent.parent.parent / "config" / "websites"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._load_all_configs()

    @classmethod
    def get_instance(cls, config_dir: Optional[Path] = None) -> "SelectorManager":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls(config_dir)
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """重置单例（测试用）"""
        if cls._instance:
            cls._instance._registry.clear()
        cls._instance = None    
    def register(self, domain: str, name: str, selector: Selector):
        """注册选择器"""
        if domain not in self._registry:
            self._registry[domain] = {}
        self._registry[domain][name] = selector
        logger.debug(f"Registered selector '{name}' for domain '{domain}': {selector}")
    
    def resolve(self, domain: str, name: str) -> Optional[Selector]:
        """解析选择器"""
        return self._registry.get(domain, {}).get(name)
    
    def get_all(self, domain: str) -> Dict[str, Selector]:
        """获取域名所有选择器"""
        return dict(self._registry.get(domain, {}))
    
    def has_domain(self, domain: str) -> bool:
        """检查域名是否存在"""
        return domain in self._registry
    
    def list_domains(self) -> List[str]:
        """列出所有已注册域名"""
        return list(self._registry.keys())
    
    def _load_all_configs(self):
        """加载所有配置文件"""
        if not self._config_dir.exists():
            return
        for config_file in self._config_dir.glob("*.json"):
            self._load_config(config_file)
    
    def _load_config(self, config_path: Path):
        """从 JSON 文件加载选择器配置"""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            domain = data.get("domain", config_path.stem)
            selectors = data.get("selectors", {})
            for name, sel_data in selectors.items():
                if isinstance(sel_data, str):
                    sel = Selector(type=SelectorType.CSS, value=sel_data)
                elif isinstance(sel_data, dict):
                    sel = Selector.from_dict(sel_data)
                else:
                    continue
                self.register(domain, name, sel)
            logger.info(f"Loaded {len(selectors)} selectors from {config_path.name}")
        except Exception as e:
            logger.warning(f"Failed to load config {config_path}: {e}")
    
    def save_config(self, domain: str, selectors: Dict[str, Selector]):
        """保存选择器配置到 JSON 文件"""
        config_path = self._config_dir / f"{domain}.json"
        data = {
            "domain": domain,
            "selectors": {name: sel.to_dict() for name, sel in selectors.items()},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 同步更新内存注册表，避免新实例需要重新加载
        self._registry.setdefault(domain, {}).update(
            {name: sel for name, sel in selectors.items()}
        )
        logger.info(f"Saved {len(selectors)} selectors to {config_path}")
    
    def remove_domain(self, domain: str):
        """移除域名所有选择器"""
        self._registry.pop(domain, None)