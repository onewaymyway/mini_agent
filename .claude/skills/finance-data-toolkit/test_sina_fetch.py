import sys
sys.path.insert(0, '.')
import asyncio
from finance_toolkit.core import create_scraper

async def test():
    # 测试新浪实时行情
    s = create_scraper('sina')
    print('=== 新浪实时行情测试 ===')
    count = 0
    async for data in s.fetch(['600000.SH', '000001.SZ'], 'quote'):
        print(f'  {data.symbol}: {data.payload.get("name", "")} price={data.payload.get("price", "N/A")}')
        count += 1
    print(f'  共获取 {count} 条行情')
    await s.close()
    
    # 测试新浪K线
    print('\n=== 新浪K线测试 ===')
    s = create_scraper('sina')
    count = 0
    async for data in s.fetch(['600000.SH'], 'kline', period='240', datalen=10):
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