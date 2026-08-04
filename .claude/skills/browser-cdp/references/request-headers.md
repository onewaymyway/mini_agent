# 请求头伪装模块（request_headers.py）

> 按站点自定义请求头、Sec-Fetch-* 现代浏览器头、预定义站点配置、动态 Referer 生成。

---

## 1. 核心功能

| 功能 | 说明 |
|------|------|
| 现代浏览器头 | 自动添加 `Sec-Fetch-*`、`Accept`、`Accept-Language` 等 |
| 站点自定义 | 按域名配置专属请求头 |
| 动态 Referer | 根据目标 URL 自动生成 Referer |
| 全局单例 | `get_header_manager()` 返回全局管理器 |

---

## 2. 快速开始

```python
from src.core.request_headers import get_header_manager, HeaderConfig

# 获取全局管理器
mgr = get_header_manager()

# 获取标准浏览器头
headers = mgr.get_headers("https://www.example.com")
print(headers["User-Agent"])
print(headers["Sec-Fetch-Site"])

# 为特定站点配置
mgr.update_config("bilibili", HeaderConfig(
    custom_headers={"X-Requested-With": "XMLHttpRequest"}
))
```

---

## 3. 预定义站点配置

| 站点 | 特殊配置 |
|------|----------|
| `bilibili` | `X-Requested-With: XMLHttpRequest` |
| `zhihu` | `Sec-Fetch-User: ?1` |
| `jd` | `Referer: https://www.jd.com/` |
| `taobao` | `Cookie: _m_h5_tk=...` |
| `weibo` | `X-Requested-With: XMLHttpRequest` |

---

## 4. API 参考

### 4.1 HeaderConfig

```python
@dataclass
class HeaderConfig:
    custom_headers: dict = field(default_factory=dict)
    sec_fetch_site: str = "same-origin"
    sec_fetch_mode: str = "navigate"
    sec_fetch_dest: str = "document"
    accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8"
```

### 4.2 RequestHeaderManager

| 方法 | 说明 |
|------|------|
| `get_headers(url)` | 获取指定 URL 的请求头 |
| `update_config(site, config)` | 更新站点配置 |
| `clear()` | 清空所有自定义配置 |

### 4.3 全局函数

```python
# 获取/设置/重置全局管理器
mgr = get_header_manager()
set_header_manager(mgr)
reset_header_manager()
```

---

## 5. 使用示例

### 5.1 基础用法

```python
from src.core.request_headers import get_header_manager

mgr = get_header_manager()
headers = mgr.get_headers("https://www.bilibili.com")
# {'User-Agent': '...', 'Sec-Fetch-Site': 'same-origin', ...}
```

### 5.2 自定义站点配置

```python
from src.core.request_headers import get_header_manager, HeaderConfig

mgr = get_header_manager()
mgr.update_config("my_site", HeaderConfig(
    custom_headers={"X-Custom-Header": "value"},
    sec_fetch_site="cross-site"
))
headers = mgr.get_headers("https://my_site.com")
```

### 5.3 动态 Referer

```python
# Referer 自动根据目标 URL 生成
headers = mgr.get_headers("https://example.com/page")
# Referer: https://example.com/
```

---

## 6. 注意事项

- 全局管理器是单例，所有模块共享同一配置
- 调用 `reset_header_manager()` 可清空所有自定义配置
- 预定义站点配置优先级高于通用配置
