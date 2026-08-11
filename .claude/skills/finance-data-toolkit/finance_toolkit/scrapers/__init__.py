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

try:
    from .yahoo_scraper import YahooScraper
except ImportError:
    YahooScraper = None

try:
    from .fund_scraper import FundScraper
except ImportError:
    FundScraper = None

try:
    from .bond_scraper import BondScraper
except ImportError:
    BondScraper = None

try:
    from .futures_scraper import FuturesScraper
except ImportError:
    FuturesScraper = None

try:
    from .index_scraper import IndexScraper
except ImportError:
    IndexScraper = None

try:
    from .macro_scraper import MacroScraper
except ImportError:
    MacroScraper = None

try:
    from .forex_scraper import ForexScraper
except ImportError:
    ForexScraper = None

try:
    from .crypto_scraper import CryptoScraper
except ImportError:
    CryptoScraper = None

try:
    from .etf_scraper import ETFScraper
except ImportError:
    ETFScraper = None

try:
    from .sector_scraper import SectorScraper
except ImportError:
    SectorScraper = None

__all__ = []
if AKShareScraper:
    __all__.append('AKShareScraper')
if TushareScraper:
    __all__.append('TushareScraper')
if EastmoneyScraper:
    __all__.append('EastmoneyScraper')
if SinaScraper:
    __all__.append('SinaScraper')
if YahooScraper:
    __all__.append('YahooScraper')
if FundScraper:
    __all__.append('FundScraper')
if BondScraper:
    __all__.append('BondScraper')
if FuturesScraper:
    __all__.append('FuturesScraper')
if IndexScraper:
    __all__.append('IndexScraper')
if MacroScraper:
    __all__.append('MacroScraper')
if ForexScraper:
    __all__.append('ForexScraper')
if CryptoScraper:
    __all__.append('CryptoScraper')
if ETFScraper:
    __all__.append('ETFScraper')
if SectorScraper:
    __all__.append('SectorScraper')
try:
    from .stock_scraper import StockScraper
except ImportError:
    StockScraper = None

try:
    from .news_scraper import NewsScraper
except ImportError:
    NewsScraper = None

try:
    from .social_scraper import SocialScraper
except ImportError:
    SocialScraper = None

try:
    from .guba_scraper import GubaScraper
except ImportError:
    GubaScraper = None

try:
    from .commodity_scraper import CommodityScraper
except ImportError:
    CommodityScraper = None

if StockScraper:
    __all__.append('StockScraper')
if NewsScraper:
    __all__.append('NewsScraper')
if SocialScraper:
    __all__.append('SocialScraper')
if GubaScraper:
    __all__.append('GubaScraper')
if CommodityScraper:
    __all__.append('CommodityScraper')