#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
config_manager.py - 网站配置管理器

管理所有网站的配置信息，支持动态加载和注册。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
import json
from pathlib import Path


@dataclass
class WebsiteConfig:
    """网站配置"""
    # 基本信息
    name: str
    domain: str
    url: str
    category: str
    subcategory: str
    
    # 技术特征
    frontend_framework: str = ""
    anti_crawl_level: int = 1
    login_required: bool = False
    captcha_type: str = "none"
    
    # 测试配置
    priority: str = "P2"
    timeout: int = 30
    retry_count: int = 3
    stealth_mode: bool = True
    
    # 评估指标
    target_success_rate: float = 0.90
    target_accuracy: float = 0.85
    
    # 自定义配置
    custom_config: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    # 元数据
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "domain": self.domain,
            "url": self.url,
            "category": self.category,
            "subcategory": self.subcategory,
            "frontend_framework": self.frontend_framework,
            "anti_crawl_level": self.anti_crawl_level,
            "login_required": self.login_required,
            "captcha_type": self.captcha_type,
            "priority": self.priority,
            "timeout": self.timeout,
            "retry_count": self.retry_count,
            "stealth_mode": self.stealth_mode,
            "target_success_rate": self.target_success_rate,
            "target_accuracy": self.target_accuracy,
            "custom_config": self.custom_config,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "WebsiteConfig":
        return cls(
            name=data.get("name", ""),
            domain=data.get("domain", ""),
            url=data.get("url", ""),
            category=data.get("category", ""),
            subcategory=data.get("subcategory", ""),
            frontend_framework=data.get("frontend_framework", ""),
            anti_crawl_level=data.get("anti_crawl_level", 1),
            login_required=data.get("login_required", False),
            captcha_type=data.get("captcha_type", "none"),
            priority=data.get("priority", "P2"),
            timeout=data.get("timeout", 30),
            retry_count=data.get("retry_count", 3),
            stealth_mode=data.get("stealth_mode", True),
            target_success_rate=data.get("target_success_rate", 0.90),
            target_accuracy=data.get("target_accuracy", 0.85),
            custom_config=data.get("custom_config", {}),
            tags=data.get("tags", []),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


class ConfigManager:
    """网站配置管理器"""
    
    def __init__(self, config_dir: str = "config/websites"):
        self.config_dir = Path(config_dir)
        self._websites: Dict[str, WebsiteConfig] = {}
        self._categories: Dict[str, List[str]] = {}
        self._load_configs()
    
    def _load_configs(self) -> None:
        """加载所有配置文件"""
        if not self.config_dir.exists():
            self.config_dir.mkdir(parents=True, exist_ok=True)
            return
        
        for config_file in self.config_dir.glob("*.json"):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                config = WebsiteConfig.from_dict(data)
                self._websites[config.domain] = config
                
                # 按分类分组
                category = config.category
                if category not in self._categories:
                    self._categories[category] = []
                self._categories[category].append(config.domain)
            except Exception as e:
                print(f"加载配置失败 {config_file.name}: {e}")
    
    def get_website(self, domain: str) -> Optional[WebsiteConfig]:
        """获取网站配置"""
        return self._websites.get(domain.lower())
    
    def get_websites_by_category(self, category: str) -> List[WebsiteConfig]:
        """按分类获取网站列表"""
        domains = self._categories.get(category, [])
        return [self._websites[d] for d in domains if d in self._websites]
    
    def get_websites_by_priority(self, priority: str) -> List[WebsiteConfig]:
        """按优先级获取网站列表"""
        return [w for w in self._websites.values() if w.priority == priority]
    
    def get_all_websites(self) -> List[WebsiteConfig]:
        """获取所有网站配置"""
        return list(self._websites.values())
    
    def register_website(self, config: WebsiteConfig) -> None:
        """注册新网站"""
        self._websites[config.domain.lower()] = config
        
        # 更新分类分组
        category = config.category
        if category not in self._categories:
            self._categories[category] = []
        if config.domain.lower() not in self._categories[category]:
            self._categories[category].append(config.domain.lower())
        
        # 保存配置
        self._save_config(config)
    
    def unregister_website(self, domain: str) -> bool:
        """注销网站"""
        domain = domain.lower()
        if domain in self._websites:
            config = self._websites.pop(domain)
            
            # 更新分类分组
            category = config.category
            if domain in self._categories.get(category, []):
                self._categories[category].remove(domain)
            
            # 删除配置文件
            config_file = self.config_dir / f"{domain}.json"
            if config_file.exists():
                config_file.unlink()
            
            return True
        return False
    
    def _save_config(self, config: WebsiteConfig) -> None:
        """保存配置文件"""
        config_file = self.config_dir / f"{config.domain}.json"
        config.updated_at = datetime.now().isoformat()
        if not config.created_at:
            config.created_at = config.updated_at
        
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
    
    def get_categories(self) -> List[str]:
        """获取所有分类"""
        return list(self._categories.keys())
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        by_category = {}
        by_priority = {}
        by_captcha = {}
        
        for config in self._websites.values():
            by_category[config.category] = by_category.get(config.category, 0) + 1
            by_priority[config.priority] = by_priority.get(config.priority, 0) + 1
            by_captcha[config.captcha_type] = by_captcha.get(config.captcha_type, 0) + 1
        
        return {
            "total_websites": len(self._websites),
            "by_category": by_category,
            "by_priority": by_priority,
            "by_captcha": by_captcha,
        }
    
    def export_all_configs(self, output_path: str) -> None:
        """导出所有配置"""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_websites": len(self._websites),
            "websites": [w.to_dict() for w in self._websites.values()],
            "exported_at": datetime.now().isoformat(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# 导出公共接口
__all__ = [
    "WebsiteConfig",
    "ConfigManager",
]