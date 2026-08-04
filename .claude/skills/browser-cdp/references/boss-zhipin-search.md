# BOSS 直聘搜索器（boss_zhipin_search.py）完整文档

## 概述

BOSS 直聘搜索器通过 CDP 控制浏览器访问 zhipin.com，支持职位搜索，自动处理字体加密和无限滚动加载。

## 类结构

```python
from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig, JobInfo
from src.searchers.base import SearcherConfig
```

### BossZhipinConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `wait_timeout` | int | 30 | 等待超时（秒） |
| `max_results` | int | 20 | 最大结果数 |
| `retry_count` | int | 3 | 重试次数 |
| `city` | str | "" | 城市代码（可选） |
| `salary_min` | int | 0 | 最低薪资（千） |
| `salary_max` | int | 0 | 最高薪资（千，0 表示不限） |
| `experience` | str | "" | 经验要求 |
| `education` | str | "" | 学历要求 |
| `scroll_limit` | int | 10 | 最大滚动次数 |
| `scroll_delay` | float | 0.8 | 滚动间隔（秒） |

### JobInfo

继承 `SearchResult`，扩展职位特有字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `company` | str | 公司名称 |
| `company_stage` | str | 公司阶段（A 轮/B 轮/上市公司等） |
| `company_size` | str | 公司规模 |
| `salary` | str | 薪资范围（如"15-25K"） |
| `salary_min` | int | 最低薪资（千） |
| `salary_max` | int | 最高薪资（千） |
| `experience_req` | str | 经验要求 |
| `education_req` | str | 学历要求 |
| `job_tags` | list[str] | 职位标签 |
| `benefits` | list[str] | 福利标签 |
| `publish_time` | str | 发布时间 |
| `job_type` | str | 职位类型（全职/实习） |

## 使用示例

### 基本搜索

```python
from src.searchers.boss_zhipin_search import BossZhipinSearcher

searcher = BossZhipinSearcher()
results = searcher.search('Python 工程师')

for job in results.results:
    print(f"职位: {job.title}")
    print(f"公司: {job.company}")
    print(f"薪资: {job.metadata.get('salary', 'N/A')}")
    print(f"地点: {job.metadata.get('city', 'N/A')}")
    print(f"经验: {job.metadata.get('experience_req', 'N/A')}")
    print(f"标签: {job.metadata.get('job_tags', [])}")
    print("---")

searcher.close()
```

### 带筛选条件搜索

```python
config = BossZhipinConfig(
    city="101010100",  # 北京
    salary_min=20,
    salary_max=40,
    experience="3-5 年",
    education="本科"
)
searcher = BossZhipinSearcher(config=config)
results = searcher.search('Java 后端')
```

### 批量搜索

```python
searcher = BossZhipinSearcher()
results = searcher.search_batch(['Python 工程师', 'Go 工程师', 'Java 工程师'])

# 保存结果
results.save_json('output/zhopin_results.json')
results.save_csv('output/zhopin_results.csv')
```

### 获取职位详情

```python
searcher = BossZhipinSearcher()
detail = searcher.get_job_detail('zpin_12345678')
print(f"职位描述: {detail.snippet}")
print(f"福利: {detail.metadata.get('benefits', [])}")
print(f"发布时间: {detail.metadata.get('publish_time', 'N/A')}")
```

## 输出格式

### 职位搜索结果

```json
{
  "source": "boss_zhipin",
  "title": "Python 后端工程师",
  "url": "https://www.zhipin.com/web/geek/job/12345678.html",
  "snippet": "负责后端服务开发...",
  "author": "某科技公司",
  "metadata": {
    "company": "某科技公司",
    "company_stage": "A 轮",
    "company_size": "100-499 人",
    "salary": "20-35K",
    "salary_min": 20,
    "salary_max": 35,
    "city": "北京",
    "district": "朝阳区",
    "experience_req": "3-5 年",
    "education_req": "本科",
    "job_tags": ["Python", "Django", "MySQL"],
    "benefits": ["五险一金", "带薪年假", "弹性工作"],
    "publish_time": "3 天前",
    "job_type": "全职"
  },
  "scraped_at": "2024-01-20T10:30:00Z"
}
```

