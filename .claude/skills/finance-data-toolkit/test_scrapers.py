import sys
sys.path.insert(0, '.')
import asyncio
from finance_toolkit.core import create_scraper

async def test():
    s = create_scraper('akshare')
    print('akshare health:', await s.health_check())
    await s.close()
    
    s = create_scraper('eastmoney')
    print('eastmoney health:', await s.health_check())
    await s.close()
    
    s = create_scraper('sina')
    print('sina health:', await s.health_check())
    await s.close()

asyncio.run(test())
