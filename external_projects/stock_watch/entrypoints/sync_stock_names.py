#!/usr/bin/env python
"""同步股票名称到数据库。"""

import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "kline.db"
ALGO_POOL_PATH = Path(__file__).resolve().parent.parent / "data" / "algo_pool.json"
MANUAL_POOL_PATH = Path(__file__).resolve().parent.parent / "data" / "manual_pool.json"


def ensure_name_column(conn: sqlite3.Connection) -> None:
    """确保 kline 表有 name 列。"""
    cursor = conn.execute('PRAGMA table_info(kline)')
    columns = [col[1] for col in cursor.fetchall()]
    
    if "name" not in columns:
        conn.execute("ALTER TABLE kline ADD COLUMN name TEXT DEFAULT ''")
        conn.commit()
        print("已添加 name 列到 kline 表")


def load_names_from_pool(pool_path: Path) -> dict:
    """从候选池 JSON 加载名称映射。"""
    name_map = {}
    if pool_path.exists():
        with open(pool_path, "r", encoding="utf-8") as f:
            pool = json.load(f)
        for key, entry in pool.items():
            if isinstance(entry, dict):
                code = re.sub(r"^(SH|SZ|BJ)", "", key)
                name = entry.get("name", "")
            else:
                code = re.sub(r"^(SH|SZ|BJ)", "", str(key))
                name = getattr(entry, "name", "")
            if code and name:
                name_map[code] = name
    return name_map


def update_names_in_db():
    """从候选池加载名称并更新到数据库。"""
    # 加载所有名称
    name_map = {}
    name_map.update(load_names_from_pool(ALGO_POOL_PATH))
    name_map.update(load_names_from_pool(MANUAL_POOL_PATH))
    
    print(f"从候选池加载 {len(name_map)} 只股票名称")
    
    # 连接数据库
    conn = sqlite3.connect(str(DB_PATH))
    ensure_name_column(conn)
    
    # 更新名称
    updated = 0
    for symbol, name in name_map.items():
        conn.execute("UPDATE kline SET name = ? WHERE symbol = ?", (name, symbol))
        updated += conn_changes(conn)
    
    conn.commit()
    print(f"已更新 {updated} 只股票的名称")
    conn.close()


def conn_changes(conn: sqlite3.Connection) -> int:
    """获取最近一次执行的行数。"""
    return conn.total_changes


if __name__ == "__main__":
    update_names_in_db()
    print("完成")
