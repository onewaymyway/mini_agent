# 新增搜索器/抓取器架构设计

> 生成时间：2026-08-02
> 目的：为 browser-cdp skill 拓展提供统一的模块架构规范

---

## 1. 架构总览

### 1.1 模块分层

```
src/searchers/
├── __init__.py              # 统一入口，导出所有搜索器
├── base.py                  # 抽象基类 BaseSearcher
├── config.py                # 通用配置类
├── utils.py                 # 通用工具函数
│
├── # 搜索引擎（已有）
├── baidu_search.py
├── bing_search.py
│
├── # 内容平台（已有）
├── zhihu_search.py
├── zhihu_hot.py
├── zhihu_column_search.py
├── zhihu_publish_answer.py
├── arxiv_search.py
├── arxiv_multi_search.py
├── wechat_search.py
│
├── # 新增：电商
├── jd_search.py             # 京东搜索
├── pdd_search.py            # 拼多多搜索
│
├── # 新增：新闻
├── sina_news.py             # 新浪财经
├── cls_news.py              # 财联社
│
├── # 新增：社交
├── douban_search.py         # 豆瓣搜索
│
├── # 新增：金融
├── eastmoney_guba.py        # 东方财富股吧
├── xueqiu_search.py         # 雪球搜索
│
├── # 新增：学术
├── scholar_search.py        # Google Scholar
│
├── # 新增：招聘
├── zhipin_search.py         # BOSS直聘
├── lagou_search.py          # 拉勾网
│
├── # 新增：房产
├── lianjia_search.py        # 链家
├── anjuke_search.py         # 安居客
│
├── # 新增：旅游
├── ctrip_search.py          # 携程
│
└── # 新增：视频/音乐
├── bilibili_search.py       # B站搜索
└── netease_music_search.py  # 网易云音乐
```

### 1.2 核心抽象基类

```python
# src/searchers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional, AsyncIterator
from datetime import datetime

@dataclass
class SearcherConfig:
    """搜索器通用配置"""
    port: int = 9333
    tab_id: Optional[str] = None
    max_results: int = 10
    wait_timeout: int = 30
    stealth: bool = True
    handle_captcha: bool = False
    output_dir: Optional[str] = None
    session_name: Optional[str] = None  # 用于登录态持久化

@dataclass
class SearchResult:
    """搜索结果统一格式"""
    source: str                    # 数据源标识
    title: str                     # 标题
    url: str                       # 原始链接
    snippet: str                   # 摘要/片段
    published_time: Optional[str] = None
    author: Optional[str] = None
    metadata: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        return {
            'source': self.source,
            'title': self.title,
            'url': self.url,
            'snippet': self.snippet,
            'published_time': self.published_time,
            'author': self.author,
            'metadata': self.metadata or {}
        }

class BaseSearcher(ABC):
    """搜索器抽象基类"""
    
    @property
    @abstractmethod
    def source_name(self) -> str: ...
    
    @abstractmethod
    async def search(self, query: str, config: SearcherConfig) -> List[SearchResult]: ...
    
    @abstractmethod
    async def get_detail(self, url: str, config: SearcherConfig) -> Dict:
        """获取详情页内容"""
        ...
    
    @property
    def default_config(self) -> SearcherConfig:
        return SearcherConfig()
```

---

## 2. 各搜索器详细设计

### 2.1 京东搜索器 (jd_search.py)

**目标**：搜索京东商品，获取价格、销量、评价等核心信息

**URL 模式**：
- 搜索：`https://search.jd.com/Search?keyword={keyword}&enc=utf-8&wq={keyword}`
- 商品详情：`https://item.jd.com/{sku_id}.html`

