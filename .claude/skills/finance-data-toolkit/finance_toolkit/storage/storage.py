# -*- coding: utf-8 -*-
"""
SQLite 数据库持久化层

提供金融数据的本地存储功能。
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class FinanceDatabase:
    """金融数据存储数据库"""
    
    def __init__(self, db_path: str = "data/finance_data.db"):
        """
        初始化数据库连接
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        
        # 初始化数据库
        self._init_db()
    
    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（单例模式）"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            logger.debug(f"数据库连接已创建: {self.db_path}")
        return self._conn
    
    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 股票实时行情表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT,
                price REAL,
                change_pct REAL,
                volume REAL,
                amount REAL,
                market_cap REAL,
                pe_ratio REAL,
                pb_ratio REAL,
                fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_hash TEXT,
                UNIQUE(symbol, fetch_time)
            )
        ''')
        
        # 行业板块表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sector_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector_code TEXT NOT NULL,
                sector_name TEXT,
                index_price REAL,
                change_pct REAL,
                total_market_cap REAL,
                leading_stock TEXT,
                fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 融资融券表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS margin_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                name TEXT,
                exchange TEXT,
                margin_balance REAL,
                margin_buy REAL,
                short_balance REAL,
                short_sell REAL,
                total_balance REAL,
                fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 资金流向表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS capital_flow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                name TEXT,
                in_flow REAL,
                out_flow REAL,
                net_flow REAL,
                fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 板块资金流向表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sector_capital_flow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector_code TEXT,
                sector_name TEXT,
                in_flow REAL,
                out_flow REAL,
                net_flow REAL,
                fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 抓取日志表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fetch_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_type TEXT NOT NULL,
                status TEXT NOT NULL,
                record_count INTEGER,
                error_message TEXT,
                fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_quotes_symbol ON stock_quotes(symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_quotes_time ON stock_quotes(fetch_time)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sector_quotes_time ON sector_quotes(fetch_time)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_margin_data_time ON margin_data(fetch_time)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_capital_flow_time ON capital_flow(fetch_time)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_fetch_log_time ON fetch_log(fetch_time)')
        
        conn.commit()
        logger.info(f"数据库初始化完成: {self.db_path}")
    
    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("数据库连接已关闭")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # ========== 股票行情操作 ==========
    
    def save_stock_quote(self, data: Dict[str, Any]) -> int:
        """
        保存股票实时行情
        
        Args:
            data: 股票数据字典
        
        Returns:
            插入的行ID
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 计算数据哈希用于去重
        data_str = json.dumps(data, sort_keys=True, default=str)
        data_hash = hash(data_str)
        
        cursor.execute('''
            INSERT OR REPLACE INTO stock_quotes
            (symbol, name, price, change_pct, volume, amount, 
             market_cap, pe_ratio, pb_ratio, fetch_time, data_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('symbol'),
            data.get('name'),
            data.get('price'),
            data.get('change_pct'),
            data.get('volume'),
            data.get('amount'),
            data.get('market_cap'),
            data.get('pe_ratio'),
            data.get('pb_ratio'),
            datetime.now(),
            data_hash
        ))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_stock_quotes(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        查询股票行情
        
        Args:
            symbol: 股票代码（可选）
            limit: 返回条数限制
        
        Returns:
            股票数据列表
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute('''
                SELECT * FROM stock_quotes 
                WHERE symbol = ? 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (symbol, limit))
        else:
            cursor.execute('''
                SELECT * FROM stock_quotes 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (limit,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    # ========== 行业板块操作 ==========
    
    def save_sector_quotes(self, data: Dict[str, Any]) -> int:
        """保存行业板块数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO sector_quotes
            (sector_code, sector_name, index_price, change_pct, 
             total_market_cap, leading_stock, fetch_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('sector_code'),
            data.get('sector_name'),
            data.get('index_price'),
            data.get('change_pct'),
            data.get('total_market_cap'),
            data.get('leading_stock'),
            datetime.now()
        ))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_sector_quotes(self, limit: int = 100) -> List[Dict]:
        """查询行业板块数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM sector_quotes 
            ORDER BY fetch_time DESC 
            LIMIT ?
        ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    # ========== 融资融券操作 ==========
    
    def save_margin_data(self, data: Dict[str, Any]) -> int:
        """保存融资融券数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO margin_data
            (symbol, name, exchange, margin_balance, margin_buy,
             short_balance, short_sell, total_balance, fetch_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('symbol'),
            data.get('name'),
            data.get('exchange'),
            data.get('margin_balance'),
            data.get('margin_buy'),
            data.get('short_balance'),
            data.get('short_sell'),
            data.get('total_balance'),
            datetime.now()
        ))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_margin_data(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """查询融资融券数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute('''
                SELECT * FROM margin_data 
                WHERE symbol = ? 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (symbol, limit))
        else:
            cursor.execute('''
                SELECT * FROM margin_data 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    # ========== 资金流向操作 ==========
    
    def save_capital_flow(self, data: Dict[str, Any]) -> int:
        """保存资金流向数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO capital_flow
            (symbol, name, in_flow, out_flow, net_flow, fetch_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data.get('symbol'),
            data.get('name'),
            data.get('in_flow'),
            data.get('out_flow'),
            data.get('net_flow'),
            datetime.now()
        ))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_capital_flow(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """查询资金流向数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if symbol:
            cursor.execute('''
                SELECT * FROM capital_flow 
                WHERE symbol = ? 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (symbol, limit))
        else:
            cursor.execute('''
                SELECT * FROM capital_flow 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    # ========== 板块资金流向操作 ==========
    
    def save_sector_capital_flow(self, data: Dict[str, Any]) -> int:
        """保存板块资金流向数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO sector_capital_flow
            (sector_code, sector_name, in_flow, out_flow, net_flow, fetch_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data.get('sector_code'),
            data.get('sector_name'),
            data.get('in_flow'),
            data.get('out_flow'),
            data.get('net_flow'),
            datetime.now()
        ))
        
        conn.commit()
        return cursor.lastrowid
    
    def get_sector_capital_flow(self, limit: int = 100) -> List[Dict]:
        """查询板块资金流向数据"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM sector_capital_flow 
            ORDER BY fetch_time DESC 
            LIMIT ?
        ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    # ========== 日志操作 ==========
    
    def log_fetch(self, data_type: str, status: str, 
                  record_count: int = None, error_message: str = None):
        """记录抓取日志"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO fetch_log
            (data_type, status, record_count, error_message, fetch_time)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            data_type,
            status,
            record_count,
            error_message,
            datetime.now()
        ))
        
        conn.commit()
    
    def get_fetch_logs(self, data_type: str = None, limit: int = 50) -> List[Dict]:
        """查询抓取日志"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if data_type:
            cursor.execute('''
                SELECT * FROM fetch_log 
                WHERE data_type = ? 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (data_type, limit))
        else:
            cursor.execute('''
                SELECT * FROM fetch_log 
                ORDER BY fetch_time DESC 
                LIMIT ?
            ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    # ========== 数据导出 ==========
    
    def export_to_json(self, table: str, output_path: str, 
                       filter_expr: str = None, filter_params: tuple = None):
        """导出数据为JSON文件"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if filter_expr:
            cursor.execute(f"SELECT * FROM {table} WHERE {filter_expr}", filter_params)
        else:
            cursor.execute(f"SELECT * FROM {table}")
        
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"数据已导出到 {output_path}: {len(data)} 条记录")
        return data
    
    def export_to_csv(self, table: str, output_path: str,
                      filter_expr: str = None, filter_params: tuple = None):
        """导出数据为CSV文件"""
        import csv
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if filter_expr:
            cursor.execute(f"SELECT * FROM {table} WHERE {filter_expr}", filter_params)
        else:
            cursor.execute(f"SELECT * FROM {table}")
        
        rows = cursor.fetchall()
        if not rows:
            logger.warning(f"表 {table} 为空")
            return []
        
        # 获取列名
        columns = [desc[0] for desc in cursor.description]
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
        
        logger.info(f"CSV已导出到 {output_path}: {len(rows)} 条记录")
        return [dict(row) for row in rows]
    
    # ========== 统计信息 ==========
    
    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        stats = {}
        
        # 各表记录数
        tables = ['stock_quotes', 'sector_quotes', 'margin_data', 
                  'capital_flow', 'sector_capital_flow', 'fetch_log']
        
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
            stats[table] = cursor.fetchone()['count']
        
        # 最新更新时间
        cursor.execute('''
            SELECT MAX(fetch_time) as latest 
            FROM stock_quotes
        ''')
        stats['latest_stock_quote'] = cursor.fetchone()['latest']
        
        cursor.execute('''
            SELECT MAX(fetch_time) as latest 
            FROM sector_quotes
        ''')
        stats['latest_sector_quote'] = cursor.fetchone()['latest']
        
        return stats
    
    def __repr__(self):
        stats = self.get_stats()
        return f"FinanceDatabase(path='{self.db_path}', stats={stats})"


def create_database(db_path: str = "data/finance_data.db") -> FinanceDatabase:
    """
    创建数据库实例的便捷函数
    
    Args:
        db_path: 数据库文件路径
    
    Returns:
        FinanceDatabase 实例
    """
    return FinanceDatabase(db_path)


# 全局数据库实例
_default_db: Optional[FinanceDatabase] = None


def get_default_database() -> FinanceDatabase:
    """获取全局默认数据库实例"""
    global _default_db
    if _default_db is None:
        _default_db = FinanceDatabase()
    return _default_db


def close_default_database():
    """关闭全局默认数据库"""
    global _default_db
    if _default_db:
        _default_db.close()
        _default_db = None
