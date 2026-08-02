# 免登录数据源详解

> 本文件覆盖无需登录即可抓取的主流财经数据源，适合快速部署和批量采集。

---

## 1. 腾讯财经

### 1.1 实时行情接口

**端点**: `https://qt.gtimg.cn/q=<code_list>`

**代码格式**:
- A股: `sh600000`(沪市) / `sz000001`(深市)
- 港股: `hk00700`(腾讯控股)
- 美股: `gb_aapl`(苹果)

**返回格式**:
```javascript
v_sh600000="1~浦发银行~600000~10.50~10.45~10.48~10.52~1000000~500000~500000~10.49~1000~10.50~2000~10.52~10.55~10.48~10.50~100000000~20240101150000~-0.50~-4.55~0.50~12.5~105000000000~95000000000~...";
```

**字段解析**:
| 位置 | 字段 | 说明 |
|------|------|------|
| 1 | 状态 | 1=正常 |
| 2 | 名称 | 股票名称 |
| 3 | 代码 | 股票代码 |
| 4 | 当前价 | 最新价格 |
| 5 | 昨收 | 昨日收盘价 |
| 6 | 今开 | 今日开盘价 |
| 7 | 成交量 | 成交量(手) |
| 14 | 最高 | 今日最高价 |
| 15 | 最低 | 今日最低价 |
| 16 | 价格 | 当前价格 |
| 18 | 成交额 | 成交额(万) |
| 21 | 涨跌幅 | 涨跌幅(%) |
| 22 | 涨跌额 | 涨跌额 |
| 23 | 换手率 | 换手率(%) |
| 24 | 市盈率 | 市盈率 |
| 25 | 总市值 | 总市值(元) |
| 26 | 流通市值 | 流通市值(元) |

**Python 解析示例**:
```python
import requests
import re

def get_tencent_quote(codes: list) -> dict:
    """获取腾讯财经实时行情"""
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    resp = requests.get(url, timeout=10)
    result = {}
    for line in resp.text.strip().split(';\n'):
        if not line.startswith('v_'):
            continue
        code = line.split('~')[1] if '~' in line else ''
        fields = line.split('~')
        if len(fields) > 3:
            result[code] = {
                'name': fields[1],
                'price': float(fields[3]) if fields[3] else 0,
                'change_pct': float(fields[31]) if len(fields) > 31 and fields[31] else 0,
                'volume': int(fields[6]) if fields[6] else 0,
                'high': float(fields[14]) if len(fields) > 14 and fields[14] else 0,
                'low': float(fields[15]) if len(fields) > 15 and fields[15] else 0,
            }
    return result

# 使用示例
quotes = get_tencent_quote(['sh600000', 'sz000001', 'hk00700'])
for code, data in quotes.items():
    print(f"{code}: {data['name']} = {data['price']} ({data['change_pct']:+.2f}%)")
```

### 1.2 历史 K 线接口

**端点**: `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`

**参数**:
| 参数 | 必填 | 说明 |
|------|------|------|
| `param` | 是 | `代码,周期,起始日期,结束日期` |
| `_var` | 否 | 回调函数名 |

**周期代码**:
- `day`: 日K
- `week`: 周K
- `month`: 月K

**示例**:
```python
import requests

def get_tencent_kline(code: str, period: str = 'day', start: str = '', end: str = ''):
    """获取腾讯财经历史K线"""
    param = f"{code},{period},{start},{end}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_daydata&param={param}"
    resp = requests.get(url, timeout=10)
    # 提取 JSON
    match = re.search(r'"days":\[(.*?)\]', resp.text, re.DOTALL)
    if match:
        # 解析 K 线数据
        return match.group(1)
    return None
```

---

## 2. 网易财经

### 2.1 实时行情接口

**端点**: `https://api.money.126.net/data/feed/<code_list>.money`

**代码格式**: 无需 sh/sz 前缀，直接 `000001,600000`

**返回格式** (JSONP):
```javascript
jsonpgc({
  "000001": {
    "name": "平安银行",
    "code": "000001",
    "last_close": 10.50,
    "open": 10.48,
    "high": 10.55,
    "low": 10.45,
    "price": 10.52,
    "volume": 1000000,
    "amount": 105000000,
    "change": 0.02,
    "ratio": 0.19
  }
})
```

**Python 解析示例**:
```python
import requests
import json
import re

def get_163_quote(codes: list) -> dict:
    """获取网易财经实时行情"""
    url = f"https://api.money.126.net/data/feed/{','.join(codes)}.money"
    resp = requests.get(url, timeout=10)
    # 提取 JSONP
    match = re.search(r'jsonpgc\((.*)\)', resp.text, re.DOTALL)
    if match:
        data = json.loads(match.group(1))
        return data
    return {}

# 使用示例
quotes = get_163_quote(['000001', '600000'])
for code, data in quotes.items():
    print(f"{code}: {data['name']} = {data['price']} ({data['ratio']:+.2f}%)")
```

---

