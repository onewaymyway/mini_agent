import sys
sys.path.insert(0, '.')
import asyncio
import httpx
import time

async def test():
    # 直接测试东方财富K线API
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
    params = {
        'secid': '1.600000',
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': '101',
        'fqt': '1',
        'beg': '20240101',
        'end': '20261231',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        '_': str(int(time.time() * 1000)),
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://quote.eastmoney.com/',
    }
    
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        resp = await client.get(url, params=params)
        print(f'Status: {resp.status_code}')
        data = resp.json()
        print(f'rc: {data.get("rc")}')
        print(f'data keys: {list(data.get("data", {}).keys()) if data.get("data") else "None"}')
        klines = data.get('data', {}).get('klines', [])
        print(f'klines count: {len(klines)}')
        if klines:
            print(f'first: {klines[0]}')
            print(f'last: {klines[-1]}')

asyncio.run(test())