#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
base.py - 网站适配器基类

定义统一的网站适配器接口，所有网站适配器必须继承此类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class AdapterStatus(Enum):
    """适配器状态"""
    PENDING = "pending"           # 待评估
    SUPPORTED = "supported"       # 支持
    PARTIAL = "partial"           # 部分支持
    UNSUPPORTED = "unsupported"   # 不支持
    DEPRECATED = "deprecated"     # 已弃用


class AntiCrawlLevel(Enum):
    """反爬等级"""
    NONE = 0          # 无反爬
    LOW = 1           # 轻度（基础请求头检测）
    MEDIUM = 2        # 中度（IP 频率限制）
    HIGH = 3          # 高度（指纹检测 + 验证码）
    VERY_HIGH = 4     # 极高度（行为分析 + 设备指纹）
    BLOCKED = 5       # 封锁（需要特殊手段）


class EvaluationDimension(Enum):
    """评估维度"""
    PAGE_LOAD = "页面加载能力"
    ELEMENT_LOCATE = "元素定位能力"
    DATA_EXTRACT = "数据提取能力"
    ANTI_DETECTION = "反检测能力"
    STABILITY = "稳定性与恢复"


@dataclass
class WebsiteConfig:
    """网站配置"""
    # 基本信息
    name: str
    domain: str
    url: str
    category: str              # ECOM/NEWS/SOCIAL/GOV/FINANCE/EDU/...
    subcategory: str
    
    # 技术特征
    frontend_framework: str    # React/Vue/SSR/None
    anti_crawl_level: AntiCrawlLevel
    login_required: bool
    captcha_type: str          # none/slider/click/text/recaptcha/hcaptcha/turnstile
    
    # 测试配置
    priority: str              # P0/P1/P2/P3
    timeout: int               # 秒
    retry_count: int
    stealth_mode: bool
    
    # 目标指标
    target_success_rate: float
    target_accuracy: float
    
    # 自定义配置
    custom_config: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    # 元数据
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "domain": self.domain,
            "url": self.url,
            "category": self.category,
            "subcategory": self.subcategory,
            "frontend_framework": self.frontend_framework,
            "anti_crawl_level": self.anti_crawl_level.value,
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
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WebsiteConfig':
        """从字典创建配置"""
        if 'anti_crawl_level' in data and isinstance(data['anti_crawl_level'], int):
            data['anti_crawl_level'] = AntiCrawlLevel(data['anti_crawl_level'])
        return cls(**data)


