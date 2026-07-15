# 常见错误排查表

> 覆盖反爬应对、IP封禁、签名失效、数据格式变更、浏览器崩溃、内存泄漏、并发控制等高频问题

---

## 1. 反爬应对

### 1.1 症状识别
| 现象 | 可能原因 | 确认方法 |
|------|----------|----------|
| 返回 403/429 | IP 频率限制 / 签名校验失败 | 检查响应头 `X-RateLimit-*`、响应体错误码 |
| 返回空数据/乱码 | 参数加密/签名算法变更 | 对比浏览器 Network 面板请求参数 |
| 返回登录页/验证码 | Cookie 失效 / 需要人机验证 | 检查响应 Content-Type 是否为 text/html |
| 请求挂起/超时 | 服务端主动拖延 / TCP 连接被丢弃 | 抓包分析 TCP 状态 |

### 1.2 通用对策分层

**L1 - 请求层（无头模式）**
```python
# 1. 完整 Header 伪装
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.eastmoney.com/",
    "Origin": "https://www.eastmoney.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# 2. Cookie 维护
session.cookies.update({
    "qgqp_b_id": "...",  # 东方财富必需
    "st_si": "...",
    "st_psi": "...",
})

# 3. 指数退避重试
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError))
)
async def fetch_with_retry(url, **kwargs):
    ...
```

**L2 - 代理层**
```python
# 代理池轮换
proxies = [
    "http://user:pass@proxy1:port",
    "http://user:pass@proxy2:port",
]

async def fetch_with_proxy(url):
    for proxy in cycle(proxies):
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=30) as client:
                return await client.get(url)
        except Exception:
            continue  # 尝试下一个代理
    raise Exception("All proxies failed")
```

**L3 - 浏览器层 (CDP / Playwright)**
```python
# browser-cdp skill 方式
from browser_cdp import CDPClient

async with CDPClient() as cdp:
    page = await cdp.new_page()
    await page.goto(url, wait_until="networkidle")
    # 等待动态渲染完成
    content = await page.content()
    # 处理验证码：人工介入或第三方打码
    if "captcha" in content:
        await handle_captcha(page)
```

### 1.3 东方财富签名逆向要点
- **签名参数**：`sign = md5(path + sorted_params + secret_key)`
- **secret_key**：需从网页 JS 中提取（通常在 `guba.eastmoney.com/xxx.js`）
- **参数排序**：按 key ASCII 升序拼接
- **时间戳**：`_` 参数为毫秒级时间戳，需与服务端时间同步
- **更新频率**：secret_key 约每 1-3 个月轮换一次，建议自动化提取

---

## 2. IP 封禁

### 2.1 识别特征
- 同一 IP 短时间内大量 403/429
- 返回页面包含 "访问过于频繁"、"IP 已被限制" 等文案
- TCP 连接建立成功但无响应数据（黑洞路由）

### 2.2 缓解策略
| 策略 | 适用场景 | 成本 | 效果 |
|------|----------|------|------|
| 住宅代理池 | 高频、长期抓取 | 高 | ★★★★★ |
| 数据中心代理 + 低频 | 低频、非核心业务 | 低 | ★★★☆☆ |
| 请求间隔抖动 | 所有场景 | 无 | ★★★☆☆ |
| 多账号 Cookie 轮换 | 需登录的站点 | 中 | ★★★★☆ |
| CDP 真实浏览器 | 复杂交互/验证码 | 高 | ★★★★★ |

### 2.3 代理健康度监控
```python
async def check_proxy_health(proxy: str) -> bool:
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=10) as client:
            resp = await client.get("https://httpbin.org/ip")
            return resp.status_code == 200
    except:
        return False

# 定期清理失效代理
healthy_proxies = [p for p in proxies if await check_proxy_health(p)]
```

---

## 3. 签名/Token 失效

### 3.1 东方财富股吧签名
```python
# 典型失效场景：secret_key 轮换、参数顺序变更、新增必填参数
# 解决：建立自动化提取流程

async def extract_sign_secret() -> str:
    """从股吧页面 JS 中提取当前 secret_key"""
    async with CDPClient() as cdp:
        page = await cdp.new_page()
        await page.goto("https://guba.eastmoney.com/")
        # 监听网络请求，捕获签名生成逻辑
        # 或直接从页面全局变量读取
        secret = await page.evaluate("() => window._guba_sign_secret")
        return secret
```