**数据提取**：
```python
# 列表页 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.gl-item');
  const results = [];
  items.forEach((item, i) => {
    const link = item.querySelector('a[itemprop="url"]');
    const title = item.querySelector('em')?.innerText || '';
    const price = item.querySelector('.p-price strong')?.innerText || '';
    const commit = item.querySelector('.p-commit strong')?.innerText || '';
    const shop = item.querySelector('.p-shop a')?.innerText || '';
    results.push({
      title: title.replace(/\s+/g, ' ').trim(),
      price: price,
      commit: commit,
      shop: shop,
      url: link?.href || ''
    });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 随机 UA 轮换（每 3 次请求）
- 请求间隔 3-6 秒
- 避免高频搜索同一关键词

**输出格式**：
```json
{
  "source": "jd",
  "query": "iPhone 15",
  "results": [
    {
      "title": "Apple iPhone 15 (A3090) 128GB 黑色 支持移动联通电信5G 双卡双待手机",
      "url": "https://item.jd.com/100085544056.html",
      "price": "¥5999.00",
      "commit": "100万+",
      "shop": "Apple 京东自营旗舰店",
      "sku_id": "100085544056"
    }
  ],
  "total": 100000,
  "scraped_at": "2026-08-02T10:00:00Z"
}
```

---

### 2.2 拼多多搜索器 (pdd_search.py)

**目标**：搜索拼多多商品，获取价格、销量、店铺信息

**URL 模式**：
- 搜索：`https://mobile.yangkeduo.com/proxy/api/search?keyword={keyword}&page=1`
- 商品详情：`https://mobile.yangkeduo.com/proxy/api/goods/detail?goods_id={id}`

**数据提取**：
```python
# API 直接调用（无需浏览器）
import requests

def search_pdd(keyword: str, page: int = 1) -> List[Dict]:
    url = f"https://mobile.yangkeduo.com/proxy/api/search"
    params = {
        "keyword": keyword,
        "page": page,
        "page_size": 20
    }
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://mobile.yangkeduo.com/"
    }
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    data = resp.json()
    return data.get('goods_list', [])
```

**反爬策略**：
- 直接 API 调用，无需浏览器
- 请求间隔 2-4 秒
- 使用代理池（可选）

**输出格式**：
```json
{
  "source": "pdd",
  "query": "手机壳",
  "results": [
    {
      "goods_id": "123456789",
      "goods_name": "iPhone 15 手机壳 硅胶防摔",
      "price": "9.9",
      "original_price": "29.9",
      "sales": "10万+",
      "shop_name": "XX数码旗舰店",
      "image_url": "https://img.pddpic.com/..."
    }
  ],
  "total": 50000,
  "scraped_at": "2026-08-02T10:00:00Z"
}
```

---

### 2.3 新浪财经搜索器 (sina_news.py)

**目标**：抓取新浪财经新闻列表和详情

**URL 模式**：
- 列表：`https://finance.sina.com.cn/stock/`
- 详情：`https://finance.sina.com.cn/stock/.../...shtml`

**数据提取**：
```python
# 列表页 RSS 格式
import feedparser

def fetch_sina_news(category: str = 'stock') -> List[Dict]:
    urls = {
        'stock': 'https://feed.finance.sina.com.cn/rss/stock.xml',
        'macro': 'https://feed.finance.sina.com.cn/rss/macro.xml',
        'industry': 'https://feed.finance.sina.com.cn/rss/industry.xml'
    }
    feed = feedparser.parse(urls.get(category, urls['stock']))
    return [{
        'title': entry.title,
        'link': entry.link,
        'summary': entry.summary[:200],
        'published': entry.published
    } for entry in feed.entries[:20]]
```

**反爬策略**：
- 无需浏览器，直接 requests 抓取
- 请求间隔 1-2 秒
- 支持 RSS 格式，稳定性高

---

### 2.4 财联社搜索器 (cls_news.py)

**目标**：抓取财联社电报和新闻

**URL 模式**：
- 电报流：`https://www.cls.cn/telegraph`
- 详情：`https://www.cls.cn/detail/{id}`

**数据提取**：
```python
# 电报流 API
js_code = r"""
(() => {
  const items = document.querySelectorAll('.telegraph-content');
  const results = [];
  items.forEach((item, i) => {
    const time = item.querySelector('.telegraph-time')?.innerText || '';
    const content = item.querySelector('.telegraph-content-text')?.innerText || '';
    results.push({ time, content });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 3-5 秒
- 电报流实时性高，适合监控场景

---

### 2.5 豆瓣搜索器 (douban_search.py)

**目标**：搜索豆瓣书籍/电影/音乐

**URL 模式**：
- 搜索：`https://www.douban.com/search?q={keyword}`
- 书籍详情：`https://book.douban.com/subject/{id}/`
- 电影详情：`https://movie.douban.com/subject/{id}/`

