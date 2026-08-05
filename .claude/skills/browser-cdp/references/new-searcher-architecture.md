# 新增搜索器架构设计（步骤4）

> 生成时间：2026-08-05
> 目的：为 browser-cdp skill 拓展的12个新目标网站设计统一的搜索器架构

---

## 1. 架构总览

### 1.1 新增搜索器列表

| 搜索器 | 文件 | 目标网站 | 领域 | 难度 |
|--------|------|----------|------|------|
| Music163Searcher | music163_search.py | 网易云音乐 | 音乐 | ⭐⭐⭐ |
| DouyinSearcher | douyin_search.py | 抖音 | 视频 | ⭐⭐⭐⭐⭐ |
| KuaishouSearcher | kuaishou_search.py | 快手 | 视频 | ⭐⭐⭐⭐ |
| XiguaSearcher | xigua_search.py | 西瓜视频 | 视频 | ⭐⭐⭐ |
| QunarSearcher | qunar_search.py | 去哪儿 | 旅游 | ⭐⭐⭐ |
| FliggySearcher | fliggy_search.py | 飞猪 | 旅游 | ⭐⭐⭐ |
| MafengwoSearcher | mafengwo_search.py | 马蜂窝 | 旅游 | ⭐⭐ |
| AnjukeSearcher | anjuke_search.py | 安居客 | 房产 | ⭐⭐⭐ |
| ZhilianSearcher | zhilian_search.py | 智联招聘 | 招聘 | ⭐⭐⭐ |
| LiepinSearcher | liepin_search.py | 猎聘 | 招聘 | ⭐⭐⭐ |
| SematicScholarSearcher | sematic_scholar_search.py | Semantic Scholar | 学术 | ⭐⭐ |
| CnkSearcher | cnki_search.py | 中国知网 | 学术 | ⭐⭐⭐⭐ |

### 1.2 目录结构

```
src/searchers/
├── __init__.py              # 统一入口
├── base.py                  # 抽象基类
├── config.py                # 配置类
├── utils.py                 # 工具函数
│
├── # 新增搜索器
├── music163_search.py       # 网易云音乐
├── douyin_search.py         # 抖音
├── kuaishou_search.py       # 快手
├── xigua_search.py          # 西瓜视频
├── qunar_search.py          # 去哪儿
├── fliggy_search.py         # 飞猪
├── mafengwo_search.py       # 马蜂窝
├── anjuke_search.py         # 安居客
├── zhilian_search.py        # 智联招聘
├── liepin_search.py         # 猎聘
├── sematic_scholar_search.py # Semantic Scholar
└── cnki_search.py           # 中国知网
```

---

## 2. 统一基类设计

### 2.1 BaseSearcher 抽象基类

```python
# src/searchers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
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
    session_name: Optional[str] = None
    
    # 新增字段
    proxy: Optional[str] = None          # 代理地址
    delay_range: tuple = (2, 5)          # 请求延迟范围
    retry_count: int = 3                 # 重试次数
    user_agent: Optional[str] = None     # 自定义UA

class BaseSearcher(ABC):
    """搜索器抽象基类"""
    
    @property
    @abstractmethod
    def source_name(self) -> str: ...
    
    @abstractmethod
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]: ...
    
    @abstractmethod
    async def get_detail(self, url: str, config: SearcherConfig) -> Dict: ...
    
    @property
    def default_config(self) -> SearcherConfig:
        return SearcherConfig()
    
    def validate_config(self, config: SearcherConfig) -> bool:
        """验证配置合法性"""
        if config.max_results < 1 or config.max_results > 100:
            return False
        if config.wait_timeout < 5 or config.wait_timeout > 120:
            return False
        return True
```

---

## 3. 各搜索器详细设计

### 3.1 网易云音乐搜索器 (music163_search.py)

**目标**：搜索歌曲、歌手、专辑，获取元数据

**架构设计**：
```python
class Music163Searcher(BaseSearcher):
    source_name = "music163"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入关键词搜索
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
    
    async def get_detail(self, url: str, config: SearcherConfig) -> Dict:
        # 1. 访问歌曲详情页
        # 2. 提取歌曲信息
        # 3. 返回详情
        pass
```

**数据提取JS**：
```javascript
(() => {
  const results = [];
  document.querySelectorAll('.f-item').forEach(el => {
    results.push({
      title: el.querySelector('.name')?.innerText || '',
      artist: el.querySelector('.s-fc3')?.innerText || '',
      album: el.querySelector('.sub')?.innerText || '',
      duration: el.querySelector('.dur')?.innerText || '',
      url: el.querySelector('a')?.href || ''
    });
  });
  return results;
})()
```