### 3.2 雪球 Cookie 维护
```python
# xq_a_token 有效期约 30 天，需定期刷新
async def refresh_xueqiu_cookie():
    async with CDPClient() as cdp:
        page = await cdp.new_page()
        await page.goto("https://xueqiu.com/")
        # 等待登录完成（人工或自动填充）
        await page.wait_for_selector(".user-info")
        cookies = await page.context.cookies()
        # 保存到持久化存储
        save_cookies(cookies)
```

### 3.3 财联社/华尔街见闻 Token
- 登录后从 LocalStorage/IndexedDB 提取
- 设置定时任务每 6 小时检查有效性，失效自动刷新

---

## 4. 数据格式变更

### 4.1 常见变更类型
| 类型 | 示例 | 检测方法 |
|------|------|----------|
| 字段重命名 | `f43` → `f143` | 对比历史样本 Schema |
| 字段类型变更 | 整数 → 字符串 | 类型断言失败报警 |
| 新增/删除字段 | 新增 `f999` | Schema diff 监控 |
| 接口迁移 | v1 → v2 路径变更 | 404/新域名响应 |
| 加密方式变更 | 明文 → AES 加密 | 响应体不可读 |

### 4.2 自适应解析器模式
```python
from pydantic import BaseModel, Field
from typing import Optional

class QuoteSchemaV1(BaseModel):
    f43: float = Field(alias="open")
    f44: float = Field(alias="high")
    # ...

class QuoteSchemaV2(BaseModel):
    f143: float = Field(alias="open")
    f144: float = Field(alias="high")
    # ...

SCHEMAS = [QuoteSchemaV2, QuoteSchemaV1]  # 优先尝试新版本

def parse_quote(raw: dict) -> Quote:
    for schema in SCHEMAS:
        try:
            return schema(**raw)
        except ValidationError:
            continue
    raise ValueError("No matching schema")
```

### 4.3 变更监控告警
```python
# 定期抓取样本数据，对比 Schema 指纹
import hashlib

def schema_fingerprint(data: dict) -> str:
    keys = sorted(k for k, v in data.items() if not k.startswith("_"))
    types = tuple(type(data[k]).__name__ for k in keys)
    return hashlib.md5(str((keys, types)).encode()).hexdigest()[:8]

# 存储指纹，检测变化时发送告警
```

---

## 5. 浏览器崩溃 / CDP 连接断开

### 5.1 常见原因
| 原因 | 现象 | 预防 |
|------|------|------|
| 内存泄漏 | 进程内存持续增长直到 OOM | 定期重启浏览器、限制并发页数 |
| GPU 进程崩溃 | `GPU process exited unexpectedly` | 启动参数 `--disable-gpu` |
| 页面脚本死循环 | 单标签页 CPU 100% | 设置脚本执行超时 |
| CDP 端口冲突 | `Address already in use` | 动态分配端口、单例模式 |
| Chrome 自动更新 | 版本不匹配导致 CDP 协议不兼容 | 固定 Chrome 版本、禁用自动更新 |

### 5.2 健壮的浏览器池管理
```python
class BrowserPool:
    def __init__(self, max_size=2, max_pages_per_browser=5):
        self.max_size = max_size
        self.max_pages = max_pages_per_browser
        self.browsers: List[CDPClient] = []
        self.page_counts: Dict[CDPClient, int] = {}
    
    async def acquire(self) -> CDPClient:
        # 复用空闲浏览器
        for b in self.browsers:
            if self.page_counts[b] < self.max_pages:
                self.page_counts[b] += 1
                return b
        # 创建新浏览器
        if len(self.browsers) < self.max_size:
            b = await CDPClient.create(
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
            )
            self.browsers.append(b)
            self.page_counts[b] = 1
            return b
        # 等待释放
        await asyncio.sleep(1)
        return await self.acquire()
    
    async def release(self, browser: CDPClient):
        self.page_counts[browser] -= 1
        # 定期重启长时间运行的浏览器
        if self.page_counts[browser] == 0 and browser.uptime > 3600:
            await browser.close()
            self.browsers.remove(browser)
            del self.page_counts[browser]
```

### 5.3 启动参数推荐
```bash
chrome --remote-debugging-port=9222 \
    --disable-gpu \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-extensions \
    --disable-background-networking \
    --disable-sync \
    --disable-translate \
    --hide-scrollbars \
    --mute-audio \
    --no-first-run \
    --no-default-browser-check
```