**数据提取**：
```python
# 搜索结果页 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.search-result .result');
  const results = [];
  items.forEach((item, i) => {
    const title = item.querySelector('.title a')?.innerText || '';
    const rate = item.querySelector('.rating_nums')?.innerText || '';
    const info = item.querySelector('.pl')?.innerText || '';
    const url = item.querySelector('.title a')?.href || '';
    results.push({ title, rate, info, url });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--dedicated --name douban_session` 保留登录态
- 请求间隔 3-5 秒
- 首次使用需用户手动登录

---

### 2.6 东方财富股吧搜索器 (eastmoney_guba.py)

**目标**：抓取东方财富股吧帖子和评论

**URL 模式**：
- 帖子列表：`https://guba.eastmoney.com/list/{stock_code}.html`
- 帖子详情：`https://guba.eastmoney.com/news,/{post_id}.html`

**数据提取**：
```python
# 帖子列表 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.listItem');
  const results = [];
  items.forEach((item, i) => {
    const title = item.querySelector('.listTitle a')?.innerText || '';
    const url = item.querySelector('.listTitle a')?.href || '';
    const read = item.querySelector('.listRead')?.innerText || '';
    const comment = item.querySelector('.listComment')?.innerText || '';
    const time = item.querySelector('.listTime')?.innerText || '';
    results.push({ title, url, read, comment, time });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 2-4 秒
- 支持增量更新（按时间戳去重）

---

### 2.7 雪球搜索器 (xueqiu_search.py)

**目标**：搜索雪球股票讨论和组合

**URL 模式**：
- 搜索：`https://xueqiu.com/query/v1/search/status.json?q={keyword}&count=10&page=1`
- 股票详情：`https://xueqiu.com/{symbol}`

**数据提取**：
```python
# API 直接调用（需登录 Cookie）
def search_xueqiu(keyword: str, cookie: str) -> List[Dict]:
    url = "https://xueqiu.com/query/v1/search/status.json"
    params = {"q": keyword, "count": 10, "page": 1}
    headers = {
        "Cookie": cookie,
        "User-Agent": random.choice(USER_AGENTS)
    }
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    return resp.json().get('items', [])
```

**反爬策略**：
- 必须使用 `--dedicated --name xueqiu_session` 保留登录态
- 请求间隔 3-5 秒
- Cookie 需定期刷新

---

### 2.8 Google Scholar 搜索器 (scholar_search.py)

**目标**：搜索 Google Scholar 学术论文

**URL 模式**：
- 搜索：`https://scholar.google.com/scholar?q={query}&hl=zh-CN`

**数据提取**：
```python
# 搜索结果页 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.gs_r');
  const results = [];
  items.forEach((item, i) => {
    const title = item.querySelector('.gs_rt a')?.innerText || '';
    const url = item.querySelector('.gs_rt a')?.href || '';
    const snippet = item.querySelector('.gs_rs')?.innerText || '';
    const cited = item.querySelector('.gs_cit')?.innerText || '';
    results.push({ title, url, snippet, cited });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 5-10 秒（避免触发验证码）
- 随机 UA 轮换

---

### 2.9 BOSS直聘搜索器 (zhipin_search.py)

**目标**：搜索 BOSS直聘职位信息

**URL 模式**：
- 搜索：`https://www.zhipin.com/web/geek/job?query={keyword}&city={city}`
- 职位详情：`https://www.zhipin.com/web/geek/job/{job_id}`

**数据提取**：
```python
# 搜索结果页 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.job-card');
  const results = [];
  items.forEach((item, i) => {
    const title = item.querySelector('.job-title')?.innerText || '';
    const salary = item.querySelector('.salary')?.innerText || '';
    const company = item.querySelector('.company')?.innerText || '';
    const location = item.querySelector('.location')?.innerText || '';
    const url = item.querySelector('a')?.href || '';
    results.push({ title, salary, company, location, url });
  });
  return results;
})()
"""
```