---

### 3.2 抖音搜索器 (douyin_search.py)

**目标**：搜索视频、用户，获取元数据

**架构设计**：
```python
class DouyinSearcher(BaseSearcher):
    source_name = "douyin"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        # 1. 启动浏览器（必须stealth）
        # 2. 访问搜索页
        # 3. 输入关键词搜索
        # 4. 处理可能的验证码
        # 5. 提取搜索结果
        # 6. 返回结果列表
        pass
```

**反爬策略**：
- 必须使用 stealth 模式
- 请求间隔 10-30 秒
- 建议登录专用浏览器实例
- 配合代理池使用

---

### 3.3 快手搜索器 (kuaishou_search.py)

**目标**：搜索视频、用户，获取元数据

**架构设计**：
```python
class KuaishouSearcher(BaseSearcher):
    source_name = "kuaishou"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入关键词搜索
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

---

### 3.4 西瓜视频搜索器 (xigua_search.py)

**目标**：搜索视频，获取元数据

**架构设计**：
```python
class XiguaSearcher(BaseSearcher):
    source_name = "xigua"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入关键词搜索
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

**特点**：反爬较弱，适合批量抓取

---

### 3.5 去哪儿搜索器 (qunar_search.py)

**目标**：搜索酒店，获取价格、评分、地址等信息

**架构设计**：
```python
class QunarSearcher(BaseSearcher):
    source_name = "qunar"
    
    async def search(self, city: str, config: SearcherConfig, 
                     checkin: str = None, checkout: str = None) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问酒店搜索页
        # 3. 输入城市、日期
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

**数据提取JS**：
```javascript
(() => {
  const results = [];
  document.querySelectorAll('.hotel-item').forEach(el => {
    results.push({
      name: el.querySelector('.hotel-name')?.innerText || '',
      price: el.querySelector('.price')?.innerText || '',
      rating: el.querySelector('.rating')?.innerText || '',
      address: el.querySelector('.address')?.innerText || '',
      url: el.querySelector('a')?.href || ''
    });
  });
  return results;
})()
```

---

### 3.6 飞猪搜索器 (fliggy_search.py)

**目标**：搜索酒店，获取价格、评分、商家信息

**架构设计**：
```python
class FliggySearcher(BaseSearcher):
    source_name = "fliggy"
    
    async def search(self, city: str, config: SearcherConfig,
                     checkin: str = None, checkout: str = None) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问酒店搜索页
        # 3. 输入城市、日期
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

---

### 3.7 马蜂窝搜索器 (mafengwo_search.py)

**目标**：搜索旅游攻略，获取攻略标题、作者、浏览量等

**架构设计**：
```python
class MafengwoSearcher(BaseSearcher):
    source_name = "mafengwo"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入关键词搜索
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

**特点**：反爬较弱，适合批量抓取

---

### 3.8 安居客搜索器 (anjuke_search.py)

**目标**：搜索小区、房源，获取价格、面积等信息

**架构设计**：
```python
class AnjukeSearcher(BaseSearcher):
    source_name = "anjuke"
    
    async def search(self, city: str, config: SearcherConfig,
                     type: str = "xiaoqu") -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入城市、类型
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

**特点**：反爬较弱，可直接 requests 抓取

---

### 3.9 智联招聘搜索器 (zhilian_search.py)

**目标**：搜索职位，获取职位名称、公司、薪资、地点等信息

**架构设计**：
```python
class ZhilianSearcher(BaseSearcher):
    source_name = "zhilian"
    
    async def search(self, query: str, config: SearcherConfig,
                     city: str = None, min_salary: int = 0,
                     max_salary: int = 99999) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入关键词、城市、薪资范围
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

---

### 3.10 猎聘搜索器 (liepin_search.py)

**目标**：搜索高端职位，获取职位信息

**架构设计**：
```python
class LiepinSearcher(BaseSearcher):
    source_name = "liepin"
    
    async def search(self, query: str, config: SearcherConfig,
                     city: str = None, min_salary: int = 0,
                     max_salary: int = 99999) -> List[Dict]:
        # 1. 启动浏览器（stealth模式）
        # 2. 访问搜索页
        # 3. 输入关键词、城市、薪资范围
        # 4. 提取搜索结果
        # 5. 返回结果列表
        pass