---

## 6. 内存泄漏

### 6.1 Python 端常见泄漏点
| 场景 | 原因 | 解决 |
|------|------|------|
| 大量 DataFrame 累积 | 未及时写入磁盘/数据库 | 分批处理、显式 `del` + `gc.collect()` |
| 异步任务未回收 | `create_task` 无引用持有 | 使用 `TaskGroup` / 维护任务集合 |
| 连接池未关闭 | `httpx.AsyncClient` / 数据库连接 | `async with` / 显式 `aclose()` |
| 缓存无上限 | `lru_cache` / 字典无限增长 | 设置 `maxsize`、TTL 淘汰 |

### 6.2 内存剖析工具
```bash
# 1. objgraph 查找对象引用
pip install objgraph
python -c "import objgraph; objgraph.show_most_common_types(limit=20)"

# 2. memory_profiler 逐行分析
pip install memory_profiler
@profile
def my_function(): ...
python -m memory_profiler script.py

# 3. tracemalloc 标准库
import tracemalloc
tracemalloc.start()
# ... 运行代码 ...
snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics('lineno')[:10]:
    print(stat)
```

### 6.3 生产环境内存守护
```python
import psutil
import os

async def memory_guard(threshold_mb=2048):
    """超过阈值自动重启工作进程"""
    while True:
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / 1024 / 1024
        if mem_mb > threshold_mb:
            logger.warning(f"Memory {mem_mb:.0f}MB > {threshold_mb}MB, restarting...")
            # 触发优雅重启（由进程管理器处理）
            os._exit(1)  # 或发送信号给 supervisor
        await asyncio.sleep(60)
```

---

## 7. 并发控制

### 7.1 信号量限流
```python
import asyncio

class RateLimiter:
    def __init__(self, max_concurrent: int, max_per_minute: int):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.minute_slots = asyncio.Semaphore(max_per_minute)
        self.request_times: List[float] = []
    
    async def __aenter__(self):
        await self.semaphore.acquire()
        # 滑动窗口限流
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]
        if len(self.request_times) >= self.minute_slots._value:
            wait_time = 60 - (now - self.request_times[0])
            await asyncio.sleep(wait_time)
        self.request_times.append(now)
    
    async def __aexit__(self, *args):
        self.semaphore.release()

# 使用
limiter = RateLimiter(max_concurrent=5, max_per_minute=60)
async with limiter:
    await fetch(url)
```

### 7.2 优先级队列
```python
import heapq

class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._index = 0
    
    async def put(self, item, priority=0):
        heapq.heappush(self._queue, (priority, self._index, item))
        self._index += 1
    
    async def get(self):
        return heapq.heappop(self._queue)[2]

# 高优先级：实时行情、告警推送
# 低优先级：历史回补、全量同步
```

### 7.3 熔断器模式
```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = None
        self.state = "closed"  # closed/open/half-open
    
    async def call(self, func, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                raise Exception("Circuit breaker open")
        
        try:
            result = await func(*args, **kwargs)
            self.on_success()
            return result
        except Exception as e:
            self.on_failure()
            raise
    
    def on_success(self):
        self.failures = 0
        self.state = "closed"
    
    def on_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "open"
```

---

## 8. 快速诊断清单

遇到问题时按顺序排查：

1. **网络层**：`curl -v` / `telnet` 确认连通性、DNS、TLS
2. **协议层**：抓包对比浏览器请求，检查 Header、Cookie、参数签名
3. **应用层**：解析响应体，确认错误码、数据结构、加密方式
4. **业务层**：验证数据完整性、去重键、时间对齐、字段映射
5. **资源层**：检查内存、CPU、文件描述符、代理池可用率
6. **依赖层**：上游服务健康检查、Token 有效性、版本兼容性

---

## 9. 常用调试命令速查

```bash
# 查看端口占用
netstat -ano | findstr :9222

# 查看进程树
tasklist /fi "imagename eq chrome*"

# 抓包分析 (Windows)
netsh trace start capture=yes tracefile=trace.etl
# ... 复现问题 ...
netsh trace stop

# Python 进程内存
python -c "import psutil; p=psutil.Process(); print(f'RSS: {p.memory_info().rss/1024/1024:.0f}MB')"

# 异步任务堆栈
python -c "import asyncio; print(asyncio.all_tasks())"
```

---

> **完整排查案例库**请查阅 `references/full-api-docs/troubleshooting-cases/`