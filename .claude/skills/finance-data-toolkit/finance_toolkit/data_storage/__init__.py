# -*- coding: utf-8 -*-
"""
数据存储模块
支持：JSON文件、SQLite数据库、内存存储
"""

from .storage import (
    StorageBackend,
    JSONStorage,
    SQLiteStorage,
    MemoryStorage,
    DataStorage,
    save_data,
    load_data,
    default_storage,
)

__all__ = [
    'StorageBackend',
    'JSONStorage',
    'SQLiteStorage',
    'MemoryStorage',
    'DataStorage',
    'save_data',
    'load_data',
    'default_storage',
]