```

---

### 3.11 Semantic Scholar搜索器 (sematic_scholar_search.py)

**目标**：搜索学术论文，获取标题、作者、引用数、摘要等

**架构设计**：
```python
class SematicScholarSearcher(BaseSearcher):
    source_name = "sematic_scholar"
    
    async def search(self, query: str, config: SearcherConfig,
                     fields: List[str] = None) -> List[Dict]:
        # 方案1：优先使用 API（无需浏览器）
        # 方案2：使用浏览器访问网页版
        pass
    
    def search_api(self, query: str, config: SearcherConfig) -> List[Dict]:
        # 直接调用 Semantic Scholar API
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": config.max_results,
            "fields": ",".join(fields) if fields else None
        }
        # 返回结果
        pass
```

**特点**：优先使用 API，稳定且快速

---

### 3.12 中国知网搜索器 (cnki_search.py)

**目标**：搜索学术论文，获取标题、作者、期刊、摘要等

**架构设计**：
```python
class CnkSearcher(BaseSearcher):
    source_name = "cnki"
    
    async def search(self, query: str, config: SearcherConfig,
                     db: str = "SCIDB") -> List[Dict]:
        # 1. 启动浏览器（必须stealth）
        # 2. 访问搜索页
        # 3. 输入关键词、数据库
        # 4. 处理可能的验证码
        # 5. 提取搜索结果
        # 6. 返回结果列表
        pass
```

**反爬策略**：
- 必须使用 stealth 模式
- 建议登录专用浏览器实例
- 请求间隔 5-10 秒
- 配合代理池使用

---

## 4. 统一入口设计

### 4.1 __init__.py

```python
# src/searchers/__init__.py
from .base import BaseSearcher, SearcherConfig, SearchResult
from .music163_search import Music163Searcher
from .douyin_search import DouyinSearcher
from .kuaishou_search import KuaishouSearcher
from .xigua_search import XiguaSearcher
from .qunar_search import QunarSearcher
from .fliggy_search import FliggySearcher
from .mafengwo_search import MafengwoSearcher
from .anjuke_search import AnjukeSearcher
from .zhilian_search import ZhilianSearcher
from .liepin_search import LiepinSearcher
from .sematic_scholar_search import SematicScholarSearcher
from .cnki_search import CnkSearcher

# 搜索器注册表
SEARCHERS = {
    "music163": Music163Searcher,
    "douyin": DouyinSearcher,
    "kuaishou": KuaishouSearcher,
    "xigua": XiguaSearcher,
    "qunar": QunarSearcher,
    "fliggy": FliggySearcher,
    "mafengwo": MafengwoSearcher,
    "anjuke": AnjukeSearcher,
    "zhilian": ZhilianSearcher,
    "liepin": LiepinSearcher,
    "sematic_scholar": SematicScholarSearcher,
    "cnki": CnkSearcher,
}

def get_searcher(source: str) -> BaseSearcher:
    """获取搜索器实例"""
    if source not in SEARCHERS:
        raise ValueError(f"Unknown searcher: {source}")
    return SEARCHERS[source]()
```

---

## 5. 实现优先级

### 第一阶段（高优先级，易实现）
1. ✅ Semantic Scholar（API优先）
2. ✅ 马蜂窝（反爬弱）
3. ✅ 安居客（反爬弱）
4. ✅ 西瓜视频（反爬弱）

### 第二阶段（中优先级）
5. ✅ 网易云音乐
6. ✅ 去哪儿
7. ✅ 飞猪
8. ✅ 智联招聘
9. ✅ 猎聘

### 第三阶段（高难度，低频使用）
10. ⏳ 快手
11. ⏳ 抖音
12. ⏳ 中国知网

---

## 6. 通用工具函数

### 6.1 反检测工具

```python
# src/searchers/utils.py
async def apply_stealth(browser: Browser) -> None:
    """应用反检测脚本"""
    await browser.evaluate("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)

async def random_delay(min_sec: float = 2.0, max_sec: float = 5.0) -> None:
    """随机延迟"""
    import asyncio
    import random
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)

async def rotate_user_agent(browser: Browser) -> None:
    """轮换UA"""
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36...",
        # ... 更多UA
    ]
    await browser.set_user_agent(random.choice(uas))
```

---

## 7. 配置管理

### 7.1 搜索器配置

```python
# src/searchers/config.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class Music163Config:
    source: str = "music163"
    max_results: int = 10
    stealth: bool = True
    delay_range: tuple = (3, 5)

@dataclass
class DouyinConfig:
    source: str = "douyin"
    max_results: int = 5  # 低频使用
    stealth: bool = True
    delay_range: tuple = (10, 30)  # 长延迟
    need_login: bool = True

# ... 其他配置类
```

---

*本设计文档为 browser-cdp skill 拓展提供架构规范，具体实现见后续步骤。*