@dataclass
class AdapterResult:
    """适配器执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_success(self) -> bool:
        return self.success
    
    @property
    def is_failure(self) -> bool:
        return not self.success
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "duration": self.duration,
            "metadata": self.metadata,
        }


@dataclass
class DimensionScore:
    """评估维度得分"""
    name: str
    score: float           # 0-100
    weight: float          # 权重
    details: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "weight": self.weight,
            "details": self.details,
        }


class BaseWebsiteAdapter(ABC):
    """网站适配器基类
    
    所有网站适配器必须继承此类并实现以下方法：
    - navigate(): 导航到指定 URL
    - search(): 执行搜索
    - extract(): 提取页面数据
    - screenshot(): 截图
    
    示例：
        class BaiduAdapter(BaseWebsiteAdapter):
            def __init__(self):
                self._config = WebsiteConfig(
                    name="百度",
                    domain="baidu.com",
                    url="https://www.baidu.com",
                    category="SEARCH",
                    subcategory="SEARCH_ENGINE",
                    anti_crawl_level=AntiCrawlLevel.LOW,
                    ...
                )
            
            @property
            def config(self) -> WebsiteConfig:
                return self._config
            
            async def navigate(self, url: str) -> AdapterResult:
                # 实现导航逻辑
                pass
    """
    
    @property
    @abstractmethod
    def config(self) -> WebsiteConfig:
        """返回网站配置"""
        pass
    
    @abstractmethod
    async def navigate(self, url: str) -> AdapterResult:
        """导航到指定 URL
        
        Args:
            url: 目标 URL
            
        Returns:
            AdapterResult: 执行结果
        """
        pass
    
    @abstractmethod
    async def search(self, query: str, **kwargs) -> AdapterResult:
        """执行搜索
        
        Args:
            query: 搜索关键词
            **kwargs: 其他参数（如 page, sort 等）
            
        Returns:
            AdapterResult: 执行结果，data 包含搜索结果列表
        """
        pass
    
    @abstractmethod
    async def extract(self, selector: str, **kwargs) -> AdapterResult:
        """提取页面数据
        
        Args:
            selector: CSS 选择器
            **kwargs: 其他参数
            
        Returns:
            AdapterResult: 执行结果，data 包含提取的数据
        """
        pass
    
    @abstractmethod
    async def screenshot(self, path: str, annotate: bool = False) -> AdapterResult:
        """截图
        
        Args:
            path: 截图保存路径
            annotate: 是否标注可交互元素
            
        Returns:
            AdapterResult: 执行结果
        """
        pass
    
    async def health_check(self) -> bool:
        """健康检查
        
        Returns:
            bool: 是否健康
        """
        try:
            result = await self.navigate(self.config.url)
            return result.success
        except Exception:
            return False
    
    def get_capabilities(self) -> Dict[str, bool]:
        """返回支持的功能列表
        
        Returns:
            Dict[str, bool]: 功能支持情况
        """
        return {
            "search": hasattr(self, 'search') and not self.search.__func__ is BaseWebsiteAdapter.search,
            "extract": hasattr(self, 'extract') and not self.extract.__func__ is BaseWebsiteAdapter.extract,
            "screenshot": hasattr(self, 'screenshot') and not self.screenshot.__func__ is BaseWebsiteAdapter.screenshot,
            "login": self.config.login_required,
            "stealth": self.config.stealth_mode,
        }
    
    def get_status(self) -> AdapterStatus:
        """获取适配器状态
        
        Returns:
            AdapterStatus: 当前状态
        """
        # 默认返回 PENDING，实际状态由评估器更新
        return AdapterStatus.PENDING
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(domain={self.config.domain}, status={self.get_status().value})"


class BaseEvaluator(ABC):
    """评估器基类
    
    所有评估器必须继承此类并实现 evaluate() 方法。
    """
    
    @abstractmethod
    async def evaluate(self, adapter: BaseWebsiteAdapter) -> Dict[str, Any]:
        """执行评估
        
        Args:
            adapter: 待评估的适配器
            
        Returns:
            Dict[str, Any]: 评估结果，包含 overall_score 和 dimensions
        """
        pass
    
    @abstractmethod
    async def evaluate_dimension(
        self, 
        adapter: BaseWebsiteAdapter, 
        dimension: EvaluationDimension
    ) -> DimensionScore:
        """评估单个维度
        
        Args:
            adapter: 待评估的适配器
            dimension: 评估维度
            
        Returns:
            DimensionScore: 维度得分
        """
        pass
    
    def get_dimension_weights(self) -> Dict[str, float]:
        """返回各维度的权重
        
        Returns:
            Dict[str, float]: 维度权重映射
        """
        return {
            EvaluationDimension.PAGE_LOAD.value: 0.20,
            EvaluationDimension.ELEMENT_LOCATE.value: 0.25,
            EvaluationDimension.DATA_EXTRACT.value: 0.25,
            EvaluationDimension.ANTI_DETECTION.value: 0.15,
            EvaluationDimension.STABILITY.value: 0.15,
        }
    
    def calculate_overall_score(self, dimension_scores: Dict[str, float]) -> float:
        """计算综合得分
        
        Args:
            dimension_scores: 各维度得分映射
            
        Returns:
            float: 综合得分 (0-100)
        """
        weights = self.get_dimension_weights()
        total_weight = sum(weights.values())
        
        weighted_sum = sum(
            score * weights.get(dim, 0)
            for dim, score in dimension_scores.items()
        )
        
        return round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0


# 导出公共接口
__all__ = [
    "BaseWebsiteAdapter",
    "BaseEvaluator",
    "WebsiteConfig",
    "AdapterResult",
    "AdapterStatus",
    "AntiCrawlLevel",
    "EvaluationDimension",
    "DimensionScore",
]
