import sys
sys.path.insert(0, '.')
import asyncio
import time
from finance_toolkit.scrapers.eastmoney_scraper import to_em_symbol, EM_API
from finance_toolkit.core import create_scraper

async def test():
    s = create_scraper('eastmoney')
    code = to_em_symbol('600000.SH')
    print(f'EM code: {code}')
    
    params = {
        'secid': code,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': '101',
        'fqt': '1',
        'beg': '20240101',
        'end': '20261231',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        '_': str(int(time.time() * 1000)),
    }
    
    resp = await s.client.get(EM_API['kline'], params=params)
    print(f'Status: {resp.status_code}')
    print(f'Response length: {len(resp.text)}')
    data = resp.json()
    print(f'rc: {data.get("rc")}')
    klines = data.get('data', {}).get('klines', []) if data.get('data') else []
    print(f'klines count: {len(klines)}')
    if klines:
        print(f'first: {klines[0]}')
        print(f'last: {klines[-1]}')
    await s.close()

asyncio.run(test())