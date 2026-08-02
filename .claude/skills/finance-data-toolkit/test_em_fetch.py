import sys
sys.path.insert(0, '.')
import asyncio
from finance_toolkit.core import create_scraper

async def test():
    # 测试东方财富实时行情
    s = create_scraper('eastmoney')
    print('=== 东方财富实时行情测试 ===')
    count = 0
    async for data in s.fetch(['600000.SH', '000001.SZ'], 'quote'):
        print(f'  {data.symbol}: {data.payload.get("name", "")} price={data.payload.get("price", "N/A")}')
        count += 1
    print(f'  共获取 {count} 条行情')
    await s.close()
    
    # 测试东方财富K线
    print('\n=== 东方财富K线测试 ===')
    s = create_scraper('eastmoney')
    count = 0
    async for data in s.fetch(['600000.SH'], 'kline', period='101', start=None, end=None):
        print(f'  {data.symbol}: {data.payload.get("count", 0)} 条K线')
        if data.payload.get('data'):
            first = data.payload['data'][0]
            last = data.payload['data'][-1]
            print(f'  首条: {first["date"]} close={first["close"]}')
            print(f'  末条: {last["date"]} close={last["close"]}')
        count += 1
    print(f'  共获取 {count} 条结果')
    await s.close()

asyncio.run(test())