# -*- coding: utf-8 -*-
"""
适配器注册机制模块

提供插件式数据源接入架构：
- Adapter 抽象基类定义统一接口
- PluginLoader 动态加载插件
- AdapterManager 管理适配器生命周期
- 支持热插拔（运行时替换/卸载）

使用示例：
    from finance_toolkit.adapter import Adapter, AdapterManager, PluginLoader
    
    # 定义适配器
    class MyScraperAdapter(Adapter):
        @property
        def source_name(self): return 'my_source'
        @property
        def supported_types(self): return ['quote', 'kline']
        async def fetch(self, symbols, data_type, **kwargs): ...
        async def health_check(self): return True
        async def close(self): pass
    
    # 注册
    manager = AdapterManager()
    manager.register(MyScraperAdapter())
    
    # 热插拔：替换
    manager.replace('my_source', NewScraperAdapter())
    
    # 卸载
    manager.unregister('my_source')
"""

import importlib
import inspect
import logging
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type, AsyncIterator

logger = logging.getLogger(__name__)


# ============== 适配器抽象基类 ==============

class Adapter(ABC):
    """
    数据源适配器抽象基类
    
    所有数据源抓取器必须实现此接口，确保统一的数据访问协议。
    
    实现要求：
    1. source_name: 数据源唯一标识（小写，如 'akshare', 'eastmoney'）
    2. supported_types: 支持的数据类型列表
    3. fetch(): 异步数据获取，返回 FinanceData 流
    4. health_check(): 健康检查
    5. close(): 资源清理
    """
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称（唯一标识）"""
        pass
    
    @property
    @abstractmethod
    def supported_types(self) -> List[str]:
        """支持的数据类型列表"""
        pass
    
    @property
    def requires_auth(self) -> bool:
        """是否需要认证（默认 False）"""
        return False
    
    @property
    def rate_limit(self) -> int:
        """每分钟最大请求数（默认 60）"""
        return 60
    
    @property
    def description(self) -> str:
        """数据源描述"""
        return self.source_name
    
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
        pass
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    def get_adapter_info(self) -> Dict[str, Any]:
        """获取适配器信息"""
        return {
            'source_name': self.source_name,
            'supported_types': self.supported_types,
            'requires_auth': self.requires_auth,
            'rate_limit': self.rate_limit,
            'description': self.description,
        }


# ============== 适配器元数据 ==============

@dataclass
class AdapterMetadata:
    """适配器元数据"""
    source_name: str
    class_name: str
    module_path: str
    supported_types: List[str]
    requires_auth: bool
    rate_limit: int
    description: str
    loaded_at: datetime = field(default_factory=datetime.utcnow)
    instance: Optional[Adapter] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'source_name': self.source_name,
            'class_name': self.class_name,
            'module_path': self.module_path,
            'supported_types': self.supported_types,
            'requires_auth': self.requires_auth,
            'rate_limit': self.rate_limit,
            'description': self.description,
            'loaded_at': self.loaded_at.isoformat(),
        }


# ============== 插件加载器 ==============

class PluginLoader:
    """
    插件加载器
    
    支持从指定目录动态加载适配器插件。
    """
    
    def __init__(self, search_paths: Optional[List[str]] = None):
        """
        初始化插件加载器
        
        Args:
            search_paths: 搜索路径列表，默认包含 finance_toolkit.adapters 和 finance_toolkit.scrapers
        """
        self.search_paths = search_paths or [
            str(Path(__file__).parent / 'adapters'),
            str(Path(__file__).parent / 'scrapers'),
        ]
        self._loaded_modules: Set[str] = set()
        self._load_callbacks: List[Callable] = []
    
    def add_search_path(self, path: str):
        """添加搜索路径"""
        if path not in self.search_paths:
            self.search_paths.append(path)
            logger.info(f"添加搜索路径: {path}")
    
    def add_load_callback(self, callback: Callable):
        """添加加载回调（插件加载后调用）"""
        self._load_callbacks.append(callback)
    
    def load_from_module(self, module_path: str) -> List[Adapter]:
        """
        从指定模块路径加载适配器
        
        Args:
            module_path: 模块路径，如 'finance_toolkit.adapters.my_adapter'
        
        Returns:
            加载的适配器实例列表
        """
        if module_path in self._loaded_modules:
            logger.debug(f"模块已加载，跳过: {module_path}")
            return []
        
        try:
            module = importlib.import_module(module_path)
            self._loaded_modules.add(module_path)
            
            adapters = []
            for attr_name in dir(module):
                if attr_name.endswith('Adapter') or attr_name.endswith('Scraper'):
                    cls = getattr(module, attr_name)
                    if isinstance(cls, type) and issubclass(cls, Adapter) and cls != Adapter:
                        try:
                            instance = cls()
                            adapters.append(instance)
                            logger.info(f"加载适配器: {instance.source_name} from {module_path}")
                        except Exception as e:
                            logger.warning(f"实例化适配器失败 {attr_name}: {e}")
            
            # 调用回调
            for callback in self._load_callbacks:
                try:
                    callback(module_path, adapters)
                except Exception as e:
                    logger.warning(f"加载回调执行失败: {e}")
            
            return adapters
            
        except ImportError as e:
            logger.warning(f"导入模块失败: {module_path} - {e}")
            return []
        except Exception as e:
            logger.error(f"加载模块异常: {module_path} - {e}")
            return []
    
    def load_from_directory(self, dir_path: str) -> List[Adapter]:
        """
        从目录加载所有适配器模块
        
        Args:
            dir_path: 目录路径
        
        Returns:
            加载的适配器实例列表
        """
        path = Path(dir_path)
        if not path.exists():
            logger.debug(f"目录不存在，跳过: {dir_path}")
            return []
        
        adapters = []
        for _, module_name, is_pkg in pkgutil.iter_modules([str(path)]):
            if is_pkg or not module_name.endswith('.py'):
                continue
            
            full_path = f"{path.parent.name}.{path.name}.{module_name}"
            # 尝试从 finance_toolkit 包导入
            for prefix in ['finance_toolkit', '']:
                module_path = f"{prefix}{full_path}" if prefix else full_path
                result = self.load_from_module(module_path)
                if result:
                    adapters.extend(result)
                    break
        
        return adapters
    
    def discover_and_load(self) -> List[Adapter]:
        """
        自动发现并加载所有适配器
        
        Returns:
            加载的适配器实例列表
        """
        all_adapters = []
        
        for search_path in self.search_paths:
            adapters = self.load_from_directory(search_path)
            all_adapters.extend(adapters)
        
        logger.info(f"自动发现完成，共加载 {len(all_adapters)} 个适配器")
        return all_adapters


# ============== 适配器管理器 ==============

class AdapterManager:
    """
    适配器管理器
    
    管理所有已注册的适配器，支持：
    - 注册/注销
    - 热插拔（运行时替换）
    - 按数据类型查询
    - 健康状态监控
    """
    
    def __init__(self):
        self._adapters: Dict[str, Adapter] = {}
        self._metadata: Dict[str, AdapterMetadata] = {}
        self._loader = PluginLoader()
        self._lock = None  # 延迟初始化，避免在模块导入时创建 asyncio.Lock
    
    def _get_lock(self):
        """延迟获取锁（避免模块导入时创建事件循环）"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                if not hasattr(self, '_async_lock'):
                    self._async_lock = asyncio.Lock()
                return self._async_lock
        except RuntimeError:
            pass
        return None
    
    def register(self, adapter: Adapter) -> bool:
        """
        注册适配器
        
        Args:
            adapter: 适配器实例
        
        Returns:
            是否注册成功
        """
        name = adapter.source_name
        
        if name in self._adapters:
            logger.warning(f"适配器已存在，将被覆盖: {name}")
        
        self._adapters[name] = adapter
        self._metadata[name] = AdapterMetadata(
            source_name=name,
            class_name=adapter.__class__.__name__,
            module_path=adapter.__class__.__module__,
            supported_types=adapter.supported_types,
            requires_auth=adapter.requires_auth,
            rate_limit=adapter.rate_limit,
            description=adapter.description,
            instance=adapter,
        )
        
        logger.info(f"注册适配器: {name} ({adapter.__class__.__name__})")
        return True
    
    def unregister(self, source_name: str) -> bool:
        """
        注销适配器
        
        Args:
            source_name: 数据源名称
        
        Returns:
            是否注销成功
        """
        if source_name not in self._adapters:
            logger.warning(f"适配器不存在: {source_name}")
            return False
        
        adapter = self._adapters[source_name]
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(adapter.close())
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"关闭适配器失败: {source_name} - {e}")
        
        del self._adapters[source_name]
        del self._metadata[source_name]
        logger.info(f"注销适配器: {source_name}")
        return True
    
    def replace(self, source_name: str, new_adapter: Adapter) -> bool:
        """
        热插拔：替换适配器
        
        Args:
            source_name: 原适配器名称
            new_adapter: 新适配器实例
        
        Returns:
            是否替换成功
        """
        if source_name not in self._adapters:
            logger.warning(f"适配器不存在，无法替换: {source_name}")
            return False
        
        # 关闭旧适配器
        old_adapter = self._adapters[source_name]
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(old_adapter.close())
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"关闭旧适配器失败: {source_name} - {e}")
        
        # 注册新适配器
        self.register(new_adapter)
        logger.info(f"热插拔完成: {source_name} -> {new_adapter.source_name}")
        return True
    
    def get(self, source_name: str) -> Optional[Adapter]:
        """获取适配器实例"""
        return self._adapters.get(source_name)
    
    def get_metadata(self, source_name: str) -> Optional[AdapterMetadata]:
        """获取适配器元数据"""
        return self._metadata.get(source_name)
    
    def list_adapters(self) -> Dict[str, Adapter]:
        """列出所有已注册适配器"""
        return dict(self._adapters)
    
    def list_metadata(self) -> Dict[str, AdapterMetadata]:
        """列出所有适配器元数据"""
        return dict(self._metadata)
    
    def find_by_type(self, data_type: str) -> List[str]:
        """
        查找支持指定数据类型的所有适配器
        
        Args:
            data_type: 数据类型
        
        Returns:
            适配器名称列表
        """
        return [
            name for name, adapter in self._adapters.items()
            if data_type in adapter.supported_types
        ]
    
    def get_adapter_info(self, source_name: str) -> Optional[Dict[str, Any]]:
        """获取适配器详细信息"""
        adapter = self._adapters.get(source_name)
        if adapter:
            return adapter.get_adapter_info()
        return None
    
    def get_all_info(self) -> Dict[str, Dict[str, Any]]:
        """获取所有适配器信息"""
        return {
            name: adapter.get_adapter_info()
            for name, adapter in self._adapters.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """获取适配器统计"""
        return {
            'total': len(self._adapters),
            'sources': list(self._adapters.keys()),
            'types_supported': self._get_all_supported_types(),
        }
    
    def _get_all_supported_types(self) -> Set[str]:
        """获取所有支持的数据类型"""
        types = set()
        for adapter in self._adapters.values():
            types.update(adapter.supported_types)
        return types
    
    def load_plugins(self) -> int:
        """
        加载插件目录中的所有适配器
        
        Returns:
            加载的适配器数量
        """
        adapters = self._loader.discover_and_load()
        count = 0
        for adapter in adapters:
            if self.register(adapter):
                count += 1
        return count
    
    def load_plugin(self, module_path: str) -> int:
        """
        加载指定模块的适配器
        
        Args:
            module_path: 模块路径
        
        Returns:
            加载的适配器数量
        """
        adapters = self._loader.load_from_module(module_path)
        count = 0
        for adapter in adapters:
            if self.register(adapter):
                count += 1
        return count
    
    async def health_check_all(self) -> Dict[str, bool]:
        """
        对所有适配器进行健康检查
        
        Returns:
            {source_name: is_healthy}
        """
        results = {}
        for name, adapter in self._adapters.items():
            try:
                results[name] = await adapter.health_check()
            except Exception as e:
                logger.warning(f"健康检查失败: {name} - {e}")
                results[name] = False
        return results
    
    def __len__(self) -> int:
        return len(self._adapters)
    
    def __contains__(self, source_name: str) -> bool:
        return source_name in self._adapters
    
    def __iter__(self):
        return iter(self._adapters.items())


# ============== 全局单例 ==============

_default_manager: Optional[AdapterManager] = None


def get_adapter_manager() -> AdapterManager:
    """
    获取全局适配器管理器实例（单例）
    
    Returns:
        AdapterManager 实例
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = AdapterManager()
        _default_manager.load_plugins()
    return _default_manager


def reset_adapter_manager():
    """重置全局适配器管理器（用于测试）"""
    global _default_manager
    if _default_manager:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                for adapter in _default_manager._adapters.values():
                    asyncio.create_task(adapter.close())
        except RuntimeError:
            pass
    _default_manager = None


# ============== 便捷函数 ==============

def register_adapter(adapter: Adapter) -> bool:
    """注册适配器到全局管理器"""
    return get_adapter_manager().register(adapter)


def get_adapter(source_name: str) -> Optional[Adapter]:
    """从全局管理器获取适配器"""
    return get_adapter_manager().get(source_name)


def find_adapters_by_type(data_type: str) -> List[str]:
    """查找支持指定数据类型的适配器"""
    return get_adapter_manager().find_by_type(data_type)


if __name__ == '__main__':
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    manager = AdapterManager()
    
    # 加载插件
    count = manager.load_plugins()
    print(f"加载了 {count} 个适配器")
    
    # 列出所有适配器
    print(f"\n已注册适配器: {list(manager.list_adapters().keys())}")
    
    # 查询支持 quote 的适配器
    quote_adapters = manager.find_by_type('quote')
    print(f"支持 quote 的适配器: {quote_adapters}")
    
    # 统计
    stats = manager.get_stats()
    print(f"\n统计: {stats}")