**反爬策略**：
- 必须使用 `--dedicated --name zhipin_session` 保留登录态
- 薪资数据需处理字体加密（建议用 API 获取明文）
- 请求间隔 5-10 秒
- 建议仅用于低频监控

---

### 2.10 链家搜索器 (lianjia_search.py)

**目标**：搜索链家小区和房源信息

**URL 模式**：
- 小区列表：`https://{city}.lianjia.com/xiaoqu/{district}/`
- 房源列表：`https://{city}.lianjia.com/ershoufang/`

**数据提取**：
```python
# 小区列表 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.xiaoquListItem');
  const results = [];
  items.forEach((item, i) => {
    const name = item.querySelector('.title a')?.innerText || '';
    const district = item.querySelector('.district')?.innerText || '';
    const avg_price = item.querySelector('.avgPrice')?.innerText || '';
    const url = item.querySelector('.title a')?.href || '';
    results.push({ name, district, avg_price, url });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 3-5 秒
- 注意过滤"幽灵房"假数据

---

### 2.11 携程搜索器 (ctrip_search.py)

**目标**：搜索携程酒店和机票信息

**URL 模式**：
- 酒店：`https://hotels.ctrip.com/hotel/{city_id}`
- 机票：`https://flights.ctrip.com/online/list/oneway-{from}-{to}`

**数据提取**：
```python
# 酒店列表 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.hotelItem');
  const results = [];
  items.forEach((item, i) => {
    const name = item.querySelector('.hotelName')?.innerText || '';
    const price = item.querySelector('.price')?.innerText || '';
    const rating = item.querySelector('.rating')?.innerText || '';
    const url = item.querySelector('.hotelName a')?.href || '';
    results.push({ name, price, rating, url });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 5-10 秒
- sign 参数需动态生成（建议用浏览器环境）

---

### 2.12 B站搜索器 (bilibili_search.py)

**目标**：搜索 B站视频和UP主

**URL 模式**：
- 搜索：`https://search.bilibili.com/all?keyword={keyword}`
- 视频详情：`https://www.bilibili.com/video/{bv_id}`

**数据提取**：
```python
# 搜索结果页 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.bili-video-card');
  const results = [];
  items.forEach((item, i) => {
    const title = item.querySelector('.title')?.innerText || '';
    const author = item.querySelector('.author')?.innerText || '';
    const play = item.querySelector('.play')?.innerText || '';
    const danmu = item.querySelector('.danmu')?.innerText || '';
    const url = item.querySelector('a')?.href || '';
    results.push({ title, author, play, danmu, url });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 3-5 秒
- 适合批量抓取视频元数据

---

### 2.13 网易云音乐搜索器 (netease_music_search.py)

**目标**：搜索网易云音乐歌曲和歌手

**URL 模式**：
- 搜索：`https://music.163.com/search?type=1&s={keyword}`
- 歌曲详情：`https://music.163.com/#/song?id={song_id}`

**数据提取**：
```python
# 搜索结果页 JS 提取
js_code = r"""
(() => {
  const items = document.querySelectorAll('.nb');
  const results = [];
  items.forEach((item, i) => {
    const title = item.querySelector('.f-fc2')?.innerText || '';
    const artist = item.querySelector('.s-fc3')?.innerText || '';
    const album = item.querySelector('.s-fc3:last-child')?.innerText || '';
    const url = item.querySelector('a')?.href || '';
    results.push({ title, artist, album, url });
  });
  return results;
})()
"""
```

**反爬策略**：
- 使用 `--stealth` 模式
- 请求间隔 3-5 秒
- 注意版权限制，仅抓取元数据

---

## 3. 统一配置与工具函数

### 3.1 通用配置 (config.py)