## 核心实现要点

### 1. 字体加密处理

BOSS 直聘使用字体加密保护薪资信息，需要解码映射：

```python
def _decode_font_encryption(self, text: str, mapping: dict) -> str:
    """解码字体加密文本"""
    if not mapping:
        return text
    
    result = text
    for encoded_char, real_char in mapping.items():
        result = result.replace(encoded_char, real_char)
    return result

def _load_font_mapping(self, session) -> dict:
    """从页面加载字体映射"""
    js_code = """
    (function() {
        var fontFace = document.fonts;
        var mapping = {};
        // 提取字体映射
        return JSON.stringify(mapping);
    })()
    """
    result = session.execute_cdp_cmd('Runtime.evaluate', {'expression': js_code})
    try:
        return json.loads(result.get('result', {}).get('value', '{}'))
    except:
        return {}
```

### 2. 搜索 URL 构建

```python
BASE_URL = "https://www.zhipin.com/web/geek/jobs"

def _build_search_url(self, keyword: str) -> str:
    params = {'query': keyword}
    if self.config.city:
        params['city'] = self.config.city
    if self.config.salary_min:
        params['salMin'] = self.config.salary_min
    if self.config.salary_max:
        params['salMax'] = self.config.salary_max
    # 构建完整 URL
    return f"{BASE_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
```

### 3. 职位卡片解析

```python
def _parse_job_card(self, card_html: str, font_mapping: dict) -> JobInfo:
    """解析单个职位卡片"""
    # 提取标题、公司、薪资等信息
    # 应用字体解密
    title = self._decode_font_encryption(raw_title, font_mapping)
    salary = self._decode_font_encryption(raw_salary, font_mapping)
    
    return JobInfo(
        source="boss_zhipin",
        title=title,
        url=job_url,
        snippet=description,
        author=company_name,
        metadata={
            'company': company_name,
            'salary': salary,
            'salary_min': salary_min,
            'salary_max': salary_max,
            'city': city,
            'experience_req': experience,
            'education_req': education,
            'job_tags': tags,
            'benefits': benefits
        }
    )
```

### 4. 无限滚动加载

```python
def _scroll_to_load(self, session, max_scrolls: int = 10) -> int:
    """滚动加载更多职位"""
    from src.core.dynamic_loader import DynamicLoader
    loader = DynamicLoader(session)
    return loader.scroll_to_load(
        max_height_change=500,
        height_threshold=100,
        max_scrolls=max_scrolls,
        scroll_delay=0.8
    )
```

### 5. 去重策略

```python
# 基于 URL 去重
results.deduplicate(by="url")

# 基于标题去重（相似度阈值 0.9）
results.deduplicate(by="title", threshold=0.9)
```

## 已知限制

1. **登录态依赖**：部分职位信息需要登录才能查看
2. **字体加密**：薪资信息可能被字体加密，需要解码
3. **反爬机制**：高频搜索可能触发验证码或 IP 封禁
4. **数据延迟**：职位发布时间可能有延迟
5. **分页限制**：最多加载 100 个结果

## 最佳实践

1. **控制搜索频率**：每次搜索间隔至少 3 秒
2. **使用去重**：批量搜索时启用去重避免重复
3. **限制滚动次数**：设置合理的 `scroll_limit` 避免过度加载
4. **异常处理**：捕获 `CDPError` 和超时异常
5. **字体解密**：自动处理字体加密，无需手动干预

## 测试覆盖

- JobInfo 测试：字段验证、序列化
- 配置测试：默认值、序列化
- 字体解密测试：映射解码、空文本、无映射、部分映射
- 字体映射加载测试：成功、失败
- 职位卡片解析测试：完整数据、空标题、字体加密、标签解析
- URL 构建测试：基础、城市、薪资范围
- 去重测试：标题去重、URL 去重
- 滚动加载测试：停止条件、继续条件
- 集成测试：搜索流程、详情获取、批量搜索
- 边界测试：空结果、JS 错误、JSON 解析错误

共 31 个测试用例，全部通过。