## 3. 百度股市通

### 3.1 实时行情接口

**端点**: `https://finance.pae.baidu.com/vapi/v1/getquotation`

**参数**:
| 参数 | 必填 | 说明 |
|------|------|------|
| `query` | 是 | 股票代码，如 `600000` |
| `type` | 否 | 市场类型 `sh,sz` |
| `ktype` | 否 | K线类型 `daily`/`weekly`/`monthly` |

**返回格式** (JSON):
```json
{
  "result": {
    "600000": {
      "name": "浦发银行",
      "price": 10.50,
      "change": 0.02,
      "changeRatio": 0.19,
      "high": 10.55,
      "low": 10.45,
      "volume": 1000000,
      "amount": 105000000,
      "open": 10.48,
      "lastClose": 10.48,
      "time": "2024-01-01 15:00:00"
    }
  }
}
```

**Python 解析示例**:
```python
import requests

def get_baidu_quote(codes: list) -> dict:
    """获取百度股市通实时行情"""
    url = "https://finance.pae.baidu.com/vapi/v1/getquotation"
    params = {
        "srcid": "5352",
        "query": ",".join(codes),
        "type": "sh,sz",
        "finClientType": "pc"
    }
    resp = requests.get(url, params=params, timeout=10)
    data = resp.json()
    return data.get('result', {})
```

---

## 4. 中金在线

### 4.1 实时行情接口

**端点**: `https://quote.jrj.com.cn/js/<code>/realtime.js`

**代码格式**: `sh600000` 或 `sz000001`

**返回格式** (JS 变量):
```javascript
var js_stockdata = {
  "sh600000": {
    "name": "浦发银行",
    "price": 10.50,
    "change": 0.02,
    "changeRatio": 0.19,
    "high": 10.55,
    "low": 10.45,
    "volume": 1000000
  }
};
```

**Python 解析示例**:
```python
import requests
import re
import json

def get_jrj_quote(codes: list) -> dict:
    """获取中金在线实时行情"""
    result = {}
    for code in codes:
        url = f"https://quote.jrj.com.cn/js/{code}/realtime.js"
        resp = requests.get(url, timeout=10)
        match = re.search(r'var js_stockdata\s*=\s*(\{.*?\});', resp.text, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            result.update(data)
    return result
```

---

## 5. 和讯网

### 5.1 实时行情接口

**端点**: `https://quotes.money.163.com/service/<code_list>.html`

**代码格式**: `0sh600000,0sz000001`

**返回格式** (CSV):
```
代码,名称,最新价,涨跌幅,涨跌额,成交量,成交额,今开,最高,最低,昨收
600000,浦发银行,10.50,0.19,0.02,1000000,105000000,10.48,10.55,10.45,10.48
```

**Python 解析示例**:
```python
import requests
import csv
import io

def get_hexun_quote(codes: list) -> dict:
    """获取和讯网实时行情"""
    url = f"https://quotes.money.163.com/service/{','.join(codes)}.html"
    resp = requests.get(url, timeout=10)
    resp.encoding = 'gbk'
    reader = csv.DictReader(io.StringIO(resp.text))
    result = {}
    for row in reader:
        code = row['代码']
        result[code] = {
            'name': row['名称'],
            'price': float(row['最新价']),
            'change_pct': float(row['涨跌幅']),
            'volume': int(row['成交量']),
        }
    return result
```

---

## 6. 凤凰财经

### 6.1 历史 K 线接口

**端点**: `https://api.finance.ifeng.com/akdaily/`

**参数**:
| 参数 | 必填 | 说明 |
|------|------|------|
| `code` | 是 | 股票代码，如 `sh600000` |

**返回格式** (JSON):
```json
{
  "data": {
    "sh600000": {
      "daily": [
        {"date": "2024-01-01", "open": 10.48, "close": 10.50, "high": 10.55, "low": 10.45, "volume": 1000000}
      ]
    }
  }
}
```

**Python 解析示例**:
```python
import requests

def get_ifeng_kline(code: str) -> list:
    """获取凤凰财经历史K线"""
    url = f"https://api.finance.ifeng.com/akdaily/?code={code}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    return data.get('data', {}).get(code, {}).get('daily', [])
```

---

## 7. 东方财富板块行情

### 7.1 板块列表接口

**端点**: `https://push2.eastmoney.com/api/qt/clist/get`

**参数**:
| 参数 | 必填 | 说明 |
|------|------|------|
| `pn` | 否 | 页码，默认 1 |
| `pz` | 否 | 每页条数，默认 20 |
| `po` | 否 | 排序方向，1=升序 -1=降序 |
| `np` | 否 | 是否翻页，1=是 |
| `fltt` | 否 | 复权类型，2=前复权 |
| `invt` | 否 | 市场类型，2=沪深A股 |
| `fid` | 否 | 排序字段，f3=涨跌幅 |
| `fs` | 是 | 市场代码 |
| `fields` | 否 | 返回字段 |