```python
# src/searchers/config.py
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class SearcherConfig:
    """搜索器通用配置"""
    # 浏览器配置
    port: int = 9333
    tab_id: Optional[str] = None
    session_name: Optional[str] = None
    
    # 搜索配置
    query: str = ""
    max_results: int = 10
    page_size: int = 20
    
    # 等待配置
    wait_timeout: int = 30
    wait_strategy: str = "networkidle"  # networkidle/route/stable/selector
    
    # 反爬配置
    stealth: bool = True
    handle_captcha: bool = False
    random_delay: tuple = (2, 5)  # 随机延迟范围（秒）
    
    # 输出配置
    output_dir: Optional[str] = None
    output_format: str = "json"  # json/csv/markdown
    save_detail: bool = False
    
    # 去重配置
    dedup_by: str = "url"  # url/title/simhash
    dedup_threshold: float = 0.9
```

### 3.2 通用工具函数 (utils.py)

```python
# src/searchers/utils.py
import random
import time
from typing import List, Dict, Optional
from pathlib import Path
import hashlib

def random_delay(min_sec: float = 2.0, max_sec: float = 5.0) -> float:
    """随机延迟"""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    return delay

def get_random_ua() -> str:
    """获取随机 User-Agent"""
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]
    return random.choice(uas)

def compute_simhash(text: str, dim: int = 64) -> int:
    """计算 SimHash（用于去重）"""
    # 简化实现：使用 MD5 作为占位
    return int(hashlib.md5(text.encode()).hexdigest(), 16)

def dedup_results(results: List[Dict], by: str = "url", threshold: float = 0.9) -> List[Dict]:
    """结果去重"""
    seen = set()
    unique = []
    for r in results:
        key = r.get(by, "")
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

def save_results(results: List[Dict], output_dir: str, filename: str = None):
    """保存结果到文件"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if filename is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"results_{timestamp}.json"
    path = Path(output_dir) / filename
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return str(path)
```

---

## 4. 实现优先级与排期

### 第一阶段（高优先级，易实现）

| 搜索器 | 预计工时 | 依赖 |
|--------|----------|------|
| 拼多多搜索器 | 2h | 无 |
| 新浪财经搜索器 | 1h | 无 |
| 豆瓣搜索器 | 3h | 登录态 |
| 东方财富股吧 | 2h | 无 |

### 第二阶段（中优先级，需登录态）

| 搜索器 | 预计工时 | 依赖 |
|--------|----------|------|
| 京东搜索器 | 4h | stealth 模式 |
| 财联社搜索器 | 2h | 无 |
| 雪球搜索器 | 3h | 登录态 |
| Google Scholar | 3h | stealth 模式 |

### 第三阶段（高难度，低频使用）

| 搜索器 | 预计工时 | 依赖 |
|--------|----------|------|
| BOSS直聘搜索器 | 5h | 登录态 + 字体加密 |
| 链家搜索器 | 3h | stealth 模式 |
| 携程搜索器 | 4h | sign 参数 |
| B站搜索器 | 3h | 登录态 |
| 网易云音乐 | 2h | 无 |

---

## 5. 测试策略

### 5.1 单元测试

```python
# tests/test_new_searchers.py
import pytest
from src.searchers.jd_search import JDSearcher
from src.searchers.pdd_search import PDDSearcher
from src.searchers.douban_search import DoubanSearcher

@pytest.mark.asyncio
async def test_jd_search():
    searcher = JDSearcher()
    results = await searcher.search("iPhone 15", max_results=5)
    assert len(results) > 0
    assert all(r.source == "jd" for r in results)

@pytest.mark.asyncio
async def test_pdd_search():
    searcher = PDDSearcher()
    results = await searcher.search("手机壳", max_results=5)
    assert len(results) > 0
    assert all(r.source == "pdd" for r in results)
```

### 5.2 集成测试

```python
# tests/test_integration.py
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_workflow():
    # 启动浏览器
    # 执行搜索
    # 验证结果
    # 关闭浏览器
    pass
```

---

## 6. 文档更新计划

### 6.1 SKILL.md 更新

- 新增「新增搜索器」章节
- 更新「网站类型支持矩阵」
- 新增各搜索器使用示例

### 6.2 references 更新

- 新增 `jd-search.md`
- 新增 `pdd-search.md`
- 新增 `douban-search.md`
- 新增 `eastmoney-guba.md`
- 更新 `website-analysis.md`

---

*本架构设计为 browser-cdp skill 拓展提供统一规范，具体实现见后续步骤。*
