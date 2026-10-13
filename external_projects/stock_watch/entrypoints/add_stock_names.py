#!/usr/bin/env python
"""为 kline.db 添加股票名称字段。"""

import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "kline.db"
ALGO_POOL_PATH = Path(__file__).resolve().parent.parent / "data" / "algo_pool.json"


def add_name_column():
    """向 kline 表添加 name 列（如果不存在）。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute('PRAGMA table_info(kline)')
    columns = [col[1] for col in cursor.fetchall()]
    
    if "name" not in columns:
        conn.execute("ALTER TABLE kline ADD COLUMN name TEXT DEFAULT ''")
        conn.commit()
        print("已添加 name 列")
    else:
        print("name 列已存在")
    
    conn.close()


def load_name_from_pool() -> dict:
    """从候选池加载名称映射。"""
    name_map = {}
    if ALGO_POOL_PATH.exists():
        with open(ALGO_POOL_PATH, "r", encoding="utf-8") as f:
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


def update_names():
    """更新数据库中的股票名称。"""
    name_map = load_name_from_pool()
    print(f"从候选池加载 {len(name_map)} 只股票名称")
    
    conn = sqlite3.connect(DB_PATH)
    updated = 0
    
    for symbol, name in name_map.items():
        conn.execute("UPDATE kline SET name = ? WHERE symbol = ?", (name, symbol))
        updated += 1
    
    conn.commit()
    print(f"已更新 {updated} 只股票的名称")
    conn.close()


if __name__ == "__main__":
    add_name_column()
    update_names()
    print("完成")
