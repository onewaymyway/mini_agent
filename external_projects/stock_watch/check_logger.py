#!/usr/bin/env python
# -*- coding: utf-8 -*-
with open('stock_watch/daily_kline_db.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if '_logger' in l or 'logger.' in l:
        print(i+1, repr(l.strip()))
