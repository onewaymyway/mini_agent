#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sqlite3
import sys
import os
sys.path.insert(0, 'E:/codes/mini_claude_code')
os.chdir('E:/codes/mini_claude_code/external_projects/stock_watch')

conn = sqlite3.connect('data/kline.db')
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='download_status'")
if not cur.fetchone():
    print("download_status table does not exist")
else:
    cur.execute("SELECT COUNT(DISTINCT symbol) FROM download_status")
    print('Status symbols:', cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM download_status WHERE status='done'")
    done = cur.fetchone()[0]
    print('Done:', done)
    cur.execute("SELECT COUNT(*) FROM download_status WHERE status='todo'")
    todo = cur.fetchone()[0]
    print('Todo:', todo)
    cur.execute("SELECT COUNT(*) FROM download_status WHERE status IN ('failed','retrying')")
    failed = cur.fetchone()[0]
    print('Failed/Retry:', failed)

cur.execute("SELECT COUNT(DISTINCT symbol) FROM kline")
print('DB symbols:', cur.fetchone()[0])
cur.execute("SELECT MAX(date) FROM kline")
print('Latest date:', cur.fetchone()[0])
conn.close()
print("Done")
