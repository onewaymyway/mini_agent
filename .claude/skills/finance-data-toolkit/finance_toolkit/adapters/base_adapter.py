# -*- coding: utf-8 -*-
"""
适配器基类

定义所有数据源适配器必须实现的接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, AsyncIterator


@dataclass
class AdapterConfig:
    """适配器配置"""
    source_name: str
    enabled: bool = True
    rate_limit: int = 60
    timeout: float = 30.0
    max_retries: int = 3
    proxy_enabled: bool = False
    custom_headers: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'source_name': self.source_name,
            'enabled': self.enabled,
            'rate_limit': self.rate_limit,
            'timeout': self.timeout,
            'max_retries': self.max_retries,
            'proxy_enabled': self.proxy_enabled,
            'custom_headers': self.custom_headers,
        }


class BaseAdapter(ABC):
    """
    数据源适配器基类
    
    所有数据源抓取器必须继承此类并实现以下方法：
    - source_name: 数据源唯一标识
    - supported_types: 支持的数据类型
    - fetch(): 异步数据获取
    - health_check(): 健康检查
    - close(): 资源清理
    """
    
    def __init__(self, config: Optional[AdapterConfig] = None):
        self._config = config or AdapterConfig(source_name=self.source_name)
        self._initialized = False
        self._request_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称（唯一标识，小写）"""
        pass
    
    @property
    @abstractmethod
    def supported_types(self) -> List[str]:
        """支持的数据类型列表"""
        pass
    
    @property
    def requires_auth(self) -> bool:
        """是否需要认证"""
        return False
    
    @property
    def rate_limit(self) -> int:
        """每分钟最大请求数"""
        return self._config.rate_limit
    
    @property
    def description(self) -> str:
        """数据源描述"""
        return self.source_name
    
    @property
    def config(self) -> AdapterConfig:
        """获取配置"""
        return self._config
    
    @config.setter
    def config(self, value: AdapterConfig):
        """设置配置"""
        self._config = value
    
    async def initialize(self):
        """初始化适配器（可选覆盖）"""
        self._initialized = True
    
    @abstractmethod
    async def fetch(
        self,
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[Any]:
        """
        异步获取数据流
        
        Args:
            symbols: 标的代码列表
            data_type: 数据类型
            start: 开始时间
            end: 结束时间
            **kwargs: 其他参数
        
        Yields:
            FinanceData 对象
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            True 表示健康，False 表示不健康
        """
        pass
    
    async def close(self):
        """关闭连接/释放资源"""
        self._initialized = False
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def record_success(self):
        """记录成功请求"""
        self._request_count += 1
    
    def record_error(self, error: str):
        """记录错误"""
        self._error_count += 1
        self._last_error = error
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = self._request_count + self._error_count
        return {
            'source_name': self.source_name,
            'request_count': self._request_count,
            'error_count': self._error_count,
            'success_rate': round(
                self._request_count / total * 100, 2
            ) if total > 0 else 0.0,
            'last_error': self._last_error,
            'initialized': self._initialized,
        }
    
    def get_adapter_info(self) -> Dict[str, Any]:
        """获取适配器信息"""
        return {
            'source_name': self.source_name,
            'supported_types': self.supported_types,
            'requires_auth': self.requires_auth,
            'rate_limit': self.rate_limit,
            'description': self.description,
            'stats': self.get_stats(),
        }
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(source={self.source_name})"


# ============== 工厂函数 ==============

def create_adapter(source_name: str, **kwargs) -> BaseAdapter:
    """
    创建适配器实例（工厂函数）
    
    Args:
        source_name: 数据源名称
        **kwargs: 传递给构造函数的参数
    
    Returns:
        BaseAdapter 实例
    
    Raises:
        ValueError: 未知的数据源名称
    """
    from ..adapter import get_adapter_manager
    manager = get_adapter_manager()
    
    adapter = manager.get(source_name)
    if adapter is None:
        available = list(manager.list_adapters().keys())
        raise ValueError(
            f"Unknown adapter: {source_name}. "
            f"Available: {', '.join(available) or 'none'}"
        )
    
    return adapter


def list_adapters() -> Dict[str, Dict[str, Any]]:
    """
    列出所有可用适配器
    
    Returns:
        {source_name: adapter_info}
    """
    from ..adapter import get_adapter_manager
    return get_adapter_manager().get_all_info()


if __name__ == '__main__':
    # 测试
    print("BaseAdapter 模块测试")
    print(f"可用适配器: {list_adapters()}")
