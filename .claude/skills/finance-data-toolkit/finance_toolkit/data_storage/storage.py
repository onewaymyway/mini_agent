# -*- coding: utf-8 -*-
"""
数据存储模块
支持：JSON文件、SQLite数据库、内存存储
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class StorageBackend:
    """存储后端基类"""
    
    def save(self, data_type: str, symbol: str, data: Any) -> bool:
        raise NotImplementedError
    
    def load(self, data_type: str, symbol: str) -> Optional[Any]:
        raise NotImplementedError
    
    def delete(self, data_type: str, symbol: str) -> bool:
        raise NotImplementedError


class JSONStorage(StorageBackend):
    """JSON文件存储"""
    
    def __init__(self, base_dir: str = './data'):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_path(self, data_type: str, symbol: str) -> Path:
        return self.base_dir / data_type / f"{symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    
    def save(self, data_type: str, symbol: str, data: Any) -> bool:
        try:
            path = self._get_path(data_type, symbol)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            if isinstance(data, list):
                content = data
            else:
                content = [data] if data else []
            
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(content, f, ensure_ascii=False, indent=2, default=str)
            
            logger.info(f"数据已保存: {path}")
            return True
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
            return False
    
    def load(self, data_type: str, symbol: str) -> Optional[Any]:
        try:
            pattern = self.base_dir / data_type / f"{symbol}_*.json"
            files = list(pattern.parent.glob(pattern.name)) if pattern.parent.exists() else []
            
            if not files:
                return None
            
            latest_file = max(files, key=lambda x: x.stat().st_mtime)
            
            with open(latest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载数据失败: {e}")
            return None
    
    def delete(self, data_type: str, symbol: str) -> bool:
        try:
            pattern = self.base_dir / data_type / f"{symbol}_*.json"
            files = list(pattern.parent.glob(pattern.name)) if pattern.parent.exists() else []
            
            for f in files:
                f.unlink()
            
            return len(files) > 0
        except Exception as e:
            logger.error(f"删除数据失败: {e}")
            return False


class SQLiteStorage(StorageBackend):
    """SQLite数据库存储"""
    
    def __init__(self, db_path: str = './data/finance.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS finance_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source TEXT,
                    timestamp TEXT,
                    data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_data_type_symbol 
                ON finance_data(data_type, symbol)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON finance_data(timestamp)
            ''')
            
            conn.commit()
    
    def save(self, data_type: str, symbol: str, data: Any) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO finance_data (data_type, symbol, source, timestamp, data)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    data_type,
                    symbol,
                    data.get('source', 'unknown') if isinstance(data, dict) else 'unknown',
                    datetime.utcnow().isoformat(),
                    json.dumps(data, ensure_ascii=False, default=str)
                ))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
            return False
    
    def load(self, data_type: str, symbol: str, limit: int = 100) -> Optional[List[Dict]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT data FROM finance_data 
                    WHERE data_type = ? AND symbol = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (data_type, symbol, limit))
                
                rows = cursor.fetchall()
                
                if not rows:
                    return None
                
                return [json.loads(row[0]) for row in rows]
        except Exception as e:
            logger.error(f"加载数据失败: {e}")
            return None
    
    def delete(self, data_type: str, symbol: str) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    DELETE FROM finance_data 
                    WHERE data_type = ? AND symbol = ?
                ''', (data_type, symbol))
                
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除数据失败: {e}")
            return False
    
    def get_all_data_types(self) -> List[str]:
        """获取所有数据类型"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT data_type FROM finance_data')
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取数据类型失败: {e}")
            return []


class MemoryStorage(StorageBackend):
    """内存存储（用于测试和临时存储）"""
    
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
    
    def save(self, data_type: str, symbol: str, data: Any) -> bool:
        key = f"{data_type}:{symbol}"
        self._data[key] = {
            'data': data,
            'timestamp': datetime.utcnow().isoformat(),
            'source': data.get('source', 'unknown') if isinstance(data, dict) else 'unknown'
        }
        return True
    
    def load(self, data_type: str, symbol: str) -> Optional[Any]:
        key = f"{data_type}:{symbol}"
        item = self._data.get(key)
        return item['data'] if item else None
    
    def delete(self, data_type: str, symbol: str) -> bool:
        key = f"{data_type}:{symbol}"
        if key in self._data:
            del self._data[key]
            return True
        return False
    
    def clear(self):
        """清空所有数据"""
        self._data.clear()
    
    def get_all(self) -> Dict[str, Any]:
        """获取所有数据"""
        return {k: v['data'] for k, v in self._data.items()}


# ============== 便捷函数 ==============

def save_data(data_type: str, symbol: str, data: Any, backend: str = 'json') -> bool:
    """保存数据"""
    if backend == 'json':
        storage = JSONStorage()
    elif backend == 'sqlite':
        storage = SQLiteStorage()
    elif backend == 'memory':
        storage = MemoryStorage()
    else:
        logger.warning(f"未知的存储后端: {backend}")
        return False
    
    return storage.save(data_type, symbol, data)


def load_data(data_type: str, symbol: str, backend: str = 'json') -> Optional[Any]:
    """加载数据"""
    if backend == 'json':
        storage = JSONStorage()
    elif backend == 'sqlite':
        storage = SQLiteStorage()
    elif backend == 'memory':
        storage = MemoryStorage()
    else:
        logger.warning(f"未知的存储后端: {backend}")
        return None
    
    return storage.load(data_type, symbol)


# ============== 便捷类 ==============

class DataStorage:
    """数据存储管理器"""
    
    def __init__(self, backend: str = 'json', **kwargs):
        if backend == 'json':
            self.storage = JSONStorage(**kwargs)
        elif backend == 'sqlite':
            self.storage = SQLiteStorage(**kwargs)
        elif backend == 'memory':
            self.storage = MemoryStorage()
        else:
            raise ValueError(f"未知的存储后端: {backend}")
    
    def save(self, data_type: str, symbol: str, data: Any) -> bool:
        return self.storage.save(data_type, symbol, data)
    
    def load(self, data_type: str, symbol: str) -> Optional[Any]:
        return self.storage.load(data_type, symbol)
    
    def delete(self, data_type: str, symbol: str) -> bool:
        return self.storage.delete(data_type, symbol)
    
    def get_all_data_types(self) -> List[str]:
        if hasattr(self.storage, 'get_all_data_types'):
            return self.storage.get_all_data_types()
        return []


# 默认存储实例
default_storage = DataStorage(backend='json')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    storage = DataStorage(backend='json')
    
    test_data = {
        'source': 'test',
        'data_type': 'quote',
        'symbol': '600000',
        'payload': {'price': 10.5, 'change_pct': 1.2}
    }
    
    storage.save('quote', '600000', test_data)
    result = storage.load('quote', '600000')
    print(f"加载结果: {result}")
