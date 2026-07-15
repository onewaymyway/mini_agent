"""
去重模块
支持精确去重、业务键去重、SimHash 近似去重
"""

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Set
from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """存储后端抽象基类"""
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        pass
    
    @abstractmethod
    async def setex(self, key: str, ttl: int, value: str) -> bool:
        pass
    
    @abstractmethod
    async def get(self, key: str) -> Optional[str]:
        pass
    
    @abstractmethod
    async def set(self, key: str, value: str) -> bool:
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass
    
    @abstractmethod
    async def keys(self, pattern: str) -> List[str]:
        pass


class MemoryStorage(StorageBackend):
    """内存存储后端 (用于测试/单机)"""
    
    def __init__(self):
        self._data: Dict[str, str] = {}
        self._ttl: Dict[str, float] = {}
    
    def _clean_expired(self):
        now = time.time()
        expired = [k for k, v in self._ttl.items() if v < now]
        for k in expired:
            self._data.pop(k, None)
            self._ttl.pop(k, None)
    
    async def exists(self, key: str) -> bool:
        self._clean_expired()
        return key in self._data
    
    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self._data[key] = value
        self._ttl[key] = time.time() + ttl
        return True
    
    async def get(self, key: str) -> Optional[str]:
        self._clean_expired()
        return self._data.get(key)
    
    async def set(self, key: str, value: str) -> bool:
        self._data[key] = value
        return True
    
    async def delete(self, key: str) -> bool:
        self._data.pop(key, None)
        self._ttl.pop(key, None)
        return True
    
    async def keys(self, pattern: str) -> List[str]:
        self._clean_expired()
        import fnmatch
        return [k for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]


class Deduplicator:
    """多级去重：精确去重 + 业务键去重 + 近似去重"""
    
    def __init__(self, storage: StorageBackend = None):
        self.storage = storage or MemoryStorage()
    
    async def dedup_exact(self, data: Dict, ttl_days: int = 7) -> bool:
        """精确去重：基于内容哈希"""
        content = json.dumps(data.get('payload', {}), sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
        key = f"dedup:exact:{fingerprint}"
        
        if await self.storage.exists(key):
            return True  # 是重复
        await self.storage.setex(key, 86400 * ttl_days, '1')
        return False
    
    async def dedup_business_key(self, data: Dict, ttl_days: int = 30) -> bool:
        """业务键去重：按数据类型定义唯一键"""
        data_type = data.get('data_type')
        payload = data.get('payload', {})
        
        key_map = {
            'quote': lambda p: f"{p.get('symbol')}:{p.get('timestamp')}",
            'kline': lambda p: f"{p.get('symbol')}:{p.get('date')}:{p.get('period', 'daily')}",
            'financial': lambda p: f"{p.get('symbol')}:{p.get('report_date')}:{p.get('report_type')}",
            'news': lambda p: f"{data.get('source')}:{p.get('news_id')}",
            'guba': lambda p: f"guba:{p.get('post_id')}",
            'report': lambda p: f"{data.get('source')}:{p.get('report_id')}",
        }
        
        key_func = key_map.get(data_type)
        if not key_func:
            return False
        
        try:
            key = key_func(payload)
        except Exception:
            return False
        
        if not key or ':' not in key:
            return False
        
        full_key = f"dedup:biz:{key}"
        if await self.storage.exists(full_key):
            return True
        await self.storage.setex(full_key, 86400 * ttl_days, '1')
        return False
    
    async def dedup_simhash(self, data: Dict, threshold: int = 3, ttl_days: int = 30) -> bool:
        """近似去重：SimHash 文本指纹 (针对新闻/股吧正文)"""
        text = data.get('payload', {}).get('content', '')
        if len(text) < 100:
            return False
        
        simhash = self._compute_simhash(text)
        key = f"dedup:simhash:{simhash}"
        
        if await self.storage.exists(key):
            return True
        await self.storage.setex(key, 86400 * ttl_days, '1')
        return False
    
    def _compute_simhash(self, text: str) -> str:
        """简化 SimHash 实现"""
        try:
            import jieba
            words = jieba.lcut(text)
        except ImportError:
            # 降级：按字符分词
            words = list(text)
        
        v = [0] * 64
        for w in words:
            h = hashlib.md5(w.encode('utf-8')).hexdigest()
            for i, bit in enumerate(h):
                v[i % 64] += 1 if bit in '89abcdef' else -1
        return ''.join('1' if x > 0 else '0' for x in v)
    
    async def is_duplicate(self, data: Dict, 
                          use_exact: bool = True,
                          use_business: bool = True,
                          use_simhash: bool = False) -> bool:
        """综合去重判断"""
        if use_exact and await self.dedup_exact(data):
            return True
        if use_business and await self.dedup_business_key(data):
            return True
        if use_simhash and await self.dedup_simhash(data):
            return True
        return False
    
    async def clear_cache(self, pattern: str = "dedup:*"):
        """清理去重缓存"""
        keys = await self.storage.keys(pattern)
        for key in keys:
            await self.storage.delete(key)


class IncrementalDeduplicator:
    """增量去重：结合版本号/时间戳/内容哈希"""
    
    def __init__(self, storage: StorageBackend = None):
        self.storage = storage or MemoryStorage()
    
    async def get_last_state(self, data_type: str, symbol: str) -> Optional[Dict]:
        """获取上次处理状态"""
        key = f"incr:{data_type}:{symbol}"
        val = await self.storage.get(key)
        if val:
            return json.loads(val)
        return None
    
    async def update_state(self, data_type: str, symbol: str, state: Dict):
        """更新处理状态"""
        key = f"incr:{data_type}:{symbol}"
        await self.storage.set(key, json.dumps(state, ensure_ascii=False))
    
    def should_process(self, data: Dict, last_state: Optional[Dict]) -> bool:
        """判断是否需要处理"""
        if not last_state:
            return True  # 首次处理
        
        data_type = data.get('data_type')
        payload = data.get('payload', {})
        
        # 版本号比较
        if 'version' in payload and 'version' in last_state:
            return payload['version'] > last_state['version']
        
        # 时间戳比较
        if 'timestamp' in payload and 'timestamp' in last_state:
            return payload['timestamp'] > last_state['timestamp']
        
        # 内容哈希比较
        content_hash = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return content_hash != last_state.get('content_hash')
    
    async def process_if_new(self, data: Dict) -> bool:
        """如果是新数据则处理并更新状态"""
        data_type = data.get('data_type')
        symbol = data.get('payload', {}).get('symbol')
        
        if not data_type or not symbol:
            return True  # 无法判断，默认处理
        
        last_state = await self.get_last_state(data_type, symbol)
        if self.should_process(data, last_state):
            # 更新状态
            payload = data.get('payload', {})
            new_state = {
                'timestamp': payload.get('timestamp'),
                'version': payload.get('version'),
                'content_hash': hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                'processed_at': time.time(),
            }
            await self.update_state(data_type, symbol, new_state)
            return True
        return False