**市场代码**:
- `m:0+t:6`: 沪A
- `m:1+t:8`: 深A
- `m:0+t:7`: 沪B
- `m:1+t:9`: 深B
- `m:90+t:2`: 概念板块
- `m:90+t:3`: 行业板块

**示例**:
```python
import requests

def get_eastmoney_sector(sector_type: str = 'concept') -> list:
    """获取东方财富板块行情"""
    fs_map = {
        'concept': 'm:90+t:2',
        'industry': 'm:90+t:3',
        'area': 'm:90+t:4',
        'sh_a': 'm:0+t:6',
        'sz_a': 'm:1+t:8'
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": 50,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": fs_map.get(sector_type, 'm:90+t:2'),
        "fields": "f1,f2,f3,f4,f12,f13,f14"
    }
    resp = requests.get(url, params=params, timeout=10)
    data = resp.json()
    return data.get('data', {}).get('diff', [])
```

---

## 8. 新浪多市场数据

### 8.1 港股行情

**端点**: `https://hq.sinajs.cn/list=<code_list>`

**代码格式**: `hk00700`(腾讯), `hk09961`(阿里)

**返回格式**:
```javascript
var hq_str_hk00700="腾讯控股,300.20,298.00,302.50,299.80,301.00,300.00,302.00,298.50,1000000,300000000,2024-01-01,15:00:00,0,+0.50,+0.17,...";
```

### 8.2 美股行情

**端点**: `https://hq.sinajs.cn/list=<code_list>`

**代码格式**: `gb_aapl`(苹果), `gb_googl`(谷歌)

### 8.3 指数行情

**端点**: `https://hq.sinajs.cn/list=<code_list>`

**代码格式**: `s_sh000001`(上证指数), `s_sz399001`(深证成指), `s_sz399006`(创业板指)

---

## 9. 其他数据源

### 9.1 基金数据

**端点**: `https://fund.eastmoney.com/pingzhongdata/<code>.js`

**代码格式**: 纯数字，如 `000001`

**返回**: JS 变量，包含基金净值、持仓、规模等信息

### 9.2 期货数据

**端点**: `https://hq.sinajs.cn/list=<code_list>`

**代码格式**: `hf_GC`(黄金), `hf_CL`(原油), `hf_SI`(白银)

### 9.3 外汇数据

**端点**: `https://hq.sinajs.cn/list=<code_list>`

**代码格式**: `hf_USDCNY`(美元/人民币), `hf_EURCNY`(欧元/人民币)

---

## 10. 批量抓取最佳实践

### 10.1 并发控制

```python
import asyncio
import aiohttp
from asyncio import Semaphore

async def batch_fetch(codes: list, fetch_func, concurrency: int = 10):
    """批量并发抓取"""
    sem = Semaphore(concurrency)
    
    async def fetch_one(code):
        async with sem:
            try:
                return await fetch_func(code)
            except Exception as e:
                print(f"Error fetching {code}: {e}")
                return None
    
    tasks = [fetch_one(code) for code in codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if r is not None and not isinstance(r, Exception)]
```

### 10.2 缓存策略

```python
from functools import lru_cache
import time

@lru_cache(maxsize=1000)
def get_cached_quote(code: str, cache_time: int = 60):
    """带缓存的行情获取"""
    # 实际实现中应检查缓存时间
    return fetch_quote(code)
```

### 10.3 错误重试

```python
import random

def fetch_with_retry(url: str, max_retries: int = 3, backoff: float = 1.0):
    """带退避的重试"""
    for i in range(max_retries):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp
            time.sleep(backoff * (2 ** i) + random.uniform(0, 1))
        except Exception as e:
            if i == max_retries - 1:
                raise
            time.sleep(backoff * (2 ** i))
    return None
```

---

## 11. 数据源对比表

| 数据源 | 实时行情 | 历史K线 | 财务数据 | 板块数据 | 反爬等级 | 推荐场景 |
|--------|----------|----------|----------|----------|----------|----------|
| 腾讯财经 | ✅ | ✅ | ✅ | ❌ | 低 | 快速行情查询 |
| 网易财经 | ✅ | ✅ | ❌ | ❌ | 低 | 批量行情获取 |
| 百度股市通 | ✅ | ✅ | ✅ | ❌ | 低 | 综合数据查询 |
| 中金在线 | ✅ | ❌ | ❌ | ❌ | 低 | 备用行情源 |
| 和讯网 | ✅ | ✅ | ❌ | ❌ | 低 | CSV 格式需求 |
| 凤凰财经 | ❌ | ✅ | ❌ | ❌ | 低 | 历史K线补充 |
| 东方财富 | ✅ | ✅ | ✅ | ✅ | 中 | 板块/概念数据 |
| 新浪财经 | ✅ | ✅ | ❌ | ❌ | 低 | 港股/美股/期货 |

---

> **注意**: 以上接口可能随时变更，建议定期验证接口可用性。如遇 403 或返回空数据，请检查代码格式或尝试备用接口。
