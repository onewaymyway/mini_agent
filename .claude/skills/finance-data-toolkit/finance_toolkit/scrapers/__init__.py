"""
抓取器实现模块
包含各数据源的具体抓取器实现
"""

# 这里的抓取器会被 core.py 自动发现并注册
# 也可以显式导入

try:
    from .akshare_scraper import AKShareScraper
except ImportError:
    AKShareScraper = None

try:
    from .tushare_scraper import TushareScraper
except ImportError:
    TushareScraper = None

try:
    from .eastmoney_scraper import EastmoneyScraper
except ImportError:
    EastmoneyScraper = None

try:
    from .sina_scraper import SinaScraper
except ImportError:
    SinaScraper = None

__all__ = []
if AKShareScraper:
    __all__.append('AKShareScraper')
if TushareScraper:
    __all__.append('TushareScraper')
if EastmoneyScraper:
    __all__.append('EastmoneyScraper')
if SinaScraper:
    __all__.append('SinaScraper')