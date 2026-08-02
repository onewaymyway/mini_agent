import sys
sys.path.insert(0, '.')
import asyncio
import httpx
import time

async def test():
    # 直接测试东方财富实时行情API
    url = 'https://push2.eastmoney.com/api/qt/stock/get'
    params = {
        'secid': '1.600000',
        'fields': 'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f53,f57,f58,f59,f60',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fltt': '2',
        'invt': '2',
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
        print(f'URL: {resp.url}')
        data = resp.json()
        print(f'Response: {data}')
        
        # 测试批量
        print('\n=== 批量测试 ===')
        params2 = {
            'secid': '1.600000,0.000001',
            'fields': 'f43,f44,f45,f46,f47,f48,f49,f51,f52,f53,f57,f58,f59,f60,f12,f13,f14',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            'fltt': '2',
            'invt': '2',
            '_': str(int(time.time() * 1000)),
        }
        resp2 = await client.get(url, params=params2)
        data2 = resp2.json()
        print(f'Batch Response: {data2}')

asyncio.run(test())