"""
unified_interface.py - 统一搜索器接口

修复:
- TEST-001: SearchResults 接口不统一
- TEST-002: 浏览器启动方式不一致
- TEST-003: 异步/同步混用

所有搜索器统一使用 SearchResults 容器和 BrowserSessionManager
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, TypeVar, Callable

logger = logging.getLogger(__name__)


# ============================================================================
# 统一搜索结果容器
# ============================================================================

T = TypeVar('T')


@dataclass
class SearchResults:
    """
    统一的搜索结果容器
    
    所有搜索器的 search() 方法必须返回此类型的实例。
    """
    success: bool
    query: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    total_count: int = 0
    elapsed: float = 0.0
    
    @property
    def items(self) -> List[Dict]:
        """别名：results"""
        return self.results
    
    @property
    def empty(self) -> bool:
        """是否为空结果"""
        return len(self.results) == 0
    
    @property
    def first(self) -> Optional[Dict]:
        """第一个结果"""
        return self.results[0] if self.results else None
    
    def add_result(self, item: Dict[str, Any]) -> None:
        """添加单个结果"""
        self.results.append(item)
        self.total_count = len(self.results)
    
    def add_results(self, items: List[Dict[str, Any]]) -> None:
        """批量添加结果"""
        self.results.extend(items)
        self.total_count = len(self.results)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "success": self.success,
            "query": self.query,
            "total_count": self.total_count,
            "elapsed": round(self.elapsed, 2),
            "error": self.error,
            "results": self.results[:20],  # 限制返回数量
        }
    
    def save_json(self, path: str) -> None:
        """保存为 JSON 文件"""
        import json
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
    
    def save_csv(self, path: str) -> None:
        """保存为 CSV 文件"""
        import csv
        if not self.results:
            return
        fieldnames = list(self.results[0].keys())
        with open(path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)
    
    def __bool__(self) -> bool:
        return self.success and len(self.results) > 0
    
    def __len__(self) -> int:
        return len(self.results)
    
    def __repr__(self) -> str:
        return f"SearchResults(success={self.success}, count={len(self.results)}, query={self.query!r})"


# ============================================================================
# 统一浏览器会话管理器
# ============================================================================

class _BrowserSessionManager:
    """
    单例浏览器会话管理器
    
    所有搜索器通过 get_session() 获取会话，避免各自启动浏览器。
    """
    _instance = None
    _sessions: Dict[str, Any] = {}
    _lock = None  # 延迟初始化
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_session(self, session_name: str, **kwargs) -> Any:
        """
        获取或创建浏览器会话
        
        Args:
            session_name: 会话名称（对应 --name 参数）
            **kwargs: 额外参数（port, stealth 等）
        
        Returns:
            CDP 会话对象
        """
        if session_name not in self._sessions:
            self._sessions[session_name] = self._create_session(session_name, **kwargs)
        return self._sessions[session_name]
    
    def _create_session(self, session_name: str, **kwargs) -> Any:
        """创建新会话（由子类或外部注入实现）"""
        # 默认实现：尝试导入并启动浏览器
        try:
            from ..core.browser_launch import ensure_browser
            return ensure_browser(session_name=session_name, **kwargs)
        except ImportError:
            logger.warning(f"无法导入 browser_launch，会话创建失败")
            return None
    
    def close_session(self, session_name: str) -> None:
        """关闭会话"""
        if session_name in self._sessions:
            try:
                session = self._sessions[session_name]
                if hasattr(session, 'close'):
                    session.close()
            except Exception as e:
                logger.warning(f"关闭会话 {session_name} 失败: {e}")
            finally:
                del self._sessions[session_name]
    
    def close_all(self) -> None:
        """关闭所有会话"""
        for name in list(self._sessions.keys()):
            self.close_session(name)
    
    @classmethod
    def getInstance(cls) -> '_BrowserSessionManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# 全局单例
_browser_manager = _BrowserSessionManager()


def get_session(session_name: str, **kwargs) -> Any:
    """获取浏览器会话（便捷函数）"""
    return _browser_manager.get_session(session_name, **kwargs)


def close_session(session_name: str) -> None:
    """关闭浏览器会话"""
    _browser_manager.close_session(session_name)


def close_all_sessions() -> None:
    """关闭所有浏览器会话"""
    _browser_manager.close_all()


# ============================================================================
# 统一搜索器基类
# ============================================================================

class BaseSearcher:
    """
    统一搜索器基类
    
    所有搜索器继承此类，确保接口一致。
    """
    
    # 搜索器名称
    name: str = "base"
    # 默认会话名
    default_session_name: str = "default"
    # 默认超时
    default_timeout: float = 30.0
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.session_name = self.config.get('session_name', self.default_session_name)
        self.timeout = self.config.get('timeout', self.default_timeout)
        self._session = None
    
    def _get_session(self):
        """获取浏览器会话"""
        if self._session is None:
            self._session = get_session(self.session_name)
        return self._session
    
    def search(
        self,
        query: str,
        max_results: int = 20,
        page: int = 1,
        **kwargs,
    ) -> SearchResults:
        """
        搜索接口（同步版本）
        
        所有搜索器必须实现此方法。
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            page: 页码
            **kwargs: 额外参数
        
        Returns:
            SearchResults
        """
        raise NotImplementedError(f"{self.name}.search() 未实现")
    
    async def search_async(
        self,
        query: str,
        max_results: int = 20,
        page: int = 1,
        **kwargs,
    ) -> SearchResults:
        """
        搜索接口（异步版本）
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            page: 页码
            **kwargs: 额外参数
        
        Returns:
            SearchResults
        """
        raise NotImplementedError(f"{self.name}.search_async() 未实现")
    
    def health_check(self) -> bool:
        """健康检查"""
        return self._get_session() is not None
    
    def close(self) -> None:
        """释放资源"""
        if self._session:
            try:
                if hasattr(self._session, 'close'):
                    self._session.close()
            except Exception:
                pass
            finally:
                self._session = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ============================================================================
# 统一测试入口
# ============================================================================

def run_test(searcher_class, query: str, max_results: int = 20) -> dict:
    """
    统一的测试运行入口
    
    自动处理 async/sync 转换，返回统一格式。
    
    Args:
        searcher_class: 搜索器类
        query: 测试关键词
        max_results: 最大结果数
    
    Returns:
        dict: {success, name, duration, results_count, error, sample_results}
    """
    start_time = time.time()
    name = searcher_class.name if hasattr(searcher_class, 'name') else searcher_class.__name__
    
    try:
        searcher = searcher_class()
        
        # 尝试异步调用
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在运行中的事件中，使用 run_coroutine_threadsafe
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, searcher.search_async(query, max_results=max_results))
                    results = future.result(timeout=60)
            else:
                results = loop.run_until_complete(searcher.search_async(query, max_results=max_results))
        except RuntimeError:
            # 没有事件循环，直接同步调用
            results = searcher.search(query, max_results=max_results)
        
        duration = time.time() - start_time
        
        return {
            "success": bool(results),
            "name": name,
            "duration": round(duration, 3),
            "results_count": len(results.results) if hasattr(results, 'results') else 0,
            "error": None,
            "sample_results": results.results[:5] if hasattr(results, 'results') and results.results else [],
        }
        
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"测试 {name} 失败: {e}")
        return {
            "success": False,
            "name": name,
            "duration": round(duration, 3),
            "results_count": 0,
            "error": str(e),
            "sample_results": [],
        }
    finally:
        try:
            searcher.close()
        except Exception:
            pass


def batch_test(searcher_classes: List[type], query: str, max_results: int = 20) -> List[dict]:
    """
    批量测试多个搜索器
    
    Args:
        searcher_classes: 搜索器类列表
        query: 测试关键词
        max_results: 最大结果数
    
    Returns:
        List[dict]: 每个搜索器的测试结果
    """
    return [run_test(cls, query, max_results) for cls in searcher_classes]


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    'SearchResults',
    'BaseSearcher',
    'get_session',
    'close_session',
    'close_all_sessions',
    'run_test',
    'batch_test',
]